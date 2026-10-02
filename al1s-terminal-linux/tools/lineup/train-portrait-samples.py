"""Build a bounded portrait bank from labelled training groups and their crops."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import cv2

from al1s_terminal.lineup.detector import PortraitDetector
from al1s_terminal.lineup.layout import assign_teams
from al1s_terminal.lineup.regions import portrait_row


def collect(cases, originals, assets):
    detector = PortraitDetector(assets)
    totals, per_kind, seen = Counter(), Counter(), set()
    samples = []
    # Each geometry gets representation before filling a class with near duplicates.
    order = [
        "original",
        "pair_portraits",
        "left_portraits",
        "right_portraits",
        "left_named",
        "right_named",
    ]
    for kind in order:
        for case in cases:
            if case["split"] != "train" or case["kind"] != kind:
                continue
            original = originals[case["source_index"]]
            if case["group"] != original["group"] or original["split"] != "train":
                raise ValueError("training_group_mismatch")
            image = cv2.imread(case["file"])
            row = portrait_row(image, detector, case["layout_hint"] in ("attack", "defense"))
            teams = assign_teams(row, case["layout_hint"], original["attack_side"])
            for side, ids in case["expected"].items():
                if len(teams.get(side, [])) != len(ids):
                    raise ValueError((case["file"], side, "training_layout_mismatch"))
                for sid, region in zip(ids, teams[side], strict=True):
                    family = (
                        "single_portraits"
                        if kind.endswith("_portraits") and kind != "pair_portraits"
                        else "named"
                        if kind.endswith("_named")
                        else kind
                    )
                    if isinstance(sid, list) or totals[sid] >= 8 or per_kind[sid, family] >= 2:
                        continue
                    x, y, w, h = region["box"]
                    data = cv2.imencode(".png", image[y : y + h, x : x + w])[1].tobytes()
                    digest = hashlib.sha256(data).hexdigest()
                    if (sid, digest) in seen:
                        continue
                    seen.add((sid, digest))
                    totals[sid] += 1
                    per_kind[sid, family] += 1
                    samples.append(
                        (
                            dict(
                                student_id=sid,
                                path=f"portrait-samples/{sid}-{totals[sid]}.png",
                                sha256=digest,
                                source_sha256=original["sha256"],
                                source_index=original["index"],
                                source_box=region["box"],
                                source_kind=kind,
                                source_group=original["group"],
                            ),
                            data,
                        )
                    )
    return samples


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("labels", "variants", "assets", "output"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    cv2.setNumThreads(2)
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    cases = json.loads(args.variants.read_text(encoding="utf-8"))
    samples = collect(cases, {e["index"]: e for e in labels}, args.assets)
    validation = sorted({e["group"] for e in labels if e["split"] == "validation"})
    assert not {meta["source_group"] for meta, _ in samples} & set(validation)
    for meta, data in samples:
        path = args.output / meta["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest = dict(
        schema_version=1,
        training="visually-labelled train groups only",
        max_per_student=8,
        validation_groups=validation,
        samples=[meta for meta, _ in samples],
    )
    (args.output / "portrait-samples.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        len(samples),
        "samples",
        len({meta["student_id"] for meta, _ in samples}),
        "students; no validation overlap",
    )


if __name__ == "__main__":
    main()
