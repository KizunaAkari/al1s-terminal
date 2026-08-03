import unittest
from types import SimpleNamespace

from agent.maa import MaaAdapter


class MaaAdapterTests(unittest.TestCase):
    def test_rect_tuple_accepts_binding_list_result(self):
        self.assertEqual(MaaAdapter._rect_tuple([1, 2, 30, 40]), (1, 2, 30, 40))

    def test_rect_tuple_accepts_rect_object(self):
        rect = SimpleNamespace(x=1, y=2, w=30, h=40)
        self.assertEqual(MaaAdapter._rect_tuple(rect), (1, 2, 30, 40))

    def test_failure_retry_limit_uses_custom_action_result(self):
        result = MaaAdapter._failure_retry_limit([
            {"action": "screenshot", "result": {}},
            {
                "action": "failure_retry_limit",
                "result": {
                    "target_step_index": 2,
                    "process_script_name": "恢复.json",
                    "max_retries": 4,
                },
            },
        ])

        self.assertEqual(result, {
            "target_step_index": 2,
            "process_script_name": "恢复.json",
            "max_retries": 4,
        })

    def test_failure_retry_limit_returns_none_when_not_exhausted(self):
        self.assertIsNone(MaaAdapter._failure_retry_limit([
            {"action": "screenshot", "result": {}},
        ]))

    def test_failure_retry_process_failure_uses_latest_failed_run(self):
        result = MaaAdapter._failure_retry_process_failure([
            {
                "action": "failure_retry_process",
                "result": {"target_step_index": 1, "success": True},
            },
            {
                "action": "failure_retry_process",
                "result": {
                    "target_step_index": 2,
                    "process_script_name": "恢复.json",
                    "success": False,
                },
            },
        ])

        self.assertEqual(result["target_step_index"], 2)
        self.assertEqual(result["process_script_name"], "恢复.json")


if __name__ == "__main__":
    unittest.main()
