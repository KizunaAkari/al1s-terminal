"""Explicit DSL action-to-node mappings, independent of graph wiring passes."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from typing import Any

from al1s_terminal.execution.image_match_streak import consecutive_match_count
from al1s_terminal.execution.pipeline_builders import PipelineBuilder
from al1s_terminal.execution.pipeline_types import _StepPlan


class PipelineStepCompiler(PipelineBuilder):
    def _step_start(self, index: int, step: dict[str, Any]) -> _StepPlan:
        return self._simple_custom(index, step, self.START_ACTION)

    def _step_feedback(self, index: int, step: dict[str, Any]) -> _StepPlan:
        return self._simple_custom(index, step, self.FEEDBACK_ACTION)

    def _step_course_schedule(self, index: int, step: dict[str, Any]) -> _StepPlan:
        avatars = step.get("course_target_avatars")
        if not isinstance(avatars, list) or not avatars:
            raise ValueError("course schedule requires at least one target avatar")
        if len(avatars) > 20:
            raise ValueError("course schedule supports at most 20 target avatars")
        compiled_avatars: list[dict[str, Any]] = []
        for avatar_index, avatar in enumerate(avatars, start=1):
            if not isinstance(avatar, dict):
                raise ValueError(f"course schedule avatar {avatar_index} must be an object")
            template = str(avatar.get("template_base64") or "")
            if not template:
                raise ValueError(f"course schedule avatar {avatar_index} is missing a screenshot")
            try:
                threshold = float(avatar.get("threshold", 0.82))
                source_width = int(avatar.get("screen_width", 2400))
                source_height = int(avatar.get("screen_height", 1080))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"course schedule avatar {avatar_index} settings are invalid"
                ) from exc
            if not math.isfinite(threshold) or not 0.1 <= threshold <= 1:
                raise ValueError(f"course schedule avatar {avatar_index} threshold is invalid")
            if source_width < 1 or source_height < 1:
                raise ValueError(f"course schedule avatar {avatar_index} source size is invalid")
            filename = self._materialize_image(template)
            compiled_avatars.append(
                {
                    "name": str(avatar.get("name") or f"目标学生 {avatar_index}"),
                    "template_path": str(self.context.image_dir / filename),
                    "threshold": threshold,
                    "screen_width": source_width,
                    "screen_height": source_height,
                }
            )

        try:
            region_count = int(step.get("course_region_count", 11))
            ticket_limit = int(step.get("course_ticket_limit", 7))
            action_timeout = float(step.get("course_action_timeout_seconds", 25))
        except (TypeError, ValueError) as exc:
            raise ValueError("course schedule traversal settings are invalid") from exc
        if not 1 <= region_count <= 20:
            raise ValueError("course schedule region count must be between 1 and 20")
        if not 1 <= ticket_limit <= 20:
            raise ValueError("course schedule ticket limit must be between 1 and 20")
        if not math.isfinite(action_timeout) or not 5 <= action_timeout <= 120:
            raise ValueError("course schedule action timeout must be between 5 and 120 seconds")

        name = self._step_name(index, "CourseSchedule")
        node = self._step_node(index, step, recognition="DirectHit", action="Custom")
        node.update(
            {
                "custom_action": self.COURSE_SCHEDULE_ACTION,
                "custom_action_param": {
                    "target_avatars": compiled_avatars,
                    "region_count": region_count,
                    "ticket_limit": ticket_limit,
                    "action_timeout_seconds": action_timeout,
                    "avatar_search_roi": step.get(
                        "course_avatar_search_roi",
                        {"x": 300, "y": 180, "width": 1800, "height": 800},
                    ),
                    "next_region_point": step.get(
                        "course_next_region_point",
                        {"x": 2260, "y": 545},
                    ),
                    "reference_size": {"width": 2400, "height": 1080},
                },
            }
        )
        self.context.pipeline[name] = node
        return self._plan(name, index, step)

    def _step_cleanup(self, index: int, step: dict[str, Any]) -> _StepPlan:
        # The executor performs the final force-stop and Home operation after
        # MaaFramework has completed this marker node. Keeping cleanup outside
        # the pipeline lets it run even when the foreground package was
        # discovered dynamically rather than written into the script.
        name = self._step_name(index, "Cleanup")
        self.context.pipeline[name] = self._step_node(
            index, step, recognition="DirectHit", action="DoNothing"
        )
        return self._plan(name, index, step)

    def _step_wake(self, index: int, step: dict[str, Any]) -> _StepPlan:
        return self._key_step(index, step, 224)

    def _step_sleep(self, index: int, step: dict[str, Any]) -> _StepPlan:
        return self._key_step(index, step, 223)

    def _step_home(self, index: int, step: dict[str, Any]) -> _StepPlan:
        return self._key_step(index, step, 3)

    def _step_back(self, index: int, step: dict[str, Any]) -> _StepPlan:
        return self._key_step(index, step, 4)

    def _step_task_view(self, index: int, step: dict[str, Any]) -> _StepPlan:
        return self._key_step(index, step, 187)

    def _step_reboot(self, index: int, step: dict[str, Any]) -> _StepPlan:
        name = self._step_name(index, "Reboot")
        node = self._step_node(index, step, recognition="DirectHit", action="Shell")
        node.update({"cmd": "reboot", "shell_timeout": 10_000})
        self.context.pipeline[name] = node
        return self._plan(name, index, step)

    def _step_screenshot(self, index: int, step: dict[str, Any]) -> _StepPlan:
        return self._simple_custom(index, step, self.SCREENSHOT_ACTION)

    def _step_log(self, index: int, step: dict[str, Any]) -> _StepPlan:
        name = self._step_name(index, "Log")
        self.context.pipeline[name] = self._step_node(
            index, step, recognition="DirectHit", action="DoNothing"
        )
        return self._plan(name, index, step)

    def _step_launch_app(self, index: int, step: dict[str, Any]) -> _StepPlan:
        package = str(step.get("package") or "").strip()
        if not package:
            raise ValueError("launch_app requires package")
        activity = str(step.get("activity") or "").strip()
        launch_entry = (
            f"{package}/{activity}" if activity and "/" not in activity else (activity or package)
        )
        self.context.active_packages.add(package)

        start_name = self._step_name(index, "StartApp")
        start_node = self._step_node(index, step, recognition="DirectHit", action="StartApp")
        start_node["package"] = launch_entry
        start_node["post_delay"] = self._milliseconds(step.get("wait_seconds", 0))
        self.context.pipeline[start_name] = start_node

        if step.get("force_stop_before_launch", True) is False:
            return self._plan(start_name, index, step)

        stop_name = self._step_name(index, "ColdStop")
        stop_node = self._step_node(index, step, recognition="DirectHit", action="StopApp")
        stop_node.update(
            {"package": package, "next": [start_name], "timeout": 2_000, "rate_limit": 100}
        )
        self.context.pipeline[stop_name] = stop_node
        return _StepPlan(
            candidates=[stop_name],
            exits=[start_name],
            nodes=[stop_name, start_name],
            incoming_timeout_ms=self._timeout(step),
            incoming_rate_limit_ms=self._rate_limit(step),
        )

    def _step_close_app(self, index: int, step: dict[str, Any]) -> _StepPlan:
        package = str(step.get("package") or "").strip()
        if not package:
            raise ValueError("close_app requires package")
        name = self._step_name(index, "StopApp")
        node = self._step_node(index, step, recognition="DirectHit", action="StopApp")
        node["package"] = package
        self.context.pipeline[name] = node
        return self._plan(name, index, step)

    def _step_tap(self, index: int, step: dict[str, Any]) -> _StepPlan:
        if "x" not in step or "y" not in step:
            raise ValueError("tap requires x and y")
        name = self._step_name(index, "Click")
        node = self._step_node(index, step, recognition="DirectHit", action="Click")
        node.update(self._repeat_fields(step))
        node["target"] = [int(step["x"]), int(step["y"])]
        self.context.pipeline[name] = node
        return self._plan(name, index, step)

    def _step_swipe(self, index: int, step: dict[str, Any]) -> _StepPlan:
        name = self._step_name(index, "Swipe")
        self.context.pipeline[name] = self._swipe_node(index, step, step)
        return self._plan(name, index, step)

    def _step_wait(self, index: int, step: dict[str, Any]) -> _StepPlan:
        name = self._step_name(index, "Wait")
        node = self._step_node(index, step, recognition="DirectHit", action="DoNothing")
        node["post_delay"] = self._milliseconds(step.get("seconds", 1))
        self.context.pipeline[name] = node
        return self._plan(name, index, step)

    def _step_wait_random(self, index: int, step: dict[str, Any]) -> _StepPlan:
        return self._simple_custom(index, step, self.RANDOM_WAIT_ACTION)

    def _step_wait_image(self, index: int, step: dict[str, Any]) -> _StepPlan:
        consecutive_match_count(step)
        name = self._step_name(index, "WaitImage")
        node = self._template_node(index, step, action="DoNothing", role="step")
        self.context.pipeline[name] = node
        return self._plan(name, index, step)

    def _step_wait_text(self, index: int, step: dict[str, Any]) -> _StepPlan:
        return self._text_plan(index, step, click=False)

    def _step_click_text(self, index: int, step: dict[str, Any]) -> _StepPlan:
        return self._text_plan(index, step, click=True)

    def _step_recognize_execute(self, index: int, step: dict[str, Any]) -> _StepPlan:
        recognition = step.get("recognition_mode")
        if recognition == "image":
            node = self._template_node(index, step, action="DoNothing", role="step")
        elif recognition == "text":
            text = step.get("text")
            if not isinstance(text, str) or not text.strip() or len(text) > 200:
                raise ValueError("OCR text must contain 1 to 200 characters")
            self.context.requires_ocr = True
            node = self._step_node(index, step, recognition="OCR", action="DoNothing")
            node.update(expected=[re.escape(text)], order_by="Horizontal", index=0)
            if step.get("search_region") is not None:
                node["roi"] = self._rect(step["search_region"])
        else:
            raise ValueError("recognize_execute requires image or text recognition")

        count = int(step.get("execution_count", 1))
        interval = int(step.get("execution_interval_ms", 120))
        if count < 1 or interval < 0:
            raise ValueError("recognize_execute repetition is invalid")
        execution = step.get("execution_mode")
        name = self._step_name(index, "RecognizeExecute")
        if execution == "match_center":
            if recognition != "image":
                raise ValueError("image-center reuse requires image recognition")
            node.update(action="Click", target=True)
            node["attach"]["click_match_center"] = True
        elif execution == "fixed_tap":
            click = step.get("click")
            if not isinstance(click, dict) or not {"x", "y"}.issubset(click):
                raise ValueError("recognize_execute fixed click requires coordinates")
            node.update(action="Click", target=[int(click["x"]), int(click["y"])])
        elif execution == "fixed_swipe":
            swipe = step.get("swipe")
            required = {"x1", "y1", "x2", "y2", "duration_ms"}
            if not isinstance(swipe, dict) or not required.issubset(swipe):
                raise ValueError("recognize_execute fixed swipe requires coordinates")
            node.update(
                action="Swipe",
                begin=[int(swipe["x1"]), int(swipe["y1"])],
                end=[int(swipe["x2"]), int(swipe["y2"])],
                duration=int(swipe["duration_ms"]),
            )
        elif execution == "image_center":
            click_template = step.get("click_template_base64")
            if not isinstance(click_template, str) or not click_template:
                raise ValueError("recognize_execute image click requires a target template")
            click_name = self._step_name(index, "ExecuteImage")
            click_node = self._template_node(
                index,
                {
                    **step,
                    "template_base64": click_template,
                    "threshold": step.get("click_threshold", step.get("threshold", 0.85)),
                    "search_region": None,
                },
                action="Click",
                role="step-click",
            )
            click_node.update(target=True, repeat=count, repeat_delay=interval)
            node.update(
                next=[click_name],
                timeout=self._timeout(step),
                rate_limit=self._rate_limit(step),
            )
            self.context.pipeline[name] = node
            self.context.pipeline[click_name] = click_node
            return _StepPlan(
                candidates=[name],
                exits=[click_name],
                nodes=[name, click_name],
                incoming_timeout_ms=self._timeout(step),
                incoming_rate_limit_ms=self._rate_limit(step),
            )
        else:
            raise ValueError("recognize_execute execution mode is invalid")
        node.update(repeat=count, repeat_delay=interval)
        self.context.pipeline[name] = node
        return self._plan(name, index, step)

    def _text_plan(self, index: int, step: dict[str, Any], *, click: bool) -> _StepPlan:
        text = step.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 200:
            raise ValueError("OCR text must contain 1 to 200 characters")
        self.context.requires_ocr = True
        name = self._step_name(index, "ClickText" if click else "WaitText")
        node = self._step_node(
            index, step, recognition="OCR", action="Click" if click else "DoNothing"
        )
        # Maa interprets expected as regex; the editor contract is literal text.
        node.update(expected=[re.escape(text)], order_by="Horizontal", index=0)
        if step.get("search_region") is not None:
            node["roi"] = self._rect(step["search_region"])
        if click:
            node["target"] = True
        self.context.pipeline[name] = node
        return self._plan(name, index, step)

    def _step_wait_click(self, index: int, step: dict[str, Any]) -> _StepPlan:
        click_mode = str(step.get("click_mode") or "fixed")
        if click_mode in {"match_offset", "color_marker"} and step.get("image_branches"):
            raise ValueError(f"{click_mode} click does not support image branches")
        if click_mode == "color_marker":
            return self._step_color_marker_click(index, step)
        if click_mode == "match_offset":
            return self._step_match_offset_click(index, step)

        plans = [self._wait_click_variant(index, step, "")]
        branches = step.get("image_branches")
        if branches is not None:
            if not isinstance(branches, list) or len(branches) > 20:
                raise ValueError("image_branches must be an array with at most 20 items")
            for branch_index, branch in enumerate(branches, start=1):
                if not isinstance(branch, dict):
                    raise ValueError(f"image branch {branch_index} must be an object")
                branch_mode = str(branch.get("click_mode") or "match_center")
                if branch_mode not in {"match_center", "image"}:
                    raise ValueError(f"image branch {branch_index} click mode is invalid")
                branch_step = {
                    **step,
                    **branch,
                    "action": "wait_click",
                    "click_mode": branch_mode,
                    "image_branches": [],
                }
                plans.append(
                    self._wait_click_variant(index, branch_step, f"Branch_{branch_index:02d}_")
                )

        return _StepPlan(
            candidates=[candidate for plan in plans for candidate in plan.candidates],
            exits=[exit_name for plan in plans for exit_name in plan.exits],
            nodes=[node_name for plan in plans for node_name in plan.nodes],
            incoming_timeout_ms=self._timeout(step),
            incoming_rate_limit_ms=self._rate_limit(step),
        )

    def _wait_click_variant(
        self,
        index: int,
        step: dict[str, Any],
        name_prefix: str,
    ) -> _StepPlan:
        condition_name = self._step_name(index, f"{name_prefix}WaitClick")
        click_mode = str(step.get("click_mode") or "fixed")

        if click_mode == "image":
            condition_node = self._template_node(index, step, action="DoNothing", role="step")
            click_template = str(step.get("click_template_base64") or "")
            if not click_template:
                raise ValueError("image click mode requires click_template_base64")
            click_name = self._step_name(index, f"{name_prefix}ClickImage")
            click_config = {
                **step,
                "template_base64": click_template,
                "threshold": step.get("click_threshold", step.get("threshold", 0.85)),
                "search_region": step.get("click_search_region"),
            }
            click_node = self._template_node(index, click_config, action="Click", role="step-click")
            click_node.update(self._repeat_fields(step))
            click_node["target"] = True
            condition_node.update(
                {
                    "next": [click_name],
                    "timeout": self._milliseconds(
                        step.get("click_timeout_seconds", step.get("timeout_seconds", 30))
                    ),
                    "rate_limit": self._rate_limit(step),
                }
            )
            self.context.pipeline[condition_name] = condition_node
            self.context.pipeline[click_name] = click_node
            return _StepPlan(
                candidates=[condition_name],
                exits=[click_name],
                nodes=[condition_name, click_name],
                incoming_timeout_ms=self._timeout(step),
                incoming_rate_limit_ms=self._rate_limit(step),
            )

        node = self._template_node(index, step, action="Click", role="step")
        node.update(self._repeat_fields(step))
        if click_mode in {"match_center", "template_center"}:
            node["target"] = True
        else:
            click = step.get("click")
            if not isinstance(click, dict) or "x" not in click or "y" not in click:
                raise ValueError("fixed click mode requires click.x and click.y")
            node["target"] = [int(click["x"]), int(click["y"])]
        self.context.pipeline[condition_name] = node
        return self._plan(condition_name, index, step)

    def _step_match_offset_click(self, index: int, step: dict[str, Any]) -> _StepPlan:
        template_rect = step.get("template_rect")
        if not isinstance(template_rect, dict):
            raise ValueError("match offset click requires template_rect")
        rect = self._rect(template_rect)
        width, height = rect[2], rect[3]

        order = str(step.get("match_order") or "Vertical")
        if order not in {"Horizontal", "Vertical", "Score"}:
            raise ValueError("match order must be Horizontal, Vertical or Score")
        try:
            match_index = int(step.get("match_index", 1))
            max_clicks = int(step.get("match_max_clicks", 50))
            offset_x = int(step.get("match_offset_x", 0))
            offset_y = int(step.get("match_offset_y", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("match index, offsets and max clicks must be integers") from exc
        if not 1 <= match_index <= 100:
            raise ValueError("match index must be between 1 and 100")
        if not 1 <= max_clicks <= 200:
            raise ValueError("match max clicks must be between 1 and 200")
        if abs(offset_x) > 4096 or abs(offset_y) > 4096:
            raise ValueError("match offsets must be between -4096 and 4096")

        anchor = str(step.get("match_anchor") or "center")
        anchors = {
            "top_left": (0, 0),
            "top_right": (width - 1, 0),
            "center": ((width - 1) // 2, (height - 1) // 2),
            "bottom_left": (0, height - 1),
            "bottom_right": (width - 1, height - 1),
        }
        if anchor not in anchors:
            raise ValueError("match anchor is invalid")
        anchor_x, anchor_y = anchors[anchor]
        target_offset = [
            anchor_x + offset_x,
            anchor_y + offset_y,
            1 - width,
            1 - height,
        ]

        try:
            wait_after_click = float(step.get("wait_after_click_seconds", 0.7))
        except (TypeError, ValueError) as exc:
            raise ValueError("wait after click must be a number") from exc
        if not math.isfinite(wait_after_click) or not 0.1 <= wait_after_click <= 10:
            raise ValueError("wait after click must be between 0.1 and 10 seconds")

        done_name = self._step_name(index, "MatchLoopDone")
        self.context.pipeline[done_name] = self._step_node(
            index,
            step,
            recognition="DirectHit",
            action="DoNothing",
        )

        overflow_name = self._step_name(index, "MatchLoopLimit")
        overflow = self._template_node(
            index,
            step,
            action="Custom",
            role="step-match-loop-limit",
        )
        overflow.update(
            {
                "index": 0,
                "order_by": order,
                "custom_action": self.MATCH_LOOP_LIMIT_ACTION,
                "custom_action_param": {
                    "max_clicks": max_clicks,
                    "step_index": index,
                },
            }
        )
        self.context.pipeline[overflow_name] = overflow

        click_name = self._step_name(index, "MatchLoopClick_Target")
        template_recognition = self._template_recognition(step)
        click_node = self._step_node(
            index,
            step,
            recognition="Custom",
            action="Click",
        )
        click_node.update(
            {
                "custom_recognition": self.MATCH_OFFSET_RECOGNITION,
                "custom_recognition_param": {
                    "template": template_recognition["template"],
                    "threshold": template_recognition["threshold"],
                    "order_by": order,
                    "preferred_index": match_index - 1,
                },
            }
        )
        if "roi" in template_recognition:
            click_node["roi"] = template_recognition["roi"]

        click_node.update(
            {
                "target": True,
                "target_offset": target_offset,
                "post_delay": self._milliseconds(wait_after_click),
                "max_hit": max_clicks,
                "next": [*self.context.global_candidates, click_name, overflow_name, done_name],
                "timeout": 1_000,
                "rate_limit": self._rate_limit(step),
                **self._repeat_fields(step),
            }
        )
        self.context.pipeline[click_name] = click_node

        return _StepPlan(
            candidates=[click_name, done_name],
            exits=[done_name],
            nodes=[click_name, overflow_name, done_name],
            incoming_timeout_ms=1_000,
            incoming_rate_limit_ms=self._rate_limit(step),
        )

    def _step_color_marker_click(self, index: int, step: dict[str, Any]) -> _StepPlan:
        """Click animated multi-part color markers until they are stably absent."""
        order = str(step.get("match_order") or "Vertical")
        if order not in {"Horizontal", "Vertical", "Score"}:
            raise ValueError("color marker order must be Horizontal, Vertical or Score")
        try:
            preferred_index = int(step.get("match_index", 1))
            max_clicks = int(step.get("match_max_clicks", 50))
            offset_x = int(step.get("match_offset_x", 0))
            offset_y = int(step.get("match_offset_y", 0))
            absence_checks = int(step.get("marker_absence_checks", 3))
            component_min_area = int(step.get("marker_component_min_area", 250))
            component_max_area = int(step.get("marker_component_max_area", 1800))
            group_distance = int(step.get("marker_group_distance", 115))
        except (TypeError, ValueError) as exc:
            raise ValueError("color marker settings must be integers") from exc
        if not 1 <= preferred_index <= 100:
            raise ValueError("color marker index must be between 1 and 100")
        if not 1 <= max_clicks <= 200:
            raise ValueError("color marker max clicks must be between 1 and 200")
        if not 2 <= absence_checks <= 10:
            raise ValueError("color marker absence checks must be between 2 and 10")
        if component_min_area < 1 or component_max_area <= component_min_area:
            raise ValueError("color marker component area range is invalid")
        if not 20 <= group_distance <= 500:
            raise ValueError("color marker group distance must be between 20 and 500")
        if abs(offset_x) > 4096 or abs(offset_y) > 4096:
            raise ValueError("color marker offsets must be between -4096 and 4096")
        anchor = str(step.get("match_anchor") or "center")
        if anchor not in {"center", "top_left", "top_right", "bottom_left", "bottom_right"}:
            raise ValueError("color marker anchor is invalid")

        try:
            wait_after_click = float(step.get("wait_after_click_seconds", 0.7))
        except (TypeError, ValueError) as exc:
            raise ValueError("wait after click must be a number") from exc
        if not math.isfinite(wait_after_click) or not 0.1 <= wait_after_click <= 10:
            raise ValueError("wait after click must be between 0.1 and 10 seconds")

        lower = step.get("marker_hsv_lower", [10, 160, 200])
        upper = step.get("marker_hsv_upper", [40, 255, 255])
        if (
            not isinstance(lower, list)
            or not isinstance(upper, list)
            or len(lower) != 3
            or len(upper) != 3
        ):
            raise ValueError("color marker HSV bounds must each contain three integers")
        try:
            lower = [int(value) for value in lower]
            upper = [int(value) for value in upper]
        except (TypeError, ValueError) as exc:
            raise ValueError("color marker HSV bounds must be integers") from exc
        if any(low < 0 or high < low for low, high in zip(lower, upper, strict=True)):
            raise ValueError("color marker HSV bounds are invalid")
        if upper[0] > 179 or any(value > 255 for value in [*lower[1:], *upper[1:]]):
            raise ValueError("color marker HSV bounds exceed OpenCV ranges")

        state_key = f"{self.context.prefix}:{index}:color-marker"
        recognition_param = {
            "state_key": state_key,
            "hsv_lower": lower,
            "hsv_upper": upper,
            "component_min_area": component_min_area,
            "component_max_area": component_max_area,
            "group_distance": group_distance,
            "order_by": order,
            "preferred_index": preferred_index - 1,
            "offset_x": offset_x,
            "offset_y": offset_y,
            "anchor": anchor,
            "absence_checks": absence_checks,
        }

        done_name = self._step_name(index, "ColorMarkerDone")
        done_node = self._step_node(index, step, recognition="Custom", action="DoNothing")
        done_node.update(
            {
                "custom_recognition": self.COLOR_MARKER_RECOGNITION,
                "custom_recognition_param": {**recognition_param, "mode": "absent"},
            }
        )
        self.context.pipeline[done_name] = done_node

        overflow_name = self._step_name(index, "ColorMarkerLimit")
        overflow_node = self._step_node(index, step, recognition="Custom", action="Custom")
        overflow_node.update(
            {
                "custom_recognition": self.COLOR_MARKER_RECOGNITION,
                "custom_recognition_param": {**recognition_param, "mode": "target"},
                "custom_action": self.MATCH_LOOP_LIMIT_ACTION,
                "custom_action_param": {"max_clicks": max_clicks, "step_index": index},
            }
        )
        self.context.pipeline[overflow_name] = overflow_node

        click_name = self._step_name(index, "ColorMarkerClick")
        click_node = self._step_node(index, step, recognition="Custom", action="Click")
        click_node.update(
            {
                "custom_recognition": self.COLOR_MARKER_RECOGNITION,
                "custom_recognition_param": {**recognition_param, "mode": "target"},
                "target": True,
                "post_delay": self._milliseconds(wait_after_click),
                "max_hit": max_clicks,
                "next": [*self.context.global_candidates, click_name, overflow_name, done_name],
                "timeout": max(1_000, absence_checks * self._rate_limit(step) + 1_000),
                "rate_limit": self._rate_limit(step),
                **self._repeat_fields(step),
            }
        )
        search_region = step.get("search_region")
        if isinstance(search_region, dict):
            click_node["roi"] = list(self._rect(search_region))
            overflow_node["roi"] = list(self._rect(search_region))
            done_node["roi"] = list(self._rect(search_region))
        self.context.pipeline[click_name] = click_node

        return _StepPlan(
            candidates=[click_name, done_name],
            exits=[done_name],
            nodes=[click_name, overflow_name, done_name],
            incoming_timeout_ms=max(1_000, absence_checks * self._rate_limit(step) + 1_000),
            incoming_rate_limit_ms=self._rate_limit(step),
        )

    def _step_smart_swipe(self, index: int, step: dict[str, Any]) -> _StepPlan:
        swipe = step.get("swipe")
        if not isinstance(swipe, dict):
            raise ValueError("smart_swipe requires swipe settings")
        mode = str(step.get("mode") or "until_image")
        target_name = self._step_name(index, "SwipeTarget")
        self.context.pipeline[target_name] = self._template_node(
            index, step, action="DoNothing", role="step"
        )

        swipe_name = self._step_name(index, "SwipeAction")
        swipe_node = self._swipe_node(index, step, swipe)
        swipe_node["post_delay"] = self._milliseconds(step.get("wait_after_swipe_seconds", 1))
        self.context.pipeline[swipe_name] = swipe_node

        if mode == "after_image":
            duration_ms = self._milliseconds(step.get("swipe_for_seconds", 5))
            one_cycle_ms = max(
                1, int(swipe_node.get("duration", 350)) + int(swipe_node["post_delay"])
            )
            swipe_node["repeat"] = max(1, math.ceil(duration_ms / one_cycle_ms))
            swipe_node["repeat_delay"] = int(swipe_node["post_delay"])
            swipe_node["post_delay"] = 0
            self.context.pipeline[target_name].update(
                {
                    "next": [swipe_name],
                    "timeout": 2_000,
                    "rate_limit": 100,
                }
            )
            return _StepPlan(
                candidates=[target_name],
                exits=[swipe_name],
                nodes=[target_name, swipe_name],
                incoming_timeout_ms=self._timeout(step),
                incoming_rate_limit_ms=self._rate_limit(step),
            )

        return _StepPlan(
            candidates=[target_name, {"name": swipe_name, "jump_back": True}],
            exits=[target_name],
            nodes=[target_name, swipe_name],
            incoming_timeout_ms=self._timeout(step, default_seconds=45),
            incoming_rate_limit_ms=100,
        )

    def _step_maa(self, index: int, step: dict[str, Any]) -> _StepPlan:
        """Allow an advanced editor node to provide a native Maa pipeline node."""
        native = step.get("pipeline_node")
        if not isinstance(native, dict):
            raise ValueError("maa action requires pipeline_node")
        name = self._step_name(index, "Native")
        node = dict(native)
        node.setdefault("attach", {})
        node["attach"] = {**node["attach"], **self._attach(index, step, "step")}
        self.context.pipeline[name] = node
        return self._plan(name, index, step)

    def _step_yolo_detect(self, index: int, step: dict[str, Any]) -> _StepPlan:
        self.context.requires_yolo = True
        name = self._step_name(index, "Yolo")
        node = self._step_node(index, step, recognition="Custom", action="DoNothing")
        node.update(
            {
                "custom_recognition": self.YOLO_RECOGNITION,
                "custom_recognition_param": {
                    key: value
                    for key, value in step.items()
                    if key
                    not in {
                        "action",
                        "skip_condition",
                        "post_assertion",
                        "template_base64",
                        "click_template_base64",
                    }
                },
            }
        )
        region = step.get("search_region")
        if isinstance(region, dict):
            node["roi"] = self._rect(region)
        self.context.pipeline[name] = node
        return self._plan(name, index, step)


STEP_COMPILERS: dict[str, Callable[[PipelineStepCompiler, int, dict[str, Any]], _StepPlan]] = {
    "start": PipelineStepCompiler._step_start,
    "feedback": PipelineStepCompiler._step_feedback,
    "course_schedule": PipelineStepCompiler._step_course_schedule,
    "cleanup": PipelineStepCompiler._step_cleanup,
    "wake": PipelineStepCompiler._step_wake,
    "sleep": PipelineStepCompiler._step_sleep,
    "home": PipelineStepCompiler._step_home,
    "back": PipelineStepCompiler._step_back,
    "task_view": PipelineStepCompiler._step_task_view,
    "reboot": PipelineStepCompiler._step_reboot,
    "screenshot": PipelineStepCompiler._step_screenshot,
    "log": PipelineStepCompiler._step_log,
    "launch_app": PipelineStepCompiler._step_launch_app,
    "close_app": PipelineStepCompiler._step_close_app,
    "tap": PipelineStepCompiler._step_tap,
    "swipe": PipelineStepCompiler._step_swipe,
    "wait": PipelineStepCompiler._step_wait,
    "wait_random": PipelineStepCompiler._step_wait_random,
    "wait_image": PipelineStepCompiler._step_wait_image,
    "wait_text": PipelineStepCompiler._step_wait_text,
    "click_text": PipelineStepCompiler._step_click_text,
    "recognize_execute": PipelineStepCompiler._step_recognize_execute,
    "wait_click": PipelineStepCompiler._step_wait_click,
    "match_offset_click": PipelineStepCompiler._step_match_offset_click,
    "color_marker_click": PipelineStepCompiler._step_color_marker_click,
    "smart_swipe": PipelineStepCompiler._step_smart_swipe,
    "maa": PipelineStepCompiler._step_maa,
    "yolo_detect": PipelineStepCompiler._step_yolo_detect,
}
