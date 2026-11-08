from __future__ import annotations

import io

import pytest
import requests

from contest import app as studio
from contest.integrations import IntegrationError, PayPalSandbox, model_json


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(studio, "DATA", tmp_path)
    monkeypatch.setattr(studio, "inspect_structure", lambda path: {"result": {"atoms": 2}, "command": ["mat-vis", "inspect"]})
    monkeypatch.setattr(studio, "build_plan", lambda prompt, inspection: {
        "style": "ball_stick", "show_labels": True, "hide_hydrogen": False, "explanation": "A clean view."
    })

    def render(source, output, plan, high_res=False):
        output.write_bytes(b"high-resolution" if high_res else b"preview")
        return {"result": {}, "command": ["mat-vis", "render"], "sha256": "test"}

    monkeypatch.setattr(studio, "render_structure", render)
    studio.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    return studio.app.test_client()


def make_job(client):
    response = client.post("/api/jobs", data={"file": (io.BytesIO(b"data_test\n"), "test.cif"), "prompt": "label atoms"})
    assert response.status_code == 201
    return response.get_json()["id"]


def test_preview_and_export_require_verified_payment(client, monkeypatch):
    job_id = make_job(client)
    assert client.get(f"/api/jobs/{job_id}/preview").data == b"preview"
    assert client.get(f"/api/jobs/{job_id}/download").status_code == 403
    assert studio.app.test_client().get(f"/api/jobs/{job_id}/preview").status_code == 404

    class Gateway:
        def create_order(self, reference, amount, description):
            assert reference == job_id and amount == "2.00"
            return {"id": "ORDER-123", "approval_url": "https://sandbox.example/approve"}

        def capture_order(self, order_id, reference, amount):
            assert (order_id, reference, amount) == ("ORDER-123", job_id, "2.00")
            return {"capture_id": "CAPTURE-1", "status": "COMPLETED"}

    monkeypatch.setattr(studio, "PayPalSandbox", Gateway)
    assert client.post(f"/api/jobs/{job_id}/checkout").get_json()["approval_url"].startswith("https://")
    assert "payment=failed" in client.get("/checkout/return?token=OTHER").location
    assert client.get(f"/api/jobs/{job_id}/download").status_code == 403
    assert "payment=complete" in client.get("/checkout/return?token=ORDER-123").location
    assert client.get(f"/api/jobs/{job_id}/download").data == b"high-resolution"


def test_cancellation_and_missing_model_do_not_unlock(client, monkeypatch):
    job_id = make_job(client)
    with client.session_transaction() as state:
        state["checkout_job"] = job_id
    assert "payment=cancelled" in client.get("/checkout/cancel").location
    assert client.get(f"/api/jobs/{job_id}/download").status_code == 403

    def unavailable(*_args):
        raise IntegrationError("Model is not configured.")

    monkeypatch.setattr(studio, "build_plan", unavailable)
    response = client.post("/api/jobs", data={"file": (io.BytesIO(b"data_test\n"), "test.cif"), "prompt": "draw"})
    assert response.status_code == 503
    assert "not configured" in response.get_json()["error"]


def test_gateway_rejects_wrong_order_or_capture_amount(monkeypatch):
    gateway = PayPalSandbox()
    requests_seen = []

    def request(method, path, **kwargs):
        requests_seen.append((method, path))
        return {"status": "APPROVED", "purchase_units": [{"custom_id": "job-1", "amount": {"currency_code": "USD", "value": "3.00"}}]}

    monkeypatch.setattr(gateway, "_request", request)
    with pytest.raises(IntegrationError, match="amount"):
        gateway.capture_order("ORDER-1", "job-1", "2.00")
    assert requests_seen == [("GET", "/v2/checkout/orders/ORDER-1")]


def test_checkout_only_redirects_to_paypal_sandbox(monkeypatch):
    gateway = PayPalSandbox()
    monkeypatch.setattr(gateway, "_request", lambda *_args, **_kwargs: {
        "id": "ORDER-1", "links": [{"rel": "payer-action", "href": "https://www.paypal.com/checkoutnow?token=x"}]
    })
    with pytest.raises(IntegrationError, match="unexpected sandbox"):
        gateway.create_order("job-1", "2.00", "Figure")
    monkeypatch.setattr(gateway, "_request", lambda *_args, **_kwargs: {
        "id": "ORDER-1", "links": [{"rel": "payer-action", "href": "https://www.sandbox.paypal.com/checkoutnow?token=x"}]
    })
    assert gateway.create_order("job-1", "2.00", "Figure")["id"] == "ORDER-1"


def test_invalid_model_plan_is_not_used(monkeypatch):
    monkeypatch.setattr(studio, "model_json", lambda *_: {"style": "run-arbitrary-code", "show_labels": True, "hide_hydrogen": False, "explanation": "x"})
    with pytest.raises(IntegrationError, match="unsupported"):
        studio.build_plan("test", {})


def test_unavailable_labels_are_not_claimed(monkeypatch):
    monkeypatch.setattr(studio, "model_json", lambda *_: {"style": "ball_stick", "hide_hydrogen": False, "explanation": "Labels added."})
    plan = studio.build_plan("Show atom labels", {})
    assert plan["show_labels"] is False
    assert "not available" in plan["explanation"]


def test_model_quota_error_is_clear_without_exposing_credentials(monkeypatch):
    monkeypatch.setenv("AI_BASE_URL", "https://model.example/v1")
    monkeypatch.setenv("AI_API_KEY", "secret-for-test")
    monkeypatch.setenv("AI_MODEL", "demo")

    class Response:
        status_code = 402

        def raise_for_status(self):
            raise requests.HTTPError(response=self)

    monkeypatch.setattr(requests, "post", lambda *_args, **_kwargs: Response())
    with pytest.raises(IntegrationError, match="HTTP 402") as exc:
        model_json("system", "prompt")
    assert "secret-for-test" not in str(exc.value)
