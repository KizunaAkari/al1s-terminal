import struct

import pytest

from al1s_terminal.interactive.scrcpy_process import ScrcpyProcessFactory
from al1s_terminal.interactive.scrcpy_wire import validate_control_packet


@pytest.mark.parametrize(
    "packet", [b"", b"000chost:version", b"shell:input tap 1 1", b"\x01" + b"x" * 20, b"\x00" * 65]
)
def test_arbitrary_adb_and_unknown_control_rejected(packet):
    with pytest.raises(ValueError):
        validate_control_packet(packet)


def test_key_and_single_touch_packets_are_bounded():
    validate_control_packet(struct.pack(">BBIII", 0, 0, 3, 0, 0))
    validate_control_packet(struct.pack(">BBQiiHHHII", 2, 0, 0, 20, 30, 1280, 720, 65535, 0, 0))
    with pytest.raises(ValueError):
        validate_control_packet(
            struct.pack(">BBQiiHHHII", 2, 0, 0, 1280, 30, 1280, 720, 65535, 0, 0)
        )
    with pytest.raises(ValueError):
        validate_control_packet(struct.pack(">BBIII", 0, 0, 3, 100, 0))


def test_missing_or_wrong_server_asset_not_advertised(tmp_path):
    assert not ScrcpyProcessFactory("adb", tmp_path / "missing", "0" * 64).available()
    assert not ScrcpyProcessFactory("adb", tmp_path, "0" * 64).available()
