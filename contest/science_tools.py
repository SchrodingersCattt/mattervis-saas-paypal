"""Bounded, evidence-preserving scientific operations for the chat MCP server."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import uuid
from pathlib import Path


def inspect_cif(path: Path, *, source_label: str = "source.cif") -> dict:
    from molcrys_kit.analysis import summarize_structure
    from molcrys_kit.analysis.molecular_identity import ChemicalIdentity
    from molcrys_kit.analysis.sanity_check import sanity_check
    from molcrys_kit.io import read_mol_crystal

    crystal = read_mol_crystal(str(path), resolve_disorder=False, bond_scale=1.0)
    inventory = []
    for index, molecule in enumerate(crystal.molecules):
        identity = ChemicalIdentity.from_molecule(molecule, index, crystal=crystal).to_dict()
        identity["atom_count"] = len(molecule)
        inventory.append(identity)
    summary = summarize_structure(crystal, source=source_label, symprec=0.1)
    checks = sanity_check(crystal).to_dict()
    # Match the public MolCrysKit CLI's single-frame report shape.
    sanity = {"file": source_label, "n_frames": 1, "n_passed": int(checks["passed"]),
              "n_failed": int(not checks["passed"]), "frames": [{"frame": 0, **checks}]}
    return json.loads(json.dumps({"summary": summary, "sanity": sanity, "molecules": inventory,
                                  "info": crystal.summary()}, default=str))


def build_supercell(source: str, scales: tuple[int, int, int], root: Path) -> tuple[Path, dict]:
    if not isinstance(source, str) or not source.strip() or len(source.encode("utf-8")) > 10 * 1024 * 1024:
        raise ValueError("Load a structure of at most 10 MB first.")
    if len(scales) != 3 or any(isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > 4 for value in scales):
        raise ValueError("Each supercell factor must be an integer from 1 to 4.")
    factor = scales[0] * scales[1] * scales[2]
    if factor > 32:
        raise ValueError("The product of the supercell factors must not exceed 32.")
    folder = Path(root) / ("supercell-" + uuid.uuid4().hex)
    folder.mkdir(parents=True, mode=0o700)
    input_path = folder / "source.cif"
    output_path = folder / "supercell.cif"
    input_path.write_text(source, encoding="utf-8", newline="\n")
    before = inspect_cif(input_path)
    if not all(before["summary"].get("pbc", [])):
        raise ValueError("A three-dimensionally periodic structure is needed for this operation.")
    if before["summary"]["n_atoms"] * factor > 2000:
        raise ValueError("The requested supercell exceeds the 2,000-site limit.")
    arguments = ["operate", "supercell", "source.cif", "-o", "supercell.cif", "--scale", *map(str, scales)]
    command = [sys.executable, "-c", "from molcrys_kit.cli import main; main()", *arguments]
    result = subprocess.run(command, cwd=folder, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=180)
    (folder / "operation.stdout.txt").write_text(result.stdout, encoding="utf-8")
    (folder / "operation.stderr.txt").write_text(result.stderr, encoding="utf-8")
    if result.returncode or not output_path.is_file():
        raise RuntimeError("Supercell construction failed. See Server log for details.")
    after = inspect_cif(output_path, source_label=output_path.name)
    old, new = before["summary"], after["summary"]
    if new["n_atoms"] != old["n_atoms"] * factor:
        raise RuntimeError("The generated site count does not match the requested supercell.")
    old_volume, new_volume = old["cell"]["volume_A3"], new["cell"]["volume_A3"]
    if abs(new_volume - old_volume * factor) > max(1e-5, abs(old_volume * factor) * 1e-5):
        raise RuntimeError("The generated cell volume does not match the requested supercell.")
    if old["disorder"]["has_disorder"] and not new["disorder"]["has_disorder"]:
        raise RuntimeError("The generated file lost disorder information; it was not opened.")
    manifest = {"operation": "supercell", "scale": list(scales), "source_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
                "output_sha256": hashlib.sha256(output_path.read_bytes()).hexdigest(),
                "command": ["mck", *arguments], "before": before, "after": after,
                "interpretation": "The structure is a constructed model; checks do not establish physical stability."}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path, manifest
