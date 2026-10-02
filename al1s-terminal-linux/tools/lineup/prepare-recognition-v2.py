"""Install the pinned lineup-only OCR and report icons after prepare-lineup-assets.py."""

import argparse
import hashlib
import shutil
from pathlib import Path
from urllib.request import urlopen

COMMIT = "dabcd4681ac990dc4361de26416d986abd80e4aa"
HASHES = {
    "det.onnx": "8c3b7ee97913a7942b8565669dc9acbe8846fbbaf4b63e1d7fdb339005574a33",
    "rec.onnx": "31fb844ce3a4aaf13e4bea62ae35f43bd9a509966061980c30db9b248c542a6b",
    "keys.txt": "d1979e9f794c464c0d2e0b70a7fe14dd978e9dc644c0e71f14158cdf8342af1b",
    "README.md": "6e235b3a1b62084c976c3ff0aa39ff8ad8c62e145f51431c3e545ce4115ba1f2",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assets", type=Path, help="lineup-v1 asset directory")
    args = parser.parse_args()
    ocr = args.assets / "ocr"
    ocr.mkdir(parents=True, exist_ok=True)
    base = f"https://raw.githubusercontent.com/MaaXYZ/MaaCommonAssets/{COMMIT}/"
    for name, digest in HASHES.items():
        with urlopen(base + "OCR/ppocr_v5/zh_cn/" + name, timeout=120) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"OCR asset hash mismatch: {name}")
        (ocr / name).write_bytes(data)
    with urlopen(base + "LICENSE", timeout=30) as response:
        (ocr / "LICENSE").write_bytes(response.read())
    fallback = args.assets / "ocr-fallback"
    fallback.mkdir(exist_ok=True)
    for name in ("keys.txt", "det.onnx", "LICENSE"):
        shutil.copyfile(ocr / name, fallback / name)
    with urlopen(base + "OCR/ppocr_v5/zh_cn-server/rec.onnx", timeout=120) as response:
        data = response.read()
    if (
        hashlib.sha256(data).hexdigest()
        != "26ed471dc5f3cdb631a4112d8c100e86eb5ec6f1e446de4f81ca550ca8be7065"
    ):
        raise ValueError("OCR fallback hash mismatch")
    (fallback / "rec.onnx").write_bytes(data)
    with urlopen(base + "OCR/ppocr_v5/zh_cn-server/README.md", timeout=30) as response:
        (fallback / "README.md").write_bytes(response.read())
    shutil.copytree(Path(__file__).parent / "roles", args.assets / "roles", dirs_exist_ok=True)


if __name__ == "__main__":
    main()
