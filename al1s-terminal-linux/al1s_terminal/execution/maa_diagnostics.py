from __future__ import annotations

# User-facing Chinese diagnostics intentionally use full-width punctuation.
# ruff: noqa: RUF001
import base64
import math
import re
from typing import Any

from al1s_terminal.execution.maa_pipeline import CompiledMaaTask


class MaaDiagnostics:
    """Summarize Maa telemetry and attach evidence to failure results."""

    @classmethod
    def _node_records(cls, compiled: CompiledMaaTask, detail: Any) -> list[dict[str, Any]]:
        if detail is None:
            return []
        records: list[dict[str, Any]] = []
        for node in detail.nodes:
            record: dict[str, Any] = {
                "node_id": node.node_id,
                "name": node.name,
                "completed": bool(node.completed),
            }
            if node.name in compiled.node_steps:
                record["step_index"] = compiled.node_steps[node.name]
            if node.recognition is not None:
                best = node.recognition.best_result
                record["recognition"] = {
                    "algorithm": str(node.recognition.algorithm),
                    "hit": bool(node.recognition.hit),
                    "box": cls._rect_dict(node.recognition.box),
                    "score": getattr(best, "score", None) if best is not None else None,
                }
            if node.action is not None:
                record["action"] = {
                    "type": str(node.action.action),
                    "success": bool(node.action.success),
                }
            records.append(record)
        return records

    @staticmethod
    def _conditional_skip_event(
        compiled: CompiledMaaTask,
        step: dict[str, Any],
        index: int,
        step_count: int,
        collector: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any] | None:
        config = step.get("skip_condition")
        if not isinstance(config, dict) or config.get("enabled") is not True:
            return None
        target_index: int | None = None
        raw_target = config.get("skip_to_step_index")
        if raw_target is not None and not isinstance(raw_target, bool):
            try:
                candidate = int(raw_target)
                if float(raw_target) == candidate and index < candidate - 1 < step_count:
                    target_index = candidate - 1
            except (TypeError, ValueError, OverflowError):
                target_index = None
        skipped_indexes = list(range(index, target_index)) if target_index is not None else [index]
        event = {
            "trigger_step_index": index,
            "trigger_step_number": index + 1,
            "trigger_action": step.get("action"),
            "mode": str(config.get("mode") or "numeric"),
            "operator": config.get("operator"),
            "value": config.get("value"),
            "threshold": config.get("threshold"),
            "scope": "until_step" if target_index is not None else "current",
            "target_step_index": target_index,
            "target_step_number": target_index + 1 if target_index is not None else None,
            "skipped_step_indexes": skipped_indexes,
            "skipped_step_numbers": [item + 1 for item in skipped_indexes],
        }
        if step.get("_module_name") is not None:
            event["module_index"] = int(step.get("_module_index", 0))
            event["module_number"] = int(step.get("_module_index", 0)) + 1
            event["module_name"] = str(step.get("_module_name"))
            event["module_step_index"] = int(step.get("_module_step_index", 0))
            event["module_step_number"] = int(step.get("_module_step_index", 0)) + 1
        captures = [
            item
            for item in collector["conditional_skip_captures"]
            if item.get("node") in compiled.step_nodes.get(index, [])
        ]
        if captures:
            event["capture"] = captures[-1].get("capture")
            if config.get("mode") in {"recognition_failure", "execution_failure"}:
                event["failure_reason"] = captures[-1].get("failure_reason")
                event["failed_node"] = captures[-1].get("failed_node")
        return event

    def _summarize(
        self,
        compiled: CompiledMaaTask,
        script: dict[str, Any],
        detail: Any,
        collector: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        node_records = self._node_records(compiled, detail)

        steps: list[dict[str, Any]] = []
        conditional_skips: list[dict[str, Any]] = []
        script_steps = script.get("steps", [])
        for index, step in enumerate(script_steps):
            matching = [record for record in node_records if record.get("step_index") == index]
            skipped = any(
                "_SkipIf" in record["name"] and record["completed"] for record in matching
            )
            result: dict[str, Any] = {"backend": "maafw", "nodes": matching}
            feedback = [
                item
                for item in collector["feedback"]
                if item["node"] in compiled.step_nodes.get(index, [])
            ]
            if feedback:
                result["feedback"] = feedback[-1]
            custom_actions = [
                item
                for item in collector["custom_actions"]
                if item["node"] in compiled.step_nodes.get(index, [])
            ]
            if custom_actions:
                result["custom_actions"] = custom_actions
            loop_clicks = [
                item
                for item in matching
                if ("_MatchLoopClick_" in item["name"] or "_MatchLoopFallback_" in item["name"])
                and item["completed"]
            ]
            if loop_clicks or step.get("click_mode") == "match_offset":
                result["match_loop"] = {
                    "clicks": len(loop_clicks),
                    "completed": bool(
                        any(
                            "_MatchLoopDone" in item["name"] and item["completed"]
                            for item in matching
                        )
                    ),
                    "max_clicks": int(step.get("match_max_clicks", 50)),
                }
            conditions = [
                item
                for item in collector["numeric_conditions"]
                if item["node"] in compiled.step_nodes.get(index, [])
            ]
            record: dict[str, Any] = {
                "index": index,
                "action": step.get("action"),
                "result": result,
            }
            if skipped:
                record["skipped"] = True
                result["reason"] = (
                    "failure_skip"
                    if step.get("skip_condition", {}).get("mode")
                    in {"recognition_failure", "execution_failure"}
                    else "numeric_condition"
                )
            if conditions:
                record["condition"] = conditions[-1]
            condition_config = step.get("skip_condition")
            if (
                isinstance(condition_config, dict)
                and condition_config.get("enabled") is True
                and condition_config.get("mode") == "image"
            ):
                guard_records = [item for item in matching if "_SkipIfImage" in item["name"]]
                recognition = guard_records[-1].get("recognition") if guard_records else None
                record["condition"] = {
                    "mode": "image",
                    "threshold": float(condition_config.get("threshold", 0.85)),
                    "hit": skipped,
                    "score": (recognition.get("score") if isinstance(recognition, dict) else None),
                }
            if skipped:
                skip_event = self._conditional_skip_event(
                    compiled, step, index, len(script_steps), collector
                )
                if skip_event is not None:
                    conditional_skips.append(skip_event)
                    if skip_event.get("mode") in {"recognition_failure", "execution_failure"}:
                        record["condition"] = {"mode": skip_event["mode"], "hit": True,
                                               "failure_reason": skip_event.get("failure_reason")}
            assertion = step.get("post_assertion")
            if isinstance(assertion, dict) and assertion.get("enabled") is True:
                assertion_hits = [
                    item
                    for item in matching
                    if "_Assert" in item["name"]
                    and "_AssertRetry" not in item["name"]
                    and item["completed"]
                ]
                retries = sum(
                    1 for item in matching if "_AssertRetry" in item["name"] and item["completed"]
                )
                result["post_assertion"] = {
                    "enabled": True,
                    "succeeded": bool(assertion_hits),
                    "retries": retries,
                    "max_retries": int(assertion.get("max_retries", 2)),
                }
            failure_retry = step.get("failure_retry")
            if isinstance(failure_retry, dict) and failure_retry.get("enabled") is True:
                process_runs = [
                    item.get("result")
                    for item in collector["custom_actions"]
                    if (
                        item.get("action") == "failure_retry_process"
                        and str(item.get("node") or "").startswith(f"Web_{compiled.script_hash}_")
                        and isinstance(item.get("result"), dict)
                        and item["result"].get("target_step_index") == index
                    )
                ]
                result["failure_retry"] = {
                    "enabled": True,
                    "process_script_name": str(failure_retry.get("process_script_name") or ""),
                    "attempts": len(process_runs),
                    "max_retries": int(failure_retry.get("max_retries", 2)),
                    "executed": bool(process_runs),
                    "runs": process_runs,
                }
            if step.get("_module_name") is not None:
                record["module"] = {
                    "index": int(step.get("_module_index", 0)),
                    "name": str(step.get("_module_name")),
                    "step_index": int(step.get("_module_step_index", 0)),
                    "interval": bool(step.get("_composition_interval", False)),
                }
            steps.append(record)

        for event in conditional_skips:
            if event.get("scope") != "until_step":
                continue
            trigger_index = int(event["trigger_step_index"])
            target_index = int(event["target_step_index"])
            for skipped_index in range(trigger_index + 1, target_index):
                skipped_step = steps[skipped_index]
                skipped_step["skipped"] = True
                skipped_step["skipped_by_step_index"] = trigger_index
                skipped_step.setdefault("result", {})["reason"] = "conditional_skip"

        global_dismissals = [
            {
                "name": record["name"],
                "backend": "maafw",
                "completed": record["completed"],
            }
            for record in node_records
            if "_Global_" in record["name"] and record["completed"]
        ]
        return {
            "steps": steps,
            "maa_nodes": node_records,
            "global_popup_dismissals": global_dismissals,
            "conditional_skips": conditional_skips,
            "numeric_conditions": collector["numeric_conditions"],
            "yolo_detections": collector["yolo"],
            "color_marker_detections": collector["color_markers"],
            "custom_actions": collector["custom_actions"],
        }

    @staticmethod
    def _find_failed_step(compiled: CompiledMaaTask, detail: Any) -> dict[str, Any]:
        completed_nodes: set[str] = set()
        completed_step_indexes: set[int] = set()
        incomplete_nodes: list[tuple[int, str]] = []
        if detail is not None:
            for node in detail.nodes:
                step_index = compiled.node_steps.get(node.name)
                if node.completed:
                    completed_nodes.add(node.name)
                    if step_index is not None:
                        completed_step_indexes.add(step_index)
                elif step_index is not None:
                    incomplete_nodes.append((step_index, node.name))

        # Maa reports every unselected candidate as incomplete. Image branches,
        # conditional skips and global-popup candidates can therefore leave old
        # incomplete nodes behind even after execution has advanced far beyond
        # them. Locate the actual execution frontier instead of returning the
        # first incomplete candidate in Maa's detail list.
        if completed_step_indexes:
            latest_started = max(completed_step_indexes)
            latest_exits = compiled.step_exits.get(latest_started, [])
            if latest_exits and not any(node in completed_nodes for node in latest_exits):
                failed_node = next(
                    (name for index, name in reversed(incomplete_nodes) if index == latest_started),
                    "",
                )
                result: dict[str, Any] = {
                    "index": latest_started,
                    "number": latest_started + 1,
                }
                if failed_node:
                    result["node"] = failed_node
                return result

            frontier = latest_started + 1
            failed_node = next(
                (name for index, name in incomplete_nodes if index == frontier),
                "",
            )
            if frontier in compiled.step_exits:
                result = {"index": frontier, "number": frontier + 1}
                if failed_node:
                    result["node"] = failed_node
                return result

        for step_index in sorted(compiled.step_exits):
            if not any(node in completed_nodes for node in compiled.step_exits[step_index]):
                failed_node = next(
                    (name for index, name in incomplete_nodes if index == step_index),
                    "",
                )
                result = {"index": step_index, "number": step_index + 1}
                if failed_node:
                    result["node"] = failed_node
                return result
        return {"index": None, "number": None}

    @classmethod
    def enrich_failure_diagnosis(
        cls,
        failure: dict[str, Any],
        script: dict[str, Any],
    ) -> None:
        """Attach screenshot-time template scores without altering the evidence PNG."""
        diagnosis = failure.get("failure_diagnosis")
        failed_step = failure.get("failed_step")
        if not isinstance(diagnosis, dict) or not isinstance(failed_step, dict):
            return
        failed_index = failed_step.get("index")
        steps = script.get("steps")
        if (
            not isinstance(failed_index, int)
            or not isinstance(steps, list)
            or not 0 <= failed_index < len(steps)
            or not isinstance(steps[failed_index], dict)
        ):
            return
        step = steps[failed_index]
        action = str(step.get("action") or failed_step.get("action") or "")
        title = str(diagnosis.get("title") or "")
        error_type = str(failure.get("error_type") or "")

        template_value = ""
        threshold = 0.85
        region: Any = None
        template_role = "condition"
        if error_type == "PostAssertionFailed":
            assertion = step.get("post_assertion")
            if not isinstance(assertion, dict):
                return
            template_value = str(assertion.get("template_base64") or "")
            threshold = float(assertion.get("threshold", 0.85))
            region = assertion.get("search_region")
            template_role = "post_assertion"
        elif action == "wait_click" and title == "点击图片识别失败":
            template_value = str(step.get("click_template_base64") or "")
            threshold = float(step.get("click_threshold", step.get("threshold", 0.85)))
            region = step.get("click_search_region")
            template_role = "click"
        elif action in {"wait_click", "wait_image"} and diagnosis.get("stage") in {
            "recognition",
            "transition",
        }:
            template_value = str(step.get("template_base64") or "")
            threshold = float(step.get("threshold", 0.85))
            region = step.get("search_region")
        else:
            return
        if not template_value:
            return

        screenshot = failure.get("failure_screenshot")
        encoded_screen = screenshot.get("data_base64") if isinstance(screenshot, dict) else None
        if not isinstance(encoded_screen, str) or not encoded_screen:
            return
        try:
            evidence = cls._template_match_evidence(
                encoded_screen,
                template_value,
                threshold,
                region,
            )
        except Exception as exc:
            diagnosis["recognition"] = {
                "template_role": template_role,
                "configured_threshold": round(threshold, 6),
                "score_error": str(exc),
            }
            return

        evidence["template_role"] = template_role
        diagnosis["recognition"] = evidence
        score = float(evidence["actual_score"])
        score_text = f"失败现场匹配值 {score:.3f} / 设定阈值 {threshold:.3f}"
        message = str(diagnosis.get("message") or "").rstrip("。")
        if score_text not in message:
            diagnosis["message"] = f"{message}（{score_text}）。"

    @staticmethod
    def _template_match_evidence(
        encoded_screen: str,
        template_value: str,
        threshold: float,
        region: Any,
    ) -> dict[str, Any]:
        try:
            import cv2
            import numpy as np
        except ImportError as exc:
            raise RuntimeError("OpenCV template diagnostics are unavailable") from exc

        encoded_template = template_value.split(",", 1)[-1]
        screen_bytes = base64.b64decode(encoded_screen, validate=True)
        template_bytes = base64.b64decode(encoded_template, validate=True)
        screen = cv2.imdecode(np.frombuffer(screen_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        template = cv2.imdecode(np.frombuffer(template_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if screen is None or template is None:
            raise ValueError("failure screenshot or template is not a valid image")

        origin_x = 0
        origin_y = 0
        search_image = screen
        if isinstance(region, dict):
            origin_x = max(0, int(region.get("x", 0)))
            origin_y = max(0, int(region.get("y", 0)))
            width = int(region.get("width", 0))
            height = int(region.get("height", 0))
            end_x = min(screen.shape[1], origin_x + width) if width > 0 else screen.shape[1]
            end_y = min(screen.shape[0], origin_y + height) if height > 0 else screen.shape[0]
            search_image = screen[origin_y:end_y, origin_x:end_x]
        if (
            search_image.size == 0
            or template.shape[0] > search_image.shape[0]
            or template.shape[1] > search_image.shape[1]
        ):
            raise ValueError("template is larger than its configured search region")

        scores = cv2.matchTemplate(search_image, template, cv2.TM_CCOEFF_NORMED)
        _, actual_score, _, location = cv2.minMaxLoc(scores)
        if not math.isfinite(float(actual_score)):
            raise ValueError("template matching produced a non-finite score")
        x = origin_x + int(location[0])
        y = origin_y + int(location[1])
        return {
            "actual_score": round(float(actual_score), 6),
            "configured_threshold": round(float(threshold), 6),
            "passed_on_failure_screenshot": bool(actual_score >= threshold),
            "best_box": {
                "x": x,
                "y": y,
                "width": int(template.shape[1]),
                "height": int(template.shape[0]),
            },
            "screen_size": {
                "width": int(screen.shape[1]),
                "height": int(screen.shape[0]),
            },
        }

    @classmethod
    def _diagnose_failure(
        cls,
        script: dict[str, Any],
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        """Turn Maa node telemetry into a stable, user-facing failure reason."""
        failed_step = summary.get("failed_step")
        failed_step = failed_step if isinstance(failed_step, dict) else {}
        failed_index = failed_step.get("index")
        failed_node = str(failed_step.get("node") or "")
        script_steps = script.get("steps")
        script_steps = script_steps if isinstance(script_steps, list) else []
        step = (
            script_steps[failed_index]
            if isinstance(failed_index, int)
            and 0 <= failed_index < len(script_steps)
            and isinstance(script_steps[failed_index], dict)
            else {}
        )
        action = str(step.get("action") or failed_step.get("action") or "")
        records = summary.get("maa_nodes")
        records = (
            [item for item in records if isinstance(item, dict)]
            if isinstance(records, list)
            else []
        )
        current_records = [
            item
            for item in records
            if isinstance(failed_index, int) and item.get("step_index") == failed_index
        ]
        failed_record = next(
            (item for item in reversed(current_records) if item.get("name") == failed_node),
            next((item for item in reversed(current_records) if not item.get("completed")), None),
        )
        current_click = cls._latest_click_record(current_records, succeeded_only=True)
        previous_click = cls._latest_click_record(
            [
                item
                for item in records
                if isinstance(item.get("step_index"), int)
                and isinstance(failed_index, int)
                and item["step_index"] < failed_index
            ],
            succeeded_only=True,
        )
        error_type = str(summary.get("error_type") or "")
        error = str(summary.get("error") or "MaaFramework pipeline execution failed")

        diagnosis: dict[str, Any]
        click_record: dict[str, Any] | None = None
        if error_type == "FailureRetryLimitExceeded":
            diagnosis = {
                "stage": "retry_limit",
                "title": "失败恢复次数已耗尽",
                "message": error,
            }
            click_record = current_click
        elif error_type == "FailureRetryProcessFailed":
            diagnosis = {
                "stage": "retry_process",
                "title": "失败恢复脚本执行失败",
                "message": error,
            }
            click_record = current_click
        elif error_type == "MatchLoopLimitExceeded":
            color_marker = step.get("click_mode") == "color_marker"
            diagnosis = {
                "stage": "match_loop",
                "title": "动态好感标记仍然存在" if color_marker else "重复图片仍然存在",
                "message": error,
            }
            click_record = current_click
        elif error_type == "PostAssertionFailed":
            diagnosis = {
                "stage": "transition",
                "title": "点击后进入下一步失败" if current_click else "执行后断言失败",
                "message": "点击动作已执行，但执行后断言图片在重试后仍未出现。",
            }
            click_record = current_click
        elif error_type == "CourseScheduleFailed":
            diagnosis = {
                "stage": "course_schedule",
                "title": "课程表巡回执行失败",
                "message": error,
            }
        elif action == "wait_click":
            recognition: dict[str, Any] = {}
            if isinstance(failed_record, dict):
                raw_recognition = failed_record.get("recognition")
                if isinstance(raw_recognition, dict):
                    recognition = raw_recognition
            action_result: dict[str, Any] = {}
            if isinstance(failed_record, dict):
                raw_action_result = failed_record.get("action")
                if isinstance(raw_action_result, dict):
                    action_result = raw_action_result
            if recognition.get("hit") is True and action_result.get("success") is False:
                diagnosis = {
                    "stage": "click",
                    "title": "点击操作失败",
                    "message": "目标图片已经识别，但 MaaFramework 未能完成点击动作。",
                }
                click_record = failed_record
            else:
                separate_click_image = str(
                    step.get("click_mode") or ""
                ) == "image" or "ClickImage" in str(
                    failed_record.get("name") if isinstance(failed_record, dict) else failed_node
                )
                timeout = step.get(
                    "click_timeout_seconds" if separate_click_image else "timeout_seconds", 30
                )
                diagnosis = {
                    "stage": "recognition",
                    "title": "点击图片识别失败" if separate_click_image else "点击按钮识别失败",
                    "message": (
                        f"触发图片已出现，但等待 {timeout} 秒仍未识别到用于点击的图片，点击未执行。"
                        if separate_click_image
                        else f"等待 {timeout} 秒仍未识别到目标图片，点击未执行。"
                    ),
                }
        elif action == "wait_image":
            if previous_click is not None:
                diagnosis = {
                    "stage": "transition",
                    "title": "点击后进入下一步失败",
                    "message": "上一点击已经执行，但当前步骤等待的目标图片未出现。",
                }
                click_record = previous_click
            else:
                diagnosis = {
                    "stage": "recognition",
                    "title": "目标图片识别失败",
                    "message": "在设定时间内未识别到当前步骤的目标图片。",
                }
        else:
            action_titles = {
                "start": "终端或手机初始化失败",
                "launch_app": "打开应用失败",
                "feedback": "反馈截图失败",
                "cleanup": "清理应用资源失败",
                "back": "返回操作失败",
                "home": "返回主页失败",
                "swipe": "滑动操作失败",
                "smart_swipe": "滑动操作失败",
            }
            diagnosis = {
                "stage": "action" if action else "pipeline",
                "title": action_titles.get(action, "脚本步骤执行失败"),
                "message": error,
            }

        if click_record is not None:
            click = cls._click_location(script_steps, click_record)
            if click is not None:
                diagnosis["click"] = click
        return diagnosis

    @staticmethod
    def _latest_click_record(
        records: list[dict[str, Any]],
        *,
        succeeded_only: bool,
    ) -> dict[str, Any] | None:
        for record in reversed(records):
            action = record.get("action")
            if not isinstance(action, dict):
                continue
            action_type = str(action.get("type") or "").lower()
            if "click" not in action_type or "key" in action_type:
                continue
            if succeeded_only and action.get("success") is not True:
                continue
            return record
        return None

    @classmethod
    def _click_location(
        cls,
        script_steps: list[Any],
        record: dict[str, Any],
    ) -> dict[str, Any] | None:
        step_index = record.get("step_index")
        if not isinstance(step_index, int) or not 0 <= step_index < len(script_steps):
            return None
        raw_step = script_steps[step_index]
        if not isinstance(raw_step, dict):
            return None
        step = raw_step
        branch_match = re.search(r"_Branch_(\d+)_", str(record.get("name") or ""))
        if branch_match and isinstance(raw_step.get("image_branches"), list):
            branch_index = int(branch_match.group(1)) - 1
            branches = raw_step["image_branches"]
            if 0 <= branch_index < len(branches) and isinstance(branches[branch_index], dict):
                step = {**raw_step, **branches[branch_index]}

        click_mode = str(step.get("click_mode") or "fixed")
        if click_mode == "fixed":
            click = step.get("click")
            if isinstance(click, dict) and "x" in click and "y" in click:
                return {
                    "x": int(click["x"]),
                    "y": int(click["y"]),
                    "source": "固定坐标",
                    "step_number": step_index + 1,
                    "executed": bool(
                        isinstance(record.get("action"), dict)
                        and record["action"].get("success") is True
                    ),
                }

        recognition = record.get("recognition")
        box = recognition.get("box") if isinstance(recognition, dict) else None
        if not isinstance(box, dict):
            return None
        try:
            x = int(box["x"])
            y = int(box["y"])
            width = int(box["width"])
            height = int(box["height"])
        except (KeyError, TypeError, ValueError):
            return None
        if click_mode == "match_offset":
            anchor = str(step.get("match_anchor") or "center")
            anchors = {
                "top_left": (0, 0),
                "top_right": (width - 1, 0),
                "center": ((width - 1) // 2, (height - 1) // 2),
                "bottom_left": (0, height - 1),
                "bottom_right": (width - 1, height - 1),
            }
            anchor_x, anchor_y = anchors.get(anchor, anchors["center"])
            x += anchor_x + int(step.get("match_offset_x", 0))
            y += anchor_y + int(step.get("match_offset_y", 0))
            source = "识别图绑定偏移"
        else:
            x += width // 2
            y += height // 2
            source = "识别图片中心"
        return {
            "x": x,
            "y": y,
            "source": source,
            "step_number": step_index + 1,
            "executed": bool(
                isinstance(record.get("action"), dict) and record["action"].get("success") is True
            ),
        }

    @staticmethod
    def _rect_dict(rect: Any) -> dict[str, int] | None:
        if rect is None:
            return None
        x, y, width, height = MaaDiagnostics._rect_tuple(rect)
        return {
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        }

    @staticmethod
    def _rect_tuple(rect: Any) -> tuple[int, int, int, int]:
        if isinstance(rect, (list, tuple)) and len(rect) == 4:
            return int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])
        return int(rect.x), int(rect.y), int(rect.w), int(rect.h)
