param(
    [int]$PollSeconds = 30,
    [int]$Iterations = 0
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$managedDir = Join-Path $repoRoot 'uploads\managed'
New-Item -ItemType Directory -Force -Path $managedDir | Out-Null

$arguments = @(
    (Join-Path $repoRoot 'refresh_worker.py'),
    '--managed-dir', $managedDir,
    '--poll-seconds', $PollSeconds
)

if ($Iterations -gt 0) {
    $arguments += @('--iterations', $Iterations)
}

Start-Process `
    -FilePath 'python' `
    -ArgumentList $arguments `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden

Write-Host "Refresh worker started for $repoRoot"
