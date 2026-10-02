"""Bounded scrcpy 3.3.4 input subset; never accept ADB services or shell commands."""

import struct


def validate_control_packet(packet: bytes) -> None:
    if not packet:
        raise ValueError("empty control packet")
    if packet[0] == 0 and len(packet) == 14:
        _, action, key, repeat, meta = struct.unpack(">BBIII", packet)
        if (
            action in (0, 1)
            and key in (3, 4, 24, 25, 26, 82, 187, 223, 224)
            and repeat == 0
            and meta == 0
        ):
            return
    if packet[0] == 2 and len(packet) == 32:
        _, action, pointer, x, y, width, height, pressure, action_button, buttons = struct.unpack(
            ">BBQiiHHHII", packet
        )
        if (
            action in (0, 1, 2)
            and pointer == 0
            and 0 < width <= 8192
            and 0 < height <= 8192
            and 0 <= x < width
            and 0 <= y < height
            and pressure in (0, 65535)
            and action_button == 0
            and buttons == 0
        ):
            return
    raise ValueError("unsupported or invalid control packet")
