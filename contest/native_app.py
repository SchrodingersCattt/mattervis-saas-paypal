"""Native MatterVis viewer with a human-authorized MCP chat assistant."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from flask import Response, abort, request, send_file, session
from mat_viewer.app import create_app
from mat_viewer.extensions import Extension
from mcp.shared.memory import create_connected_server_and_client_session

from .chat_agent import ChatService
from .disorder_service import DisorderService
from .mcp_server import ToolContext, create_mcp_server, decode_tool_result

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
LOGGER = logging.getLogger(__name__)


def owner_id():
    if "premium_owner" not in session:
        session["premium_owner"] = secrets.token_hex(24)
    return session["premium_owner"]


def payment_result_page(message):
    return Response(
        "<!doctype html><html><head><meta charset='utf-8'><title>PayPal Sandbox</title></head>"
        "<body style='font-family:system-ui;padding:2rem;color:#172b4d;background:#fff'>"
        f"<p>{message}</p><p>You can close this tab and return to MatterVis.</p>"
        "<script>setTimeout(function(){window.close()},1000)</script></body></html>",
        mimetype="text/html",
    )


class MatterVisChat(Extension):
    name = "mattervis.mcp_chat"

    def __init__(self, root):
        self.root = Path(root)
        self.service = DisorderService(self.root / "jobs")
        self.chat = ChatService(self.service, self.root / "artifacts")

    def close(self):
        self.chat.close()
        self.service.close()

    def build_web_panel(self, context):
        from dash import dcc, html

        return html.Div([
            html.H3("MatterVis AI"),
            html.P("Ask about the current structure, build a supercell, or request disorder analysis."),
            html.Div(id="chat-messages", className="chat-messages", role="log", **{"aria-live": "polite"}),
            html.Div(id="chat-status", className="chat-status", role="status"),
            dcc.Textarea(id="chat-input", placeholder="例如：检查当前结构，或帮我建立 2×2×1 超胞…", maxLength=2000,
                         style={"width": "100%", "height": "76px"}),
            html.Button("Send / 发送", id="chat-send", n_clicks=0, style={"marginTop": "8px"}),
            html.Span("Enter 发送 · Ctrl+Enter 换行", className="chat-key-hint"),
            html.Div(id="chat-actions", style={"marginTop": "12px"}),
            html.Div(id="chat-open-trigger", style={"display": "none"}),
            html.Div(id="chat-paypal-url", style={"display": "none"}),
            dcc.Store(id="chat-submit-ack"),
            html.P("AI summarizes tool results; review structures and checks. Disorder analysis uses USD 2.00 in PayPal Sandbox, with no real money charged.", className="chat-footnote"),
            dcc.Interval(id="chat-poll", interval=2000, n_intervals=0),
        ], id="chat-panel", style={"padding": "18px", "overflowY": "auto", "height": "100%"})

    def register_web(self, app, context):
        from dash import ALL, Input, Output, State, ctx, dcc, html, no_update
        from dash.exceptions import PreventUpdate

        service, chat = self.service, self.chat

        @app.server.before_request
        def ensure_chat_owner():
            if request.path == "/":
                owner_id()

        @app.server.get("/checkout/return")
        def checkout_return():
            owner = session.get("premium_owner")
            job_id = session.get("premium_job", "")
            token = request.args.get("token", "")
            try:
                service.load(job_id, owner)
                tool_context = ToolContext(service=service, owner=owner, source="", scene_id=None, scene_name="",
                                           browser_target=None, structures=None, artifact_root=self.root / "artifacts",
                                           user_goal="", pending_job_id=job_id)

                async def capture():
                    async with create_connected_server_and_client_session(create_mcp_server(tool_context)) as client:
                        return decode_tool_result(await client.call_tool("capture_paypal_sandbox_order", {"order_id": token}))

                result = asyncio.run(capture())
                success = result.get("status") == "captured"
                chat.payment_returned(owner, job_id, success)
                return payment_result_page("Sandbox payment verified; analysis is running." if success else "Sandbox payment could not be verified.")
            except Exception:
                LOGGER.exception("Could not verify Sandbox return")
                if owner:
                    chat.payment_returned(owner, job_id, False)
                return payment_result_page("Sandbox payment could not be verified.")

        @app.server.get("/checkout/cancel")
        def checkout_cancel():
            owner = session.get("premium_owner")
            if owner:
                job_id = session.get("premium_job", "")
                try:
                    cancelled = service.cancel_unpaid_order(job_id, owner)
                    chat.payment_returned(owner, job_id, not cancelled)
                except (ValueError, FileNotFoundError):
                    chat.payment_returned(owner, job_id, False)
            return payment_result_page("Sandbox payment was cancelled.")

        @app.server.get("/premium/jobs/<job_id>/download")
        def download(job_id):
            try:
                return send_file(service.result_path(job_id, session.get("premium_owner")), as_attachment=True,
                                 download_name="mattervis-disorder-results.zip")
            except (FileNotFoundError, PermissionError):
                abort(404)

        @app.server.get("/health")
        def health():
            return {"ok": True, "application": "mattervis-mcp-chat", "paypal_environment": "sandbox"}

        @app.callback(
            Output("chat-submit-ack", "data"), Input("chat-send", "n_clicks"),
            Input({"type": "chat-choice", "prompt": ALL, "index": ALL}, "n_clicks"),
            State("chat-input", "value"), State("scene-tabs", "value"),
            State("extension-browser-target", "data"), prevent_initial_call=True,
        )
        def submit_chat(_send, _choices, text, scene_id, browser_target):
            owner = owner_id()
            triggered = ctx.triggered_id
            selected_prompt = triggered if isinstance(triggered, dict) and triggered.get("type") == "chat-choice" else None
            if selected_prompt and (not ctx.triggered or not ctx.triggered[0].get("value")):
                raise PreventUpdate
            if triggered != "chat-send" and not selected_prompt:
                raise PreventUpdate
            try:
                selected_text = chat.choice_text(owner, selected_prompt["prompt"], int(selected_prompt["index"])) if selected_prompt else text
                try:
                    exported = context.viewer.export_cif(scene_id=scene_id)
                except Exception:
                    # A decline still works if the user closed the scene; an
                    # affirmative reply with no matching source is rejected.
                    exported = {"content": "", "scene_id": scene_id, "structure": ""}
                chat.submit(owner, selected_text, source=exported["content"], scene_id=exported.get("scene_id"),
                            scene_name=exported.get("structure") or "current structure",
                            browser_target=browser_target, structures=context.structures,
                            expected_prompt_id=selected_prompt["prompt"] if selected_prompt else None)
                return {"id": secrets.token_hex(8), "clear": not bool(selected_prompt), "sent": text or "", "error": ""}
            except (ValueError, RuntimeError, FileNotFoundError) as exc:
                return {"id": secrets.token_hex(8), "clear": False, "sent": "", "error": str(exc)}

        @app.callback(Output("chat-input", "value"), Input("chat-submit-ack", "data"),
                      State("chat-input", "value"), prevent_initial_call=True)
        def clear_input(ack, value):
            if ack and ack.get("clear") and value == ack.get("sent"):
                return ""
            return no_update

        @app.callback(
            Output("chat-messages", "children"), Output("chat-status", "children"),
            Output("chat-send", "disabled"), Output("chat-actions", "children"), Output("chat-paypal-url", "children"),
            Input("chat-poll", "n_intervals"), Input("chat-submit-ack", "data"), prevent_initial_call=False,
        )
        def update_chat(_poll, ack):
            owner = owner_id()
            feedback = (ack or {}).get("error", "")
            handle = chat.take_view_action(owner)
            if handle is not None:
                try:
                    if context.structures.handle(handle.scene_id) == handle:
                        context.viewer.patch_state({"display_mode": "unit_cell"}, scene_id=handle.scene_id, broadcast=True)
                except (KeyError, ValueError, RuntimeError):
                    LOGGER.exception("Could not switch the new scene to unit-cell view")
            view = chat.snapshot(owner)
            if view["job_id"] and session.get("premium_job") != view["job_id"]:
                session["premium_job"] = view["job_id"]
            messages = view["messages"] or [{"role": "assistant", "text": "你好！我可以检查当前结构、建立超胞，或在你同意后使用 PayPal 沙箱进行无序拆分。\n\nI can inspect this structure, build a supercell, or ask before paid disorder analysis."}]
            bubbles = []
            for item in messages[-60:]:
                content = [dcc.Markdown(item["text"], link_target="_blank") if item["role"] == "assistant" else item["text"]]
                if item.get("options"):
                    content.append(html.Div([
                        html.Button(label, id={"type": "chat-choice", "prompt": item["prompt_id"], "index": index},
                                    n_clicks=0, disabled=item["prompt_id"] != view["pending_id"],
                                    **({"data-payment-consent": "yes"} if item.get("kind") == "payment" and index == 0 else {}))
                        for index, label in enumerate(item["options"])
                    ], className="chat-choices"))
                bubbles.append(html.Div(content, className="chat-bubble chat-bubble--" + item["role"]))
            phase = {"thinking": "AI is thinking…", "checking": "Checking the structure…", "inspecting": "Inspecting the structure…",
                     "building": "Building the supercell…", "awaiting_consent": "Waiting for payment confirmation.",
                     "awaiting_question": "Waiting for your answer.", "preparing_checkout": "Preparing PayPal Sandbox checkout…",
                     "awaiting_paypal": "Waiting for PayPal Sandbox approval.", "running": "Analyzing candidates…"}.get(view["phase"], "")
            actions = []
            if view["approval_url"]:
                actions.extend([html.A("Open PayPal Sandbox", href=view["approval_url"], target="_blank", rel="noopener noreferrer", className="chat-payment-link"),
                                html.A("Cancel / 取消", href="/checkout/cancel", target="_blank", rel="noopener noreferrer", style={"marginLeft": "12px"})])
            if view["job_id"]:
                try:
                    job = service.load(view["job_id"], owner)
                    if job.get("paid") and job.get("status") == "complete":
                        actions.append(html.Div([html.Button(f"Open candidate {item['index'] + 1}",
                                                            id={"type": "chat-open-candidate", "index": item["index"]}, n_clicks=0)
                                                 for item in job["candidates"]], style={"display": "flex", "gap": "6px", "flexWrap": "wrap"}))
                        actions.append(html.P(html.A("Download results and checks", href=f"/premium/jobs/{view['job_id']}/download", target="_blank")))
                        if view["open_status"]:
                            actions.append(html.P(view["open_status"]))
                except FileNotFoundError:
                    pass
            return bubbles, phase or feedback, not view["can_reply"], actions, view["approval_url"] or ""

        @app.callback(
            Output("chat-open-trigger", "children"),
            Input({"type": "chat-open-candidate", "index": ALL}, "n_clicks"),
            State("extension-browser-target", "data"), prevent_initial_call=True,
        )
        def open_candidate(clicks, target):
            triggered = ctx.triggered_id
            if not isinstance(triggered, dict) or not any(clicks or []):
                raise PreventUpdate
            try:
                chat.open_candidate(owner_id(), int(triggered["index"]), target, context.structures)
            except (ValueError, FileNotFoundError, PermissionError):
                raise PreventUpdate from None
            return ""


def create_native_app(*, data_root=None, input_path=None):
    secret = os.getenv("APP_SECRET_KEY", "")
    if not secret:
        raise RuntimeError("Set APP_SECRET_KEY in .env before starting the app.")
    data_root = Path(data_root or ROOT / ".local" / "native")
    data_root.mkdir(parents=True, exist_ok=True)
    extension = MatterVisChat(data_root)
    app = create_app(root_dir=str(data_root), preset_path=str(data_root / "preset.json"),
                     input_path=input_path, extensions=(extension,))
    app.index_string = app.index_string.replace("<html>", '<html data-mattervis-theme="light">', 1)
    styles = """<style>
      #mv-extension-panels {background:#fff; color:#172b4d; border-left:1px solid #d9e2ec}
      #chat-panel textarea {box-sizing:border-box; resize:vertical; padding:9px; font:inherit; color:#172b4d; background:#fff; border:1px solid #b9c9d9; border-radius:7px}
      #chat-panel button, .chat-payment-link {font:inherit; border:1px solid #b9c9d9; border-radius:6px; padding:7px 10px; background:#fff; color:#172b4d; cursor:pointer}
      #chat-send:not(:disabled), .chat-payment-link {background:#0f766e; border-color:#0f766e; color:white; font-weight:650; text-decoration:none}
      #chat-panel button:disabled {background:#f2f4f7; color:#98a2b3; border-color:#e4e7ec; cursor:not-allowed}
      .chat-messages {max-height:58vh; overflow-y:auto; display:flex; flex-direction:column; gap:9px; padding:6px 0 12px}
      .chat-bubble {white-space:pre-wrap; overflow-wrap:anywhere; padding:10px 12px; border-radius:10px; line-height:1.5}
      .chat-bubble--assistant {background:#f3f7fb; color:#172b4d; align-self:flex-start}
      .chat-bubble--user {background:#e7f4f1; color:#154d48; align-self:flex-end}
      .chat-bubble p:first-child {margin-top:0} .chat-bubble p:last-child {margin-bottom:0}
      .chat-bubble pre, .chat-bubble table {max-width:100%; overflow-x:auto; display:block}
      .chat-status, .chat-footnote {font-size:12px; color:#667085; margin:8px 0}
      .chat-key-hint {font-size:11px; color:#667085; margin-left:8px}
      .chat-choices {display:flex; gap:6px; flex-wrap:wrap; margin-top:9px}
      #status-banner.status-banner--idle {display:none}
    </style>"""
    script = """<script>
      let paypalWindow = null;
      function openPaypalWindow() {
        if (paypalWindow && !paypalWindow.closed) return;
        paypalWindow = window.open('about:blank', '_blank');
        if (paypalWindow) {
          paypalWindow.document.title = 'PayPal Sandbox';
          paypalWindow.document.body.textContent = 'Opening PayPal Sandbox…';
          paypalWindow.opener = null;
        }
      }
      document.addEventListener('keydown', function(event) {
        const input = event.target;
        if (!input || input.id !== 'chat-input' || event.key !== 'Enter' || event.isComposing || event.keyCode === 229) return;
        if (event.ctrlKey || event.metaKey) {
          event.preventDefault();
          const start = input.selectionStart, end = input.selectionEnd;
          const value = input.value.slice(0, start) + '\\n' + input.value.slice(end);
          Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(input, value);
          input.setSelectionRange(start + 1, start + 1);
          input.dispatchEvent(new Event('input', {bubbles:true}));
          return;
        }
        if (!event.shiftKey) {
          event.preventDefault();
          const button = document.getElementById('chat-send');
          if (button && !button.disabled) button.click();
        }
      }, true);
      document.addEventListener('click', function(event) {
        const button = event.target.closest('button');
        if (!button || button.disabled) return;
        if (button.dataset.paymentConsent === 'yes') { openPaypalWindow(); return; }
        if (button.id !== 'chat-send') return;
        const status = document.getElementById('chat-status');
        const input = document.getElementById('chat-input');
        if (!status || !input || !status.textContent.includes('Waiting for payment confirmation.')) return;
        const value = input.value.normalize('NFKC').trim().toLowerCase().replace(/[.!！。\\s]+$/g, '');
        if (['yes','y','agree','i agree','yes please','yes, approve','confirm','confirm payment','同意','同意支付','确认','确认支付','是','可以','好','好的'].includes(value)) openPaypalWindow();
      }, true);
      document.addEventListener('DOMContentLoaded', function() {
        function attachChatScroll() {
          const node = document.getElementById('chat-messages');
          if (!node) return false;
          let previous = '';
          function scrollNewMessage() {
            const last = node.lastElementChild;
            const signature = node.childElementCount + ':' + (last ? last.textContent : '');
            if (signature !== previous) { previous = signature; node.scrollTop = node.scrollHeight; }
          }
          new MutationObserver(scrollNewMessage).observe(node, {childList:true, characterData:true, subtree:true});
          scrollNewMessage(); return true;
        }
        if (!attachChatScroll()) {
          const chatObserver = new MutationObserver(function() { if (attachChatScroll()) chatObserver.disconnect(); });
          chatObserver.observe(document.body, {childList:true, subtree:true});
        }
        function attach() {
          const node = document.getElementById('chat-paypal-url');
          if (!node) return false;
          function navigate() {
            const url = (node.textContent || '').trim();
            if (!url) return;
            try {
              const target = new URL(url);
              if (target.protocol !== 'https:' || !['sandbox.paypal.com', 'www.sandbox.paypal.com'].includes(target.hostname)) return;
              if (!paypalWindow || paypalWindow.closed || sessionStorage.getItem('mattervis_last_sandbox_url') === target.href) return;
              sessionStorage.setItem('mattervis_last_sandbox_url', target.href);
              paypalWindow.location.assign(target.href);
              paypalWindow.focus();
            } catch (_) {}
          }
          new MutationObserver(navigate).observe(node, {childList:true, characterData:true, subtree:true});
          navigate(); return true;
        }
        if (!attach()) {
          const observer = new MutationObserver(function() { if (attach()) observer.disconnect(); });
          observer.observe(document.body, {childList:true, subtree:true});
        }
      });
    </script>"""
    app.index_string = app.index_string.replace("</head>", styles + script + "</head>", 1)
    app.server.secret_key = secret
    app.server.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                             SESSION_COOKIE_NAME=os.getenv("MATTERVIS_SESSION_COOKIE_NAME", "mattervis_mcp_session"))
    app.chat_extension = extension
    return app


def main():
    default_example = ROOT / "contest" / "static" / "disorder-example.cif"
    app = create_native_app(data_root=os.getenv("MATTERVIS_DATA_DIR") or None,
                            input_path=os.getenv("MATTERVIS_INPUT") or str(default_example))
    try:
        app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "50001")), debug=False, threaded=True)
    finally:
        app.close_extensions()
        app.crystal_backend.close()


if __name__ == "__main__":
    main()
