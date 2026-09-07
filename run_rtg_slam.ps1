[CmdletBinding()]
param(
    [string]$Config = "configs/replica/office0.yaml",
    [switch]$Multiprocess,
    [string]$CondaEnvironment = "RTG-SLAM"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$rtgRoot = Join-Path $repoRoot "external/RTG-SLAM"

if (-not (Test-Path -LiteralPath $rtgRoot -PathType Container)) {
    throw "RTG-SLAM directory was not found: $rtgRoot"
}

$configPath = Join-Path $rtgRoot $Config
if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
    throw "Configuration file was not found: $configPath"
}

$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
if (-not $wsl) {
    throw "WSL is required. Install WSL and the RTG-SLAM Conda environment first."
}

$linuxRtgRoot = (& wsl.exe wslpath -a -u ($rtgRoot -replace '\\', '/')).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($linuxRtgRoot)) {
    throw "Could not convert the RTG-SLAM path for WSL."
}

$entryPoint = if ($Multiprocess) { "slam_mp.py" } else { "slam.py" }
$escapedConfig = $Config.Replace("'", "'\"'\"'")
$escapedEnv = $CondaEnvironment.Replace("'", "'\"'\"'")
$command = "cd '$linuxRtgRoot' && conda run --no-capture-output -n '$escapedEnv' python '$entryPoint' --config '$escapedConfig'"

Write-Host "Running RTG-SLAM"
Write-Host "  Config: $Config"
Write-Host "  Mode:   $(if ($Multiprocess) { 'multiprocess' } else { 'single process' })"
& wsl.exe bash -lc $command
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
