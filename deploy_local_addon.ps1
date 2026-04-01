param(
    [string]$BlenderVersion = "",
    [string]$SourceFile = (Join-Path $PSScriptRoot "NH_Blender.py")
)

$ErrorActionPreference = "Stop"

function Resolve-BlenderVersionPath {
    param(
        [string]$RequestedVersion
    )

    $base = Join-Path $env:APPDATA "Blender Foundation\Blender"
    if (-not (Test-Path -LiteralPath $base)) {
        throw "Blender config folder not found: $base"
    }

    if ($RequestedVersion) {
        $requestedPath = Join-Path $base $RequestedVersion
        if (-not (Test-Path -LiteralPath $requestedPath)) {
            throw "Requested Blender version folder not found: $requestedPath"
        }
        return $requestedPath
    }

    $versions = Get-ChildItem -LiteralPath $base -Directory |
        Where-Object { $_.Name -match '^\d+(\.\d+)*$' } |
        Sort-Object {
            [version]$_.Name
        } -Descending

    if (-not $versions) {
        throw "No Blender version folders found in $base"
    }

    return $versions[0].FullName
}

if (-not (Test-Path -LiteralPath $SourceFile)) {
    throw "Source addon file not found: $SourceFile"
}

$versionPath = Resolve-BlenderVersionPath -RequestedVersion $BlenderVersion
$addonsDir = Join-Path $versionPath "scripts\addons"
$pycacheDir = Join-Path $addonsDir "__pycache__"
$targetFile = Join-Path $addonsDir "NH_Blender.py"
$runningBlender = @(Get-Process -Name "blender" -ErrorAction SilentlyContinue)

New-Item -ItemType Directory -Force -Path $addonsDir | Out-Null
Copy-Item -LiteralPath $SourceFile -Destination $targetFile -Force

$pycFiles = @()
if (Test-Path -LiteralPath $pycacheDir) {
    $pycFiles = @(Get-ChildItem -LiteralPath $pycacheDir -Filter "NH_Blender*.pyc" -ErrorAction SilentlyContinue)
    if ($pycFiles.Count -gt 0) {
        $pycFiles | Remove-Item -Force -ErrorAction SilentlyContinue
    }
}

$sourceHash = (Get-FileHash -LiteralPath $SourceFile -Algorithm SHA256).Hash
$targetHash = (Get-FileHash -LiteralPath $targetFile -Algorithm SHA256).Hash
$remainingPyc = @()
if (Test-Path -LiteralPath $pycacheDir) {
    $remainingPyc = @(Get-ChildItem -LiteralPath $pycacheDir -Filter "NH_Blender*.pyc" -ErrorAction SilentlyContinue)
}

Write-Host "Deployed addon to: $targetFile"
Write-Host "Blender version path: $versionPath"
Write-Host "Source hash: $sourceHash"
Write-Host "Target hash: $targetHash"
Write-Host ""
if ($runningBlender.Count -gt 0) {
    Write-Host "Warning: Blender is currently running."
    Write-Host "A running session may keep old code or pyc files in memory."
    Write-Host ""
}
if ($remainingPyc.Count -gt 0) {
    Write-Host "Warning: cached NH_Blender pyc files are still present."
    Write-Host "Close Blender and run this script again if you want a fully clean reload."
    Write-Host ""
}
Write-Host "Next step:"
Write-Host "1. Restart Blender, or disable/enable the addon in Preferences."
