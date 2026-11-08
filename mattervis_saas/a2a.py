"""Minimal A2A bridge for Bohrium Chat."""

from __future__ import annotations

import json
from uuid import uuid4

from flask import Response, jsonify, request

from .auth import Principal


def _authorized(service) -> bool:
    expected = service.config.a2a_token
    if not expected:
        return service.config.auth_mode == "dev"
    authorization = request.headers.get("Authorization", "")
    bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    return bearer == expected or request.headers.get("X-A2A-Key") == expected


def _parts(params: dict) -> tuple[str, dict]:
    message = params.get("message") or {}
    text_parts: list[str] = []
    data: dict = {}
    for part in message.get("parts") or []:
        if part.get("kind") == "text":
            text_parts.append(str(part.get("text", "")))
        elif part.get("kind") == "data" and isinstance(part.get("data"), dict):
            data.update(part["data"])
    return "\n".join(text_parts).strip(), data


def _request_from_message(params: dict) -> tuple[str, dict]:
    text, data = _parts(params)
    payload = dict(data.get("mattervis") or data)
    if not payload and text.startswith("{"):
        try:
            parsed = json.loads(text)
            payload = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            payload = {}
    return text, payload


def _run(service, params: dict) -> str:
    text, payload = _request_from_message(params)
    operation = str(payload.get("operation") or "").lower()
    if not operation:
        lower = text.lower()
        operation = "render" if any(token in lower for token in ("render", "png", "ortep", "画图", "绘图")) else "inspect"
    if operation not in {"inspect", "topology", "render"}:
        return "MatterVis A2A 支持 inspect、topology、render。请提供 operation 和 asset_id。"
    if not payload.get("asset_id"):
        return "请先通过 MatterVis API 上传结构，或在 data.mattervis 中提供 asset_id。"
    # A2A calls use a project API key only when the caller explicitly supplies
    # one. Bohrium's remote connection secret authenticates this endpoint; the
    # development service uses its default project.
    project_id = payload.get("project_id") or service.config.default_project_id
    service.db.ensure_project(project_id)
    identity = Principal(
        subject="a2a", project_id=project_id,
        scopes=frozenset({"structures:read", "jobs:read", "jobs:write", "artifacts:read"}),
        auth_type="a2a",
    )
    job, _ = service.submit(identity, operation, payload, idempotency_key=payload.get("idempotency_key"))
    if job.get("status") == "succeeded":
        artifacts = job.get("artifacts") or []
        links = "\n".join(item.get("download_url", "") for item in artifacts)
        return f"MatterVis {operation} 完成。\n{links}".strip()
    return f"MatterVis {operation} 已提交，job_id={job['id']}。请稍后查询 /v1/jobs/{job['id']}。"


def register_a2a(app, service) -> None:
    @app.get("/.well-known/agent-card.json")
    @app.get("/.well-known/agent.json")
    def agent_card():
        return jsonify({
            "protocolVersion": "0.3.0",
            "name": "MatterVis",
            "description": "Render and inspect atomistic structures and crystal topology.",
            "version": "1.0.0",
            "url": f"{service.config.public_base_url.rstrip('/')}/a2a",
            "preferredTransport": "JSONRPC",
            "capabilities": {"streaming": True},
            "defaultInputModes": ["text/plain", "application/json"],
            "defaultOutputModes": ["text/plain", "application/json"],
            "skills": [
                {"id": "render", "name": "Render structure", "description": "Create PNG, PDF, SVG, HTML or animation artifacts."},
                {"id": "inspect", "name": "Inspect structure", "description": "Return bounded structure metadata."},
                {"id": "topology", "name": "Analyze topology", "description": "Analyze a coordination shell and shape."},
            ],
        })

    @app.post("/a2a")
    def a2a_endpoint():
        if not _authorized(service):
            return jsonify({"error": {"code": "unauthorized", "message": "A2A credential required"}}), 401
        payload = request.get_json(silent=True) or {}
        rpc_id = payload.get("id")
        method = payload.get("method")
        params = payload.get("params") or {}
        if method == "message/send":
            try:
                answer = _run(service, params)
                return jsonify({"jsonrpc": "2.0", "id": rpc_id,
                                "result": {"kind": "message", "role": "agent",
                                            "parts": [{"kind": "text", "text": answer}]}})
            except Exception as exc:
                return jsonify({"jsonrpc": "2.0", "id": rpc_id,
                                "error": {"code": "execution_failed", "message": str(exc)}}), 200
        if method == "message/stream":
            task_id = uuid4().hex
            context_id = uuid4().hex
            try:
                answer = _run(service, params)
                frames = [
                    {"jsonrpc": "2.0", "id": rpc_id, "result": {
                        "kind": "status-update", "taskId": task_id, "contextId": context_id,
                        "final": False, "status": {"state": "working", "message": {
                            "role": "agent", "parts": [{"kind": "text", "text": "MatterVis 任务已接收，正在处理。"}]
                        }}
                    }},
                    {"jsonrpc": "2.0", "id": rpc_id, "result": {
                        "kind": "task", "taskId": task_id, "contextId": context_id, "state": "completed",
                        "message": {"role": "agent", "parts": [{"kind": "text", "text": answer}]}
                    }},
                ]
            except Exception as exc:
                frames = [{"jsonrpc": "2.0", "id": rpc_id, "result": {
                    "kind": "task", "taskId": task_id, "contextId": context_id, "state": "failed",
                    "message": {"role": "agent", "parts": [{"kind": "text", "text": str(exc)}]}
                }}]

            def stream():
                for frame in frames:
                    yield "data: " + json.dumps(frame, ensure_ascii=False) + "\n\n"

            return Response(stream(), mimetype="text/event-stream", headers={"Cache-Control": "no-cache"})
        return jsonify({"jsonrpc": "2.0", "id": rpc_id,
                        "error": {"code": -32601, "message": "method not found"}}), 200
