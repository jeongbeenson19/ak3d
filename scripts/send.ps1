<#
.SYNOPSIS
    Step 2 - Send a capture from this laptop to the reconstruction workstation.

.DESCRIPTION
    Launcher for scripts\transfer.py. Uploads captures\<timestamp>\capture.mkv over
    ssh to <remote_root>/<timestamp>/capture.mkv on the workstation.

    The upload resumes: if the link drops or you hit Ctrl+C, rerun the exact same
    command and it continues from the byte the workstation already has. The file
    lands as capture.mkv.part and is only renamed after both sides agree on the
    sha256, so the reconstruction scripts never see a half-written recording.

    Connection settings come from transfer.json in the repo root (copy
    transfer.example.json). Run with -Check first to confirm ssh key login works.

.EXAMPLE
    .\scripts\send.ps1 -Check

.EXAMPLE
    .\scripts\send.ps1 -Latest

.EXAMPLE
    .\scripts\send.ps1 -AllPending -DeleteLocal

.EXAMPLE
    .\scripts\send.ps1 20260820_162603
#>
param(
    # Capture directory, .mkv path, or bare timestamp name (e.g. 20260820_162603).
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$Target,
    # Test the ssh connection and the remote root, then exit.
    [switch]$Check,
    # Show which captures are already sent and which are pending, then exit.
    [switch]$List,
    # Send the newest capture.
    [switch]$Latest,
    # Send every capture that is not recorded as sent yet.
    [switch]$AllPending,
    # Resend even if .sent.json says it already arrived.
    [switch]$Force,
    # Skip the sha256 comparison (faster, less safe).
    [switch]$NoVerify,
    # Delete the local .mkv once the remote copy is verified.
    [switch]$DeleteLocal,
    # Override transfer.json for a one-off destination.
    [string]$WsHost,
    [string]$User,
    [string]$RemoteRoot
)

$repoRoot = Split-Path -Parent $PSScriptRoot   # C:\ak3d
$transferPy = Join-Path $PSScriptRoot "transfer.py"

# Prefer the project venv interpreter (the Store 'python' stub is useless).
$venvPy = Join-Path $repoRoot ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPy) { $venvPy } else { "python" }

$common = @()
if ($WsHost) { $common += @("--host", $WsHost) }
if ($User) { $common += @("--user", $User) }
if ($RemoteRoot) { $common += @("--remote-root", $RemoteRoot) }

if ($Check) {
    & $python $transferPy @common check
    exit $LASTEXITCODE
}
if ($List) {
    & $python $transferPy @common list
    exit $LASTEXITCODE
}

$pyArgs = @($transferPy) + $common + @("send")
if ($Target) { $pyArgs += $Target }
if ($Latest) { $pyArgs += "--latest" }
if ($AllPending) { $pyArgs += "--all-pending" }
if ($Force) { $pyArgs += "--force" }
if ($NoVerify) { $pyArgs += "--no-verify" }
if ($DeleteLocal) { $pyArgs += "--delete-local" }

# Nothing selected? Default to the newest capture - the usual "send what I just shot".
if (-not ($Target -or $Latest -or $AllPending)) { $pyArgs += "--latest" }

& $python @pyArgs
exit $LASTEXITCODE
