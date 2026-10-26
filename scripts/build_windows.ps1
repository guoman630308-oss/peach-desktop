param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\dist\Peach'),
    [switch]$Standalone,
    # 在独立目录包之上再编一个 Inno Setup 安装包，放在输出目录的上一层。
    [switch]$Installer,
    [switch]$EmbeddedWindow,
    [string]$CloudflaredPath = '',
    [string]$FFmpegPath = '',
    [string]$UntruncPath = ''
)

$ErrorActionPreference = 'Stop'
if ($Installer) { $Standalone = $true }
if ($EmbeddedWindow) { $Standalone = $true }
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    $BuildCommonGit = (& git -C $ProjectRoot rev-parse --path-format=absolute --git-common-dir).Trim()
    $Python = Join-Path (Split-Path -Parent $BuildCommonGit) '.venv\Scripts\python.exe'
}
$OutputPath = if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $OutputDirectory))
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Project Python not found: $Python"
}

& $Python -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller is not installed in the project venv. Install it with: uv sync --locked --extra build'
}

if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'resources\peach.ico') -PathType Leaf)) {
    throw 'Brand assets are missing. Run scripts/generate_brand_assets.py first.'
}

if (Test-Path -LiteralPath (Join-Path $ProjectRoot 'resources\seed')) {
    throw 'Private entity exports must stay outside the application resources directory.'
}

# Frontend bundles ship inside `--add-data web;web` and the runtime has no Node, so they
# must be rebuilt here: web/dist is committed to Git, but packaging reads the working tree,
# and a stale bundle would be shipped without any signal. See ADR-0022.
$FrontendPath = Join-Path $ProjectRoot 'frontend'
$Npm = Get-Command npm -ErrorAction SilentlyContinue
if (-not $Npm) {
    throw 'npm not found. Frontend bundles must be rebuilt before packaging; install Node 24+.'
}
& $Npm.Source --prefix $FrontendPath ci
if ($LASTEXITCODE -ne 0) { throw 'npm ci failed in frontend/.' }
& $Npm.Source --prefix $FrontendPath run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend bundle build failed (frontend/).' }
foreach ($FrontendAsset in @('peach-app.js', 'peach-app.css', 'peach-pages.js', 'peach-pages.css')) {
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot "web\dist\$FrontendAsset") -PathType Leaf)) {
        throw "web/dist/$FrontendAsset is missing after the frontend build."
    }
}

$BuildPath = Join-Path $ProjectRoot 'build\windows'
$WorkPath = Join-Path $BuildPath 'app'
New-Item -ItemType Directory -Path $OutputPath -Force | Out-Null
New-Item -ItemType Directory -Path $BuildPath -Force | Out-Null

# 构建身份随包一起走：冻结的托盘读它才知道自己停在哪个提交上，检出的 HEAD 只代表源码。
$BuildInfoPath = Join-Path $BuildPath 'build-info.json'
# git 不可用、或者这份源码根本不是检出（解压出来的 tarball）时 commit 留空，构建照常。
$BuildCommit = $null
try {
    $BuildCommitText = & git -C $ProjectRoot rev-parse HEAD 2>$null
    if ($BuildCommitText) { $BuildCommit = "$BuildCommitText".Trim() }
} catch {
    $BuildCommit = $null
}
$global:LASTEXITCODE = 0
$BuildVersionMatch = Select-String -LiteralPath (Join-Path $ProjectRoot 'src\peach\__init__.py') `
    -Pattern '__version__\s*=\s*"([^"]+)"'
if (-not $BuildVersionMatch) { throw 'src/peach/__init__.py does not declare __version__.' }
$BuildInfo = [ordered]@{
    commit = $BuildCommit
    version = $BuildVersionMatch.Matches[0].Groups[1].Value
    built_at = (Get-Date).ToString('o')
}
Set-Content -LiteralPath $BuildInfoPath -Value (ConvertTo-Json $BuildInfo) -Encoding utf8
$SentryConfigPath = Join-Path $BuildPath 'sentry.json'
Set-Content -LiteralPath $SentryConfigPath -Value (ConvertTo-Json @{
    dsn = "$env:PEACH_SENTRY_DSN"; library_dsn = "$env:PEACH_LIBRARY_SENTRY_DSN"
}) -Encoding utf8

$BuildMode = @('--onefile')
$WindowArguments = @()
if ($EmbeddedWindow) {
    & $Python -c "import webview, pythonnet"
    if ($LASTEXITCODE -ne 0) { throw '请先同步 desktop-window 构建依赖。' }
    $WindowArguments = @('--collect-all', 'webview', '--collect-all', 'pythonnet', '--collect-all', 'clr_loader',
        '--copy-metadata', 'bottle', '--copy-metadata', 'proxy-tools',
        '--hidden-import', 'peach.native_shell', '--hidden-import', 'System',
        '--add-data', "$(Join-Path $PSScriptRoot 'native-window.txt');.")
}
$BuildDestination = $OutputPath
if ($Standalone) {
    $BuildMode = @('--onedir', '--add-data', "$(Join-Path $PSScriptRoot 'standalone.txt');.")
    $BuildDestination = Split-Path -Parent $OutputPath
}
$CloudflaredSource = $null
$CloudflaredManifest = $null
$CloudflaredHash = $null
if ($Standalone) {
    if (-not $FFmpegPath) { $FFmpegPath = $env:PEACH_FFMPEG_BUILD }
    if (-not $FFmpegPath) { $FFmpegPath = Join-Path $ProjectRoot 'build\ffmpeg-windows\ffmpeg' }
    & $Python -X utf8 (Join-Path $PSScriptRoot 'media_tool_bundle.py') --source $FFmpegPath --validate-only
    if ($LASTEXITCODE -ne 0) {
        throw 'Standalone build requires verified FFmpeg tools and corresponding source. Run scripts/build_ffmpeg_windows.sh first or pass -FFmpegPath.'
    }
    if (-not $UntruncPath) { $UntruncPath = $env:PEACH_UNTRUNC_BUILD }
    if (-not $UntruncPath) { $UntruncPath = Join-Path $ProjectRoot 'build\untrunc-windows\untrunc' }
    & $Python -X utf8 (Join-Path $PSScriptRoot 'media_tool_bundle.py') --tool untrunc --source $UntruncPath --validate-only
    if ($LASTEXITCODE -ne 0) {
        throw 'Standalone build requires verified untrunc and corresponding source. Run scripts/build_untrunc_windows.sh first or pass -UntruncPath.'
    }
    $CloudflaredCandidates = @()
    if ($CloudflaredPath) { $CloudflaredCandidates += $CloudflaredPath }
    if ($env:PEACH_CLOUDFLARED_BUILD) { $CloudflaredCandidates += $env:PEACH_CLOUDFLARED_BUILD }
    $CloudflaredCandidates += (Join-Path $ProjectRoot 'vendor\cloudflared\cloudflared.exe')
    foreach ($Candidate in $CloudflaredCandidates) {
        if ($Candidate -and (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
            $CloudflaredSource = (Resolve-Path -LiteralPath $Candidate).Path
            break
        }
    }
    if (-not $CloudflaredSource) {
        throw 'Standalone build requires cloudflared.exe. Run scripts/fetch_cloudflared.ps1 or pass -CloudflaredPath.'
    }
    $CloudflaredManifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'cloudflared-windows.json') -Raw -Encoding utf8 | ConvertFrom-Json
    $CloudflaredHash = (Get-FileHash -LiteralPath $CloudflaredSource -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($CloudflaredHash -ne $CloudflaredManifest.sha256.ToLowerInvariant()) {
        throw "cloudflared SHA-256 mismatch: expected $($CloudflaredManifest.sha256), got $CloudflaredHash"
    }
}
& $Python -m PyInstaller --noconfirm --clean @BuildMode @WindowArguments --windowed --name Peach `
    --distpath $BuildDestination --workpath $WorkPath --specpath $BuildPath `
    --paths (Join-Path $ProjectRoot 'src') --python-option 'X utf8' `
    --collect-all curl_cffi --collect-all resvg_py `
    --copy-metadata sentry-sdk `
    --hidden-import uvicorn.logging --hidden-import uvicorn.loops.auto `
    --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.protocols.websockets.auto `
    --hidden-import uvicorn.lifespan.on `
    --icon (Join-Path $ProjectRoot 'resources\peach.ico') `
    --add-data "$(Join-Path $ProjectRoot 'web');web" `
    --add-data "$(Join-Path $ProjectRoot 'migrations');migrations" `
    --add-data "$(Join-Path $ProjectRoot 'resources');resources" `
    --add-data "${BuildInfoPath};." `
    --add-data "${SentryConfigPath};." `
    (Join-Path $ProjectRoot 'scripts\build_app_entry.py')
if ($LASTEXITCODE -ne 0) { throw 'Peach build failed.' }
if ($Standalone) {
    $BridgeDestination = Join-Path $OutputPath 'tools\amane-bridge'
    New-Item -ItemType Directory -Path $BridgeDestination -Force | Out-Null
    foreach ($BridgeFile in @('bridge.py', 'pyproject.toml', 'uv.lock')) {
        Copy-Item -LiteralPath (Join-Path $ProjectRoot "tools\amane-bridge\$BridgeFile") -Destination (Join-Path $BridgeDestination $BridgeFile) -Force
    }
    $UsageDocument = if ($EmbeddedWindow) { 'docs/NATIVE_WINDOW.md' } else { 'docs/TESTING_DESKTOP.md' }
    Copy-Item -LiteralPath (Join-Path $ProjectRoot $UsageDocument) -Destination (Join-Path $OutputPath '开始使用.md')
    Copy-Item -LiteralPath (Join-Path $ProjectRoot 'LICENSE') -Destination (Join-Path $OutputPath 'LICENSE.txt')
    Copy-Item -LiteralPath $CloudflaredSource -Destination (Join-Path $OutputPath 'cloudflared.exe') -Force
    $CloudflaredManifest | Add-Member -NotePropertyName packaged_sha256 -NotePropertyValue $CloudflaredHash -Force
    $CloudflaredManifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $OutputPath 'cloudflared-manifest.json') -Encoding utf8
    $PackagedFFmpeg = [IO.Path]::GetFullPath((Join-Path $OutputPath 'tools\ffmpeg'))
    if (-not $PackagedFFmpeg.StartsWith($OutputPath.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'FFmpeg packaging destination must stay inside the application output directory.'
    }
    if (Test-Path -LiteralPath $PackagedFFmpeg) { Remove-Item -LiteralPath $PackagedFFmpeg -Recurse -Force }
    & $Python -X utf8 (Join-Path $PSScriptRoot 'media_tool_bundle.py') --source $FFmpegPath --destination $PackagedFFmpeg
    if ($LASTEXITCODE -ne 0) { throw 'FFmpeg packaging failed.' }
    $PackagedUntrunc = [IO.Path]::GetFullPath((Join-Path $OutputPath 'tools\untrunc'))
    if (-not $PackagedUntrunc.StartsWith($OutputPath.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'untrunc packaging destination must stay inside the application output directory.'
    }
    if (Test-Path -LiteralPath $PackagedUntrunc) { Remove-Item -LiteralPath $PackagedUntrunc -Recurse -Force }
    & $Python -X utf8 (Join-Path $PSScriptRoot 'media_tool_bundle.py') --tool untrunc --source $UntruncPath --destination $PackagedUntrunc
    if ($LASTEXITCODE -ne 0) { throw 'untrunc packaging failed.' }
    if ($EmbeddedWindow) {
        foreach ($WindowSourceArchive in @('ffmpeg-corresponding-source.zip', 'untrunc-corresponding-source.zip')) {
            Copy-Item -LiteralPath (Join-Path (Split-Path -Parent $OutputPath) $WindowSourceArchive) -Destination (Join-Path $OutputPath $WindowSourceArchive) -Force
        }
    }
}

# 工作目录只服务这一次构建：`--clean` 已让下一次不复用它，留着只是几十 MB 的中间产物。
if (Test-Path -LiteralPath $WorkPath) {
    if (-not ([IO.Path]::GetFullPath($WorkPath)).StartsWith(([IO.Path]::GetFullPath($BuildPath)).TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw '构建清理目录必须位于本项目构建目录内。'
    }
    Remove-Item -LiteralPath $WorkPath -Recurse -Force
}

$InstallerPath = $null
if ($Installer) {
    $IsccCandidates = @($env:PEACH_ISCC, (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source,
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'))
    $Iscc = $IsccCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
    if (-not $Iscc) {
        throw 'Inno Setup 6 not found. Install it with: winget install JRSoftware.InnoSetup, or set PEACH_ISCC.'
    }
    $InstallerOutput = Split-Path -Parent $OutputPath
    $InstallerScript = if ($EmbeddedWindow) { 'installer\native-window.iss' } else { 'installer\peach.iss' }
    & $Iscc /Q "/DAppVersion=$($BuildInfo.version)" "/DSourceDir=$OutputPath" "/DOutputDir=$InstallerOutput" `
        (Join-Path $PSScriptRoot $InstallerScript)
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
    $InstallerPath = Join-Path $InstallerOutput "Peach-$($BuildInfo.version)-windows-x64-setup.exe"
    if ($EmbeddedWindow) { $InstallerPath = Join-Path $InstallerOutput "PeachDesktop-$($BuildInfo.version)-windows-x64-setup.exe" }
}

[pscustomobject]@{
    Executable = Join-Path $OutputPath 'Peach.exe'
    Icon = Join-Path $ProjectRoot 'resources\peach.ico'
    Installer = $InstallerPath
}
