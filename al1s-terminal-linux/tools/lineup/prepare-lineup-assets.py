"""Build a versioned local catalog from the user supplied IDs and SchaleDB assets."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / ".terminal-assets" / "lineup-v1"
SOURCE = Path("D:/Kei/Scripts/chroma/cache/students.min.json")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "portraits").mkdir(exist_ok=True)
    base = json.loads(SOURCE.read_text(encoding="utf-8"))
    with httpx.Client(timeout=40, follow_redirects=True) as client:
        response = client.get("https://schaledb.com/data/tw/students.min.json")
        response.raise_for_status()
        tw = response.json()

        def portrait(sid):
            path = OUT / "portraits" / f"{sid}.webp"
            if not path.exists():
                result = client.get(f"https://schaledb.com/images/student/icon/{sid}.webp")
                result.raise_for_status()
                path.write_bytes(result.content)
            with Image.open(path) as image:
                image.verify()
            return sid, hashlib.sha256(path.read_bytes()).hexdigest()

        hashes = dict(ThreadPoolExecutor(max_workers=4).map(portrait, base))
    students = [
        dict(
            id=int(sid),
            name=entry["Name"],
            aliases=list(
                dict.fromkeys([entry["Name"], tw.get(sid, {}).get("Name", entry["Name"])])
            ),
            portrait_sha256=hashes[sid],
        )
        for sid, entry in base.items()
    ]
    version = hashlib.sha256(json.dumps(students, sort_keys=True).encode()).hexdigest()[:16]
    catalog = dict(
        version=version,
        students=students,
        source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
    )
    (OUT / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(dict(version=version, count=len(students)), ensure_ascii=False))


if __name__ == "__main__":
    main()
