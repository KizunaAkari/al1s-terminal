import asyncio
import struct

import pytest

from al1s_terminal.interactive.ocr import MAX_BYTES, parse_result, recognize_crop, validate_crop


def png(width=100, height=100):
    return (
        b"\x89PNG\r\n\x1a\n" + b"\0" * 4 + b"IHDR" + struct.pack(">II", width, height) + b"\0" * 9
    )


@pytest.mark.parametrize(
    "body",
    [b"bad", png(0), png(4097), png(4096, 4096), b"x" * (MAX_BYTES + 1)],
    ids=["bad", "zero", "wide", "pixels", "bytes"],
)
def test_rejects_invalid_or_excessive_crops(body):
    with pytest.raises(ValueError):
        validate_crop(body)


def test_timeout_kills_and_reaps_ocr_process(monkeypatch, tmp_path):
    class Process:
        returncode = None
        killed = False

        async def communicate(self, body):
            raise TimeoutError

        def kill(self):
            self.killed = True

        async def wait(self):
            self.returncode = -9

    process = Process()

    async def spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(TimeoutError):
        asyncio.run(recognize_crop(png(), tmp_path))
    assert process.killed and process.returncode == -9


def test_child_result_accepts_bounded_texts_after_native_log_output():
    assert parse_result(b'Native ready\nAL1S_OCR_RESULT:{"texts":["HELLO", "123"]}') == {
        "texts": ["HELLO", "123"]
    }


@pytest.mark.parametrize(
    "payload",
    [
        b"{}",
        b"[]",
        b'{"texts":"text"}',
        b'{"texts":[1]}',
        b'{"texts":[],"other":1}',
        b"not JSON",
        ('{"texts":[' + ",".join(['"x"'] * 201) + "]}").encode(),
        ('{"texts":["' + "x" * 1001 + '"]}').encode(),
    ],
)
def test_child_result_rejects_invalid_shape_or_limits(payload):
    with pytest.raises(RuntimeError, match="Invalid OCR"):
        parse_result(b"AL1S_OCR_RESULT:" + payload)
