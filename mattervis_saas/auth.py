"""Project API-key authentication and explicit development mode."""

from __future__ import annotations

from dataclasses import dataclass, field
from flask import Request

from .config import Config
from .db import Database
from .errors import AuthenticationError, AuthorizationError


@dataclass(frozen=True, slots=True)
class Principal:
    subject: str
    project_id: str
    scopes: frozenset[str] = field(default_factory=frozenset)
    auth_type: str = "dev"
    key_id: str | None = None

    def allows(self, scope: str) -> bool:
        return "*" in self.scopes or scope in self.scopes


class Authenticator:
    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.db.ensure_project(config.default_project_id, "Development project")

    def authenticate(self, request: Request, *, project_id: str | None = None) -> Principal:
        raw = request.headers.get("X-API-Key")
        if raw:
            record = self.db.authenticate_key(raw)
            if record is None:
                raise AuthenticationError("invalid or revoked API key")
            actual = str(record["project_id"])
            if project_id and actual != project_id:
                raise AuthorizationError("API key is scoped to another project")
            return Principal(
                subject=f"api-key:{record['id']}", project_id=actual,
                scopes=frozenset(str(item) for item in record.get("scopes", [])),
                auth_type="api_key", key_id=str(record["id"]),
            )

        if self.config.auth_mode != "dev":
            raise AuthenticationError("X-API-Key is required")
        selected = project_id or request.headers.get("X-Project-ID") or self.config.default_project_id
        self.db.ensure_project(selected)
        return Principal(subject="dev", project_id=selected, scopes=frozenset({"*"}), auth_type="dev")

    @staticmethod
    def require(principal: Principal, scope: str) -> None:
        if not principal.allows(scope):
            raise AuthorizationError(f"API key lacks scope {scope}")
