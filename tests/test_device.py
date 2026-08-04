import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from agent.device import AndroidDevice


class ForegroundAppDetectionTests(unittest.TestCase):
    def test_extracts_resumed_activity_component(self):
        output = """
          mResumedActivity: ActivityRecord{8f31a2 u0 com.example.shop/.MainActivity t42}
          mCurrentFocus=Window{12ab u0 com.android.systemui/ImageWallpaper}
        """

        self.assertEqual(
            AndroidDevice._component_from_dumpsys(output),
            "com.example.shop/.MainActivity",
        )

    def make_device(self, component):
        device = AndroidDevice.__new__(AndroidDevice)
        device._adb = lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="device\n",
            stderr="",
        )
        device._foreground_component = lambda: component
        device._home_package = lambda: "com.miui.home"
        return device

    def test_launcher_prompts_user_to_open_an_app(self):
        result = self.make_device("com.miui.home/.launcher.Launcher").detect_foreground_app()

        self.assertFalse(result["detected"])
        self.assertEqual(result["reason"], "launcher_or_system_ui")
        self.assertIn("打开", result["message"])

    def test_returns_package_and_activity_for_foreground_app(self):
        result = self.make_device("com.example.shop/.MainActivity").detect_foreground_app()

        self.assertTrue(result["detected"])
        self.assertEqual(result["package"], "com.example.shop")
        self.assertEqual(result["activity"], ".MainActivity")
        self.assertEqual(result["component"], "com.example.shop/.MainActivity")


class OrientationTests(unittest.TestCase):
    def test_landscape_disables_auto_rotation_and_rotates_display(self):
        device = AndroidDevice.__new__(AndroidDevice)
        device._shell = Mock(side_effect=["", "", "Physical size: 1080x2400"])

        result = device.set_orientation("landscape")

        self.assertEqual(device._shell.call_args_list, [
            call("settings", "put", "system", "accelerometer_rotation", "0"),
            call("settings", "put", "system", "user_rotation", "1"),
            call("wm", "size", timeout=10),
        ])
        self.assertEqual(result["orientation"], "landscape")


class DeviceSerialSelectionTests(unittest.TestCase):
    @patch("agent.device.subprocess.run")
    def test_auto_selects_the_only_online_device(self, run):
        run.side_effect = [
            SimpleNamespace(
                returncode=0,
                stdout="List of devices attached\nc57bb86b device usb:1-1 product:apollo_global\n",
                stderr="",
            ),
            SimpleNamespace(returncode=0, stdout="device\n", stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ]
        device = AndroidDevice("", ".")

        result = device.state()

        self.assertTrue(result["connected"])
        self.assertEqual(result["serial"], "c57bb86b")
        self.assertEqual(run.call_args_list[1].args[0], ["adb", "-s", "c57bb86b", "get-state"])

    @patch("agent.device.subprocess.run")
    def test_auto_selection_reports_multiple_devices(self, run):
        run.return_value = SimpleNamespace(
            returncode=0,
            stdout="List of devices attached\na device usb:1-1\nb device usb:1-2\n",
            stderr="",
        )
        result = AndroidDevice("", ".").state()

        self.assertFalse(result["connected"])
        self.assertEqual(result["serial"], "default")
        self.assertIn("multiple online", result["warning"])


if __name__ == "__main__":
    unittest.main()
