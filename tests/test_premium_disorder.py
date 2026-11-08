"""Exercise the payment gate and real MolCrysKit candidate provenance."""

import json
import time
import zipfile
from pathlib import Path

import pytest

from contest.disorder_service import DisorderService, validate_options

EXAMPLE = Path(__file__).resolve().parents[1] / "contest" / "static" / "disorder-example.cif"


def wait_for(service, job_id, owner, states, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = service.load(job_id, owner)
        if job["status"] in states:
            return job
        time.sleep(0.2)
    pytest.fail(f"Job timed out in {job['status']}")


def test_random_requires_reproducible_seed():
    with pytest.raises(ValueError, match="seed"):
        validate_options("random", 2, None, False)
    assert validate_options("random", 2, 7, True)["seed"] == 7
    assert validate_options("optimal", 7, 99, False)["count"] == 1


def test_native_viewer_uses_explicit_light_theme(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_SECRET_KEY", "test-only-secret")
    monkeypatch.setenv("MATTERVIS_PREWARM", "0")
    from contest.native_app import create_native_app

    app = create_native_app(data_root=tmp_path, input_path=str(EXAMPLE))
    try:
        client = app.server.test_client()
        html = client.get("/").get_data(as_text=True)
        layout = client.get("/_dash-layout").get_data(as_text=True)
        assert 'data-mattervis-theme="light"' in html
        assert "crystal-graph" in layout and "premium-prepare" in layout
        assert "premium-approval-url" in layout
        assert "Source SHA-256" not in layout
        checkout = next(value for key, value in app.callback_map.items() if "premium-approval-url.children" in key)
        assert {item["id"] for item in checkout["inputs"]} == {"premium-checkout"}
    finally:
        app.close_extensions()
        app.crystal_backend.close()


def test_real_disorder_workflow_requires_payment_and_keeps_warnings(tmp_path, monkeypatch):
    monkeypatch.setattr("contest.disorder_service.model_json", lambda *_: {"summary": "Two hypotheses.", "limitations": "The source lacks hydrogen."})
    service = DisorderService(tmp_path)
    try:
        job = service.create("owner", EXAMPLE.read_text(encoding="utf-8"), validate_options("enumerate", 2, None, False), {"id": "scene"})
        job = wait_for(service, job["id"], "owner", {"ready", "failed"})
        assert job["status"] == "ready", job.get("error")
        with pytest.raises(PermissionError):
            service.result_path(job["id"], "owner")
        with pytest.raises(FileNotFoundError):
            service.load(job["id"], "another-owner")
        service.attach_order(job["id"], "owner", "sandbox-order")
        with pytest.raises(ValueError, match="does not match"):
            service.mark_paid_and_run(job["id"], "owner", "other-order", "capture")
        service.mark_paid_and_run(job["id"], "owner", "sandbox-order", "mock-capture")
        job = wait_for(service, job["id"], "owner", {"complete", "failed"})
        assert job["status"] == "complete", job.get("error")
        assert len(job["candidates"]) == 2
        assert len({c["sha256"] for c in job["candidates"]}) == 2
        for candidate in job["candidates"]:
            checks = candidate["sanity"]["frames"][0]["results"]
            assert any(c["name"] == "hydrogen_presence" and not c["passed"] for c in checks)
        with zipfile.ZipFile(service.result_path(job["id"], "owner")) as bundle:
            manifest = json.loads(bundle.read("manifest.json"))
            assert manifest["source_sha256"] == job["source_sha256"]
            assert len(manifest["candidates"]) == 2
            assert manifest["software"]["molcrys_kit"]["file_sha256"]["analysis/sanity_check.py"]
            assert any(name.startswith("preflight/") for name in bundle.namelist())
    finally:
        service.close()


def test_frozen_source_change_prevents_checkout(tmp_path):
    service = DisorderService(tmp_path)
    try:
        job = service.create("owner", EXAMPLE.read_text(encoding="utf-8"), validate_options("optimal", 1, None, False), {})
        job = wait_for(service, job["id"], "owner", {"ready", "failed"})
        assert job["status"] == "ready", job.get("error")
        (tmp_path / job["id"] / "source.cif").write_text("changed", encoding="utf-8")
        with pytest.raises(ValueError, match="source has changed"):
            service.attach_order(job["id"], "owner", "sandbox-order")
    finally:
        service.close()
