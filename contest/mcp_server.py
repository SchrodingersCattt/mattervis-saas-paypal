"""MatterVis and paid PayPal Sandbox tools exposed through the MCP protocol."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import anyio
from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field, create_model

from .disorder_service import DisorderService, validate_options
from .integrations import IntegrationError, PayPalSandbox
from .science_tools import build_supercell, inspect_cif


class PaymentApproval(BaseModel):
    approved: bool


@dataclass
class ToolContext:
    service: DisorderService
    owner: str
    source: str
    scene_id: str | None
    scene_name: str
    browser_target: str | None
    structures: Any
    artifact_root: Path
    user_goal: str
    recent_user_text: str = ""
    pending_job_id: str | None = None
    inspection: dict | None = None
    expose_paths: bool = False
    explicit_tool_request: bool = False
    view_action: Any = None
    source_changed: bool = False

    def inspect(self) -> dict:
        if self.inspection is None:
            directory = self.artifact_root / ("inspect-" + uuid.uuid4().hex)
            directory.mkdir(parents=True, mode=0o700)
            source = directory / "source.cif"
            source.write_text(self.source, encoding="utf-8", newline="\n")
            self.inspection = inspect_cif(source)
        return self.inspection


def create_mcp_server(context: ToolContext) -> FastMCP:
    server = FastMCP("MatterVis scientific and PayPal Sandbox tools", log_level="ERROR")

    @server.tool(name="inspect_structure")
    async def inspect_structure() -> dict:
        """Inspect the current structure, including disorder, molecules and sanity checks. Free."""
        data = await anyio.to_thread.run_sync(context.inspect)
        return {"status": "complete", "summary": data["summary"], "sanity": data["sanity"],
                "molecules": data["molecules"]}

    @server.tool(name="ask_question")
    async def ask_question(ctx: Context, question: str, options: list[str]) -> dict:
        """Ask the researcher to choose from 2–4 options or provide another answer."""
        question = question.strip()
        choices = [item.strip() for item in options if isinstance(item, str) and item.strip()]
        if not question or len(question) > 500 or not 2 <= len(choices) <= 4 or any(len(item) > 100 for item in choices):
            return {"status": "invalid", "message": "A short question with 2–4 short options is required."}
        if re.search(r"[\u4e00-\u9fff]", question + " ".join(choices)):
            return {"status": "invalid", "message": "Please phrase the question and choices in English."}
        if len({item.casefold() for item in choices}) != len(choices) or any(item.casefold() == "other" for item in choices):
            return {"status": "invalid", "message": "Options must be distinct; an Other option is provided automatically."}
        # MCP's elicitation validator accepts primitive annotations; attach the
        # enum to the JSON schema and validate it again after the response.
        schema = create_model("QuestionAnswer", choice=(str, Field(..., json_schema_extra={"enum": [*choices, "Other"]})),
                              other=(str, Field(default="", max_length=500)))
        try:
            answer = await ctx.elicit(question, schema)
        except Exception:
            logging.getLogger(__name__).exception("MCP question elicitation failed")
            return {"status": "unavailable", "message": "The client could not ask the researcher."}
        if answer.action != "accept" or not answer.data:
            return {"status": "cancelled", "message": "The question was not answered."}
        if answer.data.choice not in [*choices, "Other"]:
            return {"status": "invalid", "message": "The selected option was not offered."}
        selected = answer.data.other.strip() if answer.data.choice == "Other" else answer.data.choice
        if not selected:
            return {"status": "invalid", "message": "The answer cannot be empty."}
        return {"status": "answered", "answer": selected}

    @server.tool(name="build_supercell")
    async def make_supercell(a: int, b: int, c: int) -> dict:
        """Build a new periodic supercell with integer factors 1–4. Free; the source remains unchanged."""
        request_text = context.recent_user_text or context.user_goal
        if not context.explicit_tool_request and not re.search(r"supercell|replicat|expand|build|construct|create|make|超胞|扩胞|构建|生成|建模|新建|扩大", request_text, re.I):
            return {"status": "needs_clarification", "message": "Ask the researcher to explicitly request a supercell first."}
        numbers = [int(value) for value in re.findall(r"(?<!\d)\d+(?!\d)", request_text)]
        if not context.explicit_tool_request and not any(numbers[index:index + 3] == [a, b, c] for index in range(max(0, len(numbers) - 2))):
            return {"status": "needs_clarification", "message": "Ask the researcher to specify all three supercell factors explicitly."}
        output, report = await anyio.to_thread.run_sync(build_supercell, context.source, (a, b, c), context.artifact_root)
        opened = False
        label = f"Supercell {a}×{b}×{c}"
        if context.structures is not None and context.browser_target:
            def register():
                scene = context.structures.register(output, label=label).result(timeout=120)
                handle = context.structures.handle(scene)
                context.structures.focus(handle, target=context.browser_target).result(timeout=120)
                return scene, handle

            scene, handle = await anyio.to_thread.run_sync(register)
            context.scene_id = scene
            context.scene_name = label
            context.view_action = handle
            opened = True
        before, after = report["before"], report["after"]
        context.source = output.read_text(encoding="utf-8")
        context.inspection = after
        context.source_changed = True
        result = {"status": "complete", "scene_registered": opened, "focus_requested": opened, "scale": [a, b, c],
                  "before": before["summary"], "after": after["summary"], "checks": after["sanity"],
                  "disorder_preserved": bool(after["summary"]["disorder"]["has_disorder"]),
                  "interpretation": "A constructed model, not proof of physical stability."}
        if context.expose_paths:
            result["artifact_path"] = str(output)
        return result

    @server.tool(name="resolve_disorder_paid")
    async def resolve_disorder_paid(
        ctx: Context,
        method: Literal["optimal", "enumerate", "random"],
        count: int = 1,
        seed: int | None = None,
    ) -> dict:
        """Offer paid disorder analysis. Ask the human through MCP before creating a Sandbox order."""
        if context.source_changed:
            return {"status": "needs_new_request", "message": "A new structure was just created. Ask the researcher to review that scene and request paid analysis in a new message."}
        options = validate_options(method, count, seed, False)
        job = context.service.create(context.owner, context.source, options,
                                     {"id": context.scene_id, "structure": context.scene_name},
                                     research_goal=context.user_goal)
        context.pending_job_id = job["id"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            job = context.service.load(job["id"], context.owner)
            if job["status"] != "checking":
                break
            await asyncio.sleep(0.15)
        if job["status"] != "ready":
            return {"status": "unavailable", "message": job.get("error") or "The structure check did not finish."}
        failed = []
        for frame in job["source_sanity"].get("frames", []):
            for item in frame.get("results", []):
                if not item.get("passed") or item.get("status") == "skipped" or "skipped" in item.get("message", "").lower():
                    failed.append(item.get("message", "A check needs review."))
        warning = " ".join(failed[:3])
        method_label = {"optimal": "occupancy-favoured", "enumerate": "enumerated", "random": "random-sampling"}[method]
        seed_text = f" Random seed: {options['seed']}." if options["seed"] is not None else ""
        prompt = (f"Run {method_label} disorder analysis on {context.scene_name}, with up to {options['count']} candidate(s)."
                  f"{seed_text} Symmetry copies are independent. USD 2.00 in PayPal Sandbox (no real money). {warning} "
                  "Reply 'yes' to start sandbox checkout or 'cancel' to decline. PayPal approval is still required.")
        try:
            answer = await ctx.elicit(prompt, PaymentApproval)
        except Exception:
            return {"status": "consent_unavailable", "message": "The client could not request user confirmation; no order was created."}
        if answer.action != "accept" or not answer.data or not answer.data.approved:
            return {"status": "cancelled", "message": "The researcher did not authorize checkout. No order was created."}

        def create_order():
            reference = None
            try:
                quote = context.service.reserve_checkout(job["id"], context.owner, options)
                reference = quote["reference"]
                if quote["approval_url"]:
                    return quote["approval_url"]
                order = PayPalSandbox().create_order(reference, job["price"], "MatterVis disorder analysis")
                context.service.attach_order(job["id"], context.owner, order["id"], reference, order["approval_url"])
                return order["approval_url"]
            except Exception:
                if reference:
                    context.service.cancel_checkout_reservation(job["id"], context.owner, reference)
                raise

        try:
            approval_url = await anyio.to_thread.run_sync(create_order)
        except (ValueError, IntegrationError, RuntimeError):
            return {"status": "payment_error", "message": "Could not open PayPal Sandbox; no capture was made."}
        return {"status": "paypal_approval_required", "approval_url": approval_url,
                "message": "The user must approve this order in PayPal Sandbox before analysis starts."}

    @server.tool(name="capture_paypal_sandbox_order")
    async def capture_paypal_sandbox_order(order_id: str) -> dict:
        """Verify and capture the current Sandbox order after the buyer has approved it in PayPal."""
        if not context.pending_job_id:
            return {"status": "unavailable", "message": "There is no approved checkout for this session."}
        job_id = context.pending_job_id
        def capture():
            try:
                # Keep cancellation and capture serialized. A cancelled quote
                # must never be captured after the user changed their mind.
                with context.service.lock:
                    job = context.service.verify_order_quote(job_id, context.owner, order_id)
                    if not job.get("paid"):
                        captured = PayPalSandbox().capture_order(order_id, job.get("order_reference") or job_id, job["price"])
                        context.service.mark_paid_and_run(job_id, context.owner, order_id, captured["capture_id"])
                return {"status": "captured", "message": "Sandbox payment verified; disorder analysis is queued."}
            except (ValueError, FileNotFoundError, IntegrationError, RuntimeError):
                return {"status": "not_captured", "message": "The Sandbox order was not approved or could not be verified."}

        return await anyio.to_thread.run_sync(capture)

    @server.tool(name="get_disorder_result")
    def get_disorder_result() -> dict:
        """Read the paid disorder job's status and checked results; unavailable before capture."""
        if not context.pending_job_id:
            return {"status": "unavailable", "message": "There is no disorder job in this session."}
        try:
            job = context.service.load(context.pending_job_id, context.owner)
            if not job.get("paid"):
                return {"status": "payment_required", "message": "PayPal Sandbox capture has not been verified."}
            if job.get("status") != "complete":
                return {"status": job.get("status", "unknown"), "message": job.get("error")}
            candidates = []
            for item in job["candidates"]:
                summary = {key: value for key, value in item["summary"].items() if key != "source"}
                sanity = {key: value for key, value in item["sanity"].items() if key != "file"}
                candidates.append({"candidate": item["index"] + 1, "summary": summary, "sanity": sanity})
            result = {"status": "complete", "candidates": candidates, "ai_report": job.get("ai_report")}
            if context.expose_paths:
                result["bundle_path"] = str(context.service.result_path(context.pending_job_id, context.owner))
            return result
        except (ValueError, FileNotFoundError, PermissionError):
            return {"status": "unavailable", "message": "The disorder result is unavailable."}

    return server


def decode_tool_result(result) -> dict:
    if result.isError:
        raise RuntimeError("An MCP tool failed. See Server log for details.")
    if result.structuredContent is not None:
        return result.structuredContent
    for block in result.content:
        if getattr(block, "type", None) == "text":
            try:
                value = json.loads(block.text)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                return value
    raise RuntimeError("The MCP tool returned an invalid result.")


def main():
    """Run the same tools over stdio for an MCP client with elicitation support."""
    import argparse
    import contextlib
    import sys

    from dotenv import load_dotenv

    parser = argparse.ArgumentParser(description="MatterVis scientific and PayPal Sandbox MCP server")
    parser.add_argument("--source", required=True, help="CIF file selected by the user")
    parser.add_argument("--workspace", default=".local/mcp-standalone")
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    source = Path(args.source).resolve()
    if source.suffix.lower() != ".cif" or not source.is_file() or source.stat().st_size > 10 * 1024 * 1024:
        raise SystemExit("Choose an existing CIF file no larger than 10 MB.")
    workspace = Path(args.workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    service = DisorderService(workspace / "jobs")
    # On Windows, loading NumPy for the first time after the stdio event loop
    # has started can stall inside the native loader. Initialize scientific
    # dependencies before accepting MCP messages and keep protocol stdout clean.
    with contextlib.redirect_stdout(sys.stderr):
        inspection = inspect_cif(source)
    context = ToolContext(service=service, owner="standalone-" + uuid.uuid4().hex, source=source.read_text(encoding="utf-8"),
                          scene_id=None, scene_name=source.stem, browser_target=None, structures=None,
                          artifact_root=workspace / "artifacts", user_goal="build supercell or analyze disorder",
                          inspection=inspection, expose_paths=True, explicit_tool_request=True)
    try:
        create_mcp_server(context).run(transport="stdio")
    finally:
        service.close()


if __name__ == "__main__":
    main()
