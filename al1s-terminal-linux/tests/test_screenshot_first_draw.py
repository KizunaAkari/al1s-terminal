import asyncio
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from al1s_terminal.interactive.screenshot import capture_png


def png(color):
    output = BytesIO()
    Image.new("RGB", (20, 30), color).save(output, format="PNG")
    return output.getvalue()


def test_black_transition_frame_is_retried_before_returning_native_original(monkeypatch):
    blank, ready = png("black"), png("white")
    calls = []

    async def scenario():
        async def spawn(*args, **kwargs):
            body = blank if not calls else ready
            calls.append(args)
            reader = asyncio.StreamReader()
            reader.feed_data(body)
            reader.feed_eof()

            async def wait():
                return 0

            return SimpleNamespace(stdout=reader, returncode=0, wait=wait)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        result = await capture_png("adb", "phone")
        assert result == ready
        assert len(calls) == 2

    asyncio.run(scenario())
