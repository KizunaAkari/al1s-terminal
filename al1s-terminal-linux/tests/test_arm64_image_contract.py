from __future__ import annotations

from pathlib import Path


def test_arm64_image_exposes_the_pinned_rknn_runtime_at_the_vendor_path() -> None:
    project_root = Path(__file__).resolve().parents[1]
    dockerfile = (project_root / "Dockerfile.arm64").read_text(encoding="utf-8")
    asset_manifest = (project_root / "tools" / "assets-arm64.sha256").read_text(
        encoding="utf-8"
    )

    assert "rknn-runtime/librknnrt.so" in asset_manifest
    assert (
        "ln -s /opt/al1s-assets/rknn-runtime/librknnrt.so "
        "/usr/lib/librknnrt.so"
    ) in dockerfile


def test_arm64_image_installs_controlled_mdns_resolution_without_a_fixed_ip() -> None:
    project_root = Path(__file__).resolve().parents[1]
    dockerfile = (project_root / "Dockerfile.arm64").read_text(encoding="utf-8")
    compose_path = project_root.parents[1] / "al1s-deployment" / "linux" / "compose.arm64.yaml"
    compose = compose_path.read_text(encoding="utf-8")
    asset_manifest = (project_root / "tools" / "assets-arm64.sha256").read_text(
        encoding="utf-8"
    )

    assert "nss-mdns/libnss_mdns4_minimal.so.2" in asset_manifest
    assert "/usr/lib/aarch64-linux-gnu/libnss_mdns4_minimal.so.2" in dockerfile
    assert "hosts: files mdns4_minimal [NOTFOUND=return] dns" in dockerfile
    assert "/run/avahi-daemon/socket:/run/avahi-daemon/socket:ro" in compose
