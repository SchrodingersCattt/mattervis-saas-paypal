"""Stable v1 HTTP routes."""

from __future__ import annotations

from flask import Blueprint, jsonify, request, send_file
from io import BytesIO
from datetime import datetime, timezone

from .errors import ServiceError


def register_routes(app, service) -> None:
    api = Blueprint("mattervis_v1", __name__, url_prefix="/v1")

    @api.get("/healthz")
    def healthz():
        return jsonify({"ok": True, "service": "mattervis-saas", "api_version": 1})

    @api.get("/capabilities")
    def capabilities():
        return jsonify(service.capabilities())

    def principal(project_id: str | None = None):
        return service.auth.authenticate(request, project_id=project_id)

    @api.post("/assets")
    def upload():
        project_id = request.form.get("project_id") or request.headers.get("X-Project-ID")
        identity = principal(project_id)
        if "file" not in request.files:
            raise ServiceError("multipart field 'file' is required")
        file = request.files["file"]
        data = file.read(service.config.max_upload_bytes + 1)
        asset, existed = service.upload(identity, file.filename or "upload", data, request.form.get("input_format"))
        return jsonify({"asset": asset, "existing": existed}), (200 if existed else 201)

    def body() -> dict:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ServiceError("JSON object body is required")
        return payload

    def operation(kind: str):
        payload = body()
        identity = principal(payload.get("project_id"))
        job, existed = service.submit(
            identity, kind, payload, idempotency_key=request.headers.get("Idempotency-Key")
        )
        status = 200 if job.get("status") in {"succeeded", "failed"} else 202
        return jsonify({"job": job, "existing": existed}), status

    @api.post("/inspect")
    def inspect():
        return operation("inspect")

    @api.post("/topology")
    def topology():
        return operation("topology")

    @api.post("/render")
    def render():
        return operation("render")

    @api.get("/jobs/<job_id>")
    def job(job_id: str):
        identity = principal(request.args.get("project_id"))
        return jsonify({"job": service.job(identity, job_id)})

    @api.get("/jobs/<job_id>/artifacts")
    def job_artifacts(job_id: str):
        identity = principal(request.args.get("project_id"))
        return jsonify({"artifacts": service.job(identity, job_id)["artifacts"]})

    @api.get("/artifacts/<artifact_id>/download")
    def artifact_download(artifact_id: str):
        expires = request.args.get("expires")
        signature = request.args.get("signature")
        if expires and signature and service.store.verify_signature(
            artifact_id=artifact_id, expires=int(expires), signature=signature
        ):
            row = service.db.get_artifact_any(artifact_id)
            if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
                raise ServiceError("artifact has expired", status_code=404)
            data = service.store.read_bytes(row["storage_key"])
            return send_file(BytesIO(data), mimetype=row["content_type"],
                             download_name=row["filename"], as_attachment=False)
        identity = principal(request.args.get("project_id"))
        row, data = service.artifact(identity, artifact_id)
        return send_file(BytesIO(data), mimetype=row["content_type"],
                         download_name=row["filename"], as_attachment=False)

    @api.post("/projects/<project_id>/api-keys")
    def api_key(project_id: str):
        identity = principal(project_id)
        payload = body()
        raw, metadata = service.create_api_key(identity, project_id, payload.get("name", ""), payload.get("scopes", []))
        return jsonify({"key": raw, "api_key": metadata}), 201

    app.register_blueprint(api)
