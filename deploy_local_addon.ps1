param(
    [string]$BlenderVersion = "",
    [Alias("SourceFile")]
    [string]$SourcePath = ""
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

function Resolve-AddonSource {
    param(
        [string]$RequestedPath,
        [string]$RepoRoot
    )

    $candidatePaths = @()
    if ($RequestedPath) {
        $candidatePaths = @((Resolve-Path -LiteralPath $RequestedPath -ErrorAction Stop).Path)
    }
    else {
        $packageDir = Join-Path $RepoRoot "NH_Blender"
        $singleFile = Join-Path $RepoRoot "NH_Blender.py"

        if (Test-Path -LiteralPath (Join-Path $packageDir "__init__.py")) {
            $candidatePaths += $packageDir
        }
        if (Test-Path -LiteralPath $singleFile) {
            $candidatePaths += $singleFile
        }
    }

    foreach ($candidate in $candidatePaths) {
        if (Test-Path -LiteralPath $candidate -PathType Container) {
            $entryFile = Join-Path $candidate "__init__.py"
            if (-not (Test-Path -LiteralPath $entryFile -PathType Leaf)) {
                throw "Addon package folder must contain __init__.py: $candidate"
            }

            $name = Split-Path -Leaf $candidate
            return [pscustomobject]@{
                Mode       = "package"
                Name       = $name
                SourcePath = $candidate
                EntryFile  = $entryFile
            }
        }

        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            if ([System.IO.Path]::GetFileName($candidate).Equals("__init__.py", [System.StringComparison]::OrdinalIgnoreCase)) {
                $packageDir = Split-Path -Parent $candidate
                $name = Split-Path -Leaf $packageDir
                return [pscustomobject]@{
                    Mode       = "package"
                    Name       = $name
                    SourcePath = $packageDir
                    EntryFile  = $candidate
                }
            }

            $name = [System.IO.Path]::GetFileNameWithoutExtension($candidate)
            return [pscustomobject]@{
                Mode       = "file"
                Name       = $name
                SourcePath = $candidate
                EntryFile  = $candidate
            }
        }
    }

    throw "Addon source not found. Expected NH_Blender\__init__.py or NH_Blender.py under $RepoRoot"
}

function Remove-AddonCaches {
    param(
        [string]$AddonsDir,
        [string]$AddonName
    )

    $rootPycacheDir = Join-Path $AddonsDir "__pycache__"
    if (Test-Path -LiteralPath $rootPycacheDir) {
        Get-ChildItem -LiteralPath $rootPycacheDir -Filter "$AddonName*.pyc" -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }

    $packageDir = Join-Path $AddonsDir $AddonName
    if (Test-Path -LiteralPath $packageDir -PathType Container) {
        Get-ChildItem -LiteralPath $packageDir -Recurse -Force -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
        Get-ChildItem -LiteralPath $packageDir -Recurse -Force -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Extension -eq ".pyc" } |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }
}

function Get-PathContentHash {
    param(
        [string]$Path
    )

    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    }

    $files = @(Get-ChildItem -LiteralPath $Path -Recurse -File -Force |
        Where-Object { $_.Extension -ne ".pyc" } |
        Sort-Object FullName)

    if ($files.Count -eq 0) {
        return ""
    }

    $manifestLines = foreach ($file in $files) {
        $relativePath = $file.FullName.Substring($Path.Length).TrimStart('\', '/')
        $fileHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
        "{0}|{1}" -f $relativePath, $fileHash
    }

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes(($manifestLines -join "`n"))
        $hashBytes = $sha.ComputeHash($bytes)
        return -join ($hashBytes | ForEach-Object { $_.ToString("x2") })
    }
    finally {
        $sha.Dispose()
    }
}

$addonSource = Resolve-AddonSource -RequestedPath $SourcePath -RepoRoot $PSScriptRoot
$versionPath = Resolve-BlenderVersionPath -RequestedVersion $BlenderVersion
$addonsDir = Join-Path $versionPath "scripts\addons"
$targetFile = Join-Path $addonsDir ("{0}.py" -f $addonSource.Name)
$targetDir = Join-Path $addonsDir $addonSource.Name
$runningBlender = @(Get-Process -Name "blender" -ErrorAction SilentlyContinue)

New-Item -ItemType Directory -Force -Path $addonsDir | Out-Null

if ($addonSource.Mode -eq "package") {
    if (Test-Path -LiteralPath $targetFile) {
        Remove-Item -LiteralPath $targetFile -Force
    }
    if (Test-Path -LiteralPath $targetDir) {
        Remove-Item -LiteralPath $targetDir -Recurse -Force
    }

    Copy-Item -LiteralPath $addonSource.SourcePath -Destination $addonsDir -Recurse -Force
    Remove-AddonCaches -AddonsDir $addonsDir -AddonName $addonSource.Name
    $deployedTarget = $targetDir
}
else {
    if (Test-Path -LiteralPath $targetDir) {
        Remove-Item -LiteralPath $targetDir -Recurse -Force
    }

    Copy-Item -LiteralPath $addonSource.SourcePath -Destination $targetFile -Force
    Remove-AddonCaches -AddonsDir $addonsDir -AddonName $addonSource.Name
    $deployedTarget = $targetFile
}

$sourceHash = Get-PathContentHash -Path $addonSource.SourcePath
$targetHash = Get-PathContentHash -Path $deployedTarget
$remainingPyc = @()
$rootPycacheDir = Join-Path $addonsDir "__pycache__"
if (Test-Path -LiteralPath $rootPycacheDir) {
    $remainingPyc = @(Get-ChildItem -LiteralPath $rootPycacheDir -Filter "$($addonSource.Name)*.pyc" -ErrorAction SilentlyContinue)
}

Write-Host "Deployed addon to: $deployedTarget"
Write-Host "Addon mode: $($addonSource.Mode)"
Write-Host "Addon entry: $($addonSource.EntryFile)"
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
    Write-Host "Warning: cached addon pyc files are still present in the root __pycache__."
    Write-Host "Close Blender and run this script again if you want a fully clean reload."
    Write-Host ""
}
Write-Host "Next step:"
Write-Host "1. Restart Blender, or disable/enable the addon in Preferences."
