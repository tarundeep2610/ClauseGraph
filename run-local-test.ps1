<#
.SYNOPSIS
    Brings up both the CPU app stack and the GPU OCR service stack on this
    machine for local testing, using the LOCAL TEST compose/env files (no
    GPU required — gpu-ocr-api runs in its CPU-fallback mode). The prod
    compose files (docker-compose.yml in each directory) are untouched by
    this script.

.EXAMPLE
    .\run-local-test.ps1
#>

$ErrorActionPreference = "Stop"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "docker was not found on PATH. Install Docker Desktop and try again."
    exit 1
}

if (-not (Test-Path "docker-compose.local-test.yml")) {
    Write-Error "docker-compose.local-test.yml not found - run this script from the repo root."
    exit 1
}

Write-Host "==> Starting CPU app stack (docker-compose.local-test.yml)..."
docker compose -f docker-compose.local-test.yml --env-file .env.local-test up -d --build
if ($LASTEXITCODE -ne 0) {
    Write-Error "CPU stack failed to start (exit code $LASTEXITCODE)."
    exit 1
}

Write-Host "==> Starting GPU service stack in CPU-fallback mode (gpu-ocr-service/docker-compose.local-test.yml)..."
docker compose -f gpu-ocr-service/docker-compose.local-test.yml --env-file gpu-ocr-service/.env.local-test up -d --build
if ($LASTEXITCODE -ne 0) {
    Write-Error "GPU stack failed to start (exit code $LASTEXITCODE)."
    exit 1
}

Write-Host "==> Both stacks are up."
Write-Host "    CPU app:     http://localhost:12006/"
Write-Host "    GPU service: http://localhost:11610/health  (model loads lazily on first OCR request - may take a while on CPU)"
