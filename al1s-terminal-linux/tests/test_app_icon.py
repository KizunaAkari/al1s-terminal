from subprocess import CompletedProcess
from types import SimpleNamespace

import pytest

from al1s_terminal.interactive.app_icon import read_application_icon


def test_reads_only_metadata_and_selected_png(monkeypatch) -> None:
    calls: list[list[str]] = []
    apk = "/data/app/~~abc==/com.example.app-xyz==/base.apk"

    def run(args, **_kwargs):
        calls.append([str(item) for item in args])
        if args[1:4] == ["-s", "phone", "shell"]:
            return CompletedProcess(args, 0, f"package:{apk}\n".encode(), b"")
        if args[0] == "aapt":
            return CompletedProcess(
                args, 0,
                b"application-icon-160:'res/small.png'\n"
                b"application-icon-640:'res/large.png'\n", b"",
            )
        entry = args[-1]
        payload = b"\x89PNG\r\n\x1a\nicon" if entry == "res/large.png" else b"metadata"
        return CompletedProcess(args, 0, payload, b"")

    monkeypatch.setattr("al1s_terminal.interactive.app_icon.subprocess.run", run)
    adb = SimpleNamespace(binary_path="adb", require_device=lambda serial: serial)
    assert read_application_icon(adb, "phone", "com.example.app").endswith(b"icon")
    assert [call[-1] for call in calls if "exec-out" in call] == [
        "AndroidManifest.xml", "resources.arsc", "res/large.png",
    ]
    assert not any("pull" in call for call in calls)


def test_rejects_package_injection_before_adb(monkeypatch) -> None:
    monkeypatch.setattr(
        "al1s_terminal.interactive.app_icon.subprocess.run",
        lambda *_args, **_kwargs: pytest.fail("ADB must not be called"),
    )
    with pytest.raises(ValueError):
        read_application_icon(SimpleNamespace(), "phone", "com.example.app;rm")
