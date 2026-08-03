import time
import unittest

from agent.interactive import AdbWebSocketRelay
from agent.main import Agent


class FakeInteractive:
    def __init__(self):
        self.stop_calls = 0

    def stop_all(self):
        self.stop_calls += 1


class FakeExecutor:
    def __init__(self):
        self.automation_state = None

    def run(self, content, params):
        self.automation_state = (content, params)
        return {"ok": True}


class InteractiveRelayTests(unittest.TestCase):
    def make_relay(self, timeout=1200):
        relay = AdbWebSocketRelay(
            "127.0.0.1",
            8766,
            "ws://terminal.test:8766",
            idle_timeout_seconds=timeout,
        )
        relay.available = True
        relay._serving = True
        relay.start = lambda: True
        return relay

    def test_session_has_random_authenticated_url_and_can_be_revoked(self):
        relay = self.make_relay()
        session = relay.create_session("android-001")

        self.assertEqual(session["device_serial"], "android-001")
        self.assertTrue(session["ws_url"].startswith("ws://terminal.test:8766/adb/"))
        self.assertGreaterEqual(len(session["session_token"]), 32)
        self.assertTrue(relay.status()["active"])
        self.assertTrue(relay.stop(session["session_token"]))
        self.assertFalse(relay.status()["active"])

    def test_new_session_revokes_previous_session(self):
        relay = self.make_relay()
        first = relay.create_session("android-001")
        second = relay.create_session("android-001")

        self.assertNotEqual(first["session_token"], second["session_token"])
        self.assertFalse(relay.stop(first["session_token"]))
        self.assertTrue(relay.stop(second["session_token"]))

    def test_disconnected_session_expires(self):
        relay = self.make_relay(timeout=1)
        session = relay.create_session("android-001")
        relay._sessions[session["session_token"]].last_activity = time.monotonic() - 2

        self.assertIsNone(relay._get_session(session["session_token"]))


class DeviceLeaseTests(unittest.TestCase):
    def test_automation_revokes_interactive_session(self):
        agent = Agent.__new__(Agent)
        agent.interactive = FakeInteractive()
        agent.executor = FakeExecutor()
        agent.automation_active = False

        result = agent.run_automation('{"version": 2}', {"sample": True})

        self.assertEqual(result, {"ok": True})
        self.assertEqual(agent.interactive.stop_calls, 1)
        self.assertFalse(agent.automation_active)
        self.assertEqual(agent.executor.automation_state, ('{"version": 2}', {"sample": True}))

    def test_single_step_can_keep_interactive_video_session(self):
        agent = Agent.__new__(Agent)
        agent.interactive = FakeInteractive()
        agent.executor = FakeExecutor()
        agent.automation_active = False

        result = agent.run_automation('{"version": 2}', {}, keep_interactive=True)

        self.assertEqual(result, {"ok": True})
        self.assertEqual(agent.interactive.stop_calls, 0)
        self.assertFalse(agent.automation_active)


if __name__ == "__main__":
    unittest.main()
