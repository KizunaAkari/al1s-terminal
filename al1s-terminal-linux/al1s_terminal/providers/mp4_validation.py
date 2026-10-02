from pathlib import Path


def validate_recording_container(path: Path) -> None:
    """Reject unfinished screenrecord output without loading video data into RAM.

    This is a container completeness check, not a codec/decode guarantee.
    """
    required = {b"ftyp", b"moov", b"mdat"}
    end = path.stat().st_size
    with path.open("rb") as stream:
        offset = 0
        for _ in range(4096):
            if offset == end:
                break
            header = stream.read(8)
            if len(header) != 8:
                raise ValueError("recording MP4 has a truncated box header")
            size = int.from_bytes(header[:4], "big")
            header_size = 8
            if size == 1:
                extended = stream.read(8)
                if len(extended) != 8:
                    raise ValueError("recording MP4 has a truncated extended size")
                size = int.from_bytes(extended, "big")
                header_size = 16
            elif size == 0:
                size = end - offset
            if size <= header_size or offset + size > end:
                raise ValueError("recording MP4 has an incomplete box")
            required.discard(header[4:8])
            offset += size
            stream.seek(offset)
        else:
            raise ValueError("recording MP4 exceeds box count limit")
    if required:
        missing = ",".join(sorted(item.decode("ascii") for item in required))
        raise ValueError(f"recording MP4 is unfinished: missing {missing}")
