import sys
from types import ModuleType

from al1s_terminal.execution.debug_events import install_step_sink


def test_nested_recovery_pipeline_emits_its_own_immutable_module_identity(monkeypatch):
    context = ModuleType("maa.context")
    context.ContextEventSink = type("ContextEventSink", (), {})
    monkeypatch.setitem(sys.modules, "maa", ModuleType("maa"))
    monkeypatch.setitem(sys.modules, "maa.context", context)
    key = "Web_recovery_Global_000_ForStep_000"
    pipeline = {
        "outer": {
            "custom_action_param": {
                "pipeline": {
                    key: {"attach": {"popup_key": key, "definition_key": "recovery:nested"}},
                }
            }
        }
    }
    events = []
    tasker = type("Tasker", (), {"add_context_sink": lambda *_: 1})()
    sink = install_step_sink(
        tasker, {}, {}, lambda kind, index: events.append((kind, index)), pipeline=pipeline
    )
    for phase in ["Starting", "Succeeded"]:
        sink.on_raw_notification(None, "Node.Action." + phase, {"name": key})
    assert events == [("rule_started:recovery:nested", 0), ("rule_succeeded:recovery:nested", 0)]
