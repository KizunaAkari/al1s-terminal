"""Materialize content-addressed image assets for one compilation bundle."""

import base64
import binascii
import hashlib
from pathlib import Path


class PipelineAssetWriter:
    def __init__(self, image_dir: Path) -> None:
        self.image_dir = image_dir

    def materialize(self, value: str) -> str:
        header = ""
        encoded = value
        if "," in value:
            header, encoded = value.split(",", 1)
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("template image is not valid base64") from exc
        if not data:
            raise ValueError("template image is empty")
        extension = ".png"
        lower_header = header.lower()
        if "image/jpeg" in lower_header or data.startswith(b"\xff\xd8\xff"):
            extension = ".jpg"
        elif "image/webp" in lower_header or data.startswith(b"RIFF"):
            extension = ".webp"
        digest = hashlib.sha256(data).hexdigest()
        name = f"{digest}{extension}"
        path = self.image_dir / name
        if not path.exists():
            path.write_bytes(data)
        return name
