from __future__ import annotations

import base64
from pathlib import Path

import pytest

from al1s_terminal.execution import pipeline_assets
from al1s_terminal.execution.pipeline_assets import PipelineAssetWriter


def test_materialize_decodes_duplicate_uri_once_and_rebuilds_removed_output(
    tmp_path: Path, monkeypatch
) -> None:
    data = b"same-image"
    value = f"data:image/png;base64,{base64.b64encode(data).decode('ascii')}"
    calls = 0
    original_decode = pipeline_assets.base64.b64decode

    def counted_decode(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_decode(*args, **kwargs)

    monkeypatch.setattr(pipeline_assets.base64, "b64decode", counted_decode)
    writer = PipelineAssetWriter(tmp_path)

    first = writer.materialize(value)
    second = writer.materialize(value)
    assert first == second
    assert (tmp_path / first).read_bytes() == data
    assert calls == 1

    (tmp_path / first).unlink()
    rebuilt = writer.materialize(value)
    assert rebuilt == first
    assert (tmp_path / rebuilt).read_bytes() == data
    assert calls == 2


def test_materialize_keeps_mime_extensions_distinct_and_does_not_cache_errors(
    tmp_path: Path, monkeypatch
) -> None:
    data = b"same-image"
    encoded = base64.b64encode(data).decode("ascii")
    png = f"data:image/png;base64,{encoded}"
    jpeg = f"data:image/jpeg;base64,{encoded}"
    calls = 0
    original_decode = pipeline_assets.base64.b64decode

    def counted_decode(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_decode(*args, **kwargs)

    monkeypatch.setattr(pipeline_assets.base64, "b64decode", counted_decode)
    writer = PipelineAssetWriter(tmp_path)

    png_name = writer.materialize(png)
    jpeg_name = writer.materialize(jpeg)
    assert png_name.endswith(".png")
    assert jpeg_name.endswith(".jpg")
    assert png_name != jpeg_name

    invalid = "data:image/png;base64,not-base64"
    with pytest.raises(ValueError, match="not valid base64"):
        writer.materialize(invalid)
    with pytest.raises(ValueError, match="not valid base64"):
        writer.materialize(invalid)
    assert calls == 4
