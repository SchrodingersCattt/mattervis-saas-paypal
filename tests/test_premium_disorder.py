"""Exercise the payment gate and real MolCrysKit candidate provenance."""

import json
import time
import zipfile
from pathlib import Path

import pytest

from contest.disorder_service import DisorderService, validate_options
from contest.chat_agent import ChatService

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
    with pytest.raises(ValueError):
        validate_options("enumerate", 1.5, None, False)


def test_native_viewer_uses_explicit_light_theme(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_SECRET_KEY", "test-only-secret")
    monkeypatch.setenv("MATTERVIS_PREWARM", "0")
    monkeypatch.delenv("MATTERVIS_SESSION_COOKIE_NAME", raising=False)
    from contest.native_app import create_native_app

    app = create_native_app(data_root=tmp_path, input_path=str(EXAMPLE))
    try:
        client = app.server.test_client()
        html = client.get("/").get_data(as_text=True)
        layout = client.get("/_dash-layout").get_data(as_text=True)
        assert 'data-mattervis-theme="light"' in html
        assert app.server.config["SESSION_COOKIE_NAME"] == "mattervis_mcp_session"
        assert "crystal-graph" in layout and "chat-input" in layout
        assert "chat-paypal-url" in layout
        assert "Source SHA-256" not in layout
    finally:
        app.close_extensions()
        app.crystal_backend.close()


def test_real_disorder_workflow_requires_payment_and_keeps_warnings(tmp_path, monkeypatch):
    observed = {}

    def model(_system, prompt):
        observed["evidence"] = json.loads(prompt)
        return {"summary": "Two hypotheses.", "limitations": "The source lacks hydrogen."}

    monkeypatch.setattr("contest.disorder_service.model_json", model)
    service = DisorderService(tmp_path)
    try:
        job = service.create("owner", EXAMPLE.read_text(encoding="utf-8"), validate_options("enumerate", 2, None, False), {"id": "scene"})
        job = wait_for(service, job["id"], "owner", {"ready", "failed"})
        assert job["status"] == "ready", job.get("error")
        with pytest.raises(PermissionError):
            service.result_path(job["id"], "owner")
        with pytest.raises(FileNotFoundError):
            service.load(job["id"], "another-owner")
        quote = service.reserve_checkout(job["id"], "owner", job["options"])
        service.attach_order(job["id"], "owner", "sandbox-order", quote["reference"], "https://sandbox.paypal.com/test")
        with pytest.raises(ValueError, match="does not match"):
            service.mark_paid_and_run(job["id"], "owner", "other-order", "capture")
        service.mark_paid_and_run(job["id"], "owner", "sandbox-order", "mock-capture")
        job = wait_for(service, job["id"], "owner", {"complete", "failed"})
        assert job["status"] == "complete", job.get("error")
        assert len(job["candidates"]) == 2
        assert len({c["sha256"] for c in job["candidates"]}) == 2
        assert "sha256" not in json.dumps(observed["evidence"])
        assert any(item["check"] == "Formula comparison" and item["status"] == "skipped" for item in observed["evidence"]["source_checks"])
        for candidate in job["candidates"]:
            checks = candidate["sanity"]["frames"][0]["results"]
            assert any(c["name"] == "hydrogen_presence" and not c["passed"] for c in checks)
        with zipfile.ZipFile(service.result_path(job["id"], "owner")) as bundle:
            manifest = json.loads(bundle.read("manifest.json"))
            assert manifest["source_sha256"] == job["source_sha256"]
            assert len(manifest["candidates"]) == 2
            assert manifest["software"]["molcrys_kit"]["file_sha256"]["analysis/sanity_check.py"]
            assert any(name.startswith("preflight/") for name in bundle.namelist())
        chat = ChatService(service, tmp_path / "artifacts")
        try:
            assert not chat.restore_paid_job("other-owner", job["id"])
            assert chat.restore_paid_job("owner", job["id"])
            restored = chat.snapshot("owner")
            assert restored["job_id"] == job["id"]
            assert any("Two hypotheses" in item["text"] for item in restored["messages"])
            assert not chat.restore_paid_job("owner", job["id"])
        finally:
            chat.close()
    finally:
        service.close()


def test_paypal_quote_freezes_the_users_choice(tmp_path):
    service = DisorderService(tmp_path)
    try:
        original = validate_options("enumerate", 2, None, False)
        job = service.create("owner", EXAMPLE.read_text(encoding="utf-8"), original, {})
        job = wait_for(service, job["id"], "owner", {"ready", "failed"})
        assert job["status"] == "ready", job.get("error")
        assert service.load(job["id"], "owner")["options"] == original
        assert not service.load(job["id"], "owner")["paid"]
        chosen = validate_options("random", 3, 11, False)
        quote = service.reserve_checkout(job["id"], "owner", chosen)
        with pytest.raises(ValueError, match="already being prepared"):
            service.reserve_checkout(job["id"], "owner", original)
        service.attach_order(job["id"], "owner", "sandbox-order", quote["reference"], "https://sandbox.paypal.com/test")
        frozen = service.verify_order_quote(job["id"], "owner", "sandbox-order")
        assert frozen["quote"]["parameters"] == chosen
        with pytest.raises(ValueError, match="already sent"):
            service.reserve_checkout(job["id"], "owner", original)
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
            service.reserve_checkout(job["id"], "owner", job["options"])
    finally:
        service.close()


def test_cancelling_checkout_invalidates_the_old_order(tmp_path):
    service = DisorderService(tmp_path)
    try:
        job = service.create("owner", EXAMPLE.read_text(encoding="utf-8"), validate_options("enumerate", 2, None, False), {})
        job = wait_for(service, job["id"], "owner", {"ready", "failed"})
        assert job["status"] == "ready", job.get("error")
        quote = service.reserve_checkout(job["id"], "owner", job["options"])
        service.attach_order(job["id"], "owner", "sandbox-order", quote["reference"], "https://sandbox.paypal.com/test")
        assert service.cancel_unpaid_order(job["id"], "owner")
        with pytest.raises(ValueError, match="does not match"):
            service.verify_order_quote(job["id"], "owner", "sandbox-order")
    finally:
        service.close()
