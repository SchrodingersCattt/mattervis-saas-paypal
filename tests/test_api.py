from __future__ import annotations

import io
import json

import pytest

from mattervis_saas.app import create_app
from mattervis_saas.config import Config


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("MATTERVIS_AUTH_MODE", "dev")
    monkeypatch.setenv("MATTERVIS_SYNC_TIMEOUT_SECONDS", "3")
    config = Config.from_env(tmp_path)
    instance = create_app(config)
    yield instance
    instance.extensions["mattervis_service"].close()


def upload(client):
    response = client.post(
        "/v1/assets",
        headers={"X-Project-ID": "demo"},
        data={"file": (io.BytesIO(b"data_demo\n"), "sample.cif")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    return response.get_json()["asset"]["id"]


def test_health_capabilities_and_upload(app):
    client = app.test_client()
    assert client.get("/v1/healthz").get_json()["ok"] is True
    capabilities = client.get("/v1/capabilities").get_json()
    assert "render" in capabilities["operations"]
    assert capabilities["forge_dependency"] is False
    asset_id = upload(client)
    assert asset_id.startswith("asset_")


def test_inspect_job_and_signed_artifact(app, monkeypatch):
    monkeypatch.setattr(
        "mattervis_saas.service.inspect_structure",
        lambda path, request: {"schema": "mattervis.inspect/v1", "atom_count": 3},
    )
    client = app.test_client()
    asset_id = upload(client)
    response = client.post(
        "/v1/inspect",
        headers={"X-Project-ID": "demo", "Idempotency-Key": "inspect-1"},
        json={"asset_id": asset_id},
    )
    assert response.status_code == 200
    job = response.get_json()["job"]
    assert job["status"] == "succeeded"
    assert job["result"]["data"]["atom_count"] == 3
    artifact = job["artifacts"][0]
    download = client.get(artifact["download_url"])
    assert download.status_code == 200
    assert json.loads(download.data)["atom_count"] == 3
    replay = client.post(
        "/v1/inspect",
        headers={"X-Project-ID": "demo", "Idempotency-Key": "inspect-1"},
        json={"asset_id": asset_id},
    )
    assert replay.status_code == 200
    assert replay.get_json()["existing"] is True
    assert replay.get_json()["job"]["id"] == job["id"]


def test_api_key_is_project_scoped(app):
    client = app.test_client()
    key_response = client.post(
        "/v1/projects/demo/api-keys",
        headers={"X-Project-ID": "demo"},
        json={"name": "automation", "scopes": ["structures:write"]},
    )
    assert key_response.status_code == 201
    key = key_response.get_json()["key"]
    response = client.post(
        "/v1/assets",
        headers={"X-API-Key": key},
        data={"file": (io.BytesIO(b"data_demo\n"), "sample.cif")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 201
    denied = client.get("/v1/jobs/unknown", headers={"X-API-Key": key})
    assert denied.status_code == 403


def test_topology_requires_explicit_ligand_species(app):
    client = app.test_client()
    asset_id = upload(client)
    response = client.post(
        "/v1/topology",
        headers={"X-Project-ID": "demo"},
        json={"asset_id": asset_id, "center_index": 0},
    )
    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_request"
