import re

import pytest

from al1s_terminal.execution.maa_pipeline import MaaPipelineCompiler


@pytest.mark.parametrize("mode", ["match_center", "fixed", "image"])
def test_compiles_literal_text_condition_with_native_region_and_click_mode(tmp_path, mode):
    rule = {
        "recognition_mode": "text",
        "text": "追加(1)+",
        "click_mode": mode,
        "click": {"x": 30, "y": 40},
        "search_region": {"x": 10, "y": 20, "width": 200, "height": 70},
    }
    if mode == "image":
        rule["click_template_base64"] = (
            "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
            "AAAAC0lEQVR42mP8/x8AAwMCAO+aPj8AAAAASUVORK5CYII="
        )
    compiled = MaaPipelineCompiler(tmp_path).compile(
        {"steps": [{"action": "wait", "seconds": 1}], "global_popups": [rule]}
    )
    assert compiled.requires_ocr
    sources = [node for node in compiled.pipeline.values() if node.get("recognition") == "OCR"]
    assert sources
    assert all(
        node["expected"] == [re.escape(rule["text"])] and node["roi"] == [10, 20, 200, 70]
        for node in sources
    )
    if mode == "image":
        assert any(
            node.get("recognition") == "TemplateMatch" for node in compiled.pipeline.values()
        )
    else:
        assert any(
            node.get("action") == "Click"
            and node.get("target") == (True if mode == "match_center" else [30, 40])
            for node in sources
        )
