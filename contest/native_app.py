"""MatterVis native viewer plus a PayPal Sandbox disorder extension."""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from flask import abort, redirect, request, send_file, session
from mat_viewer.app import create_app
from mat_viewer.extensions import Extension

from .disorder_service import DisorderService, validate_options
from .integrations import IntegrationError, PayPalSandbox

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def owner_id():
    if "premium_owner" not in session:
        session["premium_owner"] = secrets.token_hex(24)
    return session["premium_owner"]


def warning_lines(report):
    warnings = []
    labels = {"hard_clash": "Very short contacts", "intermolecular_clash": "Intermolecular contacts",
              "isolated_atoms": "Isolated atoms", "hydrogen_presence": "Hydrogen atoms",
              "formula_consistency": "Formula comparison", "bond_distances": "Bond lengths"}
    for frame in report.get("frames", []):
        for item in frame.get("results", []):
            message = item.get("message", "")
            if not item.get("passed") or item.get("status") == "skipped" or "skipped" in message.lower():
                name = item.get("name", "check")
                warnings.append(f"{labels.get(name, name.replace('_', ' ').capitalize())}: {message}")
    return warnings


class PremiumDisorder(Extension):
    name = "mattervis.disorder_premium"

    def __init__(self, root):
        self.service = DisorderService(root)
        self.open_status = {}

    def close(self):
        self.service.close()

    def build_web_panel(self, context):
        from dash import dcc, html

        return html.Div([
            html.H3("Disorder ensemble"),
            html.P("Prepare a reproducible set of ordered candidates from the current scene. "
                   "Inspect each candidate before drawing scientific conclusions."),
            html.Label("Resolution method"),
            dcc.Dropdown(id="premium-method", clearable=False, value="enumerate", options=[
                {"label": "Enumerate alternatives", "value": "enumerate"},
                {"label": "Optimal (one candidate)", "value": "optimal"},
                {"label": "Random (seed required)", "value": "random"},
            ]),
            html.Label("Maximum candidates", style={"display": "block", "marginTop": "10px"}),
            dcc.Input(id="premium-count", type="number", min=1, max=8, step=1, value=2),
            html.Label("Random seed (for repeatable sampling)", style={"display": "block", "marginTop": "10px"}),
            dcc.Input(id="premium-seed", type="number", min=0, max=4294967295, step=1, value=42),
            dcc.Checklist(id="premium-coupled", options=[{"label": " Couple symmetry-expanded assemblies", "value": "coupled"}], value=[]),
            html.Button("Check current scene", id="premium-prepare", n_clicks=0, style={"marginTop": "12px"}),
            html.Div(id="premium-status", style={"whiteSpace": "pre-wrap", "margin": "12px 0", "fontSize": "0.9em"}),
            html.Button("Pay $2.00 with PayPal Sandbox", id="premium-checkout", n_clicks=0, disabled=True),
            html.Button("Retry paid job", id="premium-retry", n_clicks=0, disabled=True, style={"display": "none"}),
            html.Div(id="premium-checkout-feedback", role="status", style={"marginTop": "6px"}),
            html.Div(html.A("Continue to PayPal Sandbox", id="premium-approval-link", href="", style={"display": "none"}), style={"marginTop": "10px"}),
            html.Div(id="premium-report", style={"marginTop": "12px", "fontSize": "0.9em"}),
            html.Div([
                dcc.Dropdown(id="premium-candidate", placeholder="Select a candidate to inspect"),
                html.Button("Open selected candidate", id="premium-open", n_clicks=0, disabled=True, style={"marginTop": "8px"}),
                html.Div(id="premium-open-status", style={"marginTop": "6px"}),
                html.P(html.A("Download results and provenance", id="premium-download", href="", target="_blank", style={"display": "none"})),
            ], id="premium-results-section", style={"display": "none"}),
            html.P("PayPal Sandbox: no real money is charged.", style={"fontSize": "0.8em"}),
            dcc.Interval(id="premium-poll", interval=2500, n_intervals=0),
            html.Div(id="premium-approval-url", style={"display": "none"}),
        ], id="premium-panel", style={"padding": "18px", "overflowY": "auto", "height": "100%"})

    def register_web(self, app, context):
        from dash import Input, Output, State, ctx, html

        service = self.service

        @app.server.get("/checkout/return")
        def premium_return():
            owner = session.get("premium_owner")
            job_id = session.get("premium_job", "")
            try:
                record = service.load(job_id, owner)
                token = request.args.get("token", "")
                if not token or token != record.get("order_id"):
                    raise IntegrationError("The returned order does not match this job.")
                if not record.get("paid"):
                    capture = PayPalSandbox().capture_order(token, job_id, record["price"])
                    service.mark_paid_and_run(job_id, owner, token, capture["capture_id"])
                return redirect("/?premium_payment=complete")
            except (FileNotFoundError, ValueError, IntegrationError):
                return redirect("/?premium_payment=failed")

        @app.server.get("/checkout/cancel")
        def premium_cancel():
            return redirect("/?premium_payment=cancelled")

        @app.server.get("/premium/jobs/<job_id>/download")
        def premium_download(job_id):
            try:
                path = service.result_path(job_id, session.get("premium_owner"))
                return send_file(path, as_attachment=True, download_name="mattervis-disorder-results.zip")
            except (FileNotFoundError, PermissionError):
                abort(404)

        @app.server.get("/health")
        def premium_health():
            return {"ok": True, "application": "mattervis-native-disorder", "paypal_environment": "sandbox"}

        @app.callback(
            Output("premium-approval-url", "children"), Output("premium-checkout-feedback", "children"),
            Input("premium-checkout", "n_clicks"), prevent_initial_call=True,
        )
        def start_checkout(_clicks):
            # Checkout must have its own callback: a slow PayPal response can
            # otherwise be superseded by the frequent status-poll callback.
            owner = owner_id()
            job_id = session.get("premium_job", "")
            try:
                record = service.load(job_id, owner)
                if record["status"] != "ready" or record.get("paid"):
                    raise ValueError("Check the current scene before checkout.")
                order = PayPalSandbox().create_order(job_id, record["price"], "MatterVis disorder ensemble")
                service.attach_order(job_id, owner, order["id"])
                return order["approval_url"], "Opening PayPal Sandbox…"
            except (ValueError, FileNotFoundError, IntegrationError, RuntimeError) as exc:
                return "", f"Could not open PayPal: {exc}"

        @app.callback(
            Output("premium-status", "children"), Output("premium-checkout", "disabled"),
            Output("premium-retry", "disabled"), Output("premium-retry", "style"), Output("premium-report", "children"),
            Output("premium-candidate", "options"), Output("premium-open", "disabled"),
            Output("premium-download", "href"), Output("premium-download", "style"),
            Output("premium-open-status", "children"), Output("premium-results-section", "style"),
            Input("premium-prepare", "n_clicks"), Input("premium-retry", "n_clicks"), Input("premium-open", "n_clicks"),
            Input("premium-poll", "n_intervals"), Input("premium-candidate", "value"),
            State("premium-method", "value"), State("premium-count", "value"),
            State("premium-seed", "value"), State("premium-coupled", "value"),
            State("scene-tabs", "value"), State("extension-browser-target", "data"),
            prevent_initial_call=False,
        )
        def update(_prepare, _retry, _open, _tick, selected, method, count, seed, coupled, scene_id, browser_target):
            owner = owner_id()
            job_id = session.get("premium_job", "")
            error = None
            try:
                if ctx.triggered_id == "premium-prepare":
                    options = validate_options(method, count, seed, coupled)
                    exported = context.viewer.export_cif(scene_id=scene_id)
                    record = service.create(owner, exported["content"], options,
                                            {"id": exported.get("scene_id"), "structure": exported.get("structure")})
                    session["premium_job"] = job_id = record["id"]
                elif ctx.triggered_id == "premium-retry":
                    service.retry(job_id, owner)
                elif ctx.triggered_id == "premium-open":
                    record = service.load(job_id, owner)
                    names = {item["filename"] for item in record["candidates"]}
                    if selected not in names or not browser_target:
                        raise ValueError("Select a candidate first.")
                    path = service.result_path(job_id, owner, selected)
                    self.open_status[job_id] = "Opening candidate…"

                    def open_candidate():
                        try:
                            scene = context.structures.register(path, label=selected).result(timeout=120)
                            context.structures.focus(scene, target=browser_target).result(timeout=120)
                            self.open_status[job_id] = "Candidate opened in a new MatterVis scene."
                        except Exception:
                            logging.getLogger(__name__).exception("Could not open candidate")
                            self.open_status[job_id] = "Could not open this candidate. See Server log for details."

                    service.pool.submit(open_candidate)
            except (ValueError, FileNotFoundError, IntegrationError, RuntimeError) as exc:
                error = str(exc)
            try:
                record = service.load(job_id, owner)
            except (FileNotFoundError, OSError):
                return (error or "Choose or upload a structure, then check the current scene.", True, True, {"display": "none"}, "", [], True, "", {"display": "none"}, "", {"display": "none"})
            status = record["status"]
            messages = {"checking": "Checking this structure and its disorder…", "ready": "Disorder detected. Review the source checks below, then approve the $2.00 sandbox payment.",
                        "queued": "Payment captured; job queued.", "running": "Generating candidates and checking each structure…",
                        "complete": f"Completed: {len(record['candidates'])} candidate(s). Select one to inspect.",
                        "failed": "The job could not finish: " + (record.get("error") or "unknown error")}
            status_text = error or messages.get(status, status)
            findings = warning_lines(record.get("source_sanity", {}))
            report = []
            if findings:
                report += [html.Strong("Source checks needing attention"), html.Ul([html.Li(line) for line in findings])]
            elif record.get("source_sanity"):
                report.append(html.P("Source checks: no failed or skipped checks reported."))
            for candidate in record.get("candidates", []):
                lines = warning_lines(candidate["sanity"])
                report.append(html.P(f"Candidate {candidate['index'] + 1}: " + ("; ".join(lines) if lines else "No failed checks reported.")))
            if record.get("ai_report"):
                report += [html.Strong("AI evidence summary"), html.P(record["ai_report"]["summary"]), html.P(record["ai_report"]["limitations"])]
            choices = [{"label": f"Candidate {c['index'] + 1}", "value": c["filename"]} for c in record.get("candidates", [])]
            complete = status == "complete" and record.get("paid")
            retryable = status == "failed" and record.get("paid")
            return (status_text, status != "ready" or record.get("paid"), not retryable, {"display": "inline-block" if retryable else "none", "marginLeft": "6px"}, report,
                    choices, not (complete and selected in {x['value'] for x in choices}),
                    f"/premium/jobs/{job_id}/download" if complete else "", {"display": "inline" if complete else "none"},
                    self.open_status.get(job_id, ""), {"display": "block" if complete else "none"})


def create_native_app(*, data_root=None, input_path=None):
    secret = os.getenv("APP_SECRET_KEY", "")
    if not secret:
        raise RuntimeError("Set APP_SECRET_KEY in .env before starting the app.")
    data_root = Path(data_root or ROOT / ".local" / "native")
    data_root.mkdir(parents=True, exist_ok=True)
    extension = PremiumDisorder(data_root / "jobs")
    app = create_app(root_dir=str(data_root), preset_path=str(data_root / "preset.json"),
                     input_path=input_path, extensions=(extension,))
    # The native workbench is light; set an explicit light color scheme so OS
    # dark preferences cannot make browser controls dark on a light canvas.
    app.index_string = app.index_string.replace("<html>", '<html data-mattervis-theme="light">', 1)
    styles = """<style>
      #mv-extension-panels {background:#fff; color:#172b4d; border-left:1px solid #d9e2ec}
      #premium-panel button, #premium-approval-link {font:inherit; border:1px solid #b9c9d9; border-radius:6px; padding:7px 10px; background:#fff; color:#172b4d; cursor:pointer}
      #premium-panel button:hover:not(:disabled) {border-color:#0f766e; color:#0f766e}
      #premium-panel button:disabled {background:#f2f4f7; color:#98a2b3; border-color:#e4e7ec; cursor:not-allowed}
      #premium-checkout:not(:disabled), #premium-approval-link {background:#0f766e; border-color:#0f766e; color:white; font-weight:650; text-decoration:none}
      #premium-panel #premium-checkout:hover:not(:disabled) {background:#0a5b55; color:white}
      #premium-panel input:not([type=checkbox]) {background:white; color:#172b4d}
      #status-banner.status-banner--idle {display:none}
    </style>"""
    # A regular link is retained as a fallback if a browser blocks navigation.
    # Only the extension's own DOM nodes are touched; native controls stay owned
    # by MatterVis. The server has already verified the Sandbox URL host.
    checkout_script = """<script>
      document.addEventListener('click', function(event) {
        const button = event.target.closest('#premium-checkout');
        if (button && !button.disabled) button.textContent = 'Opening PayPal…';
      }, true);
      document.addEventListener('DOMContentLoaded', function() {
        let lastUrl = '';
        function attach() {
          const node = document.getElementById('premium-approval-url');
          if (!node) return false;
          function navigate() {
            const url = (node.textContent || '').trim();
            if (!url || url === lastUrl) return;
            try {
              const target = new URL(url);
              if (target.protocol !== 'https:' || !['sandbox.paypal.com', 'www.sandbox.paypal.com'].includes(target.hostname)) return;
              lastUrl = url;
              const link = document.getElementById('premium-approval-link');
              if (link) { link.href = target.href; link.style.display = 'inline-block'; }
              window.setTimeout(function() { window.location.assign(target.href); }, 100);
            } catch (_) {}
          }
          new MutationObserver(navigate).observe(node, {childList:true, characterData:true, subtree:true});
          navigate();
          return true;
        }
        if (!attach()) {
          const observer = new MutationObserver(function() { if (attach()) observer.disconnect(); });
          observer.observe(document.body, {childList:true, subtree:true});
        }
      });
    </script>"""
    app.index_string = app.index_string.replace("</head>", styles + checkout_script + "</head>", 1)
    app.server.secret_key = secret
    app.server.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
    app.premium_extension = extension
    return app


def main():
    # This is a real, intentionally incomplete synthetic sample, not a rendered
    # placeholder. Users can switch scenes or upload their own structure.
    default_example = ROOT / "contest" / "static" / "disorder-example.cif"
    app = create_native_app(input_path=os.getenv("MATTERVIS_INPUT") or str(default_example))
    try:
        app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "50001")), debug=False, threaded=True)
    finally:
        app.close_extensions()
        app.crystal_backend.close()


if __name__ == "__main__":
    main()
