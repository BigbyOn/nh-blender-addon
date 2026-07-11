@echo off
setlocal EnableExtensions DisableDelayedExpansion

cd /d "%~dp0"

set "REPO_URL=https://github.com/BigbyOn/nh-blender-addon.git"
set "ADDON_SINGLE_FILE=NH_Blender.py"
set "ADDON_PACKAGE_DIR=NH_Blender"
set "TOOLS_BUNDLE_DIR=_nh_blender_tools"
set "DIST_DIR=dist"
set "ZIP_BASENAME=nh-blender-addon"
set "ADDON_ENTRY_FILE="
set "ADDON_SOURCE_MODE="
set "ZIP_PATH="
set "ZIP_LATEST_PATH="
set "BASE_VERSION="
set "RELEASE_VERSION="
set "DO_RELEASE=0"
set "DO_PUSH=0"
set "WRITE_LATEST=1"
set "UPDATE_DOCS=0"
set "PS_VERSION_FILE=%TEMP%\nh_blender_build_version_%RANDOM%%RANDOM%.txt"
set "PS_BUILD_INFO_FILE=%TEMP%\nh_blender_build_info_%RANDOM%%RANDOM%.txt"
set "GIT_EXE="

for %%A in (%*) do (
    if /I "%%~A"=="--release" set "DO_RELEASE=1"
    if /I "%%~A"=="--push" set "DO_PUSH=1"
    if /I "%%~A"=="--no-latest" set "WRITE_LATEST=0"
    if /I "%%~A"=="--update-docs" set "UPDATE_DOCS=1"
    if /I "%%~A"=="--help" goto :usage
    if /I "%%~A"=="-h" goto :usage
)

if "%DO_PUSH%"=="1" set "DO_RELEASE=1"

echo.
echo === NH Blender addon ZIP build ===
echo Working directory: %CD%
echo.

if exist "%ADDON_SINGLE_FILE%" (
    set "ADDON_ENTRY_FILE=%ADDON_SINGLE_FILE%"
    set "ADDON_SOURCE_MODE=file"
) else (
    echo ERROR: addon source not found.
    echo Expected:
    echo   %CD%\%ADDON_SINGLE_FILE%
    exit /b 1
)

if not exist "%ADDON_PACKAGE_DIR%\tools\xray_tex_converter\dds_python.py" (
    if not exist "tools\xray_tex_converter\dds_python.py" (
        echo ERROR: built-in DDS converter is missing:
        echo   %CD%\%ADDON_PACKAGE_DIR%\tools\xray_tex_converter\dds_python.py
        echo or:
        echo   %CD%\tools\xray_tex_converter\dds_python.py
        echo.
        echo The ZIP would show "Built-in Python: missing" in Blender.
        exit /b 1
    )
)
if not exist "%ADDON_PACKAGE_DIR%\tools\xray_tex_converter\converter.js" (
    if not exist "tools\xray_tex_converter\converter.js" (
        echo WARNING: bundled Node converter is missing:
        echo   %CD%\%ADDON_PACKAGE_DIR%\tools\xray_tex_converter\converter.js
        echo or:
        echo   %CD%\tools\xray_tex_converter\converter.js
        echo Built-in Python can still work, but Node fallback will be unavailable.
        echo.
    )
)

if "%DO_RELEASE%"=="1" (
    call :find_git
    if errorlevel 1 exit /b 1
)

if exist "%PS_VERSION_FILE%" del /f /q "%PS_VERSION_FILE%" >nul 2>&1
if exist "%PS_BUILD_INFO_FILE%" del /f /q "%PS_BUILD_INFO_FILE%" >nul 2>&1

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference='Stop';" ^
    "$repo=(Resolve-Path '.').Path;" ^
    "$dist=Join-Path $repo '%DIST_DIR%';" ^
    "$zipBase='%ZIP_BASENAME%';" ^
    "$single=Join-Path $repo '%ADDON_SINGLE_FILE%';" ^
    "if(-not (Test-Path -LiteralPath $single)){ throw 'No addon source file found for version update: %ADDON_SINGLE_FILE%'; }" ^
    "function Get-BlInfoVersion([string]$path){" ^
    "  $text=Get-Content -Raw -Encoding UTF8 -LiteralPath $path;" ^
    "  $m=[regex]::Match($text,'\"version\"\s*:\s*\((?<tuple>[^)]*)\)');" ^
    "  if(-not $m.Success){ throw ('bl_info version tuple not found: ' + $path); }" ^
    "  $parts=@();" ^
    "  foreach($part in ($m.Groups['tuple'].Value -split ',')){" ^
    "    $clean=$part.Trim();" ^
    "    if($clean -eq ''){ continue };" ^
    "    if($clean -notmatch '^\d+$'){ throw ('Invalid bl_info version component in {0}: {1}' -f $path, $clean); }" ^
    "    $parts += [int]$clean;" ^
    "  }" ^
    "  if($parts.Count -lt 3){ throw ('bl_info version tuple must contain at least 3 numeric components: ' + $path); }" ^
    "  return ($parts -join '.');" ^
    "}" ^
    "function Version-Greater([string]$a, [string]$b){ return ([version]$a).CompareTo([version]$b) -gt 0 }" ^
    "function Increment-LastVersionComponent([string]$v){" ^
    "  $parts=@($v -split '\.' | ForEach-Object { [int]$_ });" ^
    "  if($parts.Count -lt 3){ throw ('Version must have at least 3 components: ' + $v); }" ^
    "  $parts[$parts.Count - 1] = $parts[$parts.Count - 1] + 1;" ^
    "  return ($parts -join '.');" ^
    "}" ^
    "function Set-BlInfoVersion([string]$path, [string]$version){" ^
    "  $tuple='(' + (($version -split '\.' | ForEach-Object { [int]$_ }) -join ', ') + ')';" ^
    "  $text=Get-Content -Raw -Encoding UTF8 -LiteralPath $path;" ^
    "  $current=Get-BlInfoVersion $path;" ^
    "  if($current -eq $version){ return }" ^
    "  $new=[regex]::Replace($text,'(\"version\"\s*:\s*)\([^)]+\)', ('$1' + $tuple), 1);" ^
    "  if($new -eq $text){ throw ('Could not update bl_info version in ' + $path); }" ^
    "  $enc=New-Object System.Text.UTF8Encoding($false);" ^
    "  [System.IO.File]::WriteAllText($path, $new, $enc);" ^
    "}" ^
    "$sourceMax=Get-BlInfoVersion $single;" ^
    "$latestZip=$null;" ^
    "if(Test-Path -LiteralPath $dist){" ^
    "  Get-ChildItem -LiteralPath $dist -Filter ($zipBase + '-v*.zip') -File -ErrorAction SilentlyContinue | ForEach-Object {" ^
    "    $m=[regex]::Match($_.BaseName, ('^' + [regex]::Escape($zipBase) + '-v(?<v>\d+(?:\.\d+){2,})$'));" ^
    "    if($m.Success){" ^
    "      $v=$m.Groups['v'].Value;" ^
    "      if($latestZip -eq $null -or (Version-Greater $v $latestZip)){ $latestZip=$v }" ^
    "    }" ^
    "  }" ^
    "}" ^
    "$nextFromZip=$sourceMax;" ^
    "if($latestZip){ $nextFromZip=Increment-LastVersionComponent $latestZip }" ^
    "$releaseVersion=$nextFromZip;" ^
    "if(Version-Greater $sourceMax $releaseVersion){ $releaseVersion=$sourceMax }" ^
    "Set-BlInfoVersion $single $releaseVersion;" ^
    "$enc=New-Object System.Text.UTF8Encoding($false);" ^
    "[System.IO.File]::WriteAllText('%PS_VERSION_FILE%', $releaseVersion, $enc);"
if errorlevel 1 (
    echo ERROR: failed to resolve or update addon version.
    exit /b 1
)

if not exist "%PS_VERSION_FILE%" (
    echo ERROR: version temp file was not created.
    exit /b 1
)

set /p BASE_VERSION=<"%PS_VERSION_FILE%"
del /f /q "%PS_VERSION_FILE%" >nul 2>&1

if not defined BASE_VERSION (
    echo ERROR: BASE_VERSION is empty.
    exit /b 1
)

set "RELEASE_VERSION=%BASE_VERSION%"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$v='%RELEASE_VERSION%'; if($v -match '^\d+(?:\.\d+){2,}$'){ exit 0 } else { exit 1 }"
if errorlevel 1 (
    echo ERROR: invalid version: "%RELEASE_VERSION%"
    exit /b 1
)

if "%UPDATE_DOCS%"=="1" (
    call :update_docs
    if errorlevel 1 exit /b 1
)

if not exist "%DIST_DIR%" mkdir "%DIST_DIR%"
if errorlevel 1 (
    echo ERROR: failed to create dist directory: %DIST_DIR%
    exit /b 1
)

set "ZIP_PATH=%DIST_DIR%\%ZIP_BASENAME%-v%RELEASE_VERSION%.zip"
set "ZIP_LATEST_PATH=%DIST_DIR%\%ZIP_BASENAME%-latest.zip"

echo Source mode: %ADDON_SOURCE_MODE%
echo Entry file:  %ADDON_ENTRY_FILE%
echo Version:     %RELEASE_VERSION%
echo ZIP:         %ZIP_PATH%
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference='Stop';" ^
    "Add-Type -AssemblyName System.IO.Compression;" ^
    "Add-Type -AssemblyName System.IO.Compression.FileSystem;" ^
    "$repo=(Resolve-Path '.').Path;" ^
    "$dist=(Resolve-Path '%DIST_DIR%').Path;" ^
    "$zip=Join-Path $repo '%ZIP_PATH%';" ^
    "$zipLatest=Join-Path $repo '%ZIP_LATEST_PATH%';" ^
    "$sourceMode='%ADDON_SOURCE_MODE%';" ^
    "$packageDir='%ADDON_PACKAGE_DIR%';" ^
    "$singleFile='%ADDON_SINGLE_FILE%';" ^
    "$version='%RELEASE_VERSION%';" ^
    "$writeLatest='%WRITE_LATEST%';" ^
    "$tempRoot=Join-Path ([System.IO.Path]::GetTempPath()) ('nh_blender_zip_' + [guid]::NewGuid().ToString('N'));" ^
    "function Add-FileToZip([System.IO.Compression.ZipArchive]$archive, [string]$filePath, [string]$entryName){" ^
    "  $entryName=$entryName.Replace('\','/').TrimStart('/');" ^
    "  [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, $filePath, $entryName, [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null;" ^
    "}" ^
    "New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null;" ^
    "try {" ^
    "  Copy-Item -LiteralPath (Join-Path $repo $singleFile) -Destination (Join-Path $tempRoot $singleFile) -Force;" ^
    "  $toolsBundleDir='%TOOLS_BUNDLE_DIR%';" ^
    "  $toolDstRoot=Join-Path $tempRoot $toolsBundleDir;" ^
    "  $toolSrcCandidates=@((Join-Path $repo 'tools'), (Join-Path $repo (Join-Path $packageDir 'tools')));" ^
    "  $toolSrc=$null;" ^
    "  foreach($candidate in $toolSrcCandidates){ if(Test-Path -LiteralPath $candidate -PathType Container){ $toolSrc=$candidate; break } }" ^
    "  if($toolSrc -eq $null){ throw 'Bundled tools folder not found. Expected NH_Blender\tools or tools.' }" ^
    "  Copy-Item -LiteralPath $toolSrc -Destination $toolDstRoot -Recurse -Force;" ^
    "  foreach($doc in @('README.md','LICENSE','CHANGELOG.md')){" ^
    "    $docPath=Join-Path $repo $doc;" ^
    "    if(Test-Path -LiteralPath $docPath){" ^
    "      Copy-Item -LiteralPath $docPath -Destination (Join-Path $tempRoot $doc) -Force;" ^
    "    }" ^
    "  };" ^
    "  $manifest=Join-Path $tempRoot '_build_manifest.txt';" ^
    "  $manifestLines=@(" ^
    "    'NH Blender addon build manifest'," ^
    "    ('version=' + $version)," ^
    "    ('source_mode=' + $sourceMode)," ^
    "    ('built_utc=' + (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss') + 'Z')," ^
    "    ('repo_path=' + $repo)" ^
    "  );" ^
    "  Set-Content -LiteralPath $manifest -Value $manifestLines -Encoding UTF8;" ^
    "  Get-ChildItem -LiteralPath $tempRoot -Recurse -Force -Directory -ErrorAction SilentlyContinue | Where-Object {" ^
    "    $_.Name -in @('__pycache__','.pytest_cache','.mypy_cache','.ruff_cache','.git','.github')" ^
    "  } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue;" ^
    "  Get-ChildItem -LiteralPath $tempRoot -Recurse -Force -File -ErrorAction SilentlyContinue | Where-Object {" ^
    "    $_.Extension -in @('.pyc','.pyo','.log','.tmp','.bak','.old','.orig','.zip','.7z','.rar') -or $_.Name -like '~$*'" ^
    "  } | Remove-Item -Force -ErrorAction SilentlyContinue;" ^
    "  if(Test-Path -LiteralPath $zip){ Remove-Item -LiteralPath $zip -Force };" ^
    "  $archive=[System.IO.Compression.ZipFile]::Open($zip, [System.IO.Compression.ZipArchiveMode]::Create);" ^
    "  try {" ^
    "    $files=Get-ChildItem -LiteralPath $tempRoot -Recurse -Force -File | Sort-Object FullName;" ^
    "    if(-not $files){ throw 'No files collected for archive'; }" ^
    "    foreach($file in $files){" ^
    "      $rel=$file.FullName.Substring($tempRoot.Length).TrimStart('\','/');" ^
    "      Add-FileToZip $archive $file.FullName $rel;" ^
    "    }" ^
    "  } finally {" ^
    "    if($archive){ $archive.Dispose(); }" ^
    "  };" ^
    "  $archive=[System.IO.Compression.ZipFile]::OpenRead($zip);" ^
    "  try {" ^
    "    $entries=@($archive.Entries | ForEach-Object { $_.FullName });" ^
    "    $badSlash=@($entries | Where-Object { $_ -like '*\*' });" ^
    "    if($badSlash.Count -gt 0){ throw ('Archive validation failed. Backslash entries: ' + ($badSlash -join ', ')); }" ^
    "    $required=@(" ^
    "      $singleFile," ^
    "      ($toolsBundleDir + '/xray_tex_converter/dds_python.py')," ^
    "      ($toolsBundleDir + '/xray_tex_converter/converter.js')" ^
    "    );" ^
    "    foreach($req in $required){" ^
    "      if($entries -notcontains $req){ throw ('Archive validation failed. Missing entry: ' + $req); }" ^
    "    }" ^
    "    $bad=@($entries | Where-Object { $_ -match '(^|/)__pycache__/' -or $_ -match '\.pyc$' -or $_ -match '\.pyo$' });" ^
    "    if($bad.Count -gt 0){ throw ('Archive validation failed. Forbidden entries: ' + ($bad -join ', ')); }" ^
    "  } finally {" ^
    "    $archive.Dispose();" ^
    "  };" ^
    "  $listPath=Join-Path $dist ('%ZIP_BASENAME%-v' + $version + '-contents.txt');" ^
    "  $archive=[System.IO.Compression.ZipFile]::OpenRead($zip);" ^
    "  try {" ^
    "    $archive.Entries | Sort-Object FullName | ForEach-Object { $_.FullName } | Set-Content -LiteralPath $listPath -Encoding UTF8;" ^
    "  } finally {" ^
    "    $archive.Dispose();" ^
    "  };" ^
    "  if($writeLatest -eq '1'){" ^
    "    Copy-Item -LiteralPath $zip -Destination $zipLatest -Force;" ^
    "  };" ^
    "  $info=@(" ^
    "    ('zip=' + $zip)," ^
    "    ('zip_latest=' + $zipLatest)," ^
    "    ('contents=' + $listPath)" ^
    "  );" ^
    "  Set-Content -LiteralPath '%PS_BUILD_INFO_FILE%' -Value $info -Encoding UTF8;" ^
    "} finally {" ^
    "  if(Test-Path -LiteralPath $tempRoot){ Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue }" ^
    "}"
if errorlevel 1 (
    echo ERROR: failed to build or validate ZIP archive.
    exit /b 1
)

echo Archive built successfully.
echo.

if exist "%PS_BUILD_INFO_FILE%" (
    type "%PS_BUILD_INFO_FILE%"
    del /f /q "%PS_BUILD_INFO_FILE%" >nul 2>&1
)

if "%DO_RELEASE%"=="1" (
    call :git_release
    if errorlevel 1 exit /b 1
)

echo.
echo Done.
echo Archive: %ZIP_PATH%
if "%WRITE_LATEST%"=="1" echo Latest:  %ZIP_LATEST_PATH%
exit /b 0

:update_docs
echo Updating README.md and CHANGELOG.md version markers...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ErrorActionPreference='Stop';" ^
    "$v='%RELEASE_VERSION%';" ^
    "$date=(Get-Date).ToString('yyyy-MM-dd');" ^
    "$enc=New-Object System.Text.UTF8Encoding($false);" ^
    "if(Test-Path -LiteralPath 'README.md'){" ^
    "  $readme=Get-Content -Raw -Encoding UTF8 -LiteralPath 'README.md';" ^
    "  $readme=[regex]::Replace($readme,'(?im)^(version\s*[:=-]\s*)\d+(?:\.\d+){2,}','$1' + $v);" ^
    "  $readme=[regex]::Replace($readme,'(?im)^(????????????\s*[:=-]\s*)\d+(?:\.\d+){2,}','$1' + $v);" ^
    "  $readme=[regex]::Replace($readme,'(?i)nh-blender-addon-v\d+(?:\.\d+){2,}\.zip','nh-blender-addon-v' + $v + '.zip');" ^
    "  [System.IO.File]::WriteAllText((Resolve-Path 'README.md'), $readme, $enc);" ^
    "}" ^
    "if(Test-Path -LiteralPath 'CHANGELOG.md'){" ^
    "  $changelog=Get-Content -Raw -Encoding UTF8 -LiteralPath 'CHANGELOG.md';" ^
    "  if($changelog -notmatch ('(?m)^##\s+\[?' + [regex]::Escape($v) + '\]?')){" ^
    "    $entry=@(" ^
    "      ('## ' + $v + ' - ' + $date)," ^
    "      ''," ^
    "      '- Single-file NH_Blender.py ZIP build with bundled texture converter tools.'," ^
    "      '- Built-in Python DDS converter is included in _nh_blender_tools/xray_tex_converter/dds_python.py.'," ^
    "      '- ZIP validation now checks NH_Blender.py plus single-file bundled tools and forward-slash archive paths.'," ^
    "      ''" ^
    "    ) -join [Environment]::NewLine;" ^
    "    if($changelog -match '(?is)^\s*#\s+.+?\r?\n'){" ^
    "      $changelog=[regex]::Replace($changelog,'(?is)^(\s*#\s+.+?\r?\n)', ('$1' + [Environment]::NewLine + $entry), 1);" ^
    "    } else {" ^
    "      $changelog=('# Changelog' + [Environment]::NewLine + [Environment]::NewLine + $entry + [Environment]::NewLine + $changelog);" ^
    "    }" ^
    "    [System.IO.File]::WriteAllText((Resolve-Path 'CHANGELOG.md'), $changelog, $enc);" ^
    "  }" ^
    "}"
if errorlevel 1 (
    echo ERROR: failed to update README.md / CHANGELOG.md.
    exit /b 1
)
exit /b 0

:find_git
for /f "delims=" %%G in ('where git 2^>nul') do if not defined GIT_EXE set "GIT_EXE=%%G"
if not defined GIT_EXE if exist "C:\Program Files\Git\cmd\git.exe" set "GIT_EXE=C:\Program Files\Git\cmd\git.exe"
if not defined GIT_EXE if exist "C:\Program Files\Git\bin\git.exe" set "GIT_EXE=C:\Program Files\Git\bin\git.exe"

if not defined GIT_EXE (
    echo ERROR: Git is not found. Install Git first or run without --release.
    exit /b 1
)
exit /b 0

:git_release
echo.
echo === Git release mode ===
echo Git: %GIT_EXE%

"%GIT_EXE%" remote get-url origin >nul 2>&1
if errorlevel 1 (
    "%GIT_EXE%" remote add origin "%REPO_URL%"
) else (
    "%GIT_EXE%" remote set-url origin "%REPO_URL%"
)
if errorlevel 1 (
    echo ERROR: failed to configure remote origin.
    exit /b 1
)

"%GIT_EXE%" add "%ADDON_PACKAGE_DIR%" "%ADDON_SINGLE_FILE%" README.md LICENSE CHANGELOG.md 2>nul
"%GIT_EXE%" add -f "%ZIP_PATH%" 2>nul
if "%WRITE_LATEST%"=="1" "%GIT_EXE%" add -f "%ZIP_LATEST_PATH%" 2>nul

"%GIT_EXE%" diff --cached --quiet
if not errorlevel 1 (
    echo Nothing to commit after staging.
) else (
    "%GIT_EXE%" commit -m "release: v%RELEASE_VERSION%"
    if errorlevel 1 (
        echo ERROR: commit failed.
        exit /b 1
    )
)

"%GIT_EXE%" rev-parse --verify "refs/tags/v%RELEASE_VERSION%" >nul 2>&1
if not errorlevel 1 (
    echo ERROR: tag v%RELEASE_VERSION% already exists.
    echo Update bl_info version or delete the existing tag.
    exit /b 1
)

"%GIT_EXE%" tag -a "v%RELEASE_VERSION%" -m "v%RELEASE_VERSION%"
if errorlevel 1 (
    echo ERROR: failed to create tag.
    exit /b 1
)

if "%DO_PUSH%"=="1" (
    "%GIT_EXE%" push -u origin HEAD:main
    if errorlevel 1 (
        echo ERROR: push to main failed.
        exit /b 1
    )

    "%GIT_EXE%" push origin "v%RELEASE_VERSION%"
    if errorlevel 1 (
        echo ERROR: push tag failed.
        exit /b 1
    )
) else (
    echo Push skipped. Run with --push to push main and tag.
)

exit /b 0

:usage
echo.
echo Usage:
echo   build_addon_zip.bat
echo   build_addon_zip.bat --update-docs
echo   build_addon_zip.bat --release
echo   build_addon_zip.bat --release --push
echo   build_addon_zip.bat --no-latest
echo.
echo Default mode builds and validates ZIP only.
echo --update-docs updates README/CHANGELOG version markers before build.
echo --release creates git commit and tag, but does not push.
echo --push creates git commit/tag and pushes main + tag.
echo The ZIP uses NH_Blender.py as the addon entry and bundles _nh_blender_tools.
exit /b 0
