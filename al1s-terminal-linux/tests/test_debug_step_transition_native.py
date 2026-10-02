"""Production compiler/sink against pinned Maa, with no ADB or physical inputs."""

# Native dependencies are optional in the Windows development environment.
import base64
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    import cv2
    import numpy as np
    from maa.controller import CustomController
except ImportError as exc:
    raise unittest.SkipTest("Pinned Maa native dependencies are unavailable") from exc

from al1s_terminal.execution import screen_guard
from al1s_terminal.execution.maa_adapter import MaaAdapter, MaaExecutionError


def image_data(image):
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return "data:image/png;base64," + base64.b64encode(encoded).decode()


class TransitionController(CustomController):
    def __init__(self, events, *, never_ready=False):
        super().__init__()
        rng = np.random.default_rng(31001)
        self.screens = [rng.integers(0, 255, (64, 96, 3), dtype=np.uint8) for _ in range(4)]
        self.events = events
        self.never_ready = never_ready
        self.launched = False
        self.main_clicks = 0
        self.popup_clicks = 0
        self.wait_started_before_popup = []

    def connect(self):
        return True

    def request_uuid(self):
        return "debug-step-transition-memory-only"

    def get_features(self):
        return 0

    def start_app(self, package):
        self.launched = True
        return True

    def screencap(self):
        if self.main_clicks == 0:
            return self.screens[0].copy()
        if self.popup_clicks < 2:
            screen = self.screens[1].copy()
            # Both notifications share their recognition and click template.
            screen[40:56, 64:80] = self.screens[2][40:56, 64:80]
            return screen
        return self.screens[1 if self.never_ready else 3].copy()

    def click(self, x, y):
        if 24 <= x <= 40 and 12 <= y <= 28:
            self.main_clicks += 1
        elif 64 <= x <= 80 and 40 <= y <= 56:
            self.popup_clicks += 1
            self.wait_started_before_popup.append(("step_started", 4) in self.events)
        else:
            raise AssertionError(f"Unexpected memory click: {x}, {y}")
        return True

    def unsupported(self, *args):
        raise AssertionError("Physical device operations are forbidden in this test")

    stop_app = swipe = touch_down = touch_move = touch_up = unsupported
    click_key = input_text = key_down = key_up = unsupported


class DebugStepTransitionNativeTests(unittest.TestCase):
    def run_scenario(self, *, popup_mode="image", timeout=False, cancel=False):
        events = []
        controller = TransitionController(events, never_ready=timeout or cancel)
        controller.set_screenshot_use_raw_size(True)
        self.assertTrue(controller.post_connection().wait().succeeded)
        popup = image_data(controller.screens[2][40:56, 64:80])
        script = {
            "target": {"screen_size": {"width": 96, "height": 64}},
            "steps": [
                {"action": "log"},
                {"action": "launch_app", "package": "memory", "force_stop_before_launch": False},
                {"action": "wait_click", "click_mode": "match_center", "threshold": 0.999,
                 "template_base64": image_data(controller.screens[0][12:28, 24:40]),
                 "wait_after_execution_seconds": 0.05},
                {"action": "wait_image", "threshold": 0.999, "poll_interval_seconds": 0.05,
                 "template_base64": image_data(controller.screens[3][12:28, 24:40]),
                 "timeout_seconds": 0.5 if timeout else 5},
            ],
            "global_popups": [{
                "template_base64": popup, "click_template_base64": popup,
                "threshold": 0.999, "click_threshold": 0.999,
                "click_mode": popup_mode, "step_indexes": [4], "wait_after_click_seconds": 0,
            }],
        }
        wait_guarded = screen_guard.wait_guarded

        def controlled_wait(tasker, job, failures, watchdog=None):
            timer = threading.Timer(0.6, lambda: tasker.post_stop().wait()) if cancel else None
            if timer:
                timer.start()
            try:
                return wait_guarded(tasker, job, failures, watchdog)
            finally:
                if timer:
                    timer.join()

        # Maa's process-wide logger retains its current file handle on Windows.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            adapter = MaaAdapter(SimpleNamespace(
                workdir=Path(folder), serial="memory", screenshot=controller.screencap,
            ))
            adapter.controller = controller
            with patch.object(screen_guard, "wait_guarded", controlled_wait):
                try:
                    adapter.run(
                        script, {}, on_step_event=lambda kind, step: events.append((kind, step))
                    )
                    succeeded = True
                except MaaExecutionError:
                    succeeded = False
        self.assertEqual(controller.main_clicks, 1, events)
        self.assertEqual(controller.popup_clicks, 2, events)
        self.assertEqual(controller.wait_started_before_popup, [True, True], events)
        expected = [
            ("step_started", 1), ("step_succeeded", 1),
            ("step_started", 2), ("step_succeeded", 2),
            ("step_started", 3), ("step_succeeded", 3), ("step_started", 4),
            ("rule_started", 0), ("rule_succeeded", 0),
            ("rule_started", 0), ("rule_succeeded", 0),
        ]
        self.assertEqual(events[:len(expected)], expected)
        if timeout or cancel:
            if timeout:
                self.assertFalse(succeeded)
            # Native post_stop may return a successful job; the owning worker
            # reports cancellation separately. It must never complete the step.
            self.assertNotIn(("step_succeeded", 4), events)
        else:
            self.assertTrue(succeeded)
            self.assertEqual(events, [*expected, ("step_succeeded", 4)])

    def test_two_image_popups_resume_step_four_without_repeating_step_three(self):
        self.run_scenario()

    def test_two_direct_popups_resume_step_four_without_repeating_step_three(self):
        self.run_scenario(popup_mode="match_center")

    def test_wait_timeout_does_not_complete_or_restart_step_three(self):
        self.run_scenario(timeout=True)

    def test_cancel_does_not_complete_waiting_step(self):
        self.run_scenario(cancel=True)


if __name__ == "__main__":
    unittest.main()
