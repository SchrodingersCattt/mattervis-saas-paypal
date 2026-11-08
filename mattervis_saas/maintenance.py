"""Retention maintenance entry point for cron or a platform scheduler."""

from __future__ import annotations

from .app import create_app


def main() -> None:
    app = create_app()
    service = app.extensions["mattervis_service"]
    try:
        print(service.cleanup_expired())
    finally:
        service.close()


if __name__ == "__main__":
    main()
