import base64
import tempfile
import unittest
from pathlib import Path

from agent.maa_pipeline import MaaPipelineCompiler


PNG_A = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\ncondition").decode()
PNG_B = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nclick").decode()
PNG_C = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\nassertion").decode()


class MaaPipelineCompilerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.compiler = MaaPipelineCompiler(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_wait_click_uses_native_template_match_and_separate_click_image(self):
        compiled = self.compiler.compile({
            "version": 2,
            "steps": [
                {"action": "start"},
                {
                    "action": "wait_click",
                    "template_base64": PNG_A,
                    "click_mode": "image",
                    "click_template_base64": PNG_B,
                    "click_count": 3,
                    "click_interval_ms": 140,
                },
            ],
        })

        condition_name, click_name = compiled.step_nodes[1]
        condition = compiled.pipeline[condition_name]
        click = compiled.pipeline[click_name]
        self.assertEqual(condition["recognition"], "TemplateMatch")
        self.assertEqual(condition["action"], "DoNothing")
        self.assertEqual(condition["next"], [click_name])
        self.assertEqual(click["recognition"], "TemplateMatch")
        self.assertEqual(click["action"], "Click")
        self.assertTrue(click["target"])
        self.assertEqual(click["repeat"], 3)
        self.assertEqual(click["repeat_delay"], 140)
        self.assertEqual(compiled.step_exits[1], [click_name])
        self.assertNotEqual(condition["template"], click["template"])
        self.assertTrue((compiled.image_dir / condition["template"]).is_file())
        self.assertTrue((compiled.image_dir / click["template"]).is_file())

    def test_match_offset_click_rechecks_until_all_templates_are_gone(self):
        compiled = self.compiler.compile({
            "version": 2,
            "steps": [
                {
                    "action": "wait_click",
                    "template_base64": PNG_A,
                    "template_rect": {"x": 100, "y": 200, "width": 20, "height": 10},
                    "threshold": 0.8,
                    "click_mode": "match_offset",
                    "match_order": "Vertical",
                    "match_index": 2,
                    "match_anchor": "bottom_right",
                    "match_offset_x": 3,
                    "match_offset_y": -2,
                    "match_max_clicks": 2,
                    "wait_after_click_seconds": 0.6,
                },
                {"action": "home"},
            ],
        })

        click, overflow, done = compiled.step_nodes[0]
        next_step = compiled.step_nodes[1][0]
        self.assertEqual(
            compiled.pipeline[compiled.entry]["next"][:2],
            [click, done],
        )
        self.assertEqual(compiled.pipeline[click]["recognition"], "Custom")
        self.assertEqual(compiled.pipeline[click]["action"], "Click")
        self.assertEqual(
            compiled.pipeline[click]["custom_recognition"],
            "MaaProjectMatchOffset",
        )
        recognition_param = compiled.pipeline[click]["custom_recognition_param"]
        self.assertEqual(recognition_param["order_by"], "Vertical")
        self.assertEqual(recognition_param["preferred_index"], 1)
        self.assertEqual(compiled.pipeline[click]["target_offset"], [22, 7, -19, -9])
        self.assertEqual(compiled.pipeline[click]["max_hit"], 2)
        self.assertEqual(compiled.pipeline[click]["next"], [click, overflow, done])
        self.assertEqual(compiled.pipeline[overflow]["action"], "Custom")
        self.assertEqual(
            compiled.pipeline[overflow]["custom_action"],
            "MaaProjectMatchLoopLimit",
        )
        self.assertEqual(compiled.pipeline[done]["next"], [next_step])
        self.assertEqual(compiled.step_exits[0], [done])

    def test_match_offset_click_pipeline_size_is_constant(self):
        compiled = self.compiler.compile({
            "version": 2,
            "steps": [{
                "action": "wait_click",
                "template_base64": PNG_A,
                "template_rect": {"x": 0, "y": 0, "width": 20, "height": 10},
                "click_mode": "match_offset",
                "match_index": 2,
                "match_max_clicks": 200,
            }],
        })

        click, overflow, done = compiled.step_nodes[0]
        self.assertEqual(len(compiled.step_nodes[0]), 3)
        self.assertEqual(compiled.pipeline[click]["max_hit"], 200)
        self.assertEqual(compiled.pipeline[click]["next"], [click, overflow, done])

    def test_match_offset_click_with_first_result_uses_zero_based_preference(self):
        compiled = self.compiler.compile({
            "version": 2,
            "steps": [{
                "action": "wait_click",
                "template_base64": PNG_A,
                "template_rect": {"x": 0, "y": 0, "width": 20, "height": 10},
                "click_mode": "match_offset",
                "match_index": 1,
                "match_max_clicks": 1,
            }],
        })

        click, overflow, done = compiled.step_nodes[0]
        self.assertEqual(compiled.pipeline[click]["recognition"], "Custom")
        self.assertEqual(
            compiled.pipeline[click]["custom_recognition_param"]["preferred_index"],
            0,
        )
        self.assertEqual(compiled.pipeline[click]["max_hit"], 1)
        self.assertEqual(compiled.pipeline[click]["next"], [click, overflow, done])
        self.assertEqual(compiled.pipeline[overflow]["action"], "Custom")

    def test_match_offset_click_rejects_invalid_anchor(self):
        with self.assertRaisesRegex(ValueError, "anchor"):
            self.compiler.compile({
                "version": 2,
                "steps": [{
                    "action": "wait_click",
                    "template_base64": PNG_A,
                    "template_rect": {"x": 0, "y": 0, "width": 20, "height": 10},
                    "click_mode": "match_offset",
                    "match_anchor": "outside",
                }],
            })

    def test_global_popup_is_a_native_jump_back_candidate(self):
        compiled = self.compiler.compile({
            "version": 2,
            "global_popups": [{
                "name": "announcement",
                "template_base64": PNG_A,
                "click_mode": "match_center",
            }],
            "steps": [{"action": "wait", "seconds": 1}],
        })

        root_next = compiled.pipeline[compiled.entry]["next"]
        self.assertIsInstance(root_next[0], dict)
        self.assertTrue(root_next[0]["jump_back"])
        popup = compiled.pipeline[root_next[0]["name"]]
        self.assertEqual(popup["recognition"], "TemplateMatch")
        self.assertEqual(popup["action"], "Click")
        self.assertTrue(popup["target"])

    def test_global_popup_is_only_attached_to_selected_steps(self):
        compiled = self.compiler.compile({
            "version": 2,
            "global_popups": [{
                "name": "announcement",
                "template_base64": PNG_A,
                "click_mode": "match_center",
                "step_indexes": [1, 2, 3],
            }],
            "steps": [
                {"action": "wait", "seconds": 0.1},
                {"action": "wait", "seconds": 0.1},
                {"action": "wait", "seconds": 0.1},
                {"action": "wait", "seconds": 0.1},
            ],
        })

        popup_candidate = compiled.pipeline[compiled.entry]["next"][0]
        self.assertIsInstance(popup_candidate, dict)
        popup_name = popup_candidate["name"]
        for step_index in (0, 1):
            exit_name = compiled.step_exits[step_index][0]
            self.assertEqual(compiled.pipeline[exit_name]["next"][0]["name"], popup_name)

        third_exit = compiled.step_exits[2][0]
        fourth_entry = compiled.step_nodes[3][0]
        self.assertEqual(compiled.pipeline[third_exit]["next"][0], fourth_entry)
        self.assertNotIn(
            popup_name,
            [
                item.get("name") if isinstance(item, dict) else item
                for item in compiled.pipeline[third_exit]["next"]
            ],
        )
        fourth_exit = compiled.step_exits[3][0]
        self.assertFalse(any(
            isinstance(item, dict) and item.get("name") == popup_name
            for item in compiled.pipeline[fourth_exit]["next"]
        ))

    def test_global_popup_rejects_an_out_of_range_step(self):
        with self.assertRaisesRegex(ValueError, "out of range"):
            self.compiler.compile({
                "version": 2,
                "global_popups": [{
                    "template_base64": PNG_A,
                    "click_mode": "match_center",
                    "step_indexes": [2],
                }],
                "steps": [{"action": "wait", "seconds": 0.1}],
            })

    def test_numeric_skip_uses_maa_custom_recognition_before_actual_step(self):
        compiled = self.compiler.compile({
            "version": 2,
            "steps": [{
                "action": "home",
                "skip_condition": {
                    "enabled": True,
                    "operator": "gt",
                    "value": 10,
                    "region": {"x": 1, "y": 2, "width": 30, "height": 20},
                },
            }],
        })

        self.assertTrue(compiled.requires_ocr)
        root_next = compiled.pipeline[compiled.entry]["next"]
        guard = compiled.pipeline[root_next[0]]
        self.assertEqual(guard["recognition"], "Custom")
        self.assertEqual(guard["custom_recognition"], "MaaProjectNumericCompare")
        self.assertEqual(guard["roi"], [1, 2, 30, 20])
        self.assertIn("SkipIf", root_next[0])
        self.assertIn("Key_3", root_next[1])

    def test_skip_remaining_steps_routes_condition_hit_to_end(self):
        compiled = self.compiler.compile({
            "version": 2,
            "steps": [
                {"action": "home"},
                {
                    "action": "wait",
                    "seconds": 1,
                    "skip_condition": {
                        "enabled": True,
                        "operator": "gt",
                        "value": 10,
                        "region": {"x": 1, "y": 2, "width": 30, "height": 20},
                        "skip_remaining_steps": True,
                    },
                },
                {"action": "back"},
            ],
        })

        guard_name = next(name for name in compiled.pipeline if "SkipIf" in name)
        guard = compiled.pipeline[guard_name]
        self.assertEqual(guard["next"], [next(name for name in compiled.pipeline if "_End" in name)])
        previous_exit = compiled.step_exits[0][0]
        self.assertIn(guard_name, [item.get("name", item) if isinstance(item, dict) else item for item in compiled.pipeline[previous_exit]["next"]])

    def test_image_skip_uses_native_template_match_before_actual_step(self):
        compiled = self.compiler.compile({
            "version": 2,
            "steps": [{
                "action": "home",
                "skip_condition": {
                    "enabled": True,
                    "mode": "image",
                    "preview_base64": PNG_A,
                    "threshold": 0.75,
                    "region": {"x": 10, "y": 20, "width": 80, "height": 40},
                },
            }],
        })

        self.assertFalse(compiled.requires_ocr)
        root_next = compiled.pipeline[compiled.entry]["next"]
        guard = compiled.pipeline[root_next[0]]
        self.assertEqual(guard["recognition"], "TemplateMatch")
        self.assertEqual(guard["action"], "DoNothing")
        self.assertEqual(guard["threshold"], 0.75)
        self.assertNotIn("roi", guard)
        self.assertIn("SkipIfImage", root_next[0])
        self.assertIn("Key_3", root_next[1])
        self.assertTrue((compiled.image_dir / guard["template"]).is_file())

    def test_image_skip_rejects_invalid_threshold(self):
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            self.compiler.compile({
                "version": 2,
                "steps": [{
                    "action": "home",
                    "skip_condition": {
                        "enabled": True,
                        "mode": "image",
                        "preview_base64": PNG_A,
                        "threshold": 1.5,
                    },
                }],
            })

    def test_post_assertion_retries_the_step_before_advancing(self):
        compiled = self.compiler.compile({
            "version": 2,
            "steps": [
                {
                    "action": "wait_click",
                    "template_base64": PNG_A,
                    "click_mode": "image",
                    "click_template_base64": PNG_B,
                    "post_assertion": {
                        "enabled": True,
                        "template_base64": PNG_C,
                        "threshold": 0.9,
                        "timeout_seconds": 2.5,
                        "poll_interval_seconds": 0.2,
                        "max_retries": 3,
                    },
                },
                {"action": "home"},
            ],
        })

        condition_name, click_name, assertion_name, retry_name = compiled.step_nodes[0]
        click = compiled.pipeline[click_name]
        assertion = compiled.pipeline[assertion_name]
        retry = compiled.pipeline[retry_name]
        next_step_name = compiled.step_nodes[1][0]

        self.assertEqual(click["next"], [assertion_name])
        self.assertEqual(click["on_error"], [retry_name])
        self.assertEqual(click["timeout"], 2_500)
        self.assertEqual(click["rate_limit"], 200)
        self.assertEqual(assertion["recognition"], "TemplateMatch")
        self.assertEqual(assertion["action"], "DoNothing")
        self.assertEqual(assertion["threshold"], 0.9)
        self.assertEqual(assertion["next"], [next_step_name])
        self.assertEqual(retry["recognition"], "DirectHit")
        self.assertEqual(retry["max_hit"], 3)
        self.assertEqual(retry["next"], [condition_name])
        self.assertEqual(compiled.step_exits[0], [assertion_name])
        self.assertNotEqual(assertion["template"], click["template"])
        self.assertTrue((compiled.image_dir / assertion["template"]).is_file())

    def test_numeric_skip_bypasses_post_assertion(self):
        compiled = self.compiler.compile({
            "version": 2,
            "steps": [
                {
                    "action": "home",
                    "skip_condition": {
                        "enabled": True,
                        "operator": "gt",
                        "value": 10,
                        "region": {"x": 1, "y": 2, "width": 30, "height": 20},
                    },
                    "post_assertion": {
                        "enabled": True,
                        "template_base64": PNG_C,
                        "max_retries": 2,
                    },
                },
                {"action": "back"},
            ],
        })

        guard_name, action_name, assertion_name, _retry_name = compiled.step_nodes[0]
        next_step_name = compiled.step_nodes[1][0]
        self.assertEqual(compiled.pipeline[guard_name]["next"], [next_step_name])
        self.assertEqual(compiled.pipeline[action_name]["next"], [assertion_name])
        self.assertEqual(compiled.pipeline[assertion_name]["next"], [next_step_name])

    def test_post_assertion_rejects_fractional_retry_count(self):
        with self.assertRaisesRegex(ValueError, "integer between 1 and 20"):
            self.compiler.compile({
                "version": 2,
                "steps": [{
                    "action": "home",
                    "post_assertion": {
                        "enabled": True,
                        "template_base64": PNG_C,
                        "max_retries": 2.5,
                    },
                }],
            })

    def test_failure_retry_runs_resolved_process_script_before_retrying_target(self):
        compiled = self.compiler.compile({
            "version": 2,
            "steps": [
                {"action": "start"},
                {"action": "wait", "seconds": 0.1},
                {
                    "action": "wait_image",
                    "template_base64": PNG_A,
                    "timeout_seconds": 2,
                    "failure_retry": {
                        "enabled": True,
                        "max_retries": 3,
                        "process_script_name": "返回流程.json",
                        "process_script": {
                            "version": 2,
                            "script_type": "module_process",
                            "cleanup_on_finish": False,
                            "steps": [
                                {"action": "back"},
                                {"action": "wait", "seconds": 0.1},
                            ],
                        },
                    },
                },
                {"action": "home"},
            ],
        })

        previous_exit = compiled.step_exits[1][0]
        target_node = next(
            name for name in compiled.step_nodes[2]
            if "_WaitImage" in name
        )
        target_success = compiled.step_exits[2][0]
        retry_entry = next(
            name for name in compiled.step_nodes[2]
            if "_FailureRetryProcess" in name
        )
        recovery_success = next(
            name for name in compiled.step_nodes[2]
            if "_FailureRecoverySucceeded" in name
        )
        exhausted = next(
            name for name in compiled.step_nodes[2]
            if "_FailureRetryLimit" in name
        )
        next_step = next(
            name for name in compiled.step_nodes[3]
            if "_Key_3" in name
        )

        self.assertEqual(compiled.pipeline[previous_exit]["next"], [target_node])
        self.assertEqual(
            compiled.pipeline[previous_exit]["on_error"],
            [retry_entry, exhausted],
        )
        self.assertEqual(compiled.pipeline[target_node]["next"], [target_success])
        self.assertEqual(
            compiled.pipeline[target_node]["on_error"],
            [retry_entry, exhausted],
        )
        self.assertEqual(compiled.pipeline[target_success]["next"], [next_step])
        self.assertEqual(compiled.pipeline[retry_entry]["max_hit"], 3)
        self.assertEqual(
            compiled.pipeline[retry_entry]["custom_action"],
            self.compiler.FAILURE_RETRY_PROCESS_ACTION,
        )
        process_param = compiled.pipeline[retry_entry]["custom_action_param"]
        self.assertEqual(process_param["process_script_name"], "返回流程.json")
        self.assertIn(process_param["entry"], process_param["pipeline"])
        self.assertEqual(compiled.pipeline[retry_entry]["next"], [recovery_success])
        self.assertEqual(compiled.pipeline[recovery_success]["next"], [target_node])
        self.assertEqual(
            compiled.pipeline[recovery_success]["on_error"],
            [retry_entry, exhausted],
        )
        self.assertEqual(
            compiled.pipeline[exhausted]["custom_action"],
            "MaaProjectFailureRetryLimit",
        )

    def test_failure_retry_preserves_internal_error_handler_before_recovery(self):
        compiled = self.compiler.compile({
            "version": 2,
            "steps": [
                {
                    "action": "wait_click",
                    "template_base64": PNG_A,
                    "click_mode": "image",
                    "click_template_base64": PNG_B,
                    "post_assertion": {
                        "enabled": True,
                        "template_base64": PNG_C,
                        "max_retries": 2,
                    },
                    "failure_retry": {
                        "enabled": True,
                        "max_retries": 4,
                        "process_script_name": "恢复.json",
                        "process_script": {
                            "version": 2,
                            "script_type": "module_process",
                            "steps": [{"action": "back"}],
                        },
                    },
                },
            ],
        })

        action_name = next(
            name for name in compiled.step_nodes[0]
            if "_ClickImage" in name
        )
        assertion_retry = next(
            name for name in compiled.step_nodes[0]
            if "_AssertRetry" in name
        )
        recovery_entry = next(
            name for name in compiled.step_nodes[0]
            if "_FailureRetryProcess" in name
        )
        retry_limit = next(
            name for name in compiled.step_nodes[0]
            if "_FailureRetryLimit" in name
        )
        self.assertEqual(
            compiled.pipeline[action_name]["on_error"],
            [assertion_retry, recovery_entry, retry_limit],
        )

    def test_failure_retry_requires_resolved_process_script_and_integer_count(self):
        with self.assertRaisesRegex(ValueError, "process_script_name"):
            self.compiler.compile({
                "version": 2,
                "steps": [{
                    "action": "back",
                    "failure_retry": {"enabled": True, "max_retries": 2},
                }],
            })
        with self.assertRaisesRegex(ValueError, "was not resolved"):
            self.compiler.compile({
                "version": 2,
                "steps": [{
                    "action": "back",
                    "failure_retry": {
                        "enabled": True,
                        "max_retries": 2,
                        "process_script_name": "恢复.json",
                    },
                }],
            })
        with self.assertRaisesRegex(ValueError, "module_process"):
            self.compiler.compile({
                "version": 2,
                "steps": [{
                    "action": "back",
                    "failure_retry": {
                        "enabled": True,
                        "max_retries": 2,
                        "process_script_name": "普通.json",
                        "process_script": {
                            "version": 2,
                            "script_type": "standard",
                            "steps": [{"action": "home"}],
                        },
                    },
                }],
            })
        with self.assertRaisesRegex(ValueError, "between 1 and 20"):
            self.compiler.compile({
                "version": 2,
                "steps": [{
                    "action": "back",
                    "failure_retry": {
                        "enabled": True,
                        "max_retries": 2.5,
                        "process_script_name": "恢复.json",
                        "process_script": {
                            "version": 2,
                            "script_type": "module_process",
                            "steps": [{"action": "home"}],
                        },
                    },
                }],
            })

    def test_smart_swipe_uses_jump_back_instead_of_python_polling(self):
        compiled = self.compiler.compile({
            "version": 2,
            "steps": [{
                "action": "smart_swipe",
                "mode": "until_image",
                "template_base64": PNG_A,
                "swipe": {"x1": 500, "y1": 1700, "x2": 500, "y2": 600},
            }],
        })

        root_next = compiled.pipeline[compiled.entry]["next"]
        self.assertEqual(compiled.pipeline[root_next[0]]["recognition"], "TemplateMatch")
        self.assertIsInstance(root_next[1], dict)
        self.assertTrue(root_next[1]["jump_back"])
        swipe = compiled.pipeline[root_next[1]["name"]]
        self.assertEqual(swipe["action"], "Swipe")
        self.assertEqual(swipe["begin"], [500, 1700])
        self.assertEqual(swipe["end"], [500, 600])

    def test_launch_app_compiles_to_native_stop_and_start_actions(self):
        compiled = self.compiler.compile({
            "version": 2,
            "steps": [{
                "action": "launch_app",
                "package": "com.example.app",
                "activity": ".MainActivity",
            }],
        })

        stop_name, start_name = compiled.step_nodes[0]
        self.assertEqual(compiled.pipeline[stop_name]["action"], "StopApp")
        self.assertEqual(compiled.pipeline[stop_name]["next"], [start_name])
        self.assertEqual(compiled.pipeline[start_name]["action"], "StartApp")
        self.assertEqual(compiled.pipeline[start_name]["package"], "com.example.app/.MainActivity")
        self.assertEqual(compiled.active_packages, ["com.example.app"])

    def test_compiled_pipeline_is_saved_for_debugging(self):
        compiled = self.compiler.compile({"version": 2, "steps": [{"action": "home"}]})
        path = Path(self.tempdir.name) / "maa" / "compiled" / compiled.script_hash / "pipeline" / "compiled.json"
        self.assertTrue(path.is_file())

    def test_screenshot_uses_registered_custom_action(self):
        compiled = self.compiler.compile({"version": 2, "steps": [{"action": "screenshot"}]})
        node = compiled.pipeline[compiled.step_nodes[0][0]]
        self.assertEqual(node["action"], "Custom")
        self.assertEqual(node["custom_action"], "MaaProjectScreenshot")


if __name__ == "__main__":
    unittest.main()
