"""Real MCP protocol tests: human consent precedes Sandbox checkout."""

import asyncio
import json
import time
from pathlib import Path

from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import ElicitResult

from contest.chat_agent import ChatService
from contest.disorder_service import DisorderService
from contest.integrations import IntegrationError, PayPalSandbox
from contest.mcp_server import ToolContext, create_mcp_server, decode_tool_result

EXAMPLE = Path(__file__).resolve().parents[1] / "contest" / "static" / "disorder-example.cif"
SOURCE = EXAMPLE.read_text(encoding="utf-8")


def context(service, tmp_path):
    return ToolContext(service=service, owner="owner", source=SOURCE, scene_id="scene-1", scene_name="example",
                       browser_target=None, structures=None, artifact_root=tmp_path / "artifacts",
                       user_goal="Build a 2 by 1 by 1 supercell and inspect disorder")


def test_mcp_inspection_and_real_supercell(tmp_path):
    service = DisorderService(tmp_path / "jobs")

    async def run():
        async with create_connected_server_and_client_session(create_mcp_server(context(service, tmp_path))) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert {"inspect_structure", "build_supercell", "resolve_disorder_paid", "capture_paypal_sandbox_order"} <= names
            inspected = decode_tool_result(await client.call_tool("inspect_structure", {}))
            assert inspected["summary"]["disorder"]["has_disorder"]
            result = decode_tool_result(await client.call_tool("build_supercell", {"a": 2, "b": 1, "c": 1}))
            assert result["after"]["n_atoms"] == result["before"]["n_atoms"] * 2
            assert result["disorder_preserved"]
            assert any(not item["passed"] and item["name"] == "hydrogen_presence" for item in result["checks"]["frames"][0]["results"])

    try:
        asyncio.run(run())
    finally:
        service.close()


def test_mcp_ask_question_elicits_and_returns_a_choice(tmp_path):
    service = DisorderService(tmp_path / "jobs")

    async def choose(_context, params):
        assert (params.requestedSchema["properties"]["choice"]["enum"]) == ["Enumerate", "Random", "Other"]
        return ElicitResult(action="accept", content={"choice": "Enumerate", "other": ""})

    async def run():
        async with create_connected_server_and_client_session(create_mcp_server(context(service, tmp_path)), elicitation_callback=choose) as client:
            result = decode_tool_result(await client.call_tool("ask_question", {"question": "Which method?", "options": ["Enumerate", "Random"]}))
            assert result == {"status": "answered", "answer": "Enumerate"}

    try:
        asyncio.run(run())
    finally:
        service.close()


def test_mcp_question_requires_english_labels(tmp_path):
    service = DisorderService(tmp_path / "jobs")

    async def run():
        async with create_connected_server_and_client_session(create_mcp_server(context(service, tmp_path))) as client:
            result = decode_tool_result(await client.call_tool("ask_question", {"question": "选择方法？", "options": ["枚举", "随机"]}))
            assert result["status"] == "invalid"

    try:
        asyncio.run(run())
    finally:
        service.close()


def test_supercell_does_not_invent_missing_factors(tmp_path):
    service = DisorderService(tmp_path / "jobs")
    tool_context = context(service, tmp_path)
    tool_context.user_goal = "Please build a supercell"

    async def run():
        async with create_connected_server_and_client_session(create_mcp_server(tool_context)) as client:
            result = decode_tool_result(await client.call_tool("build_supercell", {"a": 2, "b": 2, "c": 1}))
            assert result["status"] == "needs_clarification"
            assert not (tmp_path / "artifacts").exists()

    try:
        asyncio.run(run())
    finally:
        service.close()


def test_mcp_declined_consent_never_creates_paypal_order(tmp_path, monkeypatch):
    service = DisorderService(tmp_path / "jobs")
    tool_context = context(service, tmp_path)
    monkeypatch.setattr(PayPalSandbox, "create_order", lambda *_: (_ for _ in ()).throw(AssertionError("must not create order")))

    async def decline(_context, _params):
        return ElicitResult(action="decline")

    async def run():
        async with create_connected_server_and_client_session(create_mcp_server(tool_context), elicitation_callback=decline) as client:
            result = decode_tool_result(await client.call_tool("resolve_disorder_paid", {"method": "enumerate", "count": 2}))
            assert result["status"] == "cancelled"
            job = service.load(tool_context.pending_job_id, "owner")
            assert not job["paid"] and not job.get("order_id")

    try:
        asyncio.run(run())
    finally:
        service.close()


def test_mcp_accept_creates_order_but_needs_paypal_capture(tmp_path, monkeypatch):
    service = DisorderService(tmp_path / "jobs")
    tool_context = context(service, tmp_path)
    orders = []

    def fake_order(_self, reference, amount, _description):
        orders.append((reference, amount))
        return {"id": "sandbox-order", "approval_url": "https://www.sandbox.paypal.com/checkoutnow?token=sandbox-order"}

    monkeypatch.setattr(PayPalSandbox, "create_order", fake_order)
    monkeypatch.setattr(PayPalSandbox, "capture_order", lambda *_: (_ for _ in ()).throw(IntegrationError("not approved")))

    async def accept(_context, _params):
        assert not orders
        return ElicitResult(action="accept", content={"approved": True})

    async def run():
        async with create_connected_server_and_client_session(create_mcp_server(tool_context), elicitation_callback=accept) as client:
            result = decode_tool_result(await client.call_tool("resolve_disorder_paid", {"method": "enumerate", "count": 2}))
            assert result["status"] == "paypal_approval_required"
            job = service.load(tool_context.pending_job_id, "owner")
            assert len(orders) == 1 and orders[0] == (job["order_reference"], "2.00")
            assert not job["paid"] and not job["candidates"]
            result = decode_tool_result(await client.call_tool("capture_paypal_sandbox_order", {"order_id": "sandbox-order"}))
            assert result["status"] == "not_captured"
            assert not service.load(tool_context.pending_job_id, "owner")["paid"]
            monkeypatch.setattr(PayPalSandbox, "capture_order", lambda *_: {"capture_id": "mock-capture"})
            monkeypatch.setattr(service, "_run", lambda *_: None)
            result = decode_tool_result(await client.call_tool("capture_paypal_sandbox_order", {"order_id": "sandbox-order"}))
            assert result["status"] == "captured"
            assert service.load(tool_context.pending_job_id, "owner")["paid"]
            assert not service.cancel_unpaid_order(tool_context.pending_job_id, "owner")

    try:
        asyncio.run(run())
    finally:
        service.close()


def wait_phase(chat, owner, phase, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = chat.snapshot(owner)
        if state["phase"] == phase:
            return state
        time.sleep(0.1)
    raise AssertionError(f"Expected {phase}, got {state['phase']}")


def test_chat_requires_an_explicit_reply_before_order_creation(tmp_path, monkeypatch):
    service = DisorderService(tmp_path / "jobs")
    chat = ChatService(service, tmp_path / "artifacts")
    orders = []
    monkeypatch.setattr("contest.chat_agent._model_request", lambda *_: {"role": "assistant", "content": None, "tool_calls": [{
        "id": "call-1", "type": "function", "function": {"name": "resolve_disorder_paid", "arguments": json.dumps({"method": "enumerate", "count": 2})}}]})
    monkeypatch.setattr(PayPalSandbox, "create_order", lambda _self, ref, amount, _description: orders.append((ref, amount)) or {
        "id": "sandbox-order", "approval_url": "https://www.sandbox.paypal.com/checkoutnow?token=sandbox-order"})
    params = {"source": SOURCE, "scene_id": "scene-1", "scene_name": "example", "browser_target": None, "structures": None}
    try:
        chat.submit("owner", "请拆分当前结构的无序", **params)
        wait_phase(chat, "owner", "awaiting_consent")
        assert not orders
        chat.submit("owner", "也许吧", **params)
        assert chat.snapshot("owner")["phase"] == "awaiting_consent" and not orders
        chat.submit("owner", "同意支付", **params)
        state = wait_phase(chat, "owner", "awaiting_paypal")
        assert len(orders) == 1 and state["approval_url"]
        job = service.load(state["job_id"], "owner")
        assert not job["paid"] and not job["candidates"]
    finally:
        chat.close()
        service.close()


def test_chat_rejects_consent_if_the_structure_changed(tmp_path, monkeypatch):
    service = DisorderService(tmp_path / "jobs")
    chat = ChatService(service, tmp_path / "artifacts")
    orders = []
    monkeypatch.setattr("contest.chat_agent._model_request", lambda *_: {"role": "assistant", "content": None, "tool_calls": [{
        "id": "call-1", "type": "function", "function": {"name": "resolve_disorder_paid", "arguments": '{"method":"enumerate","count":2}'}}]})
    monkeypatch.setattr(PayPalSandbox, "create_order", lambda *_: orders.append(True))
    params = {"source": SOURCE, "scene_id": "scene-1", "scene_name": "example", "browser_target": None, "structures": None}
    try:
        chat.submit("owner", "拆分无序", **params)
        wait_phase(chat, "owner", "awaiting_consent")
        chat.submit("owner", "同意支付", **{**params, "source": SOURCE + "\n# changed"})
        wait_phase(chat, "owner", "idle")
        assert not orders
    finally:
        chat.close()
        service.close()


def test_chat_choice_answers_the_pending_mcp_question(tmp_path, monkeypatch):
    service = DisorderService(tmp_path / "jobs")
    chat = ChatService(service, tmp_path / "artifacts")

    def model(messages, _tools):
        if messages[-1]["role"] == "tool":
            assert json.loads(messages[-1]["content"])["answer"] == "Enumerate"
            return {"role": "assistant", "content": "You chose enumeration."}
        return {"role": "assistant", "content": None, "tool_calls": [{
            "id": "question-1", "type": "function", "function": {"name": "ask_question",
            "arguments": json.dumps({"question": "Which method?", "options": ["Enumerate", "Random"]})}}]}

    monkeypatch.setattr("contest.chat_agent._model_request", model)
    params = {"source": SOURCE, "scene_id": "scene-1", "scene_name": "example", "browser_target": None, "structures": None}
    try:
        chat.submit("owner", "How should I compare alternatives?", **params)
        pending = wait_phase(chat, "owner", "awaiting_question")
        prompt_id = pending["pending_id"]
        assert chat.choice_text("owner", prompt_id, 0) == "Enumerate"
        chat.submit("owner", "Enumerate", expected_prompt_id=prompt_id, **params)
        state = wait_phase(chat, "owner", "idle")
        assert state["messages"][-1]["text"] == "You chose enumeration."
    finally:
        chat.close()
        service.close()


def test_generic_question_answer_cannot_authorize_payment(tmp_path, monkeypatch):
    service = DisorderService(tmp_path / "jobs")
    chat = ChatService(service, tmp_path / "artifacts")
    orders = []

    def model(messages, _tools):
        if messages[-1]["role"] == "tool":
            return {"role": "assistant", "content": None, "tool_calls": [{"id": "paid", "type": "function", "function": {
                "name": "resolve_disorder_paid", "arguments": '{"method":"enumerate","count":2}'}}]}
        return {"role": "assistant", "content": None, "tool_calls": [{"id": "question", "type": "function", "function": {
            "name": "ask_question", "arguments": json.dumps({"question": "Continue?", "options": ["Yes, approve", "Cancel"]}, ensure_ascii=False)}}]}

    monkeypatch.setattr("contest.chat_agent._model_request", model)
    monkeypatch.setattr(PayPalSandbox, "create_order", lambda *_: orders.append(True))
    params = {"source": SOURCE, "scene_id": "scene-1", "scene_name": "example", "browser_target": None, "structures": None}
    try:
        chat.submit("owner", "请处理无序", **params)
        first = wait_phase(chat, "owner", "awaiting_question")
        chat.submit("owner", "Yes, approve", expected_prompt_id=first["pending_id"], **params)
        wait_phase(chat, "owner", "awaiting_consent")
        assert not orders
        chat.submit("owner", "取消", **params)
        wait_phase(chat, "owner", "idle")
        assert not orders
    finally:
        chat.close()
        service.close()


def test_non_english_model_response_is_retried_in_english(tmp_path, monkeypatch):
    service = DisorderService(tmp_path / "jobs")
    chat = ChatService(service, tmp_path / "artifacts")
    replies = iter([{"role": "assistant", "content": "结构有无序。"},
                    {"role": "assistant", "content": "The structure contains disorder."}])
    monkeypatch.setattr("contest.chat_agent._model_request", lambda *_: next(replies))
    params = {"source": SOURCE, "scene_id": "scene-1", "scene_name": "example", "browser_target": None, "structures": None}
    try:
        chat.submit("owner", "检查这个结构", **params)
        state = wait_phase(chat, "owner", "idle")
        assert state["messages"][-1]["text"] == "The structure contains disorder."
    finally:
        chat.close()
        service.close()


def test_paid_report_goal_excludes_unrelated_prior_requests(tmp_path, monkeypatch):
    service = DisorderService(tmp_path / "jobs")
    chat = ChatService(service, tmp_path / "artifacts")

    def model(messages, _tools):
        if messages[-1]["content"] == "Explain the supercell":
            return {"role": "assistant", "content": "The supercell is ready."}
        return {"role": "assistant", "content": None, "tool_calls": [{"id": "paid", "type": "function", "function": {
            "name": "resolve_disorder_paid", "arguments": '{"method":"enumerate","count":2}'}}]}

    monkeypatch.setattr("contest.chat_agent._model_request", model)
    params = {"source": SOURCE, "scene_id": "scene-1", "scene_name": "example", "browser_target": None, "structures": None}
    try:
        chat.submit("owner", "Explain the supercell", **params)
        wait_phase(chat, "owner", "idle")
        chat.submit("owner", "Enumerate two disorder candidates", **params)
        state = wait_phase(chat, "owner", "awaiting_consent")
        job = service.load(state["job_id"], "owner")
        assert job["research_goal"] == "Enumerate two disorder candidates"
        chat.submit("owner", "Cancel", **params)
        wait_phase(chat, "owner", "idle")
    finally:
        chat.close()
        service.close()
