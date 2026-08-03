from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.maa_pipeline import MaaPipelineCompiler


PNG_1X1 = "data:image/png;base64," + base64.b64encode(
    base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlZb1sAAAAASUVORK5CYII=")
).decode()


def noise_png_data_url(size: int = 64) -> str:
    """Create a valid high-entropy PNG that should not occur on a phone screen."""

    state = 0x5A17C9E3
    rows = bytearray()
    for _y in range(size):
        rows.append(0)
        for _x in range(size):
            state ^= (state << 13) & 0xFFFFFFFF
            state ^= state >> 17
            state ^= (state << 5) & 0xFFFFFFFF
            rows.extend((state & 0xFF, (state >> 8) & 0xFF, (state >> 16) & 0xFF))

    def chunk(kind: bytes, data: bytes) -> bytes:
        payload = kind + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", zlib.crc32(payload) & 0xFFFFFFFF)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), level=9))
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode()


def main() -> int:
    from maa.controller import AdbController
    from maa.library import Library
    from maa.resource import Resource
    from maa.tasker import Tasker
    from maa.toolkit import Toolkit

    with tempfile.TemporaryDirectory(prefix="maa-binding-smoke-") as temp_value:
        temp = Path(temp_value)
        parser = argparse.ArgumentParser(description="Smoke-test MaaFramework binding and pipeline")
        parser.add_argument("--adb", action="store_true", help="also connect the first ADB device and run")
        args = parser.parse_args()
        if not Toolkit.init_option(temp / "user", {"logging": False, "stdout_level": 2}):
            raise RuntimeError("Toolkit.init_option failed")

        parser_compiled = MaaPipelineCompiler(temp).compile({
            "version": 2,
            "global_popups": [{
                "name": "smoke-popup",
                "template_base64": PNG_1X1,
                "click_mode": "match_center",
            }],
            "steps": [
                {"action": "start"},
                {"action": "wake"},
                {"action": "sleep"},
                {"action": "home"},
                {"action": "back"},
                {"action": "reboot"},
                {"action": "screenshot"},
                {"action": "log", "message": "smoke"},
                {
                    "action": "launch_app",
                    "package": "com.example.app",
                    "activity": ".MainActivity",
                },
                {"action": "close_app", "package": "com.example.app"},
                {"action": "tap", "x": 1, "y": 1, "click_count": 2},
                {"action": "swipe", "x1": 1, "y1": 2, "x2": 3, "y2": 4},
                {"action": "wait", "seconds": 0},
                {"action": "wait_image", "template_base64": PNG_1X1},
                {
                    "action": "wait_click",
                    "template_base64": PNG_1X1,
                    "click_mode": "image",
                    "click_template_base64": PNG_1X1,
                    "post_assertion": {
                        "enabled": True,
                        "template_base64": PNG_1X1,
                        "threshold": 0.85,
                        "timeout_seconds": 1,
                        "poll_interval_seconds": 0.2,
                        "max_retries": 2,
                    },
                    "skip_condition": {
                        "enabled": True,
                        "operator": "gt",
                        "value": 10,
                        "region": {"x": 0, "y": 0, "width": 1, "height": 1},
                    },
                },
                {
                    "action": "smart_swipe",
                    "template_base64": PNG_1X1,
                    "swipe": {"x1": 1, "y1": 2, "x2": 3, "y2": 4},
                },
                {"action": "feedback", "subject": "smoke"},
                {
                    "action": "maa",
                    "pipeline_node": {"recognition": "DirectHit", "action": "DoNothing"},
                },
                {"action": "yolo_detect", "confidence": 0.5},
            ],
        })
        resource = Resource()
        if not resource.override_pipeline(parser_compiled.pipeline):
            raise RuntimeError("MaaFramework rejected compiled pipeline")
        output = {
            "status": "ok",
            "maa_version": Library.version(),
            "entry": parser_compiled.entry,
            "pipeline_nodes": len(parser_compiled.pipeline),
            "parsed_nodes": len(resource.node_list),
            "adb_executed": False,
        }
        if args.adb:
            execution_compiled = MaaPipelineCompiler(temp / "execution").compile({
                "version": 2,
                "steps": [{"action": "wait", "seconds": 0}],
            })
            execution_resource = Resource()
            if not execution_resource.override_pipeline(execution_compiled.pipeline):
                raise RuntimeError("MaaFramework rejected executable smoke pipeline")
            adb_path = shutil.which("adb") or "adb"
            devices = Toolkit.find_adb_devices(adb_path)
            if not devices:
                raise RuntimeError("MaaFramework did not find an ADB device")
            requested = os.getenv("ADB_SERIAL", "").strip()
            selected = next(
                (
                    device for device in devices
                    if not requested or device.address == requested or device.name == requested
                ),
                None,
            )
            if selected is None:
                raise RuntimeError(f"MaaFramework did not find configured ADB device: {requested}")

            direct = subprocess.run(
                [str(selected.adb_path), "-s", selected.address, "exec-out", "screencap", "-p"],
                capture_output=True,
                timeout=30,
                check=False,
            )
            configured = os.getenv("MAA_ADB_SCREENCAP_METHODS", "2").strip()
            try:
                configured_method = int(configured, 0)
            except ValueError as exc:
                raise RuntimeError("MAA_ADB_SCREENCAP_METHODS must be an integer bitmask") from exc
            method_candidates = []
            for method in (
                configured_method,
                int(selected.screencap_methods) & 7,
                2,
                1,
                4,
            ):
                if method > 0 and method not in method_candidates:
                    method_candidates.append(method)
            input_methods = (
                int(os.getenv("MAA_ADB_INPUT_METHODS", "7"), 0)
                or (int(selected.input_methods) & 7)
                or 7
            )
            output["adb_diagnostic"] = {
                "requested_serial": requested or None,
                "selected": {
                    "name": selected.name,
                    "adb_path": str(selected.adb_path),
                    "address": selected.address,
                    "toolkit_screencap_methods": int(selected.screencap_methods),
                    "toolkit_input_methods": int(selected.input_methods),
                    "config": selected.config,
                },
                "direct_screencap": {
                    "returncode": direct.returncode,
                    "size_bytes": len(direct.stdout),
                    "png_header": direct.stdout.startswith(b"\x89PNG\r\n\x1a\n"),
                    "stderr": direct.stderr.decode(errors="replace").strip(),
                },
                "method_candidates": method_candidates,
                "input_methods_used": input_methods,
                "attempts": [],
            }
            print(json.dumps(output, ensure_ascii=False), flush=True)

            controller = None
            for method in method_candidates:
                candidate = AdbController(
                    selected.adb_path,
                    selected.address,
                    method,
                    input_methods,
                    selected.config,
                )
                candidate.set_screenshot_use_raw_size(True)
                connection = candidate.post_connection().wait()
                output["adb_diagnostic"]["attempts"].append({
                    "screencap_methods": method,
                    "succeeded": bool(connection.succeeded),
                })
                if connection.succeeded:
                    controller = candidate
                    break
            if controller is None:
                print(json.dumps(output, ensure_ascii=False), flush=True)
                raise RuntimeError(
                    "Maa ADB controller connection failed; "
                    f"direct adb png={output['adb_diagnostic']['direct_screencap']['png_header']}, "
                    f"methods={method_candidates}"
                )
            tasker = Tasker()
            if not tasker.bind(execution_resource, controller) or not tasker.inited:
                raise RuntimeError("Tasker initialization failed")
            job = tasker.post_task(execution_compiled.entry).wait()
            if not job.succeeded:
                raise RuntimeError("Maa pipeline execution failed")
            detail = job.get()
            output.update({
                "adb_executed": True,
                "adb_address": selected.address,
                "controller": controller.info,
                "executed_nodes": len(detail.nodes) if detail is not None else 0,
            })

            assertion_compiled = MaaPipelineCompiler(temp / "assertion").compile({
                "version": 2,
                "steps": [{
                    "action": "wait",
                    "seconds": 0,
                    "post_assertion": {
                        "enabled": True,
                        "template_base64": noise_png_data_url(),
                        "threshold": 0.999,
                        "timeout_seconds": 0.2,
                        "poll_interval_seconds": 0.05,
                        "max_retries": 2,
                    },
                }],
            })
            assertion_resource = Resource()
            if not assertion_resource.override_pipeline(assertion_compiled.pipeline):
                raise RuntimeError("MaaFramework rejected assertion retry pipeline")
            image_job = assertion_resource.post_image(assertion_compiled.image_dir).wait()
            if not image_job.succeeded:
                raise RuntimeError("MaaFramework failed to load assertion image")
            assertion_tasker = Tasker()
            if not assertion_tasker.bind(assertion_resource, controller) or not assertion_tasker.inited:
                raise RuntimeError("Assertion retry Tasker initialization failed")
            assertion_job = assertion_tasker.post_task(assertion_compiled.entry).wait()
            assertion_detail = assertion_job.get()
            retry_name = next(
                name for name in assertion_compiled.step_nodes[0]
                if "_AssertRetry" in name
            )
            retry_hits = sum(
                1 for node in (assertion_detail.nodes if assertion_detail is not None else [])
                if node.name == retry_name and node.completed
            )
            if assertion_job.succeeded:
                raise RuntimeError("Impossible assertion unexpectedly succeeded")
            if retry_hits != 2:
                raise RuntimeError(f"Assertion retry expected 2 retries, observed {retry_hits}")
            output["post_assertion_diagnostic"] = {
                "expected_failure_observed": True,
                "retry_hits": retry_hits,
                "max_retries": 2,
            }
        print(json.dumps(output, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
