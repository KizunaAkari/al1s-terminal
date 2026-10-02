from __future__ import annotations

import math
import random
import time
from collections.abc import Callable
from typing import Any

from al1s_terminal.execution.course_schedule import CourseScheduleRunner
from al1s_terminal.execution.maa_pipeline import MaaPipelineCompiler
from al1s_terminal.execution.maa_runtime_types import MaaExecutionError, YoloAdapter


def _action_classes(
    maa: dict[str, Any],
    collector: dict[str, list[dict[str, Any]]],
    *,
    device: Any,
    parse_json: Callable[..., Any],
    capture_evidence: Callable[[], Any],
) -> tuple[Any, ...]:
    CustomAction: Any = maa["CustomAction"]

    class StartSessionAction(CustomAction):  # type: ignore[misc]
        def run(self, _context: Any, argv: Any) -> bool:
            result = device.start_session()
            collector["custom_actions"].append(
                {
                    "node": argv.node_name,
                    "action": "start",
                    "result": result,
                }
            )
            return True

    class FeedbackAction(CustomAction):  # type: ignore[misc]
        def run(self, _context: Any, argv: Any) -> bool:
            config = parse_json(argv.custom_action_param)
            feedback = {
                "node": argv.node_name,
                "subject": str(config.get("subject") or ""),
                "message": str(config.get("message") or ""),
                "capture": capture_evidence(),
            }
            collector["feedback"].append(feedback)
            return True

    class CourseScheduleAction(CustomAction):  # type: ignore[misc]
        def run(self, _context: Any, argv: Any) -> bool:
            config = parse_json(argv.custom_action_param)
            try:
                result = CourseScheduleRunner.run(device, config)
            except Exception as exc:
                result = {"success": False, "error": str(exc)}
            collector["custom_actions"].append(
                {
                    "node": argv.node_name,
                    "action": "course_schedule",
                    "result": result,
                }
            )
            return bool(result.get("success"))

    class ScreenshotAction(CustomAction):  # type: ignore[misc]
        def run(self, _context: Any, argv: Any) -> bool:
            collector["custom_actions"].append(
                {
                    "node": argv.node_name,
                    "action": "screenshot",
                    "result": capture_evidence(),
                }
            )
            return True

    class RandomWaitAction(CustomAction):  # type: ignore[misc]
        def run(self, context: Any, argv: Any) -> bool:
            config = parse_json(argv.custom_action_param)
            lower = float(config.get("min_seconds", -1))
            upper = float(config.get("max_seconds", -1))
            valid_range = math.isfinite(lower) and math.isfinite(upper)
            if not valid_range or not 0 <= lower <= upper <= 14_400:
                return False
            duration = random.uniform(lower, upper)
            deadline = time.monotonic() + duration
            while time.monotonic() < deadline:
                if context.tasker.stopping:
                    return False
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
            return not context.tasker.stopping

    class ConditionalSkipCaptureAction(CustomAction):  # type: ignore[misc]
        def run(self, _context: Any, argv: Any) -> bool:
            config = parse_json(argv.custom_action_param)
            collector["conditional_skip_captures"].append(
                {
                    "node": argv.node_name,
                    "step_index": config.get("step_index"),
                    "mode": str(config.get("mode") or "numeric"),
                    "capture": capture_evidence(),
                }
            )
            return True

    class MatchLoopLimitAction(CustomAction):  # type: ignore[misc]
        def run(self, _context: Any, argv: Any) -> bool:
            config = parse_json(argv.custom_action_param)
            collector["custom_actions"].append(
                {
                    "node": argv.node_name,
                    "action": "match_loop_limit",
                    "result": config,
                }
            )
            return False

    class FailureRetryLimitAction(CustomAction):  # type: ignore[misc]
        def run(self, _context: Any, argv: Any) -> bool:
            config = parse_json(argv.custom_action_param)
            collector["custom_actions"].append(
                {
                    "node": argv.node_name,
                    "action": "failure_retry_limit",
                    "result": config,
                }
            )
            return False

    class FailureRetryProcessAction(CustomAction):  # type: ignore[misc]
        def run(self, context: Any, argv: Any) -> bool:
            config = parse_json(argv.custom_action_param)
            result: dict[str, Any] = {
                "target_step_index": config.get("target_step_index"),
                "process_script_name": str(config.get("process_script_name") or ""),
                "success": False,
            }
            try:
                entry = str(config.get("entry") or "")
                pipeline = config.get("pipeline")
                if not entry or not isinstance(pipeline, dict):
                    raise ValueError("failure retry process pipeline is incomplete")
                detail = context.run_task(entry, pipeline)
                result["success"] = bool(detail is not None and detail.status.succeeded)
                if detail is not None:
                    result["task_id"] = detail.task_id
                    result["nodes"] = [
                        {
                            "name": node.name,
                            "completed": bool(node.completed),
                        }
                        for node in detail.nodes
                    ]
            except Exception as exc:
                result["error"] = str(exc)
            collector["custom_actions"].append(
                {
                    "node": argv.node_name,
                    "action": "failure_retry_process",
                    "result": result,
                }
            )
            return bool(result["success"])

    return (
        StartSessionAction(),
        FeedbackAction(),
        CourseScheduleAction(),
        ScreenshotAction(),
        ConditionalSkipCaptureAction(),
        MatchLoopLimitAction(),
        FailureRetryProcessAction(),
        FailureRetryLimitAction(),
        RandomWaitAction(),
    )


def _recognition_classes(
    maa: dict[str, Any],
    collector: dict[str, list[dict[str, Any]]],
    *,
    yolo: YoloAdapter,
    parse_json: Callable[..., Any],
    first_number: Callable[..., Any],
    rect_tuple: Callable[..., Any],
    find_color_markers: Callable[..., Any],
) -> tuple[Any, ...]:
    CustomRecognition: Any = maa["CustomRecognition"]
    JOCR: Any = maa["JOCR"]
    JRecognitionType: Any = maa["JRecognitionType"]
    JTemplateMatch: Any = maa["JTemplateMatch"]
    color_marker_misses: dict[str, int] = {}

    class NumericCompareRecognition(CustomRecognition):  # type: ignore[misc]
        def analyze(self, context: Any, argv: Any) -> Any:
            config = parse_json(argv.custom_recognition_param)
            region = config.get("region", [argv.roi.x, argv.roi.y, argv.roi.w, argv.roi.h])
            attempt: dict[str, Any] = {
                "node": argv.node_name,
                "mode": "numeric",
                "operator": config.get("operator"),
                "threshold": config.get("value"),
                "region": region,
                "hit": False,
            }
            try:
                recognition = context.run_recognition_direct(
                    JRecognitionType.OCR,
                    JOCR(expected=[], roi=tuple(int(value) for value in region)),
                    argv.image,
                )
                texts = []
                if recognition is not None:
                    texts = [
                        str(getattr(item, "text", ""))
                        for item in recognition.all_results
                        if str(getattr(item, "text", ""))
                    ]
                number = first_number(texts)
                attempt.update({"texts": texts, "recognized_value": number})
                if number is None:
                    attempt["fallback"] = "execute_step"
                    collector["numeric_conditions"].append(attempt)
                    return None
                threshold = float(config["value"])
                operator = str(config["operator"])
                hit = number > threshold if operator == "gt" else number < threshold
                attempt["hit"] = hit
                collector["numeric_conditions"].append(attempt)
                if not hit:
                    return None
                return CustomRecognition.AnalyzeResult(
                    box=tuple(int(value) for value in region),
                    detail=attempt,
                )
            except Exception as exc:
                attempt.update({"error": str(exc), "fallback": "execute_step"})
                collector["numeric_conditions"].append(attempt)
                return None

    class MatchOffsetRecognition(CustomRecognition):  # type: ignore[misc]
        def analyze(self, context: Any, argv: Any) -> Any:
            config = parse_json(argv.custom_recognition_param)
            preferred_index = max(0, int(config.get("preferred_index", 0)))
            region = (
                int(argv.roi.x),
                int(argv.roi.y),
                int(argv.roi.w),
                int(argv.roi.h),
            )
            recognition = context.run_recognition_direct(
                JRecognitionType.TemplateMatch,
                JTemplateMatch(
                    template=[str(config["template"])],
                    roi=region,
                    threshold=[float(config.get("threshold", 0.85))],
                    order_by=str(config.get("order_by") or "Vertical"),
                    index=0,
                ),
                argv.image,
            )
            if recognition is None or not recognition.hit:
                return None
            results = recognition.filtered_results or recognition.all_results
            if not results:
                return None
            selected_index = min(preferred_index, len(results) - 1)
            selected = results[selected_index]
            return CustomRecognition.AnalyzeResult(
                box=rect_tuple(selected.box),
                detail={
                    "matches": len(results),
                    "preferred_index": preferred_index,
                    "selected_index": selected_index,
                    "score": float(selected.score),
                },
            )

    class ColorMarkerRecognition(CustomRecognition):  # type: ignore[misc]
        def analyze(self, _context: Any, argv: Any) -> Any:
            config = parse_json(argv.custom_recognition_param)
            state_key = str(config.get("state_key") or argv.node_name)
            mode = str(config.get("mode") or "target")
            if mode == "absent":
                misses = color_marker_misses.get(state_key, 0)
                if misses < int(config.get("absence_checks", 3)):
                    return None
                return CustomRecognition.AnalyzeResult(
                    box=(0, 0, 1, 1),
                    detail={"absence_checks": misses, "stable_absence": True},
                )

            roi = None
            if argv.roi.w > 0 and argv.roi.h > 0:
                roi = (int(argv.roi.x), int(argv.roi.y), int(argv.roi.w), int(argv.roi.h))
            markers = find_color_markers(argv.image, config, roi)
            if not markers:
                misses = color_marker_misses.get(state_key, 0) + 1
                color_marker_misses[state_key] = misses
                collector["color_markers"].append(
                    {
                        "node": argv.node_name,
                        "matches": 0,
                        "absence_checks": misses,
                    }
                )
                return None

            color_marker_misses[state_key] = 0
            preferred_index = max(0, int(config.get("preferred_index", 0)))
            selected_index = min(preferred_index, len(markers) - 1)
            selected = markers[selected_index]
            detail = {
                "matches": len(markers),
                "preferred_index": preferred_index,
                "selected_index": selected_index,
                "selected": selected,
            }
            collector["color_markers"].append({"node": argv.node_name, **detail})
            target_x, target_y = selected["target"]
            return CustomRecognition.AnalyzeResult(
                box=(int(target_x), int(target_y), 1, 1),
                detail=detail,
            )

    class YoloRecognition(CustomRecognition):  # type: ignore[misc]
        def analyze(self, _context: Any, argv: Any) -> Any:
            config = parse_json(argv.custom_recognition_param)
            image = argv.image
            offset_x = 0
            offset_y = 0
            if argv.roi.w > 0 and argv.roi.h > 0:
                offset_x, offset_y = int(argv.roi.x), int(argv.roi.y)
                image = image[
                    offset_y : offset_y + int(argv.roi.h),
                    offset_x : offset_x + int(argv.roi.w),
                ]
            result = yolo.detect(image, config)
            if offset_x or offset_y:
                for detection in result.get("detections", []):
                    box = detection.get("box") or detection.get("xywh")
                    if isinstance(box, (list, tuple)) and len(box) == 4:
                        detection["box"] = [
                            box[0] + offset_x,
                            box[1] + offset_y,
                            box[2],
                            box[3],
                        ]
                    xyxy = detection.get("xyxy")
                    if isinstance(xyxy, (list, tuple)) and len(xyxy) == 4:
                        detection["xyxy"] = [
                            xyxy[0] + offset_x,
                            xyxy[1] + offset_y,
                            xyxy[2] + offset_x,
                            xyxy[3] + offset_y,
                        ]
            collector["yolo"].append({"node": argv.node_name, **result})
            detections = result.get("detections", [])
            if not detections:
                return None
            selected = detections[0]
            box = selected.get("box") or selected.get("xywh")
            if box is None and selected.get("xyxy") is not None:
                x1, y1, x2, y2 = selected["xyxy"]
                box = [x1, y1, x2 - x1, y2 - y1]
            if not isinstance(box, (list, tuple)) or len(box) != 4:
                raise ValueError("YOLO detection must provide box/xywh/xyxy")
            return CustomRecognition.AnalyzeResult(
                box=tuple(round(value) for value in box),
                detail=result,
            )

    return (
        NumericCompareRecognition(),
        MatchOffsetRecognition(),
        ColorMarkerRecognition(),
        YoloRecognition(),
    )


def register_custom_extensions(
    resource: Any,
    collector: dict[str, list[dict[str, Any]]],
    *,
    device: Any,
    yolo: YoloAdapter,
    maa: dict[str, Any],
    parse_json: Callable[..., Any],
    first_number: Callable[..., Any],
    rect_tuple: Callable[..., Any],
    find_color_markers: Callable[..., Any],
) -> None:
    from al1s_terminal.execution.capture_budget import CaptureBudget

    capture_evidence = CaptureBudget(getattr(device, "capture_evidence", device.screenshot))
    actions = _action_classes(
        maa,
        collector,
        device=device,
        parse_json=parse_json,
        capture_evidence=capture_evidence,
    )
    recognitions = _recognition_classes(
        maa,
        collector,
        yolo=yolo,
        parse_json=parse_json,
        first_number=first_number,
        rect_tuple=rect_tuple,
        find_color_markers=find_color_markers,
    )

    registrations = (
        resource.register_custom_action(MaaPipelineCompiler.START_ACTION, actions[0]),
        resource.register_custom_action(MaaPipelineCompiler.FEEDBACK_ACTION, actions[1]),
        resource.register_custom_action(
            MaaPipelineCompiler.COURSE_SCHEDULE_ACTION,
            actions[2],
        ),
        resource.register_custom_action(MaaPipelineCompiler.SCREENSHOT_ACTION, actions[3]),
        resource.register_custom_action(MaaPipelineCompiler.RANDOM_WAIT_ACTION, actions[8]),
        resource.register_custom_action(
            MaaPipelineCompiler.CONDITIONAL_SKIP_CAPTURE_ACTION,
            actions[4],
        ),
        resource.register_custom_action(
            MaaPipelineCompiler.MATCH_LOOP_LIMIT_ACTION,
            actions[5],
        ),
        resource.register_custom_action(
            MaaPipelineCompiler.FAILURE_RETRY_PROCESS_ACTION,
            actions[6],
        ),
        resource.register_custom_action(
            MaaPipelineCompiler.FAILURE_RETRY_LIMIT_ACTION,
            actions[7],
        ),
        resource.register_custom_recognition(
            MaaPipelineCompiler.NUMERIC_RECOGNITION,
            recognitions[0],
        ),
        resource.register_custom_recognition(
            MaaPipelineCompiler.MATCH_OFFSET_RECOGNITION,
            recognitions[1],
        ),
        resource.register_custom_recognition(
            MaaPipelineCompiler.COLOR_MARKER_RECOGNITION,
            recognitions[2],
        ),
        resource.register_custom_recognition(
            MaaPipelineCompiler.YOLO_RECOGNITION,
            recognitions[3],
        ),
    )
    if not all(registrations):
        raise MaaExecutionError("MaaFramework custom extension registration failed")
