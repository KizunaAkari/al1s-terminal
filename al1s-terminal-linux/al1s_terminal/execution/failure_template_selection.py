"""Select only the failed step/rule template for diagnostic PNG matching."""
from typing import Any


def failure_template(
    failure: dict[str, Any], script: dict[str, Any],
) -> tuple[Any, float, Any, str] | None:
    raw = failure.get("recognition_failure")
    raw = raw if isinstance(raw, dict) else {}
    failed = failure.get("failed_step")
    failed = failed if isinstance(failed, dict) else {}
    index = raw.get("step_index", failed.get("index"))
    steps = script.get("steps")
    if type(index) is not int or not isinstance(steps, list) or not 0 <= index < len(steps):
        return None
    config = steps[index]
    if not isinstance(config, dict):
        return None
    if "rule_index" in raw:
        rule, rules = raw["rule_index"], script.get("independent_rules")
        if type(rule) is not int or not isinstance(rules, list) or not 0 <= rule < len(rules):
            return None
        config = rules[rule]
        if not isinstance(config, dict):
            return None
    diagnosis = failure.get("failure_diagnosis") or {}
    role = "condition"
    if raw.get("stage") == "post_assertion" or failure.get("error_type") == "PostAssertionFailed":
        config, role = config.get("post_assertion"), "post_assertion"
        if not isinstance(config, dict):
            return None
    elif (
        raw.get("stage") == "click_target" or diagnosis.get("title") == "点击图片识别失败"
    ) and config.get("click_template_base64"):
        region = (
            None if config.get("execution_mode") == "image_center"
            else config.get("click_search_region")
        )
        config = {"template_base64": config.get("click_template_base64"),
                  "threshold": config.get("click_threshold", config.get("threshold", .85)),
                  "search_region": region}
        role = "click"
    if raw.get("algorithm") == "OCR" or config.get("recognition_mode") == "text":
        return None
    template = config.get("template_base64")
    if not isinstance(template, str) or not template:
        return None
    return template, float(config.get("threshold", .85)), config.get("search_region"), role
