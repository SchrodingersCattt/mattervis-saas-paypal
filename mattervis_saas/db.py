"""Small SQLite persistence boundary used by local and single-instance mode.

The service only stores metadata here. Binary inputs and outputs belong to the
object-store adapter, so moving to PostgreSQL/S3 later does not change the API
payloads.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import secrets
import sqlite3
import threading
from typing import Any, Iterator
from uuid import uuid4

from .errors import ConflictError, NotFoundError


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def loads(value: str | None, default: Any) -> Any:
    try:
        result = json.loads(value or "")
        return result
    except (TypeError, ValueError):
        return default


class Database:
    def __init__(self, path):
        self.path = str(path)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS api_keys (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    prefix TEXT NOT NULL,
                    key_hash BLOB NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    revoked_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    filename TEXT NOT NULL,
                    input_format TEXT,
                    sha256 TEXT NOT NULL,
                    storage_key TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id, sha256)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    error_json TEXT NOT NULL,
                    idempotency_key TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS jobs_idempotency
                    ON jobs(project_id, kind, idempotency_key)
                    WHERE idempotency_key IS NOT NULL;
                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id),
                    job_id TEXT REFERENCES jobs(id),
                    kind TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    storage_key TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            db = self._connect()
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    def ensure_project(self, project_id: str, name: str | None = None) -> dict:
        project_id = str(project_id).strip()
        if not project_id:
            raise ValueError("project_id is required")
        with self.connection() as db:
            row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO projects(id,name,created_at) VALUES (?,?,?)",
                    (project_id, name or project_id, now()),
                )
                row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            return dict(row)

    def project_exists(self, project_id: str) -> bool:
        with self.connection() as db:
            return db.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is not None

    def create_api_key(self, project_id: str, name: str, scopes: list[str]) -> tuple[str, dict]:
        self.ensure_project(project_id)
        raw = "mvk_" + secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw.encode()).digest()
        record_id = new_id("key")
        with self.connection() as db:
            db.execute(
                "INSERT INTO api_keys(id,project_id,prefix,key_hash,name,scopes_json,created_at) VALUES (?,?,?,?,?,?,?)",
                (record_id, project_id, raw[:12], key_hash, name or "MatterVis API key",
                 json.dumps(sorted(set(scopes))), now()),
            )
        return raw, {"id": record_id, "project_id": project_id, "prefix": raw[:12],
                     "name": name or "MatterVis API key", "scopes": sorted(set(scopes))}

    def authenticate_key(self, raw: str) -> dict | None:
        digest = hashlib.sha256(str(raw).encode()).digest()
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM api_keys WHERE key_hash=? AND revoked_at IS NULL", (digest,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["scopes"] = loads(result.pop("scopes_json"), [])
        return result

    def revoke_key(self, project_id: str, key_id: str) -> None:
        with self.connection() as db:
            result = db.execute(
                "UPDATE api_keys SET revoked_at=? WHERE id=? AND project_id=?",
                (now(), key_id, project_id),
            )
            if result.rowcount == 0:
                raise NotFoundError("API key not found")

    def create_asset(self, *, project_id: str, filename: str, input_format: str | None,
                     sha256: str, storage_key: str, size_bytes: int, metadata: dict | None = None) -> tuple[dict, bool]:
        self.ensure_project(project_id)
        with self.connection() as db:
            existing = db.execute(
                "SELECT * FROM assets WHERE project_id=? AND sha256=?", (project_id, sha256)
            ).fetchone()
            if existing is not None:
                return self._asset(existing), True
            asset_id = new_id("asset")
            db.execute(
                "INSERT INTO assets(id,project_id,filename,input_format,sha256,storage_key,size_bytes,status,metadata_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (asset_id, project_id, filename, input_format, sha256, storage_key, int(size_bytes),
                 "ready", json.dumps(metadata or {}, ensure_ascii=False), now()),
            )
            row = db.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
            return self._asset(row), False

    @staticmethod
    def _asset(row: sqlite3.Row | None) -> dict:
        if row is None:
            raise NotFoundError("asset not found")
        item = dict(row)
        item["metadata"] = loads(item.pop("metadata_json"), {})
        return item

    def get_asset(self, project_id: str, asset_id: str) -> dict:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM assets WHERE id=? AND project_id=?", (asset_id, project_id)
            ).fetchone()
        return self._asset(row)

    def find_asset_by_hash(self, project_id: str, sha256: str) -> dict | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM assets WHERE project_id=? AND sha256=?", (project_id, sha256)
            ).fetchone()
        return self._asset(row) if row is not None else None

    def create_job(self, *, project_id: str, kind: str, request: dict,
                   idempotency_key: str | None = None) -> tuple[dict, bool]:
        self.ensure_project(project_id)
        with self.connection() as db:
            if idempotency_key:
                old = db.execute(
                    "SELECT * FROM jobs WHERE project_id=? AND kind=? AND idempotency_key=?",
                    (project_id, kind, idempotency_key),
                ).fetchone()
                if old is not None:
                    return self._job(old), True
            job_id = new_id("job")
            timestamp = now()
            try:
                db.execute(
                    "INSERT INTO jobs(id,project_id,kind,status,request_json,result_json,error_json,idempotency_key,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (job_id, project_id, kind, "queued", json.dumps(request, ensure_ascii=False),
                     "{}", "{}", idempotency_key, timestamp, timestamp),
                )
            except sqlite3.IntegrityError as exc:
                if idempotency_key:
                    old = db.execute(
                        "SELECT * FROM jobs WHERE project_id=? AND kind=? AND idempotency_key=?",
                        (project_id, kind, idempotency_key),
                    ).fetchone()
                    if old is not None:
                        return self._job(old), True
                raise ConflictError("job could not be created") from exc
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return self._job(row), False

    @staticmethod
    def _job(row: sqlite3.Row | None) -> dict:
        if row is None:
            raise NotFoundError("job not found")
        item = dict(row)
        item["request"] = loads(item.pop("request_json"), {})
        item["result"] = loads(item.pop("result_json"), {})
        item["error"] = loads(item.pop("error_json"), {})
        return item

    def get_job(self, project_id: str, job_id: str) -> dict:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE id=? AND project_id=?", (job_id, project_id)
            ).fetchone()
        return self._job(row)

    def update_job(self, job_id: str, *, status: str | None = None,
                   result: dict | None = None, error: dict | None = None) -> dict:
        values: list[Any] = []
        assignments: list[str] = []
        if status is not None:
            assignments.append("status=?")
            values.append(status)
        if result is not None:
            assignments.append("result_json=?")
            values.append(json.dumps(result, ensure_ascii=False))
        if error is not None:
            assignments.append("error_json=?")
            values.append(json.dumps(error, ensure_ascii=False))
        assignments.append("updated_at=?")
        values.append(now())
        values.append(job_id)
        with self.connection() as db:
            db.execute(f"UPDATE jobs SET {', '.join(assignments)} WHERE id=?", values)
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._job(row)

    def create_artifact(self, *, project_id: str, job_id: str | None, kind: str, filename: str,
                        content_type: str, storage_key: str, size_bytes: int, sha256: str,
                        expires_at: str) -> dict:
        artifact_id = new_id("artifact")
        with self.connection() as db:
            db.execute(
                "INSERT INTO artifacts(id,project_id,job_id,kind,filename,content_type,storage_key,size_bytes,sha256,expires_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (artifact_id, project_id, job_id, kind, filename, content_type, storage_key,
                 int(size_bytes), sha256, expires_at, now()),
            )
            row = db.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        return dict(row)

    def get_artifact(self, project_id: str, artifact_id: str) -> dict:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM artifacts WHERE id=? AND project_id=?", (artifact_id, project_id)
            ).fetchone()
        if row is None:
            raise NotFoundError("artifact not found")
        return dict(row)

    def get_artifact_any(self, artifact_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM artifacts WHERE id=?", (artifact_id,)).fetchone()
        if row is None:
            raise NotFoundError("artifact not found")
        return dict(row)

    def list_artifacts(self, project_id: str, job_id: str) -> list[dict]:
        with self.connection() as db:
            rows = db.execute(
                "SELECT * FROM artifacts WHERE project_id=? AND job_id=? ORDER BY created_at",
                (project_id, job_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def expired_artifacts(self, before: str) -> list[dict]:
        with self.connection() as db:
            rows = db.execute("SELECT * FROM artifacts WHERE expires_at < ?", (before,)).fetchall()
        return [dict(row) for row in rows]

    def delete_artifact(self, artifact_id: str) -> None:
        with self.connection() as db:
            db.execute("DELETE FROM artifacts WHERE id=?", (artifact_id,))
