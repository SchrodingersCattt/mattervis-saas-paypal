"""Create one explicitly requested, unapproved PayPal Sandbox order."""
from __future__ import annotations

import sys
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from contest.integrations import IntegrationError, PayPalSandbox

if "--execute" not in sys.argv:
    raise SystemExit("Pass --execute to create an unapproved USD 2.00 Sandbox order.")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
try:
    order = PayPalSandbox().create_order(uuid4().hex, "2.00", "MatterVis sandbox checkout test")
except IntegrationError as exc:
    raise SystemExit(f"Sandbox order creation failed: {exc}") from exc
print("PayPal Sandbox order created with a verified Sandbox approval link:", bool(order.get("id") and order.get("approval_url")))
print("This order remains unapproved; no capture was attempted.")
