[CmdletBinding()]
param(
    [ValidateSet('full', 'auto', 'follow', 'catalog', 'media', 'sync', 'metadata', 'tooling', 'web', 'checks', 'core', 'packaging')]
    [string]$Scope = 'auto',
    [switch]$Fresh,
    [string[]]$Focus = @(),
    [string]$Base = 'master',
    [int]$ShardIndex = 0,
    [int]$ShardCount = 1,
    # 本机同时跑几个分片子进程：auto 按核数定；-Jobs 1 退回串行。CI 分片模式下运行器忽略它。
    [string]$Jobs = 'auto'
)

$ErrorActionPreference = 'Stop'
$WorktreeRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$GitCommonRaw = (& git -C $WorktreeRoot rev-parse --git-common-dir).Trim()
if ($LASTEXITCODE -ne 0 -or -not $GitCommonRaw) {
    throw '无法定位 Peach 主工作树。'
}

if ([IO.Path]::IsPathRooted($GitCommonRaw)) {
    $GitCommon = (Resolve-Path -LiteralPath $GitCommonRaw).Path
} else {
    $GitCommon = (Resolve-Path -LiteralPath (Join-Path $WorktreeRoot $GitCommonRaw)).Path
}

$MainRoot = Split-Path -Parent $GitCommon
$Python = Join-Path $MainRoot '.venv\Scripts\python.exe'
$LocalPython = Join-Path $WorktreeRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $LocalPython -PathType Leaf) { $Python = $LocalPython }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Peach 主项目 venv 不存在：$Python"
}

$SourceRoot = (Resolve-Path -LiteralPath (Join-Path $WorktreeRoot 'src')).Path
$PreviousPythonPath = $env:PYTHONPATH
$PreviousPythonIoEncoding = $env:PYTHONIOENCODING
$PreviousPath = $env:PATH
$env:PYTHONPATH = $SourceRoot
$env:PYTHONIOENCODING = 'utf-8'

# Git for Windows 自带项目证书测试所需的 OpenSSL，但默认不会把 usr\bin 放进
# 用户 PATH。只对本次测试进程补齐，不修改系统或用户环境变量。
if (-not (Get-Command openssl.exe -ErrorAction SilentlyContinue)) {
    $GitOpenSslDir = 'C:\Program Files\Git\usr\bin'
    if (Test-Path -LiteralPath (Join-Path $GitOpenSslDir 'openssl.exe') -PathType Leaf) {
        $env:PATH = "$GitOpenSslDir;$env:PATH"
    }
}

Push-Location $WorktreeRoot
try {
    & $Python -X utf8 scripts\test_environment.py $WorktreeRoot $MainRoot
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $Python = $LocalPython
    $LoadedModule = (& $Python -c 'import peach; print(peach.__file__)').Trim()
    if ($LASTEXITCODE -ne 0 -or -not $LoadedModule) {
        throw '无法导入当前工作树中的 peach。'
    }
    $LoadedPath = (Resolve-Path -LiteralPath $LoadedModule).Path
    $ExpectedPrefix = $SourceRoot.TrimEnd('\') + '\'
    if (-not $LoadedPath.StartsWith($ExpectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "测试加载了错误源码：$LoadedPath；预期位于 $SourceRoot"
    }

    Write-Host "Peach source: $LoadedPath"
    $PeachTestExtra = @()
    if ($Fresh) { $PeachTestExtra += '--fresh' }
    foreach ($PeachFocusSelector in $Focus) { $PeachTestExtra += @('--focus', $PeachFocusSelector) }
    & $Python scripts\test_runner.py --scope $Scope --base $Base --shard-index $ShardIndex --shard-count $ShardCount --jobs $Jobs @PeachTestExtra
    exit $LASTEXITCODE
} finally {
    Pop-Location
    if ($null -eq $PreviousPythonIoEncoding) {
        Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONIOENCODING = $PreviousPythonIoEncoding
    }
    if ($null -eq $PreviousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    } else {
        $env:PYTHONPATH = $PreviousPythonPath
    }
    $env:PATH = $PreviousPath
}
