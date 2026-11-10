"""The view summary distinguishes displayed atoms from the loaded structure."""

from pathlib import Path

import pytest

from mat_viewer.app.camera_helpers import _structure_summary
from mat_viewer.loader import build_bundle_scene, build_loaded_crystal
from molcrys_kit.analysis import summarize_structure


CAFFEINE = Path(__file__).resolve().parents[1] / "contest" / "static" / "caffeine.cif"


@pytest.fixture(scope="module")
def caffeine_bundle():
    return build_loaded_crystal(name="caffeine", cif_path=str(CAFFEINE), source="upload")


def test_formula_unit_reports_disorder_elsewhere_in_loaded_caffeine(caffeine_bundle):
    scene = caffeine_bundle.scene
    assert scene["draw_atoms"]
    assert not any(atom.get("is_disordered") for atom in scene["draw_atoms"])
    assert summarize_structure(caffeine_bundle.molcrys_analysis.crystal)["disorder"]["has_disorder"]

    summary = _structure_summary(scene)
    assert "No disorder in displayed atoms" in summary
    assert "loaded structure contains disorder at other sites" in summary
    assert "none detected" not in summary.lower()
    assert "may" not in summary.lower()


def test_ordered_structure_does_not_claim_other_sites_are_disordered():
    scene = {
        "draw_atoms": [{"is_disordered": False, "is_minor": False}],
        "bonds": [],
        "source_has_disorder": False,
    }
    summary = _structure_summary(scene)
    assert "No disorder detected" in summary
    assert "other sites" not in summary.lower()
    assert "may" not in summary.lower()


def test_unit_cell_summary_qualifies_displayed_copy_counts(caffeine_bundle):
    scene = build_bundle_scene(caffeine_bundle, display_mode="unit_cell")
    assert any(atom.get("is_disordered") for atom in scene["draw_atoms"])
    assert "Counts include displayed copies." in _structure_summary(scene)


def test_unknown_source_is_not_described_as_ordered():
    scene = {
        "draw_atoms": [{"is_disordered": False, "is_minor": False}],
        "bonds": [],
        "source_has_disorder": None,
    }
    assert "Source structure disorder information unavailable" in _structure_summary(scene)
