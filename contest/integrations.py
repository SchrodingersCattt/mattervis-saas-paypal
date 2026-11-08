"""Small, server-side clients for a real model and the PayPal sandbox."""

from __future__ import annotations

import json
import os
import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import requests


class IntegrationError(RuntimeError):
    """A missing configuration or upstream failure safe to show to a user."""


def money(value: object) -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise IntegrationError("Enter a valid USD amount.") from None
    if not amount.is_finite() or amount <= 0 or amount > Decimal(10000):
        raise IntegrationError("The amount must be between $0.01 and $10,000.")
    if amount.quantize(Decimal("0.01")) != amount:
        raise IntegrationError("Use at most two decimal places.")
    return f"{amount:.2f}"


def model_json(system: str, prompt: str) -> dict:
    base = os.getenv("AI_BASE_URL", "").rstrip("/")
    key = os.getenv("AI_API_KEY", "")
    model = os.getenv("AI_MODEL", "")
    if not all((base, key, model)):
        raise IntegrationError("Configure AI_BASE_URL, AI_API_KEY and AI_MODEL to use the assistant.")
    try:
        response = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "temperature": 0.1, "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ]},
            timeout=(10, 120),
        )
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"].strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
        result = json.loads(text)
        if not isinstance(result, dict):
            raise TypeError("not an object")
        return result
    except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
        raise IntegrationError("The model could not return valid JSON. Check its configuration and retry.") from exc


class PayPalSandbox:
    """Only sandbox endpoints are supported; credentials never reach the browser."""

    base = "https://api-m.sandbox.paypal.com"

    def __init__(self) -> None:
        self.client_id = os.getenv("PAYPAL_CLIENT_ID", "")
        self.secret = os.getenv("PAYPAL_CLIENT_SECRET", "")
        self.public_base = os.getenv("APP_BASE_URL", "http://127.0.0.1:50001").rstrip("/")

    def _token(self) -> str:
        if not self.client_id or not self.secret:
            raise IntegrationError("Configure PayPal sandbox client credentials before checkout.")
        try:
            response = requests.post(
                f"{self.base}/v1/oauth2/token", auth=(self.client_id, self.secret),
                data={"grant_type": "client_credentials"}, timeout=(10, 30),
            )
            response.raise_for_status()
            return response.json()["access_token"]
        except (requests.RequestException, KeyError, ValueError) as exc:
            raise IntegrationError("PayPal sandbox authentication failed; check the sandbox credentials.") from exc

    def _request(self, method: str, path: str, *, body: dict | None = None, request_id: str | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}
        if request_id:
            headers["PayPal-Request-Id"] = request_id[:38]
        try:
            response = requests.request(method, f"{self.base}{path}", headers=headers, json=body, timeout=(10, 45))
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            raise IntegrationError(f"PayPal sandbox request failed{f' (HTTP {status})' if status else ''}.") from exc

    def create_order(self, reference: str, amount: str, description: str) -> dict:
        amount = money(amount)
        order = self._request("POST", "/v2/checkout/orders", request_id=f"create-{reference}", body={
            "intent": "CAPTURE",
            "purchase_units": [{"reference_id": reference, "custom_id": reference,
                                "description": description[:120],
                                "amount": {"currency_code": "USD", "value": amount}}],
            "payment_source": {"paypal": {"experience_context": {
                "user_action": "PAY_NOW", "shipping_preference": "NO_SHIPPING",
                "return_url": f"{self.public_base}/checkout/return",
                "cancel_url": f"{self.public_base}/checkout/cancel",
            }}},
        })
        approval = next((x.get("href") for x in order.get("links", []) if x.get("rel") in ("payer-action", "approve")), None)
        if not order.get("id") or not approval:
            raise IntegrationError("PayPal did not return an approval link.")
        parsed = urlparse(approval)
        if parsed.scheme != "https" or parsed.hostname not in {"sandbox.paypal.com", "www.sandbox.paypal.com"}:
            raise IntegrationError("PayPal returned an unexpected sandbox approval URL.")
        return {"id": order["id"], "approval_url": approval}

    def capture_order(self, order_id: str, reference: str, amount: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", order_id):
            raise IntegrationError("Invalid PayPal order identifier.")
        expected = money(amount)
        order = self._request("GET", f"/v2/checkout/orders/{order_id}")
        units = order.get("purchase_units") or []
        if len(units) != 1 or units[0].get("custom_id") != reference:
            raise IntegrationError("The PayPal order does not match this request.")
        quoted = units[0].get("amount") or {}
        if quoted.get("currency_code") != "USD" or money(quoted.get("value")) != expected:
            raise IntegrationError("The PayPal order amount does not match the approved quote.")
        if order.get("status") != "COMPLETED":
            if order.get("status") != "APPROVED":
                raise IntegrationError("The buyer has not approved this PayPal order.")
            order = self._request("POST", f"/v2/checkout/orders/{order_id}/capture", body={}, request_id=f"capture-{reference}")
        units = order.get("purchase_units") or []
        captures = (units[0].get("payments") or {}).get("captures", []) if len(units) == 1 else []
        if order.get("status") != "COMPLETED" or len(captures) != 1:
            raise IntegrationError("PayPal has not completed the capture.")
        capture = captures[0]
        received = capture.get("amount") or {}
        if capture.get("status") != "COMPLETED" or received.get("currency_code") != "USD" or money(received.get("value")) != expected:
            raise IntegrationError("The captured payment could not be verified.")
        return {"order_id": order_id, "capture_id": capture.get("id"), "status": "COMPLETED"}

    def create_payout(self, reference: str, amount: str, receiver: str) -> dict:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", receiver) or len(receiver) > 254:
            raise IntegrationError("Enter a valid sandbox recipient email.")
        result = self._request("POST", "/v1/payments/payouts", request_id=f"payout-{reference}", body={
            "sender_batch_header": {"sender_batch_id": f"batch-{reference}", "email_subject": "Sandbox reimbursement"},
            "items": [{"recipient_type": "EMAIL", "receiver": receiver,
                       "amount": {"value": money(amount), "currency": "USD"},
                       "sender_item_id": reference, "note": "Sandbox reimbursement"}],
        })
        header = result.get("batch_header") or {}
        if not header.get("payout_batch_id"):
            raise IntegrationError("PayPal did not return a payout batch ID.")
        return {"batch_id": header["payout_batch_id"], "status": header.get("batch_status", "PENDING")}

    def payout_status(self, batch_id: str, reference: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9-]{1,64}", batch_id):
            raise IntegrationError("Invalid payout batch identifier.")
        result = self._request("GET", f"/v1/payments/payouts/{batch_id}")
        items = result.get("items") or []
        item = next((x for x in items if (x.get("payout_item") or {}).get("sender_item_id") == reference), None)
        return {"batch_id": batch_id, "batch_status": (result.get("batch_header") or {}).get("batch_status", "PENDING"),
                "item_status": item.get("transaction_status") if item else None}
