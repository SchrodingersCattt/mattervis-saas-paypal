"""Application service coordinating assets, jobs and artifacts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from .auth import Authenticator, Principal
from .config import Config
from .core import inspect as inspect_structure
from .core import render as render_structure
from .core import topology as analyze_topology
from .db import Database
from .errors import LimitError, NotFoundError, ServiceError
from .jobs import JobManager, JobOutcome
from .storage import LocalObjectStore


SUPPORTED_INPUT_EXTENSIONS = {
    ".cif", ".vasp", ".poscar", ".contcar", ".xyz", ".extxyz", ".traj",
    ".dump", ".lammpstrj", ".data", ".cube",
}
OUTPUT_TYPES = {
    "png": ("image/png", ".png"),
    "pdf": ("application/pdf", ".pdf"),
    "svg": ("image/svg+xml", ".svg"),
    "html": ("text/html; charset=utf-8", ".html"),
    "gif": ("image/gif", ".gif"),
    "mp4": ("video/mp4", ".mp4"),
}
SCOPES = {
    "structures:read", "structures:write", "jobs:read", "jobs:write", "artifacts:read", "*"
}


def _iso_after(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _content_type(path: Path) -> str:
    return {
        "json": "application/json",
        "png": "image/png",
        "pdf": "application/pdf",
        "svg": "image/svg+xml",
        "html": "text/html; charset=utf-8",
        "gif": "image/gif",
        "mp4": "video/mp4",
    }.get(path.suffix.lower().lstrip("."), "application/octet-stream")


class MatterVisService:
    def __init__(self, config: Config | None = None):
        self.config = config or Config.from_env()
        self.config.ensure_directories()
        self.db = Database(self.config.database_path)
        self.store = LocalObjectStore(self.config.storage_root, self.config.artifact_signing_key)
        self.auth = Authenticator(self.config, self.db)
        self.jobs = JobManager(self.db, self.config.max_concurrent_jobs)

    def close(self) -> None:
        self.jobs.shutdown(wait=True)

    def cleanup_expired(self) -> int:
        before = datetime.now(timezone.utc).isoformat()
        rows = self.db.expired_artifacts(before)
        for row in rows:
            self.store.delete(row["storage_key"])
            self.db.delete_artifact(row["id"])
        return len(rows)

    def capabilities(self) -> dict:
        return {
            "schema": "mattervis.capabilities/v1",
            "operations": ["inspect", "topology", "render"],
            "input_formats": sorted(item.lstrip(".") for item in SUPPORTED_INPUT_EXTENSIONS),
            "output_formats": sorted(OUTPUT_TYPES),
            "render_backends": ["cpu", "matplotlib", "plotly"],
            "limits": {
                "max_upload_bytes": self.config.max_upload_bytes,
                "max_atoms_per_frame": self.config.max_atoms_per_frame,
                "max_trajectory_frames": self.config.max_trajectory_frames,
                "artifact_retention_days": self.config.artifact_retention_days,
            },
            "forge_dependency": False,
        }

    def upload(self, principal: Principal, filename: str, data: bytes, input_format: str | None = None) -> tuple[dict, bool]:
        self.auth.require(principal, "structures:write")
        safe_name = Path(filename or "upload").name
        suffix = Path(safe_name).suffix.lower()
        if suffix not in SUPPORTED_INPUT_EXTENSIONS and not input_format:
            raise ServiceError(f"unsupported input extension {suffix or '<none>'}", details={"supported": sorted(SUPPORTED_INPUT_EXTENSIONS)})
        if not data:
            raise ServiceError("uploaded file is empty")
        if len(data) > self.config.max_upload_bytes:
            raise LimitError("uploaded file exceeds configured size limit", details={"max_bytes": self.config.max_upload_bytes})
        digest = hashlib.sha256(data).hexdigest()
        key = f"projects/{principal.project_id}/assets/{digest}{suffix or '.data'}"
        existing = self.db.find_asset_by_hash(principal.project_id, digest) if hasattr(self.db, "find_asset_by_hash") else None
        if existing:
            return self._public_asset(existing), True
        self.store.put_bytes(key, data)
        asset, existed = self.db.create_asset(
            project_id=principal.project_id, filename=safe_name,
            input_format=input_format or suffix.lstrip("."), sha256=digest,
            storage_key=key, size_bytes=len(data), metadata={"original_filename": safe_name},
        )
        return self._public_asset(asset), existed

    @staticmethod
    def _public_asset(asset: dict) -> dict:
        return {key: value for key, value in asset.items() if key != "storage_key"}

    def _asset_path(self, principal: Principal, asset_id: str) -> tuple[dict, Path]:
        self.auth.require(principal, "structures:read")
        asset = self.db.get_asset(principal.project_id, asset_id)
        path = (self.store.root / asset["storage_key"]).resolve()
        if self.store.root not in path.parents or not path.is_file():
            raise NotFoundError("stored asset is unavailable")
        return asset, path

    def _estimate_sync(self, path: Path, kind: str, request: dict) -> bool:
        if request.get("force_async"):
            return False
        if path.stat().st_size > self.config.sync_max_bytes:
            return False
        if kind == "render" and str(request.get("output_format", "png")).lower() in {"gif", "mp4"}:
            return int(request.get("frame_count", 1) or 1) <= self.config.sync_max_frames
        if int(request.get("frame_count", 1) or 1) > self.config.sync_max_frames:
            return False
        if request.get("atom_count") is not None and int(request["atom_count"]) > self.config.sync_max_atoms:
            return False
        return True

    def _make_work(self, principal: Principal, job_id: str, kind: str, request: dict) -> Any:
        asset_id = request.get("asset_id")
        if not asset_id:
            raise ServiceError("asset_id is required")
        _, source_path = self._asset_path(principal, str(asset_id))

        def work() -> JobOutcome:
            job_dir = self.config.root_dir / ".local" / "jobs" / job_id
            job_dir.mkdir(parents=True, exist_ok=True)
            if kind == "inspect":
                result = inspect_structure(source_path, request)
                output = job_dir / "inspect.json"
                output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                artifact = self._save_artifact(principal, job_id, output, "inspection")
                return JobOutcome(result={"data": result, "artifacts": [artifact]}, artifacts=[artifact])
            if kind == "topology":
                result = analyze_topology(source_path, request)
                output = job_dir / "topology.json"
                output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                artifact = self._save_artifact(principal, job_id, output, "topology")
                return JobOutcome(result={"data": result, "artifacts": [artifact]}, artifacts=[artifact])
            if kind != "render":
                raise ServiceError(f"unsupported operation {kind}")
            output_format = str(request.get("output_format", "png")).lower().lstrip(".")
            if output_format not in OUTPUT_TYPES:
                raise ServiceError("unsupported output_format", details={"supported": sorted(OUTPUT_TYPES)})
            output = job_dir / f"render.{output_format}"
            result = render_structure(source_path, output, request)
            artifact = self._save_artifact(principal, job_id, output, "render")
            return JobOutcome(result={"data": result, "artifacts": [artifact]}, artifacts=[artifact])

        return work

    def _save_artifact(self, principal: Principal, job_id: str, source: Path, kind: str) -> dict:
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        key = f"projects/{principal.project_id}/artifacts/{job_id}/{source.name}"
        stored = self.store.put_bytes(key, data)
        row = self.db.create_artifact(
            project_id=principal.project_id, job_id=job_id, kind=kind,
            filename=source.name, content_type=_content_type(source), storage_key=key,
            size_bytes=stored["size"], sha256=digest, expires_at=_iso_after(self.config.artifact_retention_days),
        )
        row["download_url"] = self.store.signed_url(
            artifact_id=row["id"], base_url=self.config.public_base_url, ttl_seconds=900
        )
        return self._public_artifact(row)

    @staticmethod
    def _public_artifact(row: dict) -> dict:
        return {key: value for key, value in row.items() if key != "storage_key"}

    def submit(self, principal: Principal, kind: str, request: dict, *, idempotency_key: str | None = None) -> tuple[dict, bool]:
        self.auth.require(principal, "jobs:write")
        asset_id = request.get("asset_id")
        asset, path = self._asset_path(principal, str(asset_id)) if asset_id else (None, None)
        if kind not in {"inspect", "topology", "render"}:
            raise ServiceError("unsupported operation")
        if kind == "topology" and request.get("center_index") is None:
            raise ServiceError("center_index is required")
        if kind == "topology" and not request.get("ligand_species"):
            raise ServiceError(
                "ligand_species is required for topology analysis",
                status_code=422,
                details={"example": ["O", "N", "Cl"]},
            )
        if kind == "render" and request.get("output_format", "png") not in OUTPUT_TYPES:
            raise ServiceError("unsupported output_format")
        job, existed = self.db.create_job(
            project_id=principal.project_id, kind=kind, request=request,
            idempotency_key=idempotency_key,
        )
        if existed:
            return self.job(principal, job["id"]), True
        self.jobs.submit(job["id"], self._make_work(principal, job["id"], kind, request))
        sync = self._estimate_sync(path, kind, request) if path else False
        if sync:
            self.jobs.wait(job["id"], self.config.sync_timeout_seconds)
        return self.job(principal, job["id"]), False

    def job(self, principal: Principal, job_id: str) -> dict:
        self.auth.require(principal, "jobs:read")
        job = self.db.get_job(principal.project_id, job_id)
        artifacts = self.db.list_artifacts(principal.project_id, job_id)
        for item in artifacts:
            item["download_url"] = self.store.signed_url(
                artifact_id=item["id"], base_url=self.config.public_base_url, ttl_seconds=900
            )
        job["artifacts"] = [self._public_artifact(item) for item in artifacts]
        return job

    def artifact(self, principal: Principal, artifact_id: str) -> tuple[dict, bytes]:
        self.auth.require(principal, "artifacts:read")
        row = self.db.get_artifact(principal.project_id, artifact_id)
        if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
            raise NotFoundError("artifact has expired")
        return row, self.store.read_bytes(row["storage_key"])

    def create_api_key(self, principal: Principal, project_id: str, name: str, scopes: list[str]) -> tuple[str, dict]:
        if principal.auth_type != "dev" and not principal.allows("*"):
            raise ServiceError("only an administrative principal can create API keys", status_code=403)
        if principal.project_id != project_id:
            raise ServiceError("project mismatch", status_code=403)
        invalid = sorted(set(scopes) - SCOPES)
        if invalid:
            raise ServiceError("unknown API key scopes", details={"scopes": invalid})
        return self.db.create_api_key(project_id, name, scopes)
