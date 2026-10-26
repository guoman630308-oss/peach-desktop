param(
    [Parameter(Mandatory = $true)]
    [string[]]$Paths,
    [string]$Language = "en-US"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Globalization.Language, Windows.Foundation, ContentType = WindowsRuntime]

$peachAsTask = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object {
        $_.Name -eq "AsTask" -and $_.IsGenericMethod -and
        $_.GetParameters().Count -eq 1
    } |
    Select-Object -First 1

function Wait-PeachWinRt {
    param($Operation, [Type]$ResultType)
    $peachTask = $peachAsTask.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    try {
        $peachTask.Wait()
    }
    catch {
        $peachInner = $peachTask.Exception
        while ($peachInner.InnerException) { $peachInner = $peachInner.InnerException }
        throw $peachInner
    }
    return $peachTask.Result
}

$peachLanguage = New-Object Windows.Globalization.Language $Language
$peachEngine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($peachLanguage)
if ($null -eq $peachEngine) {
    throw "Windows OCR language unavailable: $Language"
}

$peachResults = foreach ($peachPath in $Paths) {
    $peachStream = $null
    $peachBitmap = $null
    try {
        $peachFullPath = [System.IO.Path]::GetFullPath($peachPath)
        $peachFile = Wait-PeachWinRt (
            [Windows.Storage.StorageFile]::GetFileFromPathAsync($peachFullPath)
        ) ([Windows.Storage.StorageFile])
        $peachStream = Wait-PeachWinRt (
            $peachFile.OpenAsync([Windows.Storage.FileAccessMode]::Read)
        ) ([Windows.Storage.Streams.IRandomAccessStream])
        $peachDecoder = Wait-PeachWinRt (
            [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($peachStream)
        ) ([Windows.Graphics.Imaging.BitmapDecoder])
        $peachBitmap = Wait-PeachWinRt (
            $peachDecoder.GetSoftwareBitmapAsync()
        ) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $peachRecognized = Wait-PeachWinRt (
            $peachEngine.RecognizeAsync($peachBitmap)
        ) ([Windows.Media.Ocr.OcrResult])
        [pscustomobject]@{
            path = $peachFullPath
            text = $peachRecognized.Text
            lines = @($peachRecognized.Lines | ForEach-Object { $_.Text })
            error = ""
        }
    }
    catch {
        [pscustomobject]@{
            path = $peachPath
            text = ""
            lines = @()
            error = $_.Exception.Message
        }
    }
    finally {
        if ($null -ne $peachBitmap) { $peachBitmap.Dispose() }
        if ($null -ne $peachStream) { $peachStream.Dispose() }
    }
}

ConvertTo-Json -InputObject @($peachResults) -Compress -Depth 4
