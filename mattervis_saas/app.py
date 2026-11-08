"""Flask application factory and local entry point."""

from __future__ import annotations

import argparse
import logging
from uuid import uuid4

from flask import Flask, jsonify, request

from .a2a import register_a2a
from .api import register_routes
from .config import Config
from .errors import ServiceError
from .service import MatterVisService


def create_app(config: Config | None = None, service: MatterVisService | None = None) -> Flask:
    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False
    owned = service is None
    service = service or MatterVisService(config)
    app.extensions["mattervis_service"] = service
    app.extensions["mattervis_owned_service"] = owned

    @app.before_request
    def request_context():
        request.environ["mattervis_request_id"] = request.headers.get("X-Request-ID") or uuid4().hex

    @app.after_request
    def response_context(response):
        response.headers["X-Request-ID"] = request.environ.get("mattervis_request_id", "")
        return response

    @app.errorhandler(ServiceError)
    def service_error(error: ServiceError):
        return jsonify({
            "error": {"code": error.code, "message": error.message, "details": error.details},
            "request_id": request.environ.get("mattervis_request_id"),
        }), error.status_code

    @app.errorhandler(Exception)
    def unexpected(error: Exception):
        app.logger.exception("unexpected MatterVis error")
        return jsonify({
            "error": {"code": "internal_error", "message": "internal service error"},
            "request_id": request.environ.get("mattervis_request_id"),
        }), 500

    @app.get("/")
    def index():
        return jsonify({"service": "mattervis-saas", "api": "/v1", "a2a": "/a2a"})

    register_routes(app, service)
    register_a2a(app, service)

    @app.teardown_appcontext
    def close_service(_error=None):
        # The factory owns the service only for command-line applications. Test
        # clients and embedding applications can close their injected service.
        return None

    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="MatterVis stateless SaaS API")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=50001)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    app = create_app()
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
