from al1s_terminal.execution.maa_pipeline import MaaPipelineCompiler
from al1s_terminal.execution.screen_guard import GUARD


def test_coordinate_actions_keep_native_coordinates_and_guard(tmp_path):
    compiled = MaaPipelineCompiler(tmp_path).compile({
        "target": {"screen_size": {"width": 64, "height": 96}},
        "steps": [{"action": "tap", "x": 10, "y": 20},
                  {"action": "swipe", "x1": 1, "y1": 2, "x2": 30, "y2": 40, "duration_ms": 350}],
    })
    click = compiled.pipeline[compiled.step_nodes[0][0]]
    swipe = compiled.pipeline[compiled.step_nodes[1][0]]
    assert click["action"] == swipe["action"] == "Custom"
    click_action = compiled.pipeline[click["custom_action_param"]["source"]]
    swipe_action = compiled.pipeline[swipe["custom_action_param"]["source"]]
    assert click_action["action"] == "Click" and click_action["target"] == [10, 20]
    assert swipe_action["action"] == "Swipe" and swipe_action["duration"] == 350
    assert swipe_action["begin"] == [1, 2] and swipe_action["end"] == [30, 40]
    assert click_action["custom_recognition"] == swipe_action["custom_recognition"] == GUARD
