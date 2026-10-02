from pathlib import Path

import numpy as np
import pytest

from al1s_terminal.lineup.decision import decision
from al1s_terminal.lineup.layout import assign_teams, horizontal_row
from al1s_terminal.lineup.regions import merge_wrapped_names, split_joined_labels


def test_joined_ocr_labels_are_split_at_the_space_and_read_independently():
    image = np.full((40, 240, 3), 255, dtype=np.uint8)
    image[5:25, 8:103] = 25
    image[5:25, 133:231] = 25
    calls = []

    class Ocr:
        def read(self, crop):
            calls.append(crop)
            return ("花子(泳装)" if len(calls) == 1 else "白子(泳装)", 0.97)

    result = split_joined_labels(
        image, [dict(box=[0, 0, 240, 30], text="joined", confidence=0.95)], Ocr()
    )
    assert [item["text"] for item in result] == ["花子(泳装)", "白子(泳装)"]
    assert len(calls) == 2
    assert result[0]["box"][0] == 0
    assert result[1]["box"][0] >= 103
    assert result[1]["box"][0] + result[1]["box"][2] == 240


def row(count=6):
    return [dict(box=[i * 100, 100, 70, 70], kind="portrait") for i in range(count)]


def test_cropped_single_row_does_not_require_lower_half_or_split_at_image_center():
    source = row()
    actual = horizontal_row(list(reversed(source)), 6)
    assert actual == source
    assert assign_teams(actual, "auto", None) == {}
    assert assign_teams(actual, "defense", None) == {"defense": source}
    assert not horizontal_row(source[:5], 6)


def test_pair_roles_preserve_order_and_never_guess_without_icons():
    source = row(12)
    for item in source[6:]:
        item["box"][0] += 300
    assert assign_teams(source, "auto", None) == {}
    assert assign_teams(source, "right_attack", None) == dict(attack=source[6:], defense=source[:6])
    assert assign_teams(source, "auto", "left") == dict(attack=source[:6], defense=source[6:])


def test_wrapped_outfit_label_keeps_its_position_without_merging_other_names():
    names = [
        dict(box=[0, 0, 90, 20], text="寧瑠(兔女", confidence=0.96),
        dict(box=[100, 0, 40, 20], text="咲希", confidence=0.98),
        dict(box=[25, 23, 30, 20], text="郎)", confidence=0.94),
    ]
    merged = merge_wrapped_names(names)
    assert [x["text"] for x in merged] == ["寧瑠(兔女郎)", "咲希"]
    assert merged[0]["box"] == [0, 0, 90, 43]


@pytest.mark.parametrize(
    "image_id,ocr_id,score,margin,ocr,evidence",
    [
        (1, None, 0.95, 0.2, 0, "portrait_only"),
        (None, 1, 0, 0, 0.95, "text_only"),
        (1, 1, 0.9, 0.2, 0.95, "dual"),
        (1, 2, 0.99, 0.2, 0.99, "conflict"),
        (1, None, 0.6, 0.2, 0, "unresolved"),
        (None, 1, 0, 0, 0.7, "unresolved"),
    ],
)
def test_single_evidence_does_not_claim_dual_agreement(
    image_id, ocr_id, score, margin, ocr, evidence
):
    result = decision(
        dict(
            present=True,
            image_id=image_id,
            ocr_id=ocr_id,
            score=score,
            margin=margin,
            ocr_score=ocr,
        ),
        True,
    )
    assert result["evidence"] == evidence
    assert result["agreed"] == (evidence == "dual")


def test_terminal_and_platform_use_identical_confidence_policy():
    root = Path(__file__).resolve().parents[3]
    terminal = root / "terminals/al1s-terminal-linux/al1s_terminal/lineup/decision.py"
    backend = root / "backend/al1s-backend/al1s/lineup/decision.py"
    if not backend.exists():
        pytest.skip("Cross-project source not present")
    assert terminal.read_text(encoding="utf-8") == backend.read_text(encoding="utf-8")


def test_three_member_side_and_unseparated_pair_are_not_fabricated():
    left = row(3)
    right = row(6)
    for item in right:
        item["box"][0] += 900
    assert assign_teams(left + right, "right_attack", None) == dict(attack=right, defense=left)
    assert assign_teams(row(9), "right_attack", None) == {}
    assert horizontal_row(left, 6, partial=True) == left


def test_ocr_punctuation_does_not_create_a_seventh_student():
    names = [
        dict(box=[i * 100, 0, 60, 20], text="学生" + str(i), confidence=0.99) for i in range(6)
    ]
    # The fullwidth punctuation is the OCR input under test.
    names.append(dict(box=[590, 0, 10, 20], text="）", confidence=0.9))  # noqa: RUF001
    assert len(merge_wrapped_names(names)) == 6


def test_tight_strip_partial_detections_still_attempt_padding():
    from al1s_terminal.lineup.regions import portrait_row

    image = np.zeros((100, 1200, 3), dtype=np.uint8)

    class Detector:
        calls = 0

        def boxes(self, sample):
            self.calls += 1
            if self.calls == 1:
                return [[i * 100, 0, 70, 70] for i in range(5)]
            return [[120 + i * 100, 632, 70, 70] for i in range(6)]

    detector = Detector()
    assert len(portrait_row(image, detector, True)) == 6
    assert detector.calls == 2


def test_text_only_requests_the_stricter_ocr_fallback(monkeypatch):
    from types import SimpleNamespace

    from al1s_terminal.lineup.ocr import NameOcr

    calls = []

    class Ocr:
        def read_name(self, image, students, minimum):
            calls.append(minimum)
            return ("名字", 0.96, 1)

    ocr = NameOcr.__new__(NameOcr)
    ocr.primary = SimpleNamespace(read_name=lambda *args: ("名字", 0.85, 1))
    ocr.fallback = Ocr()
    assert ocr.read_name(None, [], min_confidence=0.90) == ("名字", 0.96, 1)
    assert calls == [0.90]
    calls.clear()
    assert ocr.read_name(None, []) == ("名字", 0.85, 1)
    assert calls == []


def test_reflow_uses_only_the_two_name_lines_and_bounds_bad_regions():
    from al1s_terminal.lineup.regions import reflow_wrapped_name

    image = np.full((70, 100, 3), 250, dtype=np.uint8)
    image[3:17, 4:34] = [30, 40, 50]
    image[28:47, 14:28] = [60, 70, 80]
    result = reflow_wrapped_name(image, [[0, 0, 40, 20], [10, 22, 24, 32]])
    assert result.shape[0] == 44
    assert np.any(np.all(result == [30, 40, 50], axis=2))
    assert np.any(np.all(result == [60, 70, 80], axis=2))
    assert reflow_wrapped_name(image, []) is None
    assert reflow_wrapped_name(image, [[90, 60, 5, 5], [90, 66, 5, 4]]) is None
