# MatterVis AI: crystal modeling with human-approved commerce

MatterVis AI adds a conversational assistant to the original interactive
MatterVis viewer. Ask it to inspect a crystal, build a supercell, or resolve
disorder. The assistant discovers and calls real Model Context Protocol (MCP)
tools; structure operations use MatterVis and MolCrysKit rather than invented
model output.

Inspection and supercell construction are free in this prototype. Disorder
analysis is a USD 2.00 **PayPal Sandbox** operation. The MCP tool first asks
the researcher to approve the specific structure, method, count and price.
An explicit answer initiates Sandbox checkout; the buyer must still approve in
PayPal, and the server verifies the capture before computation starts. No real
money is charged. A failed paid job can be retried without another payment.

The bundled structure is a synthetic example and deliberately lacks hydrogen.
The assistant and result checks report that limitation. Ordered candidates are
hypotheses, not proof of physical stability or an experimentally dominant state.

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
  fingerprints in the bundle, rather than in the everyday chat interface.

The viewer is pinned to MatterVis commit `d1de653154d2dba2411c71781d00c22c36f45759`
with an included disorder error-handling patch. MatterVis pins MolCrysKit at
`d02ce08ab28826f87d976aef98fefb8b86998075`. See the
[MatterVis](https://github.com/SchrodingersCattt/MatterVis) and
[MolCrysKit](https://github.com/SchrodingersCattt/MolCrysKit) repositories.

## Run locally

Requirements: Git, Python 3.11+, network access to install dependencies, an
OpenAI-compatible model with function calling, and a PayPal Sandbox merchant
app and virtual buyer. This is a single-user localhost development server;
native scenes are shared between browser tabs and it is not hardened for
public deployment.

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
patch and installs the web and MCP dependencies into the virtual environment.

Fill in `.env`: `AI_BASE_URL` (including `/v1` where required), `AI_API_KEY`,
`AI_MODEL`, `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, and a random
`APP_SECRET_KEY`. Use **Sandbox** PayPal credentials. `APP_BASE_URL` must be
reachable by the buyer's browser; the default is `http://127.0.0.1:50001`.
Never publish `.env` or private structures.

```bash
python -m contest.native_app
```

Open <http://127.0.0.1:50001>. Try asking:

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
elicitation; a client that cannot obtain consent cannot create an order. Buyer
approval in PayPal is separate, and capture verifies the approved order.

## Testing

```bash
python -m pytest tests/test_mcp_chat.py tests/test_mcp_stdio.py tests/test_premium_disorder.py
```

These tests use real scientific calculations and MCP messages, while payment
HTTP calls are mocked. Real Sandbox buyer approval and model access require
configured test credentials. The older figure prototype and original MatterVis
SaaS API remain in the repository for reference.
