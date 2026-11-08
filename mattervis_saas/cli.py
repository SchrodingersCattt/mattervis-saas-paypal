"""Command-line client for the stable MatterVis HTTP API."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
import uuid


def _headers(args, content_type: str | None = None) -> dict[str, str]:
    result = {"Accept": "application/json"}
    if args.api_key:
        result["X-API-Key"] = args.api_key
    if args.project_id:
        result["X-Project-ID"] = args.project_id
    if content_type:
        result["Content-Type"] = content_type
    return result


def _request(args, method: str, path: str, data: bytes | None = None, content_type: str | None = None):
    url = args.base_url.rstrip("/") + path
    request = urllib.request.Request(url, data=data, headers=_headers(args, content_type), method=method)
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            raw = response.read()
            if "application/json" in response.headers.get("Content-Type", ""):
                return response.status, json.loads(raw.decode("utf-8"))
            return response.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            payload = {"error": {"message": raw.decode("utf-8", "replace")}}
        return exc.code, payload


def _json_request(args, method: str, path: str, payload: dict):
    return _request(args, method, path, json.dumps(payload, ensure_ascii=False).encode(), "application/json")


def _upload(args, path: Path) -> dict:
    boundary = "----mattervis" + uuid.uuid4().hex
    data = path.read_bytes()
    chunks = [
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode(),
        data,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    _, payload = _request(args, "POST", "/v1/assets", b"".join(chunks), f"multipart/form-data; boundary={boundary}")
    if not isinstance(payload, dict) or "asset" not in payload:
        raise SystemExit(json.dumps(payload, ensure_ascii=False))
    return payload["asset"]


def _print(status: int, payload):
    print(json.dumps({"status_code": status, "response": payload}, ensure_ascii=False, indent=2, default=str))
    return 0 if 200 <= status < 300 else 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="mattervis-api")
    parser.add_argument("--base-url", default="http://127.0.0.1:50001")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--project-id", default="dev")
    parser.add_argument("--timeout", type=float, default=60.0)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("health")
    sub.add_parser("capabilities")
    for name in ("inspect", "render", "topology"):
        command = sub.add_parser(name)
        command.add_argument("input", type=Path)
        if name == "render":
            command.add_argument("--output-format", default="png")
            command.add_argument("--spec", default="{}", help="JSON RenderSpec object")
        if name == "topology":
            command.add_argument("--center-index", type=int, required=True)
            command.add_argument("--cutoff", type=float, default=10.0)
    job = sub.add_parser("job")
    job.add_argument("job_id")

    args = parser.parse_args(argv)
    if args.command == "health":
        status, payload = _request(args, "GET", "/v1/healthz")
    elif args.command == "capabilities":
        status, payload = _request(args, "GET", "/v1/capabilities")
    elif args.command == "job":
        query = urllib.parse.urlencode({"project_id": args.project_id})
        status, payload = _request(args, "GET", f"/v1/jobs/{urllib.parse.quote(args.job_id)}?{query}")
    else:
        asset = _upload(args, args.input)
        body = {"asset_id": asset["id"]}
        if args.command == "render":
            try:
                body["spec"] = json.loads(args.spec)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"--spec must be JSON: {exc}") from exc
            body["output_format"] = args.output_format
        elif args.command == "topology":
            body.update(center_index=args.center_index, cutoff=args.cutoff)
        status, payload = _json_request(args, "POST", f"/v1/{args.command}", body)
    raise SystemExit(_print(status, payload))


if __name__ == "__main__":
    main()
