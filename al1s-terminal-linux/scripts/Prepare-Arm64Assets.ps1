[CmdletBinding()]
param(
    [string]$AssetDirectory,
    [string]$SeedAssetDirectory,
    [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $AssetDirectory) {
    $AssetDirectory = Join-Path $projectRoot '.terminal-assets'
}
if (-not $SeedAssetDirectory) {
    $SeedAssetDirectory = Join-Path $projectRoot '..\..\.terminal-assets'
}
$assetRoot = [System.IO.Path]::GetFullPath($AssetDirectory)
$manifest = Join-Path $projectRoot 'tools\assets-arm64.sha256'

if (-not $VerifyOnly) {
    $wheelDirectory = Join-Path $assetRoot 'wheels'
    $ocrDirectory = Join-Path $assetRoot 'ocr'
    $runtimeDirectory = Join-Path $assetRoot 'rknn-runtime'
    $nssDirectory = Join-Path $assetRoot 'nss-mdns'
    New-Item -ItemType Directory -Force `
        $wheelDirectory, $ocrDirectory, $runtimeDirectory, $nssDirectory | Out-Null

    & python -m pip download `
        --dest $wheelDirectory `
        --only-binary=:all: `
        --platform manylinux2014_aarch64 `
        --python-version 3.12 `
        --implementation cp `
        --abi cp312 `
        --no-deps `
        -r (Join-Path $projectRoot 'requirements-arm64.lock')
    if ($LASTEXITCODE -ne 0) {
        throw "ARM64 wheel download failed with exit code $LASTEXITCODE"
    }

    Copy-Item -LiteralPath (Join-Path $SeedAssetDirectory 'det.onnx') `
        -Destination (Join-Path $ocrDirectory 'det.onnx') -Force
    Copy-Item -LiteralPath (Join-Path $SeedAssetDirectory 'rec.onnx') `
        -Destination (Join-Path $ocrDirectory 'rec.onnx') -Force
    Copy-Item -LiteralPath (Join-Path $SeedAssetDirectory 'keys.txt') `
        -Destination (Join-Path $ocrDirectory 'keys.txt') -Force
    Copy-Item -LiteralPath (Join-Path $SeedAssetDirectory 'rknn-runtime\librknnrt.so') `
        -Destination (Join-Path $runtimeDirectory 'librknnrt.so') -Force
    Copy-Item -LiteralPath `
        (Join-Path $SeedAssetDirectory 'nss-mdns\libnss_mdns4_minimal.so.2') `
        -Destination (Join-Path $nssDirectory 'libnss_mdns4_minimal.so.2') -Force
}

$rootPrefix = $assetRoot.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
foreach ($line in Get-Content -LiteralPath $manifest -Encoding UTF8) {
    if (-not $line.Trim()) {
        continue
    }
    $parts = $line -split '\s{2,}', 2
    if ($parts.Count -ne 2) {
        throw "Invalid asset manifest line: $line"
    }
    $expected = $parts[0].ToLowerInvariant()
    $relative = $parts[1].Replace('/', [System.IO.Path]::DirectorySeparatorChar)
    $candidate = [System.IO.Path]::GetFullPath((Join-Path $assetRoot $relative))
    if (-not $candidate.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Asset manifest path escapes the controlled directory: $relative"
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw "Required ARM64 asset is missing: $relative"
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $candidate).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        throw "ARM64 asset hash mismatch: $relative"
    }
}

Write-Host "ARM64 assets verified: $assetRoot"
