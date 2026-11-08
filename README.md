# MatterVis Studio

An AI-assisted scientific figure workflow built with the public MatterVis CLI
and PayPal Sandbox. A researcher uploads a CIF, describes a figure, reviews a
real MatterVis preview and buys a high-resolution PNG with sandbox funds. No
live PayPal payment is used or supported by this demo.

## What is new for the hackathon

The existing MatterVis SaaS provided structure inspection and rendering. This
project adds a separate figure-planning interface, an OpenAI-compatible model
integration, a preview/export workflow and server-verified PayPal Sandbox
checkout. The underlying MatterVis output and the commands used to produce it
are recorded with each job.

## Run locally

Requirements: Python 3.11 or later, `uv`, an OpenAI-compatible model endpoint,
and PayPal developer sandbox credentials. A desktop browser is needed for the
interactive application and PayPal buyer approval.

```bash
uv sync --locked --extra dev --python 3.11
cp .env.example .env
```

On Windows use `.venv\Scripts\python` instead of `.venv/bin/python`. Set
`AI_BASE_URL` (including `/v1` if required by your provider), `AI_API_KEY`,
`AI_MODEL`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` and a random
`APP_SECRET_KEY` in `.env`. The PayPal values must be from a **Sandbox** app.
Set `APP_BASE_URL` to the browser-reachable address; by default it is
`http://127.0.0.1:50001`.

```bash
.venv/bin/python -m contest.app
```

Open <http://127.0.0.1:50001>, use the included synthetic example or upload a
CIF, enter a figure request and generate a preview. For checkout, sign in using
a PayPal **sandbox buyer** account, not a live account. Successful server-side
capture unlocks the PNG. Cancelled or failed checkout does not unlock it.

The figure price is a fixed USD 2.00 in simulated sandbox funds. The app uses
only the PayPal sandbox API host. Do not commit `.env`, model keys, sandbox
secrets, local jobs or personal structures. The single-user development server
is intended for local judging and requires additional hardening before public
deployment.

## Testing

```bash
.venv/bin/python -m pytest tests/test_contest.py
```

The tests use mocked upstream HTTP responses and do not represent a completed
live sandbox test. During development, a real Sandbox order was created with
the configured merchant account; buyer approval and capture require an
interactive Sandbox buyer. Real model and sandbox testing requires the
credentials above. Free judge access to a model and sandbox account must be
arranged in the competition testing instructions; do not publish private API
secrets.

An optional explicit Sandbox connectivity check creates an unapproved USD 2.00
test order and never captures it:

```bash
.venv/bin/python scripts/smoke_sandbox_order.py --execute
```
