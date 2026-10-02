from pathlib import Path

import cv2
import numpy as np
import pytest

from al1s_terminal.lineup.executor import LineupExecutor
from al1s_terminal.lineup.matching import PortraitMatcher, name_id
from al1s_terminal.lineup.ocr import NameOcr, resolve_observations
from al1s_terminal.lineup.roles import RoleMatcher

ASSETS = Path(__file__).resolve().parents[1] / ".terminal-assets" / "lineup-v1"
ROLE_ASSETS = Path(__file__).resolve().parents[1] / "tools" / "lineup"


def test_ocr_exact_mapping_keeps_rare_characters_and_wrapped_outfits():
    students = [dict(id=10026, aliases=["寧瑠（兔女郎）"]), dict(id=20014, aliases=["咲希"])]  # noqa: RUF001
    assert name_id("寧瑠(兔女\n郎)", students) == 10026
    assert name_id("咲希", students) == 20014
    assert name_id("联希", students) is None
    assert name_id("寧(兔女郎)", students) is None


@pytest.mark.skipif(not (ASSETS / "ocr/keys.txt").exists(), reason="OCR assets not installed")
def test_packaged_ocr_dictionary_covers_rare_names():
    keys = (ASSETS / "ocr/keys.txt").read_text(encoding="utf-8")
    assert all(char in keys for char in "瑠咲響寧")


def test_ocr_retries_cannot_resolve_conflicting_students():
    assert resolve_observations([("A", 0.7, 1), ("A", 0.9, 1), ("?", 0.95, None)]) == (
        "A",
        0.9,
        1,
    )
    assert resolve_observations([("A", 0.9, 1), ("B", 0.8, 2)])[2] is None
    assert resolve_observations([("?", 0.99, None)])[2] is None


def test_larger_ocr_loads_only_when_needed_and_is_reused(monkeypatch):
    from types import SimpleNamespace

    loaded = []
    primary = SimpleNamespace(read_name=lambda *args: ("A", 0.95, 1))
    fallback = SimpleNamespace(read_name=lambda *args: ("A", 0.96, 1))

    def load(path):
        loaded.append(path)
        return primary if path == Path("mobile") else fallback

    monkeypatch.setattr("al1s_terminal.lineup.ocr.ImageOcr", load)
    ocr = NameOcr(Path("mobile"), Path("server"))
    assert ocr.read_name(None, []) == ("A", 0.95, 1)
    assert loaded == [Path("mobile")]
    primary.read_name = lambda *args: ("?", 0.6, None)
    assert ocr.read_name(None, []) == ("A", 0.96, 1)
    assert ocr.read_name(None, []) == ("A", 0.96, 1)
    assert loaded == [Path("mobile"), Path("server")]


def test_report_roles_follow_icons_not_position_and_reject_missing_or_duplicate():
    matcher = RoleMatcher(ROLE_ASSETS)

    def report(left, right):
        image = np.full((900, 2000, 3), 242, np.uint8)
        for x, role in ((85, left), (1130, right)):
            if role:
                icon = cv2.imread(str(ROLE_ASSETS / "roles" / f"{role}.png"))
                h, w = icon.shape[:2]
                image[200 : 200 + h, x : x + w] = icon
        return image

    for scale in (0.7, 1.0, 1.3):
        for pair, expected in ((("sword", "shield"), "left"), (("shield", "sword"), "right")):
            image = cv2.resize(report(*pair), None, fx=scale, fy=scale)
            assert matcher.attack_side(image) == expected
    assert matcher.attack_side(report("shield", "shield")) is None
    assert matcher.attack_side(report("sword", None)) is None
    assert matcher.attack_side(report(None, None)) is None
    assert matcher.attack_side(np.zeros((8192, 1, 3), np.uint8)) is None


def test_portrait_non_square_crop_preserves_identity_and_confidence(tmp_path):
    rng = np.random.default_rng(91)
    (tmp_path / "portraits").mkdir()
    students = [dict(id=1), dict(id=2)]
    originals = []
    for student in students:
        original = rng.integers(0, 256, size=(64, 64, 3), dtype=np.uint8)
        original = cv2.GaussianBlur(original, (5, 5), 0)
        cv2.imwrite(str(tmp_path / "portraits" / f"{student['id']}.webp"), original)
        originals.append(original)
    matcher = PortraitMatcher(tmp_path, students)
    result = matcher.match(cv2.resize(originals[0], (90, 78)))
    assert result[0] == 1 and result[1] >= 0.72 and result[2] >= 0.08


def test_lineup_requires_own_ocr_and_role_assets(tmp_path):
    for name in (
        "catalog.json",
        "portrait-samples.json",
        "detector.onnx",
        "ocr-fallback/det.onnx",
        "ocr-fallback/rec.onnx",
        "ocr-fallback/keys.txt",
        "ocr/det.onnx",
        "ocr/rec.onnx",
        "ocr/keys.txt",
    ):
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"placeholder")
    assert not LineupExecutor(tmp_path).available()
    (tmp_path / "roles").mkdir()
    for role in ("sword", "shield"):
        (tmp_path / "roles" / f"{role}.png").write_bytes(b"placeholder")
    assert LineupExecutor(tmp_path).available()
