"""Thin adapter over the public MatterVis Python contracts."""

from __future__ import annotations

from collections import Counter
from dataclasses import fields
from pathlib import Path
import json
import math
import subprocess
import sys
from typing import Any


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if hasattr(value, "tolist"):
        return json_safe(value.tolist())
    if hasattr(value, "item"):
        return json_safe(value.item())
    return str(value)


def _load(path: Path, request: dict | None = None):
    from mat_viewer.agent import load_structure

    request = request or {}
    input_format = request.get("input_format")
    type_map = request.get("type_map")
    frame = int(request.get("frame", 0) or 0)
    return load_structure(path, input_format=input_format, type_map=type_map, frame=frame)


def inspect(path: Path, request: dict | None = None) -> dict:
    source = _load(path, request)
    frame = source.frames[0]
    bundle = frame.bundle
    scene = getattr(bundle, "scene", {}) or {}
    atoms = list(scene.get("selected_atoms") or [])
    symbols = [str(atom.get("elem") or atom.get("symbol") or "?") for atom in atoms]
    cell = scene.get("cell")
    if isinstance(cell, dict):
        cell_payload = json_safe(cell)
    elif cell is not None:
        cell_payload = {
            name: json_safe(getattr(cell, name))
            for name in ("a", "b", "c", "alpha", "beta", "gamma", "volume")
            if hasattr(cell, name)
        }
    else:
        cell_payload = None
    result = {
        "schema": "mattervis.inspect/v1",
        "input_format": str(getattr(source, "input_format", "unknown")),
        "frame": int(getattr(frame, "index", 0)),
        "frame_count": int(getattr(source, "total_frames", getattr(source, "n_frames", 1))),
        "atom_count": len(atoms),
        "elements": dict(sorted(Counter(symbols).items())),
        "name": getattr(bundle, "name", None),
        "title": getattr(bundle, "title", None),
        "cell": cell_payload,
        "periodic": bool(cell_payload),
        "warnings": [],
    }
    return json_safe(result)


def topology(path: Path, request: dict) -> dict:
    source = _load(path, request)
    bundle = source.frames[0].bundle
    from mat_viewer.topology.analysis import analyze_topology

    if request.get("center_index") is None:
        raise ValueError("center_index is required")
    result = analyze_topology(
        bundle,
        int(request["center_index"]),
        cutoff=float(request.get("cutoff", 10.0)),
        ligand_species=request.get("ligand_species"),
        level=str(request.get("level", "molecule")),
        center_species=request.get("center_species"),
        enforce_enclosure=bool(request.get("enforce_enclosure", True)),
    )
    return json_safe({"schema": "mattervis.topology/v1", **result})


def _rgba(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    raw = value.strip().lstrip("#")
    if len(raw) == 6:
        return tuple(int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4)) + (1.0,)
    if len(raw) == 8:
        return tuple(int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4, 6))
    raise ValueError("background must be #RRGGBB, #RRGGBBAA, or an RGBA array")


def _render_specs(request: dict) -> tuple[Any, Any]:
    from mat_viewer.render.contracts import RenderSpec, ViewSpec

    raw = dict(request.get("spec") or request.get("render_spec") or {})
    if "style" in raw and "representation" not in raw:
        raw["representation"] = raw.pop("style")
    if "background" in raw:
        raw["background"] = _rgba(raw["background"])
    allowed = {item.name for item in fields(RenderSpec)}
    render_payload = {key: value for key, value in raw.items() if key in allowed}
    if "backend" not in render_payload:
        default_backend = "plotly" if str(request.get("output_format", "png")).lower() == "html" else "cpu"
        render_payload["backend"] = str(request.get("backend", default_backend))
    view_payload = request.get("view") or {}
    if isinstance(view_payload, str):
        view_payload = {"display": view_payload}
    return RenderSpec(**render_payload), ViewSpec(**view_payload)


def _cli_args(request: dict) -> list[str]:
    """Build an allowlisted CLI invocation for options not represented by v1 specs."""
    options = dict(request.get("cli_options") or {})
    allowed = {
        "backend", "renderer", "view", "style", "shading", "width", "height", "scale",
        "frame", "frame_range", "stride", "fps", "repeat", "type_map", "show_hydrogen",
        "show_cell", "show_labels", "show_axes", "orthogonal", "perspective", "camera_axis",
        "view_direction", "camera_position", "camera_up", "camera_target", "camera_distance",
        "field_of_view", "ortho_scale", "atom_scale", "bond_radius", "bond_scale",
        "sphere_detail", "cylinder_sides", "background", "ortep_probability", "ortep_mode",
        "missing_adp_policy", "aromatic_rings", "isovalue", "isosurface_opacity",
        "isosurface_mode", "periodic_isosurface", "color_by", "color_reduction", "colormap",
        "color_range", "color_center", "color_label", "color_unit", "show_colorbar",
        "atom_group", "bond_group", "polyhedron", "polyhedron_site", "polyhedron_cutoff",
        "property_data", "vector_overlays", "cell_overlays", "json",
    }
    unknown = sorted(set(options) - allowed)
    if unknown:
        raise ValueError(f"unsupported cli_options: {', '.join(unknown)}")
    repeated = {"type_map", "color_by", "atom_group", "bond_group", "polyhedron", "polyhedron_site", "frame_range"}
    flags = []
    for key, value in options.items():
        flag = "--" + key.replace("_", "-")
        if isinstance(value, bool):
            flags.append(flag if value else "--no-" + key.replace("_", "-"))
        elif isinstance(value, (list, tuple)):
            if key in repeated:
                if key == "frame_range":
                    flags.extend([flag, str(value[0]) if len(value) == 1 else ":".join(map(str, value))])
                else:
                    for item in value:
                        flags.extend([flag, str(item)])
            else:
                flags.extend([flag, *map(str, value)])
        elif value is not None:
            flags.extend([flag, str(value)])
    return flags


def render(path: Path, output: Path, request: dict) -> dict:
    if request.get("cli_options"):
        command = [sys.executable, "-m", "mat_viewer", "render", str(path), "-o", str(output)]
        command.extend(_cli_args(request))
        command.extend(["--json"])
        completed = subprocess.run(command, capture_output=True, text=True, timeout=900)
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "MatterVis CLI failed").strip())
        try:
            metadata = json.loads(completed.stdout)
        except json.JSONDecodeError:
            metadata = {"cli_stdout": completed.stdout[-4000:]}
        return {"schema": "mattervis.render/v1", "metadata": json_safe(metadata), "output": str(output)}

    if output.suffix.lower() in {".gif", ".mp4"}:
        raise ValueError("GIF/MP4 require cli_options with an explicit frame range")

    from mat_viewer.agent import render as mv_render

    render_spec, view_spec = _render_specs(request)
    source = _load(path, request)
    backend = str(render_spec.backend)
    result = mv_render(
        source,
        output=output,
        backend=backend,
        view=view_spec,
        render_spec=render_spec,
    )
    return {
        "schema": "mattervis.render/v1",
        "backend": backend,
        "format": output.suffix.lower().lstrip("."),
        "width": int(getattr(result, "width", render_spec.width)),
        "height": int(getattr(result, "height", render_spec.height)),
        "warnings": json_safe(getattr(result, "warnings", ())),
        "metadata": json_safe(getattr(result, "metadata", {})),
        "output": str(output),
    }
