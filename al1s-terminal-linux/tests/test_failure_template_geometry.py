from al1s_terminal.execution.failure_template_selection import failure_template
from al1s_terminal.execution.maa_diagnostics import MaaDiagnostics
from al1s_terminal.execution.recognition_diagnostics import RecognitionDiagnostics


def test_recognize_execute_rechecks_the_failure_png_without_changing_live_result(monkeypatch):
    received = []
    def match(screen, template, threshold, region):
        received.append((screen, template, threshold, region))
        return {"actual_score": .724205, "configured_threshold": threshold,
                "best_box": {"x": 2100, "y": 110, "width": 99, "height": 84},
                "screen_size": {"width": 2400, "height": 1080}}
    monkeypatch.setattr(MaaDiagnostics, "_template_match_evidence", staticmethod(match))
    failure = {"failed_step": {"index": 0}, "recognition_failure": {
        "step_index": 0, "stage": "recognition", "best_score": .7},
        "failure_diagnosis": {"stage": "action"}, "failure_screenshot": {"data_base64": "original"}}
    script = {"steps": [{"action": "recognize_execute", "recognition_mode": "image",
                         "template_base64": "saved-template", "threshold": .85}]}
    MaaDiagnostics.enrich_failure_diagnosis(failure, script)
    assert received == [("original", "saved-template", .85, None)]
    assert failure["failure_diagnosis"]["recognition"]["best_box"]["x"] == 2100
    assert failure["recognition_failure"]["best_score"] == .7
    assert failure["failure_screenshot"]["data_base64"] == "original"


def test_separate_click_template_is_selected_without_using_the_condition_roi():
    script = {"steps": [{"template_base64": "condition", "click_template_base64": "click",
                         "execution_mode": "image_center", "threshold": .85,
                         "click_search_region": {"x": 1, "y": 2}}]}
    failure = {"recognition_failure": {"step_index": 0, "stage": "click_target"}}
    assert failure_template(failure, script) == ("click", .85, None, "click")
    tracker = RecognitionDiagnostics({"click": {
        "recognition": "TemplateMatch",
        "attach": {"dsl_step_index": 0, "maa_project_role": "step-click"},
    }})
    tracker.observe("Node.Recognition.Failed", {"name": "click"})
    assert tracker.snapshot(failed_index=0)["stage"] == "click_target"
