"""Environment-driven service configuration.

The defaults are intentionally useful for a single-process development
instance. Hosted deployments should set a random signing key, a real database
or object-store backend, and an explicit authentication mode.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True, slots=True)
class Config:
    root_dir: Path
    database_path: Path
    storage_root: Path
    auth_mode: str
    default_project_id: str
    artifact_retention_days: int
    max_upload_bytes: int
    max_atoms_per_frame: int
    max_trajectory_frames: int
    max_concurrent_jobs: int
    job_timeout_seconds: int
    sync_timeout_seconds: float
    sync_max_bytes: int
    sync_max_frames: int
    sync_max_atoms: int
    artifact_signing_key: str
    public_base_url: str
    a2a_token: str

    @classmethod
    def from_env(cls, root_dir: str | Path | None = None) -> "Config":
        root = Path(root_dir or os.environ.get("MATTERVIS_ROOT", ".")).resolve()
        local = root / ".local"
        local.mkdir(parents=True, exist_ok=True)
        return cls(
            root_dir=root,
            database_path=Path(os.environ.get("MATTERVIS_DATABASE_PATH", local / "mattervis.sqlite3")),
            storage_root=Path(os.environ.get("MATTERVIS_STORAGE_ROOT", local / "objects")),
            auth_mode=os.environ.get("MATTERVIS_AUTH_MODE", "dev").strip().lower(),
            default_project_id=os.environ.get("MATTERVIS_DEFAULT_PROJECT", "dev").strip() or "dev",
            artifact_retention_days=_int("MATTERVIS_ARTIFACT_RETENTION_DAYS", 7),
            max_upload_bytes=_int("MATTERVIS_MAX_UPLOAD_BYTES", 500 * 1024 * 1024),
            max_atoms_per_frame=_int("MATTERVIS_MAX_ATOMS_PER_FRAME", 1_000_000),
            max_trajectory_frames=_int("MATTERVIS_MAX_TRAJECTORY_FRAMES", 10_000),
            max_concurrent_jobs=_int("MATTERVIS_MAX_CONCURRENT_JOBS", 2),
            job_timeout_seconds=_int("MATTERVIS_JOB_TIMEOUT_SECONDS", 900),
            sync_timeout_seconds=float(os.environ.get("MATTERVIS_SYNC_TIMEOUT_SECONDS", "10")),
            sync_max_bytes=_int("MATTERVIS_SYNC_MAX_BYTES", 20 * 1024 * 1024),
            sync_max_frames=_int("MATTERVIS_SYNC_MAX_FRAMES", 10),
            sync_max_atoms=_int("MATTERVIS_SYNC_MAX_ATOMS", 10_000),
            artifact_signing_key=os.environ.get(
                "MATTERVIS_ARTIFACT_SIGNING_KEY", "development-only-signing-key"
            ),
            public_base_url=os.environ.get("MATTERVIS_PUBLIC_BASE_URL", "http://localhost:50001"),
            a2a_token=os.environ.get("MATTERVIS_A2A_TOKEN", ""),
        )

    def ensure_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_root.mkdir(parents=True, exist_ok=True)
