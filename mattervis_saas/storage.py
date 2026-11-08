"""Safe local object storage with short-lived signed download URLs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from pathlib import Path
from urllib.parse import urlencode


class LocalObjectStore:
    def __init__(self, root: str | Path, signing_key: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._key = str(signing_key).encode()

    def _path(self, key: str) -> Path:
        candidate = (self.root / str(key)).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError("object key escapes storage root")
        return candidate

    def put_bytes(self, key: str, data: bytes) -> dict:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"key": key, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def put_file(self, key: str, source: str | Path, *, max_bytes: int | None = None) -> dict:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        with Path(source).open("rb") as reader, path.open("wb") as writer:
            while block := reader.read(1024 * 1024):
                size += len(block)
                if max_bytes is not None and size > max_bytes:
                    path.unlink(missing_ok=True)
                    raise ValueError("object exceeds configured size limit")
                digest.update(block)
                writer.write(block)
        return {"key": key, "size": size, "sha256": digest.hexdigest()}

    def read_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def signed_url(self, *, artifact_id: str, base_url: str, ttl_seconds: int = 900) -> str:
        expires = int((datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).timestamp())
        message = f"{artifact_id}:{expires}".encode()
        signature = hmac.new(self._key, message, hashlib.sha256).hexdigest()
        return f"{base_url.rstrip('/')}/v1/artifacts/{artifact_id}/download?{urlencode({'expires': expires, 'signature': signature})}"

    def verify_signature(self, *, artifact_id: str, expires: int, signature: str) -> bool:
        if int(expires) < int(datetime.now(timezone.utc).timestamp()):
            return False
        expected = hmac.new(self._key, f"{artifact_id}:{int(expires)}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, str(signature))
