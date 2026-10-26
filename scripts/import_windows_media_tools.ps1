param(
    [string]$DataRoot = '',
    [string[]]$SearchRoots = @(),
    [string]$ArchivePath = ''
)

$ErrorActionPreference = 'Stop'
if (-not $DataRoot) {
    $DataRoot = Join-Path $env:LOCALAPPDATA 'Peach\peach-data'
    $LocationRecord = Join-Path $DataRoot 'state\data-root.txt'
    if (Test-Path -LiteralPath $LocationRecord -PathType Leaf) {
        $SelectedRoot = (Get-Content -LiteralPath $LocationRecord -Raw -Encoding utf8).Trim()
        if ($SelectedRoot -and (Test-Path -LiteralPath $SelectedRoot -PathType Container)) {
            $DataRoot = $SelectedRoot
        }
    }
}
$DataRoot = [IO.Path]::GetFullPath($DataRoot)
if (-not (Test-Path -LiteralPath $DataRoot -PathType Container)) {
    throw "Peach data directory not found: $DataRoot. Pass -DataRoot with the directory shown in Peach Configuration."
}

function Find-PeachBinary([string]$Name, [string[]]$Roots) {
    foreach ($Root in $Roots) {
        if (-not $Root -or -not (Test-Path -LiteralPath $Root -PathType Container)) { continue }
        $Matches = @(Get-ChildItem -LiteralPath $Root -Filter $Name -File -Recurse -Depth 6 -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTimeUtc -Descending)
        foreach ($Match in $Matches) {
            # Chocolatey bin contains launch shims; select the tools in lib instead.
            if ($Match.FullName -match '[\\/]chocolatey[\\/]bin[\\/]') { continue }
            $RealPath = $Match.FullName
            if ($Match.LinkType -and $Match.Target) {
                $RealPath = [string]@($Match.Target)[0]
                if (-not [IO.Path]::IsPathRooted($RealPath)) { $RealPath = Join-Path $Match.DirectoryName $RealPath }
            }
            if (-not (Test-Path -LiteralPath $RealPath -PathType Leaf)) { continue }
            if ($Name -eq 'ffmpeg.exe' -and -not (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $RealPath) 'ffprobe.exe') -PathType Leaf)) { continue }
            $Stream = [IO.File]::OpenRead($RealPath)
            try { $IsExecutable = ($Stream.ReadByte() -eq 77 -and $Stream.ReadByte() -eq 90) }
            finally { $Stream.Dispose() }
            if ($IsExecutable) { return [IO.Path]::GetFullPath($RealPath) }
        }
    }
    return $null
}

$Roots = @($SearchRoots)
if ($Roots.Count -eq 0) {
    $Roots = @(
        "$env:LOCALAPPDATA\Microsoft\WinGet\Packages",
        "$env:ProgramFiles\WinGet\Packages",
        "$env:USERPROFILE\scoop\apps\ffmpeg\current",
        "$env:USERPROFILE\scoop\apps\ffmpeg-essentials\current",
        "$env:ProgramData\scoop\apps\ffmpeg\current",
        "$env:ProgramData\chocolatey\lib\ffmpeg",
        "$env:ProgramData\chocolatey\lib\ffmpeg-full",
        "$env:ProgramFiles\ffmpeg", "$env:LOCALAPPDATA\Programs\ffmpeg", 'C:\ffmpeg', 'C:\FF',
        "$env:USERPROFILE\Downloads"
    )
    foreach ($ToolName in @('ffmpeg.exe', 'ffprobe.exe', 'untrunc.exe')) {
        $Command = Get-Command $ToolName -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($Command) { $Roots += Split-Path -Parent $Command.Source }
    }
}

function Install-PeachBinary([string]$Source, [string]$RelativeDirectory, [string[]]$Names) {
    $SourceDirectory = Split-Path -Parent $Source
    $TargetDirectory = Join-Path (Join-Path $DataRoot 'tools') $RelativeDirectory
    $Copies = @()
    foreach ($Name in $Names) {
        $Candidate = Join-Path $SourceDirectory $Name
        if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) { throw "Companion tool not found: $Candidate" }
        $Copies += Get-Item -LiteralPath $Candidate
    }
    $Copies += @(Get-ChildItem -LiteralPath $SourceDirectory -Filter '*.dll' -File -ErrorAction SilentlyContinue)
    New-Item -ItemType Directory -Path $TargetDirectory -Force | Out-Null
    $BackupDirectory = Join-Path $DataRoot ('tools\import-backup-' + [guid]::NewGuid().ToString('N'))
    foreach ($File in $Copies) {
        $Target = Join-Path $TargetDirectory $File.Name
        if ([IO.Path]::GetFullPath($File.FullName) -eq [IO.Path]::GetFullPath($Target)) { continue }
        if (Test-Path -LiteralPath $Target -PathType Leaf) {
            if ((Get-FileHash -LiteralPath $Target).Hash -eq (Get-FileHash -LiteralPath $File.FullName).Hash) { continue }
            New-Item -ItemType Directory -Path $BackupDirectory -Force | Out-Null
            Copy-Item -LiteralPath $Target -Destination (Join-Path $BackupDirectory $File.Name)
        }
        Copy-Item -LiteralPath $File.FullName -Destination $Target -Force
        if ((Get-FileHash -LiteralPath $Target).Hash -ne (Get-FileHash -LiteralPath $File.FullName).Hash) {
            throw "File copy verification failed: $Target"
        }
    }
    Write-Output "READY: $TargetDirectory"
}

Write-Output "Peach data: $DataRoot"
$ExtractionRoot = $null
try {
    $FFmpeg = Find-PeachBinary 'ffmpeg.exe' $Roots
    if (-not $FFmpeg) {
        if (-not $ArchivePath) {
            $ArchiveFolders = @($SearchRoots)
            if ($ArchiveFolders.Count -eq 0) { $ArchiveFolders = @("$env:USERPROFILE\Downloads", $PSScriptRoot, (Split-Path -Parent $PSScriptRoot)) }
            foreach ($Folder in $ArchiveFolders) {
                $CandidateArchive = Join-Path $Folder 'ffmpeg-9.0.2-essentials_build.zip'
                if (Test-Path -LiteralPath $CandidateArchive -PathType Leaf) { $ArchivePath = $CandidateArchive; break }
            }
        }
        if ($ArchivePath) {
            $ExpectedHash = '60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba'
            if ((Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $ExpectedHash) {
                throw 'The FFmpeg ZIP does not match the verified Gyan 9.0.2 essentials download. Download the exact linked file again.'
            }
            $ExtractionRoot = Join-Path ([IO.Path]::GetTempPath()) ('peach-ffmpeg-import-' + [guid]::NewGuid().ToString('N'))
            Expand-Archive -LiteralPath $ArchivePath -DestinationPath $ExtractionRoot
            $FFmpeg = Find-PeachBinary 'ffmpeg.exe' @($ExtractionRoot)
        }
    }
    if ($FFmpeg) {
        Write-Output "Found FFmpeg: $FFmpeg"
        Install-PeachBinary $FFmpeg 'ffmpeg\bin' @('ffmpeg.exe', 'ffprobe.exe')
    } else {
        Write-Output 'NOT FOUND: ffmpeg.exe. Put ffmpeg-9.0.2-essentials_build.zip in Downloads, then run this script again.'
    }
    $Untrunc = Find-PeachBinary 'untrunc.exe' $Roots
    if ($Untrunc) {
        Write-Output "Found untrunc: $Untrunc"
        Install-PeachBinary $Untrunc 'untrunc' @('untrunc.exe')
    } else {
        Write-Output 'NOT FOUND: untrunc.exe. This does not affect FFmpeg detection.'
    }
    Write-Output 'Refresh Peach Configuration. If a running task still reports missing tools, restart Peach.'
    if (-not $FFmpeg) { exit 1 }
} finally {
    if ($ExtractionRoot -and (Test-Path -LiteralPath $ExtractionRoot)) {
        $ResolvedExtraction = [IO.Path]::GetFullPath($ExtractionRoot)
        $ResolvedTemporary = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
        if (-not $ResolvedExtraction.StartsWith($ResolvedTemporary, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Temporary extraction directory is outside TEMP; cleanup refused.'
        }
        Remove-Item -LiteralPath $ResolvedExtraction -Recurse -Force
    }
}
