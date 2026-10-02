"""Bounded OCR of an editor crop; never connects to a phone controller."""

import asyncio
import json
import struct
import sys
from pathlib import Path

MAX_BYTES = 4 * 1024 * 1024
MAX_TEXTS = 200
MAX_TEXT_LENGTH = 1000


def parse_result(output: bytes) -> dict[str, object]:
    """Validate the child-process boundary before exposing its declared result type."""
    marker = b"AL1S_OCR_RESULT:"
    if marker not in output:
        raise RuntimeError("OCR result missing")
    try:
        value: object = json.loads(output.rsplit(marker, 1)[1])
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("Invalid OCR result") from exc
    if not isinstance(value, dict) or set(value) != {"texts"}:
        raise RuntimeError("Invalid OCR result")
    texts = value["texts"]
    if not isinstance(texts, list) or len(texts) > MAX_TEXTS or any(
        not isinstance(text, str) or len(text) > MAX_TEXT_LENGTH for text in texts
    ):
        raise RuntimeError("Invalid OCR texts")
    return {"texts": texts}


def validate_crop(body: bytes) -> None:
    if (
        not 33 <= len(body) <= MAX_BYTES
        or body[:8] != b"\x89PNG\r\n\x1a\n"
        or body[12:16] != b"IHDR"
    ):
        raise ValueError("Invalid OCR PNG")
    width, height = struct.unpack_from(">II", body, 16)
    if not (0 < width <= 4096 and 0 < height <= 4096 and width * height <= 4_194_304):
        raise ValueError("OCR crop too large")


async def recognize_crop(body: bytes, model: Path) -> dict[str, object]:
    validate_crop(body)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "al1s_terminal.interactive.ocr",
        str(model),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(body), 25)
        if process.returncode != 0:
            raise RuntimeError("OCR recognition failed")
        return parse_result(output)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def recognize(body: bytes, model: Path) -> dict[str, object]:
    import cv2
    import numpy as np
    from maa.controller import CustomController
    from maa.pipeline import JOCR, JRecognitionType
    from maa.resource import Resource
    from maa.tasker import Tasker

    validate_crop(body)
    image = cv2.imdecode(np.frombuffer(body, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Cannot decode OCR PNG")

    # Maa is an optional native SDK without portable typing; keep the dynamic base local.
    class ImageOnlyController(CustomController):  # type: ignore[misc]
        def connect(self) -> bool:
            return True

        def request_uuid(self) -> str:
            return "editor-crop-only"

    resource, controller, tasker = Resource(), ImageOnlyController(), Tasker()
    if not resource.post_ocr_model(model).wait().succeeded:
        raise RuntimeError("OCR model unavailable")
    controller.post_connection().wait()
    if not tasker.bind(resource, controller):
        raise RuntimeError("OCR initialization failed")
    job = tasker.post_recognition(JRecognitionType.OCR, JOCR(expected=[]), image).wait()
    detail = job.get()
    if detail is None:
        raise RuntimeError("OCR result unavailable")
    texts = [
        str(item.text)[:1000]
        for node in detail.nodes
        if node.recognition
        for item in node.recognition.all_results
        if getattr(item, "text", "")
    ][:200]
    return {"texts": texts}


if __name__ == "__main__":
    import os
    import tempfile

    crop = sys.stdin.buffer.read(MAX_BYTES + 1)
    model = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="al1s-ocr-") as directory:
        os.chdir(directory)
        result = recognize(crop, model)
        print("AL1S_OCR_RESULT:" + json.dumps(result, ensure_ascii=False))
