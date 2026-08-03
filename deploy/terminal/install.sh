#!/usr/bin/env bash
set -Eeuo pipefail

target_dir="${MAA_TERMINAL_ROOT:-/run/media/mmcblk1p8/maa-test-terminal}"
runtime_dir="${target_dir}/data/runtime-python312"
uv_bin="${runtime_dir}/bin/uv"
venv_dir="${target_dir}/.venv"
service_source="${target_dir}/deploy/terminal/maa-terminal-agent.service"
service_target="/etc/systemd/system/maa-terminal-agent.service"
ocr_model_dir="${MAA_OCR_MODEL_DIR:-${target_dir}/data/maa/resource/model/ocr}"
uv_url="https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-unknown-linux-gnu.tar.gz"
rknn_wheel_url="https://raw.githubusercontent.com/airockchip/rknn-toolkit2/v2.3.2/rknn-toolkit-lite2/packages/rknn_toolkit_lite2-2.3.2-cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64.whl"
rknn_runtime_version="2.3.2"
rknn_runtime_url="https://raw.githubusercontent.com/airockchip/rknn-toolkit2/v2.3.2/rknpu2/runtime/Linux/librknn_api/aarch64/librknnrt.so"
rknn_runtime_sha256="d31fc19c85b85f6091b2bd0f6af9d962d5264a4e410bfb536402ec92bac738e8"
rknn_runtime_dir="${target_dir}/data/rknn/runtime/${rknn_runtime_version}"
rknn_runtime_system="/usr/lib/librknnrt.so"
rknn_runtime_backup_dir="${target_dir}/data/rknn/runtime/backups"
ocr_base_url="https://raw.githubusercontent.com/MaaXYZ/MaaCommonAssets/main/OCR/ppocr_v4/zh_cn"
env_file="${target_dir}/.env.agent"
offline_assets="${target_dir}/.terminal-assets"
bootstrap_root="${MAA_PYTHON_BOOTSTRAP_ROOT:-/run/media/mmcblk1p8/maa-python312-test}"

download_file() {
    local url="$1"
    local destination="$2"
    local temporary="${destination}.download"

    curl -fL --retry 3 --retry-delay 2 "$url" -o "$temporary"
    test -s "$temporary"
    mv "$temporary" "$destination"
}

ensure_env() {
    local key="$1"
    local value="$2"
    if ! grep -q "^${key}=" "$env_file"; then
        printf '\n%s=%s\n' "$key" "$value" >> "$env_file"
    fi
}

set_env() {
    local key="$1"
    local value="$2"
    if grep -q "^${key}=" "$env_file"; then
        sed -i "s#^${key}=.*#${key}=${value}#" "$env_file"
    else
        printf '\n%s=%s\n' "$key" "$value" >> "$env_file"
    fi
}

remove_env() {
    local key="$1"
    sed -i "/^${key}=/d" "$env_file"
}

cd "$target_dir"
if [[ ! -f "$env_file" ]]; then
    cp "${target_dir}/deploy/terminal/env.agent.example" "$env_file"
fi

# Remove files and settings from the retired Python/OpenCV/Tesseract executor.
remove_env "AGENT_EXECUTION_BACKEND"
remove_env "LD_LIBRARY_PATH"
rm -f \
    "${target_dir}/agent/ocr.py" \
    "${target_dir}/agent/vision.py" \
    "${target_dir}/scripts/terminal-visual-smoke.py" \
    "${target_dir}/scripts/rknn_smoke_test.py" \
    "${target_dir}/scripts/board-python312-install.sh"
find \
    "${target_dir}/agent" \
    "${target_dir}/deploy" \
    "${target_dir}/scripts" \
    -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
find \
    "${target_dir}/agent" \
    "${target_dir}/deploy" \
    "${target_dir}/scripts" \
    -depth -type d -name '__pycache__' -empty -delete

yolo_model="/run/media/mmcblk1p8/workspace/yolov8/yolov8n_640x640_rk3576.rknn"
for candidate in \
    "/run/media/mmcblk1p8/workspace/yolov8/yolov8n_640x640_rk3576.rknn" \
    "/run/media/mmcblk1p8/workspace/yolov5/bin/yolov8n_640x640_rk3576.rknn"; do
    if [[ -f "$candidate" ]]; then
        yolo_model="$candidate"
        break
    fi
done

ensure_env "MAA_ADB_PATH" "/usr/bin/adb"
ensure_env "MAA_ADB_SCREENCAP_METHODS" "2"
ensure_env "MAA_ADB_INPUT_METHODS" "7"
ensure_env "MAA_SCREENSHOT_MODE" "raw"
ensure_env "MAA_SCREENSHOT_SHORT_SIDE" "720"
ensure_env "MAA_OCR_MODEL_DIR" "$ocr_model_dir"
ensure_env "MAA_YOLO_PROVIDER" "agent.rknn_yolo:RknnYoloProvider"
ensure_env "MAA_YOLO_MODEL" "$yolo_model"
ensure_env "MAA_YOLO_INPUT_SIZE" "640x640"

mkdir -p \
    "${runtime_dir}/bin" \
    "${runtime_dir}/cache" \
    "${runtime_dir}/downloads" \
    "${runtime_dir}/python" \
    "$rknn_runtime_dir" \
    "$rknn_runtime_backup_dir" \
    "$ocr_model_dir"

if ! command -v curl >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl ca-certificates
fi

if [[ ! -x "$uv_bin" ]]; then
    machine="$(uname -m)"
    if [[ "$machine" != "aarch64" && "$machine" != "arm64" ]]; then
        echo "Unsupported terminal architecture for this installer: ${machine}" >&2
        exit 1
    fi
    uv_archive="${runtime_dir}/downloads/uv-aarch64-unknown-linux-gnu.tar.gz"
    if [[ -s "${offline_assets}/uv-aarch64-unknown-linux-gnu.tar.gz" ]]; then
        cp "${offline_assets}/uv-aarch64-unknown-linux-gnu.tar.gz" "$uv_archive"
    elif [[ -x "${bootstrap_root}/bin/uv" ]]; then
        install -m 0755 "${bootstrap_root}/bin/uv" "$uv_bin"
    else
        download_file "$uv_url" "$uv_archive"
    fi
    if [[ ! -x "$uv_bin" ]]; then
    tar -xzf "$uv_archive" -C "${runtime_dir}/downloads"
    install -m 0755 \
        "${runtime_dir}/downloads/uv-aarch64-unknown-linux-gnu/uv" \
        "$uv_bin"
    fi
fi

export UV_PYTHON_INSTALL_DIR="${runtime_dir}/python"
if [[ -d "${bootstrap_root}/cache" ]]; then
    export UV_CACHE_DIR="${bootstrap_root}/cache"
else
    export UV_CACHE_DIR="${runtime_dir}/cache"
fi

echo "[1/7] Preparing isolated Python 3.12"
python_bin=""
if command -v python3.12 >/dev/null 2>&1; then
    python_bin="$(command -v python3.12)"
elif [[ -x "${bootstrap_root}/bin/uv" && -d "${bootstrap_root}/python" ]]; then
    python_bin="$(
        UV_PYTHON_INSTALL_DIR="${bootstrap_root}/python" \
        UV_CACHE_DIR="${bootstrap_root}/cache" \
        "${bootstrap_root}/bin/uv" python find 3.12
    )"
fi
if [[ -z "$python_bin" || ! -x "$python_bin" ]]; then
    "$uv_bin" python install 3.12
    python_bin="$("$uv_bin" python find 3.12)"
fi
"$python_bin" --version

if [[ -x "${venv_dir}/bin/python" ]] && \
   ! "${venv_dir}/bin/python" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 12))'; then
    backup_dir="${venv_dir}.pre-maa-$(date +%Y%m%d%H%M%S)"
    echo "Backing up incompatible virtual environment to ${backup_dir}"
    mv "$venv_dir" "$backup_dir"
fi

echo "[2/7] Creating terminal virtual environment"
"$uv_bin" venv --allow-existing --python "$python_bin" "$venv_dir"

echo "[3/7] Installing MaaFramework terminal dependencies"
if compgen -G "${offline_assets}/wheels/*.whl" >/dev/null; then
    "$uv_bin" pip install \
        --offline \
        --find-links "${offline_assets}/wheels" \
        --python "${venv_dir}/bin/python" \
        -r agent/requirements.txt
else
    "$uv_bin" pip install --python "${venv_dir}/bin/python" -r agent/requirements.txt
fi

if [[ "${INSTALL_RKNN_LITE:-1}" == "1" ]]; then
    echo "[4/7] Installing RKNN Lite for the RK3576 NPU"
    local_rknn_wheel="${offline_assets}/wheels/rknn_toolkit_lite2-2.3.2-cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64.whl"
    if [[ -s "$local_rknn_wheel" ]]; then
        "$uv_bin" pip install \
            --offline \
            --find-links "${offline_assets}/wheels" \
            --python "${venv_dir}/bin/python" \
            -r agent/requirements-rknn.txt \
            "$local_rknn_wheel"
    else
        "$uv_bin" pip install \
            --python "${venv_dir}/bin/python" \
            -r agent/requirements-rknn.txt \
            "$rknn_wheel_url"
    fi
    local_rknn_runtime="${offline_assets}/rknn-runtime/librknnrt.so"
    if [[ -s "$local_rknn_runtime" ]]; then
        install -m 0644 "$local_rknn_runtime" "${rknn_runtime_dir}/librknnrt.so"
    else
        download_file "$rknn_runtime_url" "${rknn_runtime_dir}/librknnrt.so"
        chmod 0644 "${rknn_runtime_dir}/librknnrt.so"
    fi
    echo "${rknn_runtime_sha256}  ${rknn_runtime_dir}/librknnrt.so" | sha256sum --check --status
    export MAA_RKNN_RUNTIME_VERSION="$rknn_runtime_version"
else
    echo "[4/7] Skipping RKNN Lite (INSTALL_RKNN_LITE=${INSTALL_RKNN_LITE:-0})"
fi

runtime_backup=""
runtime_remove_on_error=0
rollback_rknn_runtime() {
    set +e
    if [[ -n "$runtime_backup" && -f "$runtime_backup" ]]; then
        install -m 0644 "$runtime_backup" "$rknn_runtime_system"
        ldconfig
        echo "Restored previous RKNN runtime from ${runtime_backup}" >&2
    elif [[ "$runtime_remove_on_error" == "1" ]]; then
        rm -f "$rknn_runtime_system"
        ldconfig
        echo "Removed failed RKNN runtime installation" >&2
    fi
    systemctl restart maa-terminal-agent.service >/dev/null 2>&1 || true
}

if [[ "${INSTALL_RKNN_LITE:-1}" == "1" ]]; then
    current_runtime_sha=""
    if [[ -f "$rknn_runtime_system" ]]; then
        current_runtime_sha="$(sha256sum "$rknn_runtime_system" | awk '{print $1}')"
    fi
    if [[ "$current_runtime_sha" != "$rknn_runtime_sha256" ]]; then
        if [[ -n "$current_runtime_sha" ]]; then
            runtime_backup="${rknn_runtime_backup_dir}/librknnrt-${current_runtime_sha}.so"
            if [[ ! -f "$runtime_backup" ]]; then
                cp -p "$rknn_runtime_system" "$runtime_backup"
            fi
        else
            runtime_remove_on_error=1
        fi
        trap rollback_rknn_runtime ERR
        install -m 0644 "${rknn_runtime_dir}/librknnrt.so" "${rknn_runtime_system}.maa-new"
        mv -f "${rknn_runtime_system}.maa-new" "$rknn_runtime_system"
        ldconfig
    fi
fi

echo "[5/7] Installing official MaaFramework OCR assets"
for asset in det.onnx rec.onnx keys.txt; do
    if [[ ! -s "${ocr_model_dir}/${asset}" ]]; then
        if [[ -s "${offline_assets}/${asset}" ]]; then
            cp "${offline_assets}/${asset}" "${ocr_model_dir}/${asset}"
        else
            download_file "${ocr_base_url}/${asset}" "${ocr_model_dir}/${asset}"
        fi
    fi
done

"${venv_dir}/bin/python" -c \
    'import platform, sys; from maa.library import Library; print(sys.version); print(platform.machine()); print("MaaFramework", Library.version())'

if [[ "${INSTALL_RKNN_LITE:-1}" == "1" ]]; then
    "${venv_dir}/bin/python" -c \
        'from importlib.metadata import version; from rknnlite.api import RKNNLite; print("rknn-toolkit-lite2", version("rknn-toolkit-lite2"))'
fi

echo "[6/7] Running MaaFramework and RKNN smoke tests"
"${venv_dir}/bin/python" scripts/maa-binding-smoke.py
if adb devices | awk 'NR > 1 && $2 == "device" { found = 1 } END { exit !found }'; then
    "${venv_dir}/bin/python" scripts/maa-binding-smoke.py --adb
else
    echo "No online Android device; Maa ADB smoke test skipped"
fi
if [[ "${INSTALL_RKNN_LITE:-1}" == "1" && -f "$yolo_model" ]]; then
    "${venv_dir}/bin/python" scripts/rknn-provider-smoke.py --model "$yolo_model"
    set_env "MAA_RKNN_RUNTIME_VERSION" "$rknn_runtime_version"
else
    echo "RKNN model not found or RKNN disabled; provider smoke test skipped"
fi

echo "[7/7] Installing and restarting the terminal service"
install -m 0644 "$service_source" "$service_target"
systemctl daemon-reload
systemctl enable maa-terminal-agent.service
systemctl restart maa-terminal-agent.service
systemctl --no-pager --full status maa-terminal-agent.service
trap - ERR
