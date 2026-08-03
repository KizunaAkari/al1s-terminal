import json
import unittest

from agent.executor import ScriptExecutor
from agent.maa import MaaExecutionError


class FakeDevice:
    def __init__(self):
        self.events: list[str] = []
        self.closed_apps: list[str] = []
        self.homes = 0
        self.foreground_package = ""
        self.connected = True
        self.serial = "phone-1"
        self.close_error = ""
        self.home_error = ""

    def state(self):
        return {
            "connected": self.connected,
            "serial": self.serial,
            "screen_on": True,
        }

    def screenshot(self, include_data=True):
        self.events.append("screenshot")
        return {"path": "screen.png"}

    def home(self):
        self.events.append("home")
        if self.home_error:
            raise RuntimeError(self.home_error)
        self.homes += 1
        return {"accepted": True}

    def close_app(self, package):
        self.events.append(f"close:{package}")
        if self.close_error:
            raise RuntimeError(self.close_error)
        self.closed_apps.append(package)
        return {"package": package, "accepted": True}

    def detect_foreground_app(self):
        return {
            "detected": bool(self.foreground_package),
            "package": self.foreground_package,
            "activity": ".MainActivity",
        }


class FakeMaa:
    def __init__(self, result=None, error=None):
        self.result = result or {
            "success": True,
            "backend": "maafw",
            "steps": [],
        }
        self.error = error
        self.calls = []

    def run(self, script, params):
        self.calls.append((script, params))
        if self.error:
            raise self.error
        return dict(self.result)


class ScriptExecutorTests(unittest.TestCase):
    def make_executor(self, *, result=None, error=None):
        device = FakeDevice()
        executor = ScriptExecutor(device)
        executor.maa = FakeMaa(result=result, error=error)
        return executor, device

    def test_all_scripts_run_through_maa_and_apply_cleanup(self):
        executor, device = self.make_executor()
        result = executor.run(
            json.dumps(
                {
                    "version": 2,
                    "steps": [
                        {"action": "start"},
                        {
                            "action": "launch_app",
                            "package": "com.example.app",
                        },
                    ],
                }
            ),
            {"run_id": "maa-test"},
        )

        self.assertEqual(result["backend"], "maafw")
        self.assertEqual(
            result["cleanup"]["force_stopped_packages"],
            ["com.example.app"],
        )
        self.assertEqual(device.events, ["close:com.example.app", "home"])
        self.assertEqual(executor.maa.calls[0][1], {"run_id": "maa-test"})

    def test_failure_captures_screen_before_cleanup_and_keeps_step_action(self):
        error = MaaExecutionError(
            "pipeline failed",
            {
                "success": False,
                "backend": "maafw",
                "failed_step": {"index": 1, "number": 2},
            },
        )
        executor, device = self.make_executor(error=error)

        with self.assertRaises(MaaExecutionError) as raised:
            executor.run(
                json.dumps(
                    {
                        "version": 2,
                        "steps": [
                            {"action": "start"},
                            {
                                "action": "launch_app",
                                "package": "com.example.app",
                            },
                        ],
                    }
                ),
                {},
            )

        failure = raised.exception.execution_result
        self.assertEqual(failure["failed_step"]["action"], "launch_app")
        self.assertEqual(failure["failure_screenshot"], {"path": "screen.png"})
        self.assertEqual(
            device.events,
            ["screenshot", "close:com.example.app", "home"],
        )

    def test_composition_failure_preserves_phone_while_retry_is_pending(self):
        error = MaaExecutionError(
            "pipeline failed",
            {
                "success": False,
                "backend": "maafw",
                "failed_step": {"index": 2, "number": 3},
            },
        )
        executor, device = self.make_executor(error=error)
        script = {
            "version": 2,
            "script_type": "composition",
            "steps": [
                {"action": "start", "_module_index": 0, "_module_name": "开始.json", "_module_step_index": 0},
                {"action": "launch_app", "package": "com.example.app", "_module_index": 0, "_module_name": "开始.json", "_module_step_index": 1},
                {"action": "wait", "seconds": 0, "_module_index": 1, "_module_name": "过程.json", "_module_step_index": 0},
            ],
        }

        with self.assertRaises(MaaExecutionError) as raised:
            executor.run(
                json.dumps(script, ensure_ascii=False),
                {},
                task_context={"task_kind": "composition", "attempt": 1, "max_retries": 2},
            )

        failure = raised.exception.execution_result
        self.assertEqual(
            failure["failed_step"]["module"],
            {"index": 1, "name": "过程.json", "step_index": 0, "interval": False},
        )
        self.assertEqual(failure["cleanup"]["reason"], "composition_retry_pending")
        self.assertTrue(failure["composition_retry"]["phone_state_preserved"])
        self.assertEqual(device.events, ["screenshot"])

    def test_composition_failure_without_resume_evidence_cleans_before_full_retry(self):
        executor, device = self.make_executor(error=MaaExecutionError("controller unavailable"))

        with self.assertRaises(MaaExecutionError) as raised:
            executor.run(
                json.dumps(
                    {
                        "version": 2,
                        "script_type": "composition",
                        "steps": [
                            {"action": "start", "_module_index": 0, "_module_name": "开始.json", "_module_step_index": 0},
                            {"action": "launch_app", "package": "com.example.app", "_module_index": 0, "_module_name": "开始.json", "_module_step_index": 1},
                        ],
                    },
                    ensure_ascii=False,
                ),
                {},
                task_context={"task_kind": "composition", "attempt": 1, "max_retries": 2},
            )

        failure = raised.exception.execution_result
        self.assertTrue(failure["composition_retry"]["retry_pending"])
        self.assertFalse(failure["composition_retry"]["resume_available"])
        self.assertFalse(failure["composition_retry"]["phone_state_preserved"])
        self.assertEqual(device.events, ["screenshot", "close:com.example.app", "home"])

    def test_final_composition_failure_still_cleans_phone(self):
        error = MaaExecutionError(
            "pipeline failed",
            {
                "success": False,
                "backend": "maafw",
                "failed_step": {"index": 1, "number": 2},
            },
        )
        executor, device = self.make_executor(error=error)

        with self.assertRaises(MaaExecutionError) as raised:
            executor.run(
                json.dumps(
                    {
                        "version": 2,
                        "script_type": "composition",
                        "steps": [
                            {"action": "start", "_module_index": 0, "_module_name": "开始.json", "_module_step_index": 0},
                            {"action": "launch_app", "package": "com.example.app", "_module_index": 0, "_module_name": "开始.json", "_module_step_index": 1},
                        ],
                    },
                    ensure_ascii=False,
                ),
                {},
                task_context={"task_kind": "composition", "attempt": 3, "max_retries": 2},
            )

        failure = raised.exception.execution_result
        self.assertFalse(failure["composition_retry"]["retry_pending"])
        self.assertTrue(failure["composition_retry"]["resume_available"])
        self.assertFalse(failure["composition_retry"]["phone_state_preserved"])
        self.assertFalse(failure["cleanup"].get("skipped", False))
        self.assertEqual(device.events, ["screenshot", "close:com.example.app", "home"])

    def test_resumed_composition_accepts_process_module_as_first_step(self):
        executor, device = self.make_executor()
        result = executor.run(
            json.dumps(
                {
                    "version": 2,
                    "script_type": "composition",
                    "composition_resume": {"from_module_index": 1, "from_position": 2},
                    "steps": [
                        {"action": "wait", "seconds": 0, "_module_index": 1, "_module_name": "过程.json", "_module_step_index": 0},
                    ],
                },
                ensure_ascii=False,
            ),
            {},
            task_context={"task_kind": "composition", "attempt": 2, "max_retries": 2},
        )

        self.assertTrue(result["success"])
        self.assertEqual(executor.maa.calls[0][0]["steps"][0]["_module_index"], 1)
        self.assertEqual(device.events, ["home"])

    def test_v2_standard_script_requires_start(self):
        executor, _device = self.make_executor()
        with self.assertRaisesRegex(ValueError, "开始"):
            executor.run(
                json.dumps(
                    {
                        "version": 2,
                        "steps": [{"action": "home"}],
                    }
                ),
                {},
            )
        self.assertEqual(executor.maa.calls, [])

    def test_target_phone_is_locked_before_maa_execution(self):
        executor, device = self.make_executor()
        device.serial = "another-phone"

        with self.assertRaisesRegex(RuntimeError, "目标手机不匹配"):
            executor.run(
                json.dumps(
                    {
                        "version": 2,
                        "target": {"device_serial": "phone-1"},
                        "steps": [{"action": "start"}],
                    }
                ),
                {},
            )
        self.assertEqual(executor.maa.calls, [])

    def test_module_script_rules_are_validated_before_compile(self):
        executor, _device = self.make_executor()
        with self.assertRaisesRegex(ValueError, "打开应用"):
            executor.run(
                json.dumps(
                    {
                        "version": 2,
                        "script_type": "module_start",
                        "steps": [{"action": "start"}],
                    }
                ),
                {},
            )
        with self.assertRaisesRegex(ValueError, "过程脚本"):
            executor.run(
                json.dumps(
                    {
                        "version": 2,
                        "script_type": "module_process",
                        "steps": [
                            {
                                "action": "launch_app",
                                "package": "com.example.app",
                            }
                        ],
                    }
                ),
                {},
            )

    def test_single_step_preview_does_not_cleanup_phone_state(self):
        executor, device = self.make_executor()
        result = executor.run(
            json.dumps(
                {
                    "version": 2,
                    "execution_mode": "single_step",
                    "steps": [{"action": "home"}],
                }
            ),
            {},
        )

        self.assertEqual(result["cleanup"]["reason"], "single_step_preview")
        self.assertEqual(device.events, [])

    def test_quick_test_accepts_partial_flow_and_does_not_capture_or_cleanup(self):
        error = MaaExecutionError(
            "pipeline failed",
            {
                "success": False,
                "backend": "maafw",
                "failed_step": {"index": 0, "number": 1},
            },
        )
        executor, device = self.make_executor(error=error)

        with self.assertRaises(MaaExecutionError) as raised:
            executor.run(
                json.dumps(
                    {
                        "version": 2,
                        "script_type": "module_start",
                        "steps": [{"action": "home"}],
                    }
                ),
                {},
                execution_mode="quick_test",
            )

        failure = raised.exception.execution_result
        self.assertNotIn("failure_screenshot", failure)
        self.assertEqual(failure["cleanup"]["reason"], "quick_test_preview")
        self.assertEqual(failure["execution_mode"], "quick_test")
        self.assertEqual(device.events, [])

    def test_formal_script_cannot_enable_quick_test_policy_from_json(self):
        executor, _device = self.make_executor()
        with self.assertRaises(ValueError):
            executor.run(
                json.dumps(
                    {
                        "version": 2,
                        "execution_mode": "quick_test",
                        "steps": [{"action": "home"}],
                    }
                ),
                {},
            )
        self.assertEqual(executor.maa.calls, [])

    def test_process_module_can_keep_or_cleanup_foreground_app(self):
        executor, device = self.make_executor()
        device.foreground_package = "com.example.app"
        result = executor.run(
            json.dumps(
                {
                    "version": 2,
                    "script_type": "module_process",
                    "cleanup_on_finish": False,
                    "steps": [{"action": "wait", "seconds": 0}],
                }
            ),
            {},
        )
        self.assertEqual(result["cleanup"]["reason"], "module_continues")
        self.assertEqual(device.events, [])

        result = executor.run(
            json.dumps(
                {
                    "version": 2,
                    "script_type": "module_process",
                    "cleanup_on_finish": True,
                    "steps": [{"action": "wait", "seconds": 0}],
                }
            ),
            {},
        )
        self.assertEqual(
            result["cleanup"]["force_stopped_packages"],
            ["com.example.app"],
        )

    def test_process_module_failure_always_cleans_foreground_app(self):
        error = MaaExecutionError("pipeline failed")
        executor, device = self.make_executor(error=error)
        device.foreground_package = "com.example.app"

        with self.assertRaises(MaaExecutionError) as raised:
            executor.run(
                json.dumps(
                    {
                        "version": 2,
                        "script_type": "module_process",
                        "cleanup_on_finish": False,
                        "steps": [{"action": "wait", "seconds": 0}],
                    }
                ),
                {},
            )

        self.assertEqual(device.closed_apps, ["com.example.app"])
        self.assertFalse(
            raised.exception.execution_result["cleanup"].get("skipped", False)
        )

    def test_cleanup_failure_marks_successful_maa_task_as_failed(self):
        executor, device = self.make_executor()
        device.home_error = "home unavailable"

        with self.assertRaisesRegex(RuntimeError, "资源清理失败"):
            executor.run(
                json.dumps(
                    {
                        "version": 2,
                        "steps": [{"action": "start"}],
                    }
                ),
                {},
            )

    def test_invalid_json_is_rejected_before_maa(self):
        executor, _device = self.make_executor()
        with self.assertRaisesRegex(ValueError, "JSON DSL"):
            executor.run("{broken", {})
        self.assertEqual(executor.maa.calls, [])


if __name__ == "__main__":
    unittest.main()
