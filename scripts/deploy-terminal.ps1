param(
    [string]$BoardHost = "192.168.5.21",
    [string]$BoardUser = "root",
    [string]$TargetDir = "/run/media/mmcblk1p8/maa-test-terminal",
    [switch]$PrepareOnly
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$remote = "${BoardUser}@${BoardHost}"
$assetRoot = Join-Path $projectRoot ".terminal-assets"
$wheelDir = Join-Path $assetRoot "wheels"
$rknnRuntimeDir = Join-Path $assetRoot "rknn-runtime"

function Get-TerminalAsset {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][string]$Destination,
        [string]$ExpectedSha256 = ""
    )

    if ((Test-Path -LiteralPath $Destination) -and
        (Get-Item -LiteralPath $Destination).Length -gt 0) {
        if (-not $ExpectedSha256 -or
            (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash -eq $ExpectedSha256) {
            return
        }
        throw "Existing terminal asset checksum mismatch: $Destination"
    }

    $temporary = "${Destination}.download"
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    Write-Host "Downloading terminal asset: $(Split-Path -Leaf $Destination)" -ForegroundColor Cyan
    & curl.exe --fail --location --retry 5 --retry-delay 2 --retry-all-errors `
        --output $temporary $Url
    if ($LASTEXITCODE -ne 0 -or
        -not (Test-Path -LiteralPath $temporary) -or
        (Get-Item -LiteralPath $temporary).Length -eq 0) {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        throw "Unable to download terminal asset: $Url"
    }
    if ($ExpectedSha256 -and
        (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash -ne $ExpectedSha256) {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        throw "Downloaded terminal asset checksum mismatch: $Url"
    }
    Move-Item -LiteralPath $temporary -Destination $Destination -Force
}

New-Item -ItemType Directory -Path $wheelDir -Force | Out-Null
New-Item -ItemType Directory -Path $rknnRuntimeDir -Force | Out-Null
Get-TerminalAsset `
    "https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-unknown-linux-gnu.tar.gz" `
    (Join-Path $assetRoot "uv-aarch64-unknown-linux-gnu.tar.gz")
Get-TerminalAsset `
    "https://raw.githubusercontent.com/MaaXYZ/MaaCommonAssets/main/OCR/ppocr_v4/zh_cn/det.onnx" `
    (Join-Path $assetRoot "det.onnx")
Get-TerminalAsset `
    "https://raw.githubusercontent.com/MaaXYZ/MaaCommonAssets/main/OCR/ppocr_v4/zh_cn/rec.onnx" `
    (Join-Path $assetRoot "rec.onnx")
Get-TerminalAsset `
    "https://raw.githubusercontent.com/MaaXYZ/MaaCommonAssets/main/OCR/ppocr_v4/zh_cn/keys.txt" `
    (Join-Path $assetRoot "keys.txt")
Get-TerminalAsset `
    "https://raw.githubusercontent.com/airockchip/rknn-toolkit2/v2.3.2/rknn-toolkit-lite2/packages/rknn_toolkit_lite2-2.3.2-cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64.whl" `
    (Join-Path $wheelDir "rknn_toolkit_lite2-2.3.2-cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64.whl")
Get-TerminalAsset `
    "https://raw.githubusercontent.com/airockchip/rknn-toolkit2/v2.3.2/rknpu2/runtime/Linux/librknn_api/aarch64/librknnrt.so" `
    (Join-Path $rknnRuntimeDir "librknnrt.so") `
    "D31FC19C85B85F6091B2BD0F6AF9D962D5264A4E410BFB536402EC92BAC738E8"

Write-Host "Preparing offline Python 3.12 ARM64 wheels" -ForegroundColor Cyan
& python.exe -m pip download `
    --disable-pip-version-check `
    --dest $wheelDir `
    --only-binary=:all: `
    --platform manylinux2014_aarch64 `
    --python-version 3.12 `
    --implementation cp `
    --abi cp312 `
    --requirement (Join-Path $projectRoot "agent\requirements.txt") `
    --requirement (Join-Path $projectRoot "agent\requirements-rknn.txt")
if ($LASTEXITCODE -ne 0) { throw "Unable to prepare ARM64 Python wheels." }

if ($PrepareOnly) {
    Write-Host "Terminal offline assets are ready: $assetRoot" -ForegroundColor Green
    return
}

$remoteState = ssh $remote "mkdir -p '$TargetDir'; if test -s '$TargetDir/.terminal-assets/uv-aarch64-unknown-linux-gnu.tar.gz' && test -s '$TargetDir/.terminal-assets/det.onnx' && test -s '$TargetDir/.terminal-assets/rec.onnx' && test -s '$TargetDir/.terminal-assets/wheels/maafw-5.12.1-py3-none-manylinux2014_aarch64.whl' && test -s '$TargetDir/.terminal-assets/rknn-runtime/librknnrt.so'; then echo ASSETS_READY; else echo ASSETS_MISSING; fi"
if ($LASTEXITCODE -ne 0) { throw "Unable to prepare remote target directory." }
$remoteHasAssets = $remoteState -contains "ASSETS_READY"

scp -r `
    (Join-Path $projectRoot "agent") `
    (Join-Path $projectRoot "deploy") `
    (Join-Path $projectRoot "scripts") `
    "${remote}:${TargetDir}/"
if ($LASTEXITCODE -ne 0) { throw "Unable to copy terminal package." }

if (-not $remoteHasAssets) {
    Write-Host "Uploading terminal offline assets" -ForegroundColor Cyan
    scp -r $assetRoot "${remote}:${TargetDir}/"
    if ($LASTEXITCODE -ne 0) { throw "Unable to copy terminal offline assets." }
}
else {
    Write-Host "Remote offline assets are already complete; skipping 102 MB upload." -ForegroundColor DarkGray
}

ssh $remote "test -f '$TargetDir/.env.agent' || cp '$TargetDir/deploy/terminal/env.agent.example' '$TargetDir/.env.agent'; chmod +x '$TargetDir/deploy/terminal/install.sh'; '$TargetDir/deploy/terminal/install.sh'"
if ($LASTEXITCODE -ne 0) { throw "Terminal installation failed." }

Write-Host "Terminal agent deployed to ${remote}:$TargetDir" -ForegroundColor Green
