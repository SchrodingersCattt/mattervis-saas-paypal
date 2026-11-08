"""MatterVis Figure Studio: inspect, propose, preview and buy a figure."""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, send_file, session

from .integrations import IntegrationError, PayPalSandbox, model_json
from .mattervis_cli import inspect_structure, render_structure

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
DATA = Path(os.getenv("MATTERVIS_PAYPAL_DATA_DIR", str(ROOT / ".local" / "contest"))).resolve()
PRICE = "2.00"
LOCK = threading.RLock()

app = Flask(__name__, template_folder="templates", static_folder="static")
app.secret_key = os.getenv("APP_SECRET_KEY") or secrets.token_hex(32)
app.config.update(MAX_CONTENT_LENGTH=2 * 1024 * 1024, SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
DATA.mkdir(parents=True, exist_ok=True)


@app.errorhandler(IntegrationError)
def integration_error(exc):
    return jsonify({"error": str(exc)}), 503


@app.errorhandler(413)
def too_large(_exc):
    return jsonify({"error": "Please use a CIF smaller than 2 MB."}), 413


def visitor() -> str:
    if "visitor" not in session:
        session["visitor"] = uuid4().hex
    return session["visitor"]


def job_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise ValueError("Invalid project ID.")
    path = DATA / job_id
    if not path.is_dir():
        raise ValueError("Project not found.")
    return path


def read_job(job_id: str) -> tuple[Path, dict]:
    path = job_dir(job_id)
    record = json.loads((path / "job.json").read_text(encoding="utf-8"))
    if record.get("owner") != visitor():
        raise ValueError("Project not found.")
    return path, record


def save_job(path: Path, record: dict) -> None:
    temp = path / "job.tmp"
    temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path / "job.json")


def public_job(job: dict) -> dict:
    return {key: job.get(key) for key in ("id", "filename", "prompt", "plan", "paid", "price", "preview_ready", "capture_id")}


def build_plan(prompt: str, inspection: dict) -> dict:
    # Paths are local implementation details; the model only needs structure metadata.
    safe_inspection = dict(inspection)
    if isinstance(safe_inspection.get("source"), dict):
        safe_inspection["source"] = {k: v for k, v in safe_inspection["source"].items() if k != "path"}
    result = model_json(
        "You are a scientific figure assistant. Return ONLY a JSON object with keys style, "
        "hide_hydrogen, explanation. style must be one of ball_stick, ball, "
        "space_filling, stick, wireframe. hide_hydrogen controls hydrogen visibility. "
        "Atom labels and custom atom colours are not available in this demo; never claim "
        "to have added them. "
        "Choose only rendering presentation; do not invent chemical facts or modify structure. "
        "Explain choices in one short sentence. Respect the user's request where possible.",
        f"User request: {prompt[:2000]}\nMatterVis inspection: {json.dumps(safe_inspection, ensure_ascii=False)[:12000]}",
    )
    style = result.get("style")
    if style not in {"ball_stick", "ball", "space_filling", "stick", "wireframe"}:
        raise IntegrationError("The model proposed an unsupported rendering style. Try again.")
    if not isinstance(result.get("hide_hydrogen"), bool):
        raise IntegrationError("The model returned an invalid figure plan. Try again.")
    explanation = result.get("explanation")
    if not isinstance(explanation, str) or not explanation.strip():
        raise IntegrationError("The model did not explain the figure plan. Try again.")
    if re.search(r"\blabels?\b", prompt, re.IGNORECASE):
        explanation = f"Using {style.replace('_', ' ')}. Atom labels are not available in this preview."
    return {"style": style, "show_labels": False, "hide_hydrogen": result["hide_hydrogen"],
            "explanation": explanation.strip()[:500]}


@app.get("/")
def index():
    visitor()
    return render_template("index.html", paypal_ready=bool(os.getenv("PAYPAL_CLIENT_ID") and os.getenv("PAYPAL_CLIENT_SECRET")))


@app.get("/health")
def health():
    return jsonify({"ok": True, "application": "mattervis-paypal", "paypal_environment": "sandbox"})


@app.post("/api/jobs")
def create_job():
    upload = request.files.get("file")
    prompt = (request.form.get("prompt") or "").strip()
    if not upload or not upload.filename or not upload.filename.lower().endswith(".cif"):
        return jsonify({"error": "Choose a CIF structure file."}), 400
    if not prompt or len(prompt) > 2000:
        return jsonify({"error": "Describe the figure in 1–2000 characters."}), 400
    raw = upload.read()
    if not raw:
        return jsonify({"error": "The CIF file is empty."}), 400
    job_id = uuid4().hex
    path = DATA / job_id
    path.mkdir(mode=0o700)
    source = path / "structure.cif"
    source.write_bytes(raw)
    try:
        inspection = inspect_structure(source)
        plan = build_plan(prompt, inspection["result"])
        rendered = render_structure(source, path / "preview.png", plan)
    except IntegrationError:
        raise
    except (RuntimeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 422
    record = {"id": job_id, "owner": visitor(), "filename": Path(upload.filename).name,
              "prompt": prompt, "plan": plan, "paid": False, "price": PRICE, "preview_ready": True,
              "inspection": inspection, "preview": rendered}
    save_job(path, record)
    session["last_job"] = job_id
    return jsonify(public_job(record)), 201


@app.get("/api/jobs/<job_id>")
def get_job(job_id):
    try:
        _, record = read_job(job_id)
        return jsonify(public_job(record))
    except (ValueError, FileNotFoundError):
        return jsonify({"error": "Project not found."}), 404


@app.get("/api/jobs/<job_id>/preview")
def preview(job_id):
    try:
        path, _ = read_job(job_id)
        return send_file(path / "preview.png", mimetype="image/png", conditional=True)
    except (ValueError, FileNotFoundError):
        return jsonify({"error": "Preview not found."}), 404


@app.post("/api/jobs/<job_id>/checkout")
def checkout(job_id):
    try:
        with LOCK:
            path, record = read_job(job_id)
            if record.get("paid"):
                return jsonify({"error": "This figure is already unlocked."}), 409
            order = PayPalSandbox().create_order(job_id, record["price"], "MatterVis figure export")
            record["order_id"] = order["id"]
            save_job(path, record)
            session["checkout_job"] = job_id
            return jsonify({"approval_url": order["approval_url"]})
    except ValueError:
        return jsonify({"error": "Project not found."}), 404


@app.get("/checkout/return")
def checkout_return():
    job_id = session.get("checkout_job", "")
    try:
        with LOCK:
            path, record = read_job(job_id)
            token = request.args.get("token", "")
            if not token or token != record.get("order_id"):
                raise IntegrationError("The PayPal return does not match the current order.")
            capture = PayPalSandbox().capture_order(token, job_id, record["price"])
            record.update(paid=True, capture_id=capture["capture_id"])
            save_job(path, record)
        return redirect(f"/?job={job_id}&payment=complete")
    except (ValueError, FileNotFoundError, IntegrationError):
        return redirect(f"/?job={job_id}&payment=failed")


@app.get("/checkout/cancel")
def checkout_cancel():
    return redirect(f"/?job={session.get('checkout_job', '')}&payment=cancelled")


@app.get("/api/jobs/<job_id>/download")
def download(job_id):
    try:
        with LOCK:
            path, record = read_job(job_id)
            if not record.get("paid"):
                return jsonify({"error": "Complete the sandbox payment to unlock the high-resolution export."}), 403
            output = path / "figure.png"
            if not output.exists():
                record["export"] = render_structure(path / "structure.cif", output, record["plan"], high_res=True)
                save_job(path, record)
        return send_file(output, mimetype="image/png", as_attachment=True, download_name="mattervis-figure.png")
    except (ValueError, FileNotFoundError):
        return jsonify({"error": "Project not found."}), 404
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 422


def main():
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "50001")), debug=False)


if __name__ == "__main__":
    main()
