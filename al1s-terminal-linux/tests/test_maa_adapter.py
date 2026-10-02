from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from al1s_terminal.execution.maa_adapter import MaaAdapter, MaaExecutionError


class _Connection:
    succeeded = True


class _Job:
    def wait(self) -> _Connection:
        return _Connection()


class _Controller:
    created: ClassVar[list[_Controller]] = []

    def __init__(self, _adb: str, address: str, screencap: int, input_method: int, _config: Any):
        self.address = address
        self.screencap = screencap
        self.input_method = input_method
        self.connected = True
        self.raw = False
        self.short_side: int | None = None
        self.created.append(self)

    def set_screenshot_use_raw_size(self, value: bool) -> None:
        self.raw = value

    def set_screenshot_target_short_side(self, value: int) -> None:
        self.short_side = value

    def post_connection(self) -> _Job:
        return _Job()


class _Toolkit:
    devices: ClassVar[list[object]] = []

    @classmethod
    def find_adb_devices(cls, _adb_path: str) -> list[object]:
        return cls.devices


def _adapter(tmp_path: Path, serial: str) -> MaaAdapter:
    adapter = MaaAdapter(
        SimpleNamespace(serial=serial, workdir=tmp_path),
        adb_path="/opt/android/adb",
        screenshot_mode="scaled",
        screenshot_short_side=900,
    )
    adapter._maa = {"Toolkit": _Toolkit, "AdbController": _Controller}
    adapter.available = True
    return adapter


def test_maa_adapter_selects_only_the_explicit_bound_serial(tmp_path: Path) -> None:
    _Controller.created.clear()
    _Toolkit.devices = [
        SimpleNamespace(
            adb_path="adb",
            address="phone-1",
            name="first",
            screencap_methods=7,
            input_methods=7,
            config={},
        ),
        SimpleNamespace(
            adb_path="adb",
            address="phone-2",
            name="second",
            screencap_methods=7,
            input_methods=7,
            config={},
        ),
    ]

    controller = _adapter(tmp_path, "phone-2")._ensure_controller()

    assert controller.address == "phone-2"
    assert controller.short_side == 900
    assert len(_Controller.created) == 1


def test_maa_adapter_never_falls_back_to_an_unbound_device(tmp_path: Path) -> None:
    _Controller.created.clear()
    _Toolkit.devices = [
        SimpleNamespace(
            adb_path="adb",
            address="phone-1",
            name="first",
            screencap_methods=7,
            input_methods=7,
            config={},
        )
    ]

    with pytest.raises(MaaExecutionError, match="phone-missing"):
        _adapter(tmp_path, "phone-missing")._ensure_controller()

    assert _Controller.created == []
