"""Course schedule image matching and execution.

This business action remains separate from the generic Maa adapter lifecycle.
"""

from __future__ import annotations

# User-facing Chinese diagnostics intentionally use full-width punctuation.
# ruff: noqa: RUF001
import base64
import time
from pathlib import Path
from typing import Any


class CourseScheduleRunner:
    @staticmethod
    def _load_templates(config: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Load navigation assets and configured avatar templates before touching the device."""
        import cv2

        asset_root = Path(__file__).resolve().parent / "assets" / "course_schedule"
        asset_names = {
            "location_select": "location_select.png",
            "first_region": "first_region_shale.png",
            "all_schedule": "all_schedule.png",
            "start": "start_schedule.png",
            "reward": "reward.png",
            "confirm": "confirm.png",
            "zero_ticket": "zero_ticket_digits.png",
            "close": "close.png",
            "affinity_popup": "affinity_popup.png",
        }
        templates: dict[str, Any] = {}
        for key, filename in asset_names.items():
            path = asset_root / filename
            template = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if template is None:
                raise RuntimeError(f"课程表运行资源缺失：{filename}")
            templates[key] = template

        avatar_templates: list[dict[str, Any]] = []
        for item in config.get("target_avatars", []):
            path = Path(str(item.get("template_path") or ""))
            template = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if template is None:
                raise RuntimeError(f"无法读取目标学生头像：{item.get('name') or path.name}")
            avatar_templates.append({**item, "image": template})
        if not avatar_templates:
            raise RuntimeError("课程表没有配置目标学生头像")
        return templates, avatar_templates

    @staticmethod
    def _scaled_avatar(template: Any, source_size: tuple[int, int], image: Any) -> Any:
        import cv2

        source_width, source_height = source_size
        image_height, image_width = image.shape[:2]
        scale_x = image_width / max(1, source_width)
        scale_y = image_height / max(1, source_height)
        if abs(scale_x - 1) < 0.02 and abs(scale_y - 1) < 0.02:
            return template
        return cv2.resize(
            template,
            (
                max(1, round(template.shape[1] * scale_x)),
                max(1, round(template.shape[0] * scale_y)),
            ),
            interpolation=cv2.INTER_CUBIC if scale_x > 1 or scale_y > 1 else cv2.INTER_AREA,
        )

    @classmethod
    def _target_blocks(
        cls,
        image: Any,
        region_index: int,
        avatar_templates: list[dict[str, Any]],
        avatar_roi: dict[str, Any],
        used_blocks: dict[int, set[int]],
    ) -> list[dict[str, Any]]:
        """Return the strongest unused student match per course block."""
        image_height, image_width = image.shape[:2]
        roi = (
            int(int(avatar_roi["x"]) * image_width / 2400),
            int(int(avatar_roi["y"]) * image_height / 1080),
            int(int(avatar_roi["width"]) * image_width / 2400),
            int(int(avatar_roi["height"]) * image_height / 1080),
        )
        by_block: dict[int, dict[str, Any]] = {}
        for avatar in avatar_templates:
            template = cls._scaled_avatar(
                avatar["image"],
                (int(avatar.get("screen_width", 2400)), int(avatar.get("screen_height", 1080))),
                image,
            )
            for match in cls._template_matches(
                image, template, float(avatar.get("threshold", 0.82)), roi
            ):
                center = (match["x"] + match["width"] / 2, match["y"] + match["height"] / 2)
                block = cls._course_block_index(center, (image_width, image_height))
                if block is None or block in used_blocks[region_index]:
                    continue
                candidate = {
                    "block": block,
                    "student": str(avatar.get("name") or "目标学生"),
                    "score": round(float(match["score"]), 4),
                }
                if block not in by_block or candidate["score"] > by_block[block]["score"]:
                    by_block[block] = candidate
        return sorted(by_block.values(), key=lambda item: item["score"], reverse=True)

    @staticmethod
    def _template_matches(
        image: Any,
        template: Any,
        threshold: float,
        roi: tuple[int, int, int, int] | None = None,
        max_results: int = 30,
    ) -> list[dict[str, Any]]:
        import cv2
        import numpy as np

        if image is None or template is None:
            return []
        image_height, image_width = image.shape[:2]
        offset_x = 0
        offset_y = 0
        sample = image
        if roi is not None:
            x, y, width, height = roi
            x = max(0, min(int(x), image_width - 1))
            y = max(0, min(int(y), image_height - 1))
            width = max(1, min(int(width), image_width - x))
            height = max(1, min(int(height), image_height - y))
            offset_x, offset_y = x, y
            sample = image[y : y + height, x : x + width]
        template_height, template_width = template.shape[:2]
        if template_width > sample.shape[1] or template_height > sample.shape[0]:
            return []
        scores = cv2.matchTemplate(sample, template, cv2.TM_CCOEFF_NORMED)
        ys, xs = np.where(scores >= float(threshold))
        raw = sorted(
            (
                {
                    "x": int(x) + offset_x,
                    "y": int(y) + offset_y,
                    "width": int(template_width),
                    "height": int(template_height),
                    "score": float(scores[y, x]),
                }
                for y, x in zip(ys, xs, strict=True)
            ),
            key=lambda item: item["score"],
            reverse=True,
        )
        selected: list[dict[str, Any]] = []
        for candidate in raw:
            center_x = candidate["x"] + candidate["width"] / 2
            center_y = candidate["y"] + candidate["height"] / 2
            if any(
                abs(center_x - (item["x"] + item["width"] / 2)) < template_width * 0.55
                and abs(center_y - (item["y"] + item["height"] / 2)) < template_height * 0.55
                for item in selected
            ):
                continue
            selected.append(candidate)
            if len(selected) >= max_results:
                break
        return selected

    @staticmethod
    def _course_block_index(
        point: tuple[float, float],
        screen_size: tuple[int, int],
    ) -> int | None:
        screen_width, screen_height = screen_size
        ref_x = point[0] * 2400 / max(1, screen_width)
        ref_y = point[1] * 1080 / max(1, screen_height)
        columns = ((330, 900), (900, 1510), (1510, 2115))
        rows = ((190, 480), (480, 750), (750, 1015))
        column = next(
            (index for index, (left, right) in enumerate(columns) if left <= ref_x < right), None
        )
        row = next(
            (index for index, (top, bottom) in enumerate(rows) if top <= ref_y < bottom), None
        )
        if column is None or row is None:
            return None
        block = row * 3 + column
        return block if block <= 7 else None

    @staticmethod
    def _course_block_click_point(
        block_index: int,
        screen_size: tuple[int, int],
    ) -> tuple[int, int]:
        screen_width, screen_height = screen_size
        columns = ((330, 900), (900, 1510), (1510, 2115))
        rows = ((190, 480), (480, 750), (750, 1015))
        row, column = divmod(block_index, 3)
        left, right = columns[column]
        top, _bottom = rows[row]
        ref_x = (left + right) / 2
        ref_y = top + 70
        return (
            round(ref_x * screen_width / 2400),
            round(ref_y * screen_height / 1080),
        )

    @classmethod
    def run(cls, device: Any, config: dict[str, Any]) -> dict[str, Any]:
        import cv2
        import numpy as np

        templates, avatar_templates = cls._load_templates(config)

        region_count = max(1, min(int(config.get("region_count", 11)), 20))
        ticket_limit = max(1, min(int(config.get("ticket_limit", 7)), 20))
        action_timeout = max(5.0, min(float(config.get("action_timeout_seconds", 25)), 120.0))
        next_point = config.get("next_region_point") or {"x": 2260, "y": 545}
        avatar_roi = config.get("avatar_search_roi") or {
            "x": 300,
            "y": 180,
            "width": 1800,
            "height": 800,
        }
        used_blocks: dict[int, set[int]] = {index: set() for index in range(region_count)}
        trace: list[dict[str, Any]] = []
        executed = 0

        def screenshot() -> Any:
            capture = device.screenshot()
            raw = base64.b64decode(str(capture.get("data_base64") or ""))
            image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise RuntimeError("课程表无法解析手机截图")
            return image

        def matches(
            image: Any,
            template: Any,
            threshold: float = 0.82,
            roi: tuple[int, int, int, int] | None = None,
        ) -> list[dict[str, Any]]:
            return cls._template_matches(image, template, threshold, roi)

        def match_internal(image: Any, key: str, threshold: float = 0.8) -> dict[str, Any] | None:
            roi = None
            if key == "close":
                image_height, image_width = image.shape[:2]
                roi = (
                    int(1800 * image_width / 2400),
                    0,
                    int(450 * image_width / 2400),
                    int(210 * image_height / 1080),
                )
            elif key == "zero_ticket":
                image_height, image_width = image.shape[:2]
                roi = (
                    int(900 * image_width / 2400),
                    int(90 * image_height / 1080),
                    int(650 * image_width / 2400),
                    int(150 * image_height / 1080),
                )
            found = matches(image, templates[key], threshold, roi)
            return found[0] if found else None

        def tap_match(match: dict[str, Any]) -> None:
            device.tap(
                int(match["x"] + match["width"] / 2),
                int(match["y"] + match["height"] / 2),
            )

        def scaled_point(point: dict[str, Any], image: Any) -> tuple[int, int]:
            height, width = image.shape[:2]
            return (
                round(int(point["x"]) * width / 2400),
                round(int(point["y"]) * height / 1080),
            )

        def wait_for(
            key: str, timeout: float, *, dismiss_affinity: bool = False
        ) -> tuple[Any, dict[str, Any]]:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                image = screenshot()
                found = match_internal(image, key)
                if found:
                    return image, found
                if dismiss_affinity:
                    popup = match_internal(image, "affinity_popup", 0.78)
                    if popup:
                        x, y = scaled_point({"x": 1676, "y": 782}, image)
                        device.tap(x, y)
                        time.sleep(0.6)
                        continue
                time.sleep(0.35)
            raise RuntimeError(f"等待课程表界面超时：{key}")

        def close_modal() -> None:
            image = screenshot()
            close = match_internal(image, "close", 0.78)
            if close:
                tap_match(close)
                time.sleep(0.6)

        def open_modal() -> Any:
            image = screenshot()
            if match_internal(image, "close", 0.78):
                return image
            button = match_internal(image, "all_schedule", 0.8)
            if not button:
                image, button = wait_for("all_schedule", action_timeout)
            tap_match(button)
            image, _close = wait_for("close", action_timeout)
            return image

        def enter_first_region() -> Any:
            deadline = time.monotonic() + action_timeout
            selector_seen = False
            image = screenshot()
            first_region = None
            while time.monotonic() < deadline:
                if match_internal(image, "all_schedule", 0.8):
                    return image
                first_region = match_internal(image, "first_region", 0.78)
                if first_region:
                    break
                selector_seen = selector_seen or bool(
                    match_internal(image, "location_select", 0.72)
                )
                time.sleep(0.35)
                image = screenshot()
            if not first_region:
                if selector_seen:
                    raise RuntimeError(
                        "课程表巡回启动失败：地区选择页已加载，但未识别到首个地区“沙勒业务区”"
                    )
                raise RuntimeError(
                    "课程表巡回启动失败：等待地区选择页或地区详情页加载超时；"
                    "请确认已从游戏主界面进入“日程”"
                )
            tap_match(first_region)
            x = int(first_region["x"] + first_region["width"] / 2)
            y = int(first_region["y"] + first_region["height"] / 2)
            image, _button = wait_for("all_schedule", action_timeout)
            trace.append(
                {
                    "phase": "navigation",
                    "region": 1,
                    "name": "沙勒业务区",
                    "status": "entered",
                    "score": round(float(first_region["score"]), 4),
                    "click": {"x": x, "y": y},
                }
            )
            return image

        def ticket_is_zero(image: Any) -> bool:
            # Match only the current ticket digit “0”. Shared text such as
            # “持有券” and “/7” made 6/7 score 95.9% against the former crop.
            return match_internal(image, "zero_ticket", 0.95) is not None

        def execute_block(region_index: int, block: int, phase: str, student: str = "") -> bool:
            nonlocal executed
            image = screenshot()
            width, height = image.shape[1], image.shape[0]
            x, y = cls._course_block_click_point(block, (width, height))
            device.tap(x, y)
            try:
                _start_image, start = wait_for("start", 4.0)
            except RuntimeError:
                used_blocks[region_index].add(block)
                trace.append(
                    {
                        "phase": phase,
                        "region": region_index + 1,
                        "block": block + 1,
                        "status": "unavailable",
                    }
                )
                return False
            tap_match(start)
            _reward_image, _reward = wait_for("reward", action_timeout, dismiss_affinity=True)
            _confirm_image, confirm = wait_for("confirm", action_timeout, dismiss_affinity=True)
            tap_match(confirm)
            wait_for("close", action_timeout, dismiss_affinity=True)
            used_blocks[region_index].add(block)
            executed += 1
            trace.append(
                {
                    "phase": phase,
                    "region": region_index + 1,
                    "block": block + 1,
                    "student": student or None,
                    "status": "executed",
                    "tickets_used": executed,
                }
            )
            return True

        def next_region(image: Any) -> None:
            x, y = scaled_point(next_point, image)
            device.tap(x, y)
            time.sleep(0.8)

        # The custom action owns navigation from the location selector onward.
        # Callers only need to enter the Schedule feature itself.
        enter_first_region()

        # First pass: search every region for configured students.
        for region_index in range(region_count):
            image = open_modal()
            while executed < ticket_limit and not ticket_is_zero(image):
                candidates = cls._target_blocks(
                    image, region_index, avatar_templates, avatar_roi, used_blocks
                )
                if not candidates:
                    break
                candidate = candidates[0]
                execute_block(
                    region_index,
                    int(candidate["block"]),
                    "target",
                    str(candidate["student"]),
                )
                image = screenshot()
            if ticket_is_zero(image):
                close_modal()
                return {
                    "success": True,
                    "tickets_used": executed,
                    "completed_phase": "target",
                    "trace": trace,
                }
            if executed >= ticket_limit:
                close_modal()
                raise RuntimeError(
                    f"课程表已执行 {executed} 次并达到票券安全上限，"
                    "但画面仍未识别到 0/7，拒绝提前汇报成功"
                )
            close_modal()
            if region_index < region_count - 1:
                next_region(image)

        # Second pass: wrap to the first region and consume remaining tickets from
        # unused third-row blocks. Each region has at least block 7; some have 8.
        next_region(screenshot())
        for region_index in range(region_count):
            image = open_modal()
            if ticket_is_zero(image):
                close_modal()
                return {
                    "success": True,
                    "tickets_used": executed,
                    "completed_phase": "fallback",
                    "trace": trace,
                }
            for block in (6, 7):
                if block in used_blocks[region_index]:
                    continue
                execute_block(region_index, block, "fallback")
                image = screenshot()
                if ticket_is_zero(image):
                    close_modal()
                    return {
                        "success": True,
                        "tickets_used": executed,
                        "completed_phase": "fallback",
                        "trace": trace,
                    }
                if executed >= ticket_limit:
                    close_modal()
                    raise RuntimeError(
                        f"课程表已执行 {executed} 次并达到票券安全上限，"
                        "但画面仍未识别到 0/7，拒绝提前汇报成功"
                    )
            close_modal()
            if region_index < region_count - 1:
                next_region(image)

        raise RuntimeError(
            f"课程表已遍历 {region_count} 个地区，但仍未检测到票券归零；本轮成功执行 {executed} 次"
        )

