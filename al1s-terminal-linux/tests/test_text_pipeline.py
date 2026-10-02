import pytest

from al1s_terminal.execution.maa_pipeline import MaaPipelineCompiler


@pytest.mark.parametrize(
    "action, expected_action", [("wait_text", "DoNothing"), ("click_text", "Click")]
)
def test_text_pipeline_literal_region_and_action(tmp_path, action, expected_action):
    compiled = MaaPipelineCompiler(tmp_path).compile(
        {
            "steps": [
                {
                    "action": action,
                    "text": "领取[奖励]+",
                    "search_region": {"x": 1, "y": 2, "width": 30, "height": 40},
                }
            ],
        }
    )
    node = compiled.pipeline[compiled.step_nodes[0][0]]
    recognition = compiled.pipeline[node["custom_recognition_param"]["source"]]
    action_node = compiled.pipeline[node["custom_action_param"]["source"]]
    assert compiled.requires_ocr
    assert recognition["recognition"] == "OCR"
    assert recognition["expected"] == [r"领取\[奖励\]\+"]
    assert recognition["roi"] == [1, 2, 30, 40]
    assert action_node["action"] == expected_action
    if action == "click_text":
        assert action_node["target"] is True


@pytest.mark.parametrize("text", ["", "   ", "x" * 201, None])
def test_text_pipeline_rejects_invalid_text(tmp_path, text):
    with pytest.raises(ValueError, match="OCR text"):
        MaaPipelineCompiler(tmp_path).compile({"steps": [{"action": "wait_text", "text": text}]})


def test_native_maa_accepts_compiled_ocr_schema(tmp_path):
    pytest.importorskip("maa")
    from maa.resource import Resource
    from maa.toolkit import Toolkit

    Toolkit.init_option(tmp_path)
    resource = Resource()
    for action in ("wait_text", "click_text"):
        compiled = MaaPipelineCompiler(tmp_path / action).compile(
            {
                "steps": [{"action": action, "text": "领取[奖励]"}],
            }
        )
        assert resource.override_pipeline(compiled.pipeline)


def test_ocr_assertion_preserves_literal_roi_and_retry_path(tmp_path):
    compiled = MaaPipelineCompiler(tmp_path).compile(
        {
            "steps": [
                {
                    "action": "wait",
                    "seconds": 1,
                    "post_assertion": {
                        "enabled": True,
                        "recognition_mode": "text",
                        "text": "开始[游戏]+",
                        "search_region": {"x": 1, "y": 2, "width": 30, "height": 40},
                        "max_retries": 2,
                        "timeout_seconds": 8,
                        "poll_interval_seconds": 0.5,
                    },
                }
            ]
        }
    )
    assert compiled.requires_ocr
    nodes = list(compiled.pipeline.values())
    ocr = next(node for node in nodes if node.get("recognition") == "OCR")
    assert ocr["expected"] == [r"开始\[游戏\]\+"]
    assert ocr["roi"] == [1, 2, 30, 40]
    assert ocr["action"] == "DoNothing"
    assert ocr["attach"]["maa_project_role"] == "post-assertion"
    retries = [
        node for node in nodes if node.get("attach", {}).get("dsl_action") == "post_assertion_retry"
    ]
    assert retries and all(node["attach"]["assertion_max_retries"] == 2 for node in retries)
    assert any(node.get("timeout") == 8000 and node.get("on_error") for node in nodes)


@pytest.mark.parametrize("text", ["", "  ", "x" * 201, None])
def test_ocr_assertion_rejects_invalid_text(tmp_path, text):
    with pytest.raises(ValueError, match="OCR assertion text"):
        MaaPipelineCompiler(tmp_path).compile(
            {
                "steps": [
                    {
                        "action": "wait",
                        "seconds": 1,
                        "post_assertion": {
                            "enabled": True,
                            "recognition_mode": "text",
                            "text": text,
                        },
                    }
                ]
            }
        )
