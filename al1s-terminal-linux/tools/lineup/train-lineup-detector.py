"""Synthetic portrait localization data; user's battle report stays a holdout."""

import json
import random
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[4]
ASSETS = ROOT / ".terminal-assets" / "lineup-v1"
DATA = ROOT / ".tool-cache" / "lineup-training"


def generate():
    rng = random.Random(3576)
    portraits = [
        Image.open(p).convert("RGB") for p in sorted((ASSETS / "portraits").glob("*.webp"))
    ]
    font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 15)
    for split, count in [("train", 500), ("val", 60)]:
        for sub in ["images", "labels"]:
            (DATA / sub / split).mkdir(parents=True, exist_ok=True)
        for index in range(count):
            w, h = 1024, 576
            image = Image.new("RGB", (w, h), (rng.randrange(170, 230),) * 3)
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle((30, 28, 995, 548), radius=15, fill=(245, 245, 245))
            draw.rectangle((530, 72, 990, 545), fill=(255, 222, 215))
            draw.text((460, 35), "战斗报告", font=font, fill=(60, 65, 70))
            boxes = []
            ybase = rng.randrange(415, 470)
            size = rng.randrange(35, 57)
            for side in range(2):
                for slot in range(6):
                    x = 80 + side * 500 + slot * 65 + rng.randrange(-5, 5)
                    y = ybase + rng.randrange(-4, 5)
                    picture = rng.choice(portraits).resize((size, size))
                    if rng.random() < 0.5:
                        arr = np.array(picture)
                        mat = np.float32([[1, -0.10, 0.10 * size], [0, 1, 0]])
                        picture = Image.fromarray(
                            cv2.warpAffine(arr, mat, (size, size), borderValue=(240, 240, 240))
                        )
                    image.paste(picture, (x, y))
                    draw.rounded_rectangle(
                        (x - 1, y - 1, x + size + 1, y + size + 1),
                        radius=4,
                        outline=(140, 160, 170),
                        width=2,
                    )
                    draw.text(
                        (x, y + size + 4),
                        rng.choice(["英美", "白子(泳裝)", "寧瑠", "花子", "椿", "八雲"]),
                        font=font,
                        fill=(55, 55, 55),
                    )
                    bh = rng.randrange(10, 210)
                    draw.rectangle(
                        (x + size // 3, y - 10 - bh, x + 2 * size // 3, y - 10),
                        fill=rng.choice([(0, 155, 250), (255, 60, 5)]),
                    )
                    draw.text(
                        (x, y - 32 - bh),
                        str(rng.randrange(1000, 900000)),
                        font=font,
                        fill=(65, 65, 65),
                    )
                    boxes.append((x, y, size, size))
            # Player avatars are detected too, but excluded by six-slot row grouping.
            for x in [225, 730]:
                image.paste(rng.choice(portraits).resize((64, 64)), (x, 100))
                boxes.append((x, 100, 64, 64))
                draw.text((x + 70, 110), "Lv.90 PLAYER", font=font, fill=(55, 55, 55))
            image.save(DATA / "images" / split / f"{index:04}.jpg", quality=rng.randrange(48, 96))
            labels = [
                f"0 {(x + bw / 2) / w:.6f} {(y + bh / 2) / h:.6f} {bw / w:.6f} {bh / h:.6f}"
                for x, y, bw, bh in boxes
            ]
            (DATA / "labels" / split / f"{index:04}.txt").write_text("\n".join(labels))
    (DATA / "data.yaml").write_text(
        f"path: {DATA.as_posix()}\ntrain: images/train\nval: images/val\nnames:\n  0: portrait\n"
    )


def main():
    from ultralytics import YOLO

    generate()
    model = YOLO("yolov8n.pt")
    model.train(
        data=str(DATA / "data.yaml"),
        epochs=35,
        imgsz=640,
        batch=24,
        device=0,
        workers=0,
        project=str(DATA),
        name="detector",
        exist_ok=True,
        degrees=4,
        translate=0.08,
        scale=0.25,
        shear=3,
        perspective=0.0002,
        fliplr=0,
        mosaic=0.3,
        hsv_h=0.01,
        hsv_s=0.2,
        hsv_v=0.2,
        plots=False,
        amp=True,
        patience=12,
    )
    best = YOLO(str(DATA / "detector/weights/best.pt"))
    path = best.export(format="onnx", imgsz=640, opset=12, simplify=True)
    (ASSETS / "detector.onnx").write_bytes(Path(path).read_bytes())
    print(json.dumps({"model": "lineup-portrait-yolov8n-v1", "validation": "synthetic-only"}))


if __name__ == "__main__":
    main()
