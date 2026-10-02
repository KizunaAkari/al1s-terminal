import pytest

from al1s_terminal.execution.rule_event_codes import bind_module_events, rule_event_code


def test_module_identity_and_rule_index_survive_the_existing_two_argument_callback():
    events = []
    bound = bind_module_events(lambda kind, number: events.append((kind, number)), "recovery:abc")
    bound("step_started", 4)
    bound("rule_started", 0)
    bound("rule_succeeded", 0)
    assert events == [
        ("step_started", 4),
        ("rule_started:recovery:abc", 0),
        ("rule_succeeded:recovery:abc", 0),
    ]
    assert rule_event_code(*events[0]) is None
    assert rule_event_code(*events[1]) == "maa_rule_started:recovery:abc:0"
    bound("rule_started:recovery:nested", 0)
    assert events[-1] == ("rule_started:recovery:nested", 0)
    assert rule_event_code(*events[-1]) == "maa_rule_started:recovery:nested:0"


@pytest.mark.parametrize("identity", ["", "非ASCII", "x" * 100])
def test_invalid_rule_identity_is_not_written_as_unbounded_or_user_text(identity):
    with pytest.raises(ValueError):
        rule_event_code("rule_started:" + identity, 0)
