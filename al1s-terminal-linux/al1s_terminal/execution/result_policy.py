"""Final result policy, after all screenshot capture/staging has completed."""

from copy import deepcopy
from typing import Any

from al1s_terminal.execution.maa_runtime import MaaExecutionOutcome

RETRYABLE_SCRIPT_ERRORS = frozenset(
    {
        "maa_pipeline_failed",
        "maa_click_repeat_timeout",
        "maa_repeated_click_failed",
        "maa_independent_rule_limit",
        "maa_independent_rule_failed",
        "maa_independent_rule_timeout",
        "maa_step_budget_failed",
        "maa_step_execution_stalled",
        "post_assertion_failed",
        "failure_retry_limit_exceeded",
        "failure_retry_process_failed",
        "match_loop_limit_exceeded",
        "course_schedule_failed",
    }
)


def final_diagnostic(outcome: MaaExecutionOutcome) -> dict[str, Any]:
    diagnostic = deepcopy(outcome.diagnostic)
    if outcome.passed:
        return diagnostic
    modules = diagnostic.get("modules")
    details = (
        [
            item["result"]
            for item in modules
            if isinstance(item, dict)
            and isinstance(item.get("result"), dict)
            and item["result"].get("success") is not True
        ]
        if isinstance(modules, list)
        else [diagnostic]
    )
    if not details and isinstance(modules, list):
        detail = {"success": False, "error": outcome.error_code}
        modules.append({"result": detail})
        details = [detail]
    for detail in details:
        # An artifact reference means an upload may still be pending. Never claim
        # it was downloaded or did not exist; the platform reconciles its state.
        if not detail.get("failure_screenshot"):
            detail.setdefault("failure_screenshot_error", "capture_not_generated")
    return diagnostic
