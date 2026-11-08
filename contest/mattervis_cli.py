"""Bounded calls to the public MatterVis CLI."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


def _executable() -> str:
    name = "mat-vis.exe" if sys.platform == "win32" else "mat-vis"
    adjacent = Path(sys.executable).parent / name
    found = str(adjacent) if adjacent.is_file() else shutil.which("mat-vis")
    if not found:
        raise RuntimeError("MatterVis is not installed in this environment.")
    return found


def _run(args: list[str], timeout: int = 180) -> tuple[dict, list[str]]:
    command = [_executable(), *args, "--json"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("MatterVis could not finish this operation.") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "Unknown MatterVis error").strip()[-1200:]
        raise RuntimeError(f"MatterVis could not process this structure: {detail}")
    try:
        payload = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError("MatterVis returned an invalid result.") from exc
    return payload, command


def inspect_structure(source: Path) -> dict:
    payload, command = _run(["inspect", str(source)])
    return {"result": payload, "command": command}


def render_structure(source: Path, output: Path, plan: dict, *, high_res: bool = False) -> dict:
    style = plan.get("style", "ball_stick")
    if style not in {"ball_stick", "ball", "space_filling", "stick", "wireframe"}:
        raise ValueError("Unsupported style.")
    width, height = (1800, 1440) if high_res else (720, 576)
    args = ["render", str(source), "-o", str(output), "--backend", "cpu", "--orthogonal",
            "--background", "#FFFFFF", "--style", style, "--width", str(width),
            "--height", str(height), "--scale", "1"]
    if plan.get("show_labels"):
        args.append("--show-labels")
    if plan.get("hide_hydrogen"):
        args.append("--no-hydrogen")
    payload, command = _run(args, timeout=300)
    return {"result": payload, "command": command, "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}
