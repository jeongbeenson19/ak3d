<#
.SYNOPSIS
    Step 1 - Record an Azure Kinect capture WITH a live RGB preview window.

.DESCRIPTION
    Launcher for scripts\record.py. The Azure Kinect can only be opened by one
    process, so a single pyk4a process both shows the live color feed and writes
    the .mkv - you see what you're capturing while moving around.

    Each capture goes into a new timestamped directory so previous recordings are
    never overwritten:  captures\yyyyMMdd_HHmmss\capture.mkv
    Defaults: NFOV depth, 720p color, 30 fps. Press 'q' or ESC in the window to stop.

.EXAMPLE
    .\scripts\record.ps1 -Seconds 60

.EXAMPLE
    .\scripts\record.ps1 -DepthMode WFOV_UNBINNED -Seconds 60 -ShowDepth
#>
param(
    [string]$Root = "captures",
    [ValidateSet("NFOV_UNBINNED", "NFOV_2X2BINNED", "WFOV_UNBINNED", "WFOV_2X2BINNED")]
    [string]$DepthMode = "NFOV_UNBINNED",
    [ValidateSet("720p", "1080p", "1440p", "1536p", "2160p", "3072p")]
    [string]$ColorResolution = "720p",
    [ValidateSet(5, 15, 30)]
    [int]$Rate = 30,
    # Optional fixed recording length in seconds. Omit / 0 to record until 'q'.
    [int]$Seconds = 0,
    # Also open a colorized depth window.
    [switch]$ShowDepth
)

$repoRoot = Split-Path -Parent $PSScriptRoot   # C:\ak3d
$recordPy = Join-Path $PSScriptRoot "record.py"

# Prefer the project venv interpreter (the Store 'python' stub is useless).
$venvPy = Join-Path $repoRoot ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPy) { $venvPy } else { "python" }

# Ensure k4a.dll (needed by pyk4a at runtime) is findable this session.
$sdkBin = Get-ChildItem "C:\Program Files\Azure Kinect SDK*\sdk\windows-desktop\amd64\release\bin" `
    -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
if ($sdkBin -and ($env:PATH -notlike "*$($sdkBin.FullName)*")) {
    $env:PATH += ";$($sdkBin.FullName)"
}

$pyArgs = @(
    $recordPy,
    "--root", $Root,
    "--color-resolution", $ColorResolution,
    "--depth-mode", $DepthMode,
    "--fps", "$Rate"
)
if ($Seconds -gt 0) { $pyArgs += @("--seconds", "$Seconds") }
if ($ShowDepth) { $pyArgs += "--show-depth" }

& $python @pyArgs
exit $LASTEXITCODE
