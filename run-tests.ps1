<#
.SYNOPSIS
    Runs the full test suite (backend pytest + frontend vitest) in one go.

.EXAMPLE
    .\run-tests.ps1
#>

$ErrorActionPreference = "Stop"
$repoRoot = $PSScriptRoot

Write-Host "==> Backend tests (pytest)..." -ForegroundColor Cyan
Push-Location $repoRoot
try {
    py -3 -m pytest tests/ -q
    $backendExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "==> Frontend tests (vitest)..." -ForegroundColor Cyan
Push-Location (Join-Path $repoRoot "frontend")
try {
    npx vitest run
    $frontendExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

Write-Host ""
if ($backendExitCode -eq 0 -and $frontendExitCode -eq 0) {
    Write-Host "==> All tests passed." -ForegroundColor Green
    exit 0
} else {
    Write-Host "==> FAILURES: backend exit=$backendExitCode, frontend exit=$frontendExitCode" -ForegroundColor Red
    exit 1
}
