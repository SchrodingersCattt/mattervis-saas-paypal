from __future__ import annotations


def test_pinned_mattervis_public_contract_is_importable():
    from mat_viewer.agent import load_structure, prepare_render, render
    from mat_viewer.render.contracts import RenderSpec, ViewSpec

    assert callable(load_structure)
    assert callable(prepare_render)
    assert callable(render)
    assert RenderSpec().backend == "cpu"
    assert ViewSpec().display == "formula_unit"
