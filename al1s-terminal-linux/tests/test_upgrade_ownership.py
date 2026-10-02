import pytest

from terminal_deployer.models import DeploymentError
from terminal_deployer.state import DeploymentStateStore


def test_unknown_upgrade_survives_store_reopen_and_cannot_be_replaced(tmp_path):
    state = DeploymentStateStore(tmp_path)
    state.claim_upgrade("first")
    state.write("first", status="recovery_required")
    reopened = DeploymentStateStore(tmp_path)
    assert reopened.unresolved_upgrade() == "first"
    with pytest.raises(DeploymentError, match="previous_upgrade_unresolved"):
        reopened.claim_upgrade("second")
    assert reopened.unresolved_upgrade() == "first"


@pytest.mark.parametrize("status", ["succeeded", "rolled_back", "failed"])
def test_definitively_settled_upgrade_allows_next_claim(tmp_path, status):
    state = DeploymentStateStore(tmp_path)
    state.claim_upgrade("first")
    state.write("first", status=status)
    state.claim_upgrade("second")
    assert state.unresolved_upgrade() == "second"


def test_invalid_owner_fails_closed(tmp_path):
    (tmp_path / ".upgrade-owner.json").write_text('{"deployment_id":"../outside"}')
    with pytest.raises(DeploymentError, match="ownership is unreadable"):
        DeploymentStateStore(tmp_path).claim_upgrade("second")


def test_old_unfinished_state_cannot_be_bypassed_by_first_owner_record(tmp_path):
    state = DeploymentStateStore(tmp_path)
    state.write("old-release", status="candidate_starting")
    assert state.unresolved_upgrade() == "old-release"
    with pytest.raises(DeploymentError, match="previous_upgrade_unresolved"):
        state.claim_upgrade("new-release")


def test_crash_between_claim_record_and_pointer_is_detected(tmp_path, monkeypatch):
    state = DeploymentStateStore(tmp_path)
    state.claim_upgrade("previous")
    state.write("previous", status="succeeded")
    original = state._write_atomic

    def fail_pointer(path, value):
        if path.name == ".upgrade-owner.json":
            raise OSError("simulated crash")
        original(path, value)

    monkeypatch.setattr(state, "_write_atomic", fail_pointer)
    with pytest.raises(OSError):
        state.claim_upgrade("next", boot_id="boot-before-crash")
    reopened = DeploymentStateStore(tmp_path)
    assert reopened.unresolved_upgrade() == "next"
    assert reopened.read("next")["host_boot_id"] == "boot-before-crash"
