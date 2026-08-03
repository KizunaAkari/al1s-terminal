import tempfile
import time
import unittest
import zipfile
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path

from agent.main import Agent


class FakeInteractive:
    def __init__(self):
        self.stop_calls = 0

    def stop_all(self):
        self.stop_calls += 1


class FakeRecordingDevice:
    def __init__(self, recording):
        self.recording = recording

    def stop_recording(self, _session):
        return self.recording


class AgentTaskServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.agent = Agent.__new__(Agent)
        self.agent.state_path = str(Path(self.tempdir.name) / "agent-state.json")
        self.agent.accepting_tasks = True
        self.agent.automation_active = False
        self.agent.started_at = datetime.now(timezone.utc).isoformat()
        self.agent.started_monotonic = time.monotonic()
        self.agent.interactive = FakeInteractive()

    def tearDown(self):
        self.tempdir.cleanup()

    def test_stop_and_start_task_service_are_persisted(self):
        stopped = self.agent.execute({"kind": "agent_stop", "payload": {}})
        self.assertEqual(stopped["state"], "stopped")
        self.assertFalse(stopped["accepting_tasks"])
        self.assertEqual(self.agent.interactive.stop_calls, 1)
        self.assertFalse(self.agent._load_accepting_tasks())

        started = self.agent.execute({"kind": "agent_start", "payload": {}})
        self.assertEqual(started["state"], "running")
        self.assertTrue(started["accepting_tasks"])
        self.assertTrue(self.agent._load_accepting_tasks())

    def test_busy_service_status_is_reported(self):
        self.agent.automation_active = True
        status = self.agent.service_status()
        self.assertEqual(status["state"], "busy")
        self.assertTrue(status["automation_active"])

    def test_multiple_recording_segments_are_uploaded_as_zip_and_removed(self):
        first = Path(self.tempdir.name) / "task-001.mp4"
        second = Path(self.tempdir.name) / "task-002.mp4"
        first.write_bytes(b"0000ftyp" + b"a" * 32)
        second.write_bytes(b"0000ftyp" + b"b" * 32)
        self.agent.device = FakeRecordingDevice({
            "paths": [str(first), str(second)],
            "size_bytes": first.stat().st_size + second.stat().st_size,
            "segment_count": 2,
            "duration_seconds": 240.0,
            "time_limit_seconds": 180,
            "errors": [],
        })
        captured = {}

        def fake_request(method, path, **kwargs):
            captured["method"] = method
            captured["path"] = path
            filename, stream, mime = kwargs["files"]["recording"]
            captured["filename"] = filename
            captured["mime"] = mime
            captured["content"] = stream.read()
            return {"size_bytes": len(captured["content"]), "recording_url": "/recording", "mime": mime}

        self.agent.request = fake_request
        result = self.agent.finish_task_recording("task-id", {"active": True})

        self.assertTrue(result["uploaded"])
        self.assertEqual(result["segment_count"], 2)
        self.assertEqual(captured["mime"], "application/zip")
        with zipfile.ZipFile(BytesIO(captured["content"]), "r") as archive:
            self.assertEqual(archive.namelist(), ["segment-001.mp4", "segment-002.mp4"])
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())


if __name__ == "__main__":
    unittest.main()
