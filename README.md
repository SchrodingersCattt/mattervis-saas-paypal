# MatterVis Studio: Disorder Lab

An interactive MatterVis viewer with a PayPal Sandbox workflow for researchers
who need to inspect and export possible resolutions of crystallographic disorder.
The original viewer's structure uploads, 3D controls and existing operations
remain available. The extension checks the **current scene**, then offers a
reproducible candidate bundle for a simulated USD 2.00 Sandbox payment.

This is a hackathon prototype. The included synthetic example deliberately has
no hydrogen atoms: the source and candidate checks call that out. Candidates
are hypotheses; the app does not claim that any candidate is experimentally
dominant or chemically validated. Review the raw CIFs, warnings and structures.

## What the hackathon adds

* A native MatterVis extension rather than a separate static image preview.
* A frozen source CIF and method, candidate count, coupling and random seed.
* MolCrysKit disorder detection, candidate generation and six sanity checks;
  candidate CIFs, checks, API/command records and SHA-256 hashes are bundled.
* An OpenAI-compatible model summarizes the actual results and limitations. If
  the model fails, raw results remain available with an explicit failure notice.
* A real PayPal Sandbox order and server-verified capture before a job runs.
  Sandbox funds are fictitious; this app never calls live PayPal endpoints.

The underlying MatterVis source is pinned to commit
`d1de653154d2dba2411c71781d00c22c36f45759`. A small local patch prevents
disorder errors being silently reported as an ordered structure. MatterVis in
turn pins MolCrysKit to `d02ce08ab28826f87d976aef98fefb8b86998075`.
See the [MatterVis repository](https://github.com/SchrodingersCattt/MatterVis)
and [MolCrysKit repository](https://github.com/SchrodingersCattt/MolCrysKit).

## Run locally

Requirements: Git, Python 3.11+, network access to install dependencies, a
PayPal developer Sandbox merchant app and buyer, and an OpenAI-compatible API.
This development server is intended for one researcher on localhost; native
MatterVis scenes are shared across tabs, so do not expose it to the public.

```bash
git clone https://github.com/SchrodingersCattt/mattervis-saas-paypal.git
cd mattervis-saas-paypal
python -m venv .venv
source .venv/bin/activate
python scripts/setup_native.py
cp .env.example .env
```

On Windows use `.venv\Scripts\Activate.ps1` and `Copy-Item .env.example .env`.
The setup script checks out the pinned open-source MatterVis, applies the
included patch, and installs its web dependencies into this virtual environment.
It will stop rather than overwrite a checkout at a different revision.

Fill in `.env`: `AI_BASE_URL` (including `/v1` where appropriate),
`AI_API_KEY`, `AI_MODEL`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, and a
random `APP_SECRET_KEY`. Use **Sandbox** PayPal credentials. `APP_BASE_URL`
must be the address reachable by the buyer's browser; the default is
`http://127.0.0.1:50001`. Do not publish `.env` or private structures.

```bash
python -m contest.native_app
```

Open <http://127.0.0.1:50001>. The sample is an actual synthetic CIF, loaded
into the original interactive viewer. Upload a different CIF using the native
viewer if desired. In the right panel select a method, click **Check current
scene**, read the warnings, and click **Pay $2.00 with PayPal Sandbox**. Sign in
as the virtual buyer and approve the order. After returning, wait for the job;
select each candidate to open it in MatterVis or download the ZIP. A failed
paid job can be retried without another payment.

The configured AI provider receives the structural summary and check results,
which can include scientific data from the selected structure. The provider's
availability and data handling are determined by your own account settings.

## Testing

```bash
python -m pytest tests/test_premium_disorder.py
```

The test generates real structures with MolCrysKit but **mocks the payment
capture**. It verifies that access is restricted until the server records an
entitlement, candidates are distinct, and the missing-hydrogen warnings are
preserved. A real PayPal Sandbox transaction requires interactive buyer
approval; set up your own Sandbox buyer to test it. The older figure prototype
and the original MatterVis SaaS API remain in the repository for reference.
