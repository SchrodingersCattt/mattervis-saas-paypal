"""Structured errors returned by the stable HTTP API."""

from __future__ import annotations


class ServiceError(RuntimeError):
    status_code = 400
    code = "invalid_request"

    def __init__(self, message: str, *, details: dict | None = None, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if status_code is not None:
            self.status_code = status_code


class AuthenticationError(ServiceError):
    status_code = 401
    code = "authentication_required"


class AuthorizationError(ServiceError):
    status_code = 403
    code = "forbidden"


class NotFoundError(ServiceError):
    status_code = 404
    code = "not_found"


class ConflictError(ServiceError):
    status_code = 409
    code = "conflict"


class LimitError(ServiceError):
    status_code = 413
    code = "resource_limit"


class JobExecutionError(ServiceError):
    status_code = 422
    code = "job_failed"
