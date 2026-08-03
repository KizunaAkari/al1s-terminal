import unittest
from unittest.mock import patch

from agent.main import Agent


class FakeRemoteDevice:
    def __init__(self):
        self.calls = []

    def tap(self, x, y):
        self.calls.append(("tap", x, y))
        return {"x": x, "y": y}

    def swipe(self, x1, y1, x2, y2, duration_ms):
        self.calls.append(("swipe", x1, y1, x2, y2, duration_ms))
        return {"from": [x1, y1], "to": [x2, y2]}

    def back(self):
        self.calls.append(("back",))
        return {"accepted": True}

    def home(self):
        self.calls.append(("home",))
        return {"accepted": True}

    def wake(self):
        self.calls.append(("wake",))
        return {"accepted": True}

    def sleep(self):
        self.calls.append(("sleep",))
        return {"accepted": True}

    def set_orientation(self, orientation):
        self.calls.append(("orientation", orientation))
        return {"accepted": True, "orientation": orientation}

    def screenshot(self):
        self.calls.append(("screenshot",))
        return {"mime": "image/png", "data_base64": "frame"}


class RemoteControlTests(unittest.TestCase):
    def setUp(self):
        self.agent = Agent.__new__(Agent)
        self.agent.device = FakeRemoteDevice()

    @patch("agent.main.time.sleep", return_value=None)
    def test_tap_returns_post_action_screen(self, _sleep):
        result = self.agent.execute_control({"action": "tap", "x": 123, "y": 456, "capture_after": True})
        self.assertEqual(self.agent.device.calls, [("tap", 123, 456), ("screenshot",)])
        self.assertEqual(result["control"], {"x": 123, "y": 456})
        self.assertEqual(result["data_base64"], "frame")

    def test_swipe_can_skip_capture(self):
        result = self.agent.execute_control({
            "action": "swipe", "x1": 500, "y1": 1700, "x2": 500, "y2": 600,
            "duration_ms": 420, "capture_after": False,
        })
        self.assertEqual(self.agent.device.calls, [("swipe", 500, 1700, 500, 600, 420)])
        self.assertEqual(result["control"]["to"], [500, 600])

    def test_landscape_orientation_can_skip_lossless_capture(self):
        result = self.agent.execute_control({
            "action": "orientation", "orientation": "landscape", "capture_after": False,
        })
        self.assertEqual(self.agent.device.calls, [("orientation", "landscape")])
        self.assertEqual(result["control"]["orientation"], "landscape")


if __name__ == "__main__":
    unittest.main()
