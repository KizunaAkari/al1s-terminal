import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from al1s_terminal.lineup.resources import match_portraits
from al1s_terminal.lineup.session import InferenceSession

FAKE_SERVER = """
import json,os,time
from pathlib import Path
root=Path(__import__('sys').argv[1])
while True:
 p=root/'request.json'
 if not p.exists(): time.sleep(.01); continue
 request=json.loads(p.read_text());p.unlink()
 mode=request['mode']
 if mode=='crash': os._exit(2)
 if mode=='wait': time.sleep(10)
 value={'id':request['id'] if mode!='stale' else 'old',
        'result':{'pid':os.getpid(),'source':request['image']}}
 (root/'result.tmp').write_text(json.dumps(value));(root/'result.tmp').replace(root/'response.json')
"""


@pytest.fixture
def session(tmp_path, monkeypatch):
    launch = subprocess.Popen

    def fake(args, **kwargs):
        assert Path(
            kwargs["env"]["PYTHONPATH"].split(__import__("os").pathsep)[0], "al1s_terminal"
        ).is_dir()
        return launch([sys.executable, "-c", FAKE_SERVER, args[-2]], **kwargs)

    monkeypatch.setattr("al1s_terminal.lineup.session.subprocess.Popen", fake)
    instance = InferenceSession(tmp_path)
    yield instance
    instance.close()


def run(session, name="one", mode="auto", cancelled=lambda: False, timeout=5):
    return session.execute(Path(name), "auto", mode, timeout, cancelled)


def test_reuses_process_and_isolates_consecutive_results(session):
    first, second = run(session), run(session, "two")
    assert first.passed and second.passed
    assert first.diagnostic["lineup"]["pid"] == second.diagnostic["lineup"]["pid"]
    assert second.diagnostic["lineup"]["source"].endswith("two")
    process = session._process
    session.close()
    assert process.poll() is not None


@pytest.mark.parametrize("mode", ["crash", "stale"])
def test_failed_worker_is_discarded_and_next_request_recovers(session, mode):
    assert run(session, mode=mode).error_code == "lineup_inference_failed"
    assert session._process is None
    assert run(session).passed


@pytest.mark.parametrize("cancel", [True, False])
def test_cancellation_and_timeout_kill_child_and_recover(session, cancel):
    started = time.monotonic()
    result = run(
        session,
        mode="wait",
        timeout=1,
        cancelled=lambda: cancel and time.monotonic() - started > 0.15,
    )
    assert result.error_code == ("execution_cancelled" if cancel else "lineup_timeout")
    assert time.monotonic() - started < 3
    assert session._process is None
    assert run(session).passed


def test_waiting_for_single_worker_is_cancellable_without_killing_other_job(session):
    assert session._lock.acquire()
    try:
        assert run(session, cancelled=lambda: True).error_code == "execution_cancelled"
    finally:
        session._lock.release()
    assert run(session).passed


def test_expired_idle_worker_is_replaced_before_request(session):
    first = run(session)
    process = session._process
    session._last_response -= 51
    second = run(session)
    assert process.poll() is not None
    assert first.diagnostic["lineup"]["pid"] != second.diagnostic["lineup"]["pid"]


def test_parallel_matches_keep_side_and_slot_order():
    image = np.arange(60, dtype=np.uint8).reshape(2, 10, 3)

    class Matcher:
        def match(self, crop):
            time.sleep((60 - int(crop[0, 0, 0])) / 10000)
            return int(crop[0, 0, 0]), 0.9, 0.2

    teams = {
        "attack": [{"kind": "portrait", "box": [5, 0, 1, 1]}],
        "defense": [
            {"kind": "portrait", "box": [1, 0, 1, 1]},
            {"kind": "text", "box": [0, 0, 1, 1]},
        ],
    }
    assert match_portraits(image, teams, SimpleNamespace(matcher=Matcher())) == {
        ("attack", 0): (15, 0.9, 0.2),
        ("defense", 0): (3, 0.9, 0.2),
    }


def test_npu_letterbox_uses_training_padding_and_restores_source_coordinates():
    from al1s_terminal.lineup.detector import PortraitDetector

    def detect(canvas, params):
        assert canvas.shape == (640, 640, 3)
        assert np.all(canvas[:160] == 114)
        assert np.all(canvas[160:480] == 22)
        return {"detections": [{"box": [64, 192, 128, 64]}]}

    detector = PortraitDetector.__new__(PortraitDetector)
    detector.rknn = SimpleNamespace(detect=detect)
    assert detector.boxes(np.full((100, 200, 3), 22, dtype=np.uint8)) == [[20, 10, 40, 20]]


def test_parent_watch_terminates_when_owner_disappears(monkeypatch):
    from al1s_terminal.lineup import server

    parents = iter([77, 78])
    monkeypatch.setattr(server.os, "getppid", lambda: next(parents))
    monkeypatch.setattr(server.time, "sleep", lambda delay: None)

    def exit_process(code):
        raise SystemExit(code)

    monkeypatch.setattr(server.os, "_exit", exit_process)
    with pytest.raises(SystemExit, match="1"):
        server.watch_parent(77)


def test_server_reuses_models_and_exits_when_idle(tmp_path, monkeypatch):
    import threading

    from al1s_terminal.lineup import server

    monkeypatch.setattr(server, "watch_parent", lambda parent: None)
    clock = [0.0]
    idle_reset = threading.Event()
    cleaning_up = [False]
    resources = []

    def now():
        current = time.monotonic() if cleaning_up[0] else clock[0]
        if len(resources) == 2:
            idle_reset.set()  # Return the captured time before the test advances it.
        return current

    monkeypatch.setattr(
        server, "time", SimpleNamespace(monotonic=now, sleep=time.sleep)
    )
    monkeypatch.setattr(
        server, "recognize", lambda *a, **kw: resources.append(kw["resources"]) or {}
    )
    from al1s_terminal.lineup.session import atomic_json

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(server.serve, tmp_path, tmp_path, 0, 0.3)
        try:
            for request_id in ("first", "second"):
                atomic_json(
                    tmp_path / "request.json",
                    {"id": request_id, "image": "unused", "hint": "auto", "mode": "auto"},
                )
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    response = tmp_path / "response.json"
                    if response.exists() and json.loads(response.read_text())["id"] == request_id:
                        break
                    time.sleep(0.01)
                else:
                    pytest.fail("No worker response")
            assert idle_reset.wait(2), "Worker did not reset idle time after its response"
            clock[0] = 1.0
            future.result(timeout=2)
        finally:
            cleaning_up[0] = True  # A failed assertion must not freeze executor shutdown.
    assert len(resources) == 2 and resources[0] is resources[1]
