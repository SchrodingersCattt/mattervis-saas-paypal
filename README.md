# MatterVis AI: crystal modeling with human-approved commerce

MatterVis AI adds a conversational assistant to the original interactive
MatterVis viewer. Ask it to inspect a crystal, build a supercell, or resolve
disorder. The assistant discovers and calls real Model Context Protocol (MCP)
tools. MatterVis and MolCrysKit perform the structure operations.

Inspection and supercell construction are free in this prototype. Disorder
analysis is a USD 2.00 **PayPal Sandbox** operation. The MCP tool first asks
the researcher to approve the specific structure, method, count and price.
An explicit answer initiates Sandbox checkout; the buyer must still approve in
PayPal, and the server verifies the capture before computation starts. All
payments use virtual Sandbox funds. A paid job is eligible for a free retry if
processing fails.

The bundled example is an experimentally reported disordered caffeine crystal,
[COD 4504310](https://www.crystallography.net/cod/4504310.html), from Enright,
Terskikh, Brouwer and Ripmeester, *Crystal Growth & Design* **7** (2007),
1406–1410, [doi:10.1021/cg070291o](https://doi.org/10.1021/cg070291o).
The Crystallography Open Database dedicates its data to the public domain under
[CC0](https://creativecommons.org/publicdomain/zero/1.0/). In the formula-unit
view, only part of the crystal is displayed; this caffeine structure has
disorder outside that subset. Select **Unit cell** under Display Scope to see
the overlapping alternative sites. Counts in the view include displayed copies.
These alternatives can produce source clash warnings before resolution. The
generated candidates are hypotheses for human review. Establishing physical
stability and the experimentally dominant state requires independent
validation. The synthetic fixture remains available for automated tests.

## What is new for the hackathon

* An AI chat panel in the existing MatterVis viewer, preserving native upload,
  visualization and operations.
* A real MCP server and client, including MCP elicitation for explicit human
  confirmation before the payment tool creates an order.
* A PayPal Sandbox order whose server-side quote binds the source structure,
  method, candidate count, random seed and price; capture is verified before
  the paid tool runs.
* Free source inspection and supercell construction with before/after checks;
  paid disorder candidates with checks, AI comparison and a downloadable bundle.
* Source and candidate hashes, parameters, API/command records and software
  fingerprints in the downloadable bundle, with concise findings in chat.

The viewer is pinned to MatterVis commit `d1de653154d2dba2411c71781d00c22c36f45759`
with included disorder error-handling and view-summary patches. MatterVis pins MolCrysKit at
`d02ce08ab28826f87d976aef98fefb8b86998075`. See the
[MatterVis](https://github.com/SchrodingersCattt/MatterVis) and
[MolCrysKit](https://github.com/SchrodingersCattt/MolCrysKit) repositories.

## Run locally

Requirements: Git, Python 3.11+, network access to install dependencies, an
OpenAI-compatible model with function calling, and a PayPal Sandbox merchant
app and virtual buyer. The single-user development server runs on localhost;
native scenes are shared between browser tabs.

```bash
git clone https://github.com/SchrodingersCattt/mattervis-saas-paypal.git
cd mattervis-saas-paypal
python -m venv .venv
source .venv/bin/activate
python scripts/setup_native.py
cp .env.example .env
```

On Windows use `.venv\Scripts\Activate.ps1` and `Copy-Item .env.example .env`.
The setup script checks out the pinned open-source viewer, applies the included
patches and installs the web and MCP dependencies into the virtual environment.

Fill in `.env`: `AI_BASE_URL` (including `/v1` where required), `AI_API_KEY`,
`AI_MODEL`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, and a random
`APP_SECRET_KEY`. Use **Sandbox** PayPal credentials. `APP_BASE_URL` must be
reachable by the buyer's browser; the default is `http://127.0.0.1:50001`.
Keep `.env` and private structures private.

```bash
python -m contest.native_app
```

Open <http://127.0.0.1:50001>. The default sample is COD 4504310 caffeine.
Read the formula-unit summary, then choose **Unit cell** under Display Scope
to inspect the disordered sites. Try asking:

* “Inspect this structure and explain any warnings.”
* “Build a 2×1×1 supercell and tell me whether disorder remains.”
* “Enumerate up to two ordered disorder candidates.”

When the assistant needs a choice it can show clickable options. Press Enter to
send a message and Ctrl+Enter to insert a newline. For a paid request, read the
quote and click or reply **yes** or **cancel**. If you agree, PayPal Sandbox
opens in a new tab while MatterVis waits; approve the virtual order there. The assistant reports
the checked candidates after the verified capture; use the result buttons to
open candidates or download their bundle.

The configured AI provider receives the chat and structural tool results. Use
an endpoint whose data handling is appropriate for your structures. The optional
Dockerfile follows the same setup path; the local virtual-environment path is
the tested installation path.

## External MCP clients

The same server can run over standard MCP stdio for a client that supports MCP
elicitation:

```bash
python -m contest.mcp_server --source path/to/structure.cif --workspace .local/mcp-client
```

The server exposes `inspect_structure`, `build_supercell`, `ask_question`,
`resolve_disorder_paid`, `capture_paypal_sandbox_order` and
`get_disorder_result`. The paid tool requests user consent through MCP
elicitation; orders are created only after the client obtains human consent.
Buyer approval in PayPal is separate, and capture verifies the approved order.

## Testing

```bash
python -m pytest tests/test_scope_summary.py tests/test_runtime_contract.py tests/test_premium_disorder.py tests/test_mcp_chat.py tests/test_mcp_stdio.py
```

These tests use real scientific calculations and MCP messages, while payment
HTTP calls are mocked. Real Sandbox buyer approval and model access require
configured test credentials. The older figure prototype and original MatterVis
SaaS API remain in the repository for reference.
