<#
.SYNOPSIS
    Steps 4-5 - Run the Open3D Reconstruction System on an extracted dataset.

.DESCRIPTION
    Clones the Open3D repo on first run (only run_system.py + its package are
    needed; the pip 'open3d' wheel provides the actual library), then executes:

        --make      RGBD odometry per fragment  -> local pose graphs
        --register  global fragment registration (RANSAC features + ICP)
        --refine    global pose-graph refinement (includes loop closure)
        --integrate TSDF volume integration     -> scene/integrated.ply

    Intermediate results land under <path_dataset>/fragments/ and /scene/, so if
    alignment looks wrong you can see which stage broke.

.EXAMPLE
    .\scripts\reconstruct.ps1
    .\scripts\reconstruct.ps1 -Dataset data\myscan\20260805_155421
    .\scripts\reconstruct.ps1 -Config config.json -Stages make,register,refine,integrate

.PARAMETER Dataset
    Point the run at a specific extracted dataset directory (containing color\,
    depth\, intrinsic.json). A per-dataset config is written to <Dataset>\config.json
    (base settings copied from -Config, paths overridden), and all outputs
    (fragments\, scene\) land inside that dataset dir. Omit to use -Config as-is.
#>
param(
    [string]$Config = "config.json",
    [string]$Dataset = "",
    [ValidateSet("make", "register", "refine", "integrate", "slac", "slac_integrate")]
    [string[]]$Stages = @("make", "register", "refine", "integrate")
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot   # C:\ak3d
$open3dDir = Join-Path $repoRoot "Open3D"
$runSystem = Join-Path $open3dDir "examples\python\reconstruction_system\run_system.py"

# Prefer the project venv's interpreter (the Store stub named 'python' is useless).
$venvPy = Join-Path $repoRoot ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPy) { $venvPy } else { "python" }

if (-not (Test-Path $runSystem)) {
    Write-Host "Open3D repo not found - cloning (shallow)..." -ForegroundColor Cyan
    # git writes normal progress to stderr; under ErrorActionPreference=Stop that
    # would be mis-read as a terminating error, so relax it just for the clone and
    # rely on the exit code instead.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    git clone --depth 1 https://github.com/isl-org/Open3D.git $open3dDir 2>&1 | Write-Host
    $cloneExit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($cloneExit -ne 0 -or -not (Test-Path $runSystem)) {
        Write-Error "Clone failed (exit $cloneExit) or run_system.py missing at $runSystem"
        exit 1
    }
}

$configPath = Join-Path $repoRoot $Config
if (-not (Test-Path $configPath)) { Write-Error "Config not found: $configPath"; exit 1 }

# If a dataset dir is given, derive a per-dataset config with overridden paths so
# outputs (fragments\, scene\) land inside that dataset directory.
if ($Dataset -ne "") {
    $dsFull = if ([System.IO.Path]::IsPathRooted($Dataset)) { $Dataset } else { Join-Path $repoRoot $Dataset }
    if (-not (Test-Path $dsFull)) { Write-Error "Dataset dir not found: $dsFull"; exit 1 }
    $intr = Join-Path $dsFull "intrinsic.json"
    if (-not (Test-Path $intr)) { Write-Error "intrinsic.json missing in $dsFull"; exit 1 }

    $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
    # Open3D accepts forward slashes on Windows; normalize to avoid JSON escaping.
    $cfg.path_dataset   = ($dsFull -replace '\\', '/')
    $cfg.path_intrinsic = ($intr   -replace '\\', '/')
    $derived = Join-Path $dsFull "config.json"
    # Write UTF-8 WITHOUT BOM: run_system.py opens the config with the system
    # default codec (cp949 on Korean Windows), which chokes on a UTF-8 BOM.
    $json = $cfg | ConvertTo-Json -Depth 10
    [System.IO.File]::WriteAllText($derived, $json, (New-Object System.Text.UTF8Encoding($false)))
    $configPath = $derived
    Write-Host "Dataset: $dsFull" -ForegroundColor Cyan
    Write-Host "Using derived config: $derived" -ForegroundColor DarkGray
    $outDir = $dsFull
    $sceneOut = Join-Path $dsFull "scene\integrated.ply"
} else {
    # No -Dataset: log/results follow config's path_dataset.
    $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
    $pd = $cfg.path_dataset
    $outDir = if ([System.IO.Path]::IsPathRooted($pd)) { $pd } else { Join-Path $repoRoot $pd }
    $sceneOut = Join-Path $outDir "scene\integrated.ply"
}
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir -Force | Out-Null }

# Flags for the requested stages, in canonical order.
$order = @("make", "register", "refine", "integrate", "slac", "slac_integrate")
$flags = @()
foreach ($s in $order) { if ($Stages -contains $s) { $flags += "--$s" } }

# SLAC integrate writes its own output (a point cloud when save_output_as=pointcloud).
if ($Stages -contains "slac_integrate") {
    $sceneOut = Join-Path (Split-Path -Parent $sceneOut) "..\slac\0.050\output_slac_pointcloud.ply"
    $sceneOut = [System.IO.Path]::GetFullPath($sceneOut)
}

# Log goes into the dataset (timestamp) directory alongside the results. Stamped so
# a later run (e.g. --slac) does not clobber the log of the run that produced the
# current results.
$runStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logPath = Join-Path $outDir "reconstruct_$runStamp.log"

Write-Host "Running reconstruction: $($flags -join ' ')" -ForegroundColor Cyan
Write-Host "Log -> $logPath" -ForegroundColor DarkGray
Push-Location $repoRoot   # dataset paths in config.json are relative to repo root
# run_system.py writes normal progress/tracebacks to stderr; under
# ErrorActionPreference=Stop that is mis-read as a terminating error, so relax it
# here and rely on the exit code. 2>&1 merges stderr so it is both shown and logged.
$prev = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $python $runSystem --config $configPath @flags 2>&1 | Tee-Object -FilePath $logPath
    $runExit = $LASTEXITCODE
}
finally {
    $ErrorActionPreference = $prev
    Pop-Location
}
if ($runExit -ne 0) {
    Write-Error "run_system.py exited with $runExit (see $logPath)"
    exit $runExit
}

Write-Host "Done. Result: $sceneOut" -ForegroundColor Green
Write-Host "Log:  $logPath" -ForegroundColor DarkGray
if ($Stages -contains "slac_integrate") {
    # SLAC already emits a point cloud; no mesh->pcd conversion needed.
    Write-Host "View: python scripts\view_result.py --path `"$sceneOut`"" -ForegroundColor DarkGray
} elseif ($Stages -contains "integrate") {
    $sceneDir = Split-Path -Parent $sceneOut
    Write-Host "Point cloud:  python scripts\export_pointcloud.py --input `"$sceneOut`" --output `"$(Join-Path $sceneDir 'integrated_pcd.ply')`"" -ForegroundColor DarkGray
}
