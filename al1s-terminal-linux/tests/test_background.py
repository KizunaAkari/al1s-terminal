from threading import Event

import pytest

from al1s_terminal.app.background import SingleFlight


def test_slow_transfer_does_not_block_or_queue_duplicate_work() -> None:
    started, release = Event(), Event()
    calls = []

    def transfer():
        calls.append(1)
        started.set()
        assert release.wait(3)

    worker = SingleFlight("test-transfer")
    try:
        worker.poll(transfer)
        assert started.wait(1)
        for _ in range(20):
            worker.poll(transfer)
        assert calls == [1]
    finally:
        release.set()
        worker.close()


def test_transfer_failure_is_reported_to_owner() -> None:
    worker = SingleFlight("test-error")

    def fail():
        raise RuntimeError("failed")

    try:
        worker.poll(fail)
        with pytest.raises(RuntimeError, match="failed"):
            worker._future.result(timeout=1)
        with pytest.raises(RuntimeError, match="failed"):
            worker.poll(fail)
    finally:
        worker.close()
