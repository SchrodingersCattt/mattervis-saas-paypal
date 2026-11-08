from __future__ import annotations

from mattervis_saas.app import create_app
from mattervis_saas.config import Config


def test_agent_card_and_stream(tmp_path, monkeypatch):
    monkeypatch.setenv("MATTERVIS_AUTH_MODE", "dev")
    app = create_app(Config.from_env(tmp_path))
    try:
        client = app.test_client()
        card = client.get("/.well-known/agent-card.json")
        assert card.status_code == 200
        assert card.get_json()["capabilities"]["streaming"] is True
        response = client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0", "id": 1, "method": "message/stream",
                "params": {"message": {"role": "user", "parts": [{"kind": "text", "text": "render"}]}}
            },
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.content_type
        assert "completed" in response.get_data(as_text=True)
    finally:
        app.extensions["mattervis_service"].close()
