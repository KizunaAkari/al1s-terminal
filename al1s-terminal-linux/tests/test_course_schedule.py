import sys
from types import SimpleNamespace

import pytest

from al1s_terminal.execution.course_schedule import CourseScheduleRunner


def test_course_block_coordinates_preserve_eight_slot_layout() -> None:
    assert CourseScheduleRunner._course_block_index((615, 330), (2400, 1080)) == 0
    assert CourseScheduleRunner._course_block_index((1200, 850), (2400, 1080)) == 7
    assert CourseScheduleRunner._course_block_index((1800, 850), (2400, 1080)) is None
    assert CourseScheduleRunner._course_block_click_point(7, (2400, 1080)) == (1205, 820)
    assert CourseScheduleRunner._course_block_click_point(7, (1200, 540)) == (602, 410)


def test_course_templates_load_before_navigation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(
        IMREAD_COLOR=1, imread=lambda path, _mode: path,
    ))
    templates, avatars = CourseScheduleRunner._load_templates({
        "target_avatars": [{"name": "student", "template_path": "student.png"}],
    })
    assert templates["first_region"].endswith("first_region_shale.png")
    assert avatars == [{"name": "student", "template_path": "student.png", "image": "student.png"}]


def test_course_missing_avatar_fails_early(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(
        IMREAD_COLOR=1,
        imread=lambda path, _mode: None if path == "missing.png" else path,
    ))
    with pytest.raises(RuntimeError, match="无法读取目标学生头像"):
        CourseScheduleRunner._load_templates({
            "target_avatars": [{"name": "student", "template_path": "missing.png"}],
        })


def test_target_phase_keeps_strongest_unused_student_per_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace())
    image = SimpleNamespace(shape=(1080, 2400, 3))
    first = SimpleNamespace(shape=(20, 30, 3))
    second = SimpleNamespace(shape=(20, 30, 3))
    seen_rois = []

    def matches(_image, template, _threshold, roi):
        seen_rois.append(roi)
        if template is first:
            return [
                {"x": 600, "y": 320, "width": 30, "height": 20, "score": 0.81},
                {"x": 1190, "y": 840, "width": 30, "height": 20, "score": 0.99},
            ]
        return [{"x": 600, "y": 320, "width": 30, "height": 20, "score": 0.91}]

    monkeypatch.setattr(CourseScheduleRunner, "_template_matches", staticmethod(matches))
    candidates = CourseScheduleRunner._target_blocks(
        image, 0,
        [{"name": "first", "image": first}, {"name": "second", "image": second}],
        {"x": 300, "y": 180, "width": 1800, "height": 800},
        {0: {7}},
    )

    assert candidates == [{"block": 0, "student": "second", "score": 0.91}]
    assert seen_rois == [(300, 180, 1800, 800)] * 2
