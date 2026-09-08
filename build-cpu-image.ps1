<#
.SYNOPSIS
    Builds the CPU service Docker image (glm-ocr-api / ocr-cpu-glm-api) and
    saves it as a gzip-compressed tarball, ready to transfer and load with
    `docker load -i ocr-cpu-glm-api.tar.gz` on another machine.

.PARAMETER Tag
    Image tag to build and save. Defaults to "latest".

.PARAMETER KeepTar
    Keep the intermediate uncompressed .tar file after gzipping it. By
    default it is deleted once the .tar.gz is written successfully.

.EXAMPLE
    .\build-cpu-image.ps1
    .\build-cpu-image.ps1 -Tag latest -KeepTar
#>
param(
    [string]$Tag = "latest",
    [switch]$KeepTar
)

$ErrorActionPreference = "Stop"

$ImageName = "ocr-cpu-glm-api"
$ComposeFile = "docker-compose.yml"
$ComposeService = "glm-ocr-api"
$ImageRef = "${ImageName}:${Tag}"
$TarPath = "$ImageName.tar"
$GzPath = "$ImageName.tar.gz"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "docker was not found on PATH. Install Docker Desktop and try again."
    exit 1
}

if (-not (Test-Path $ComposeFile)) {
    Write-Error "$ComposeFile not found - run this script from the repo root."
    exit 1
}

Write-Host "==> Building $ImageRef via 'docker compose -f $ComposeFile build $ComposeService'..."
docker compose -f $ComposeFile build $ComposeService
if ($LASTEXITCODE -ne 0) {
    Write-Error "docker compose build failed (exit code $LASTEXITCODE)."
    exit 1
}

Write-Host "==> Saving $ImageRef to $TarPath..."
docker save -o $TarPath $ImageRef
if ($LASTEXITCODE -ne 0) {
    Write-Error "docker save failed (exit code $LASTEXITCODE)."
    exit 1
}

Write-Host "==> Compressing $TarPath to $GzPath..."
try {
    $inStream = [System.IO.File]::OpenRead((Resolve-Path $TarPath))
    $outStream = [System.IO.File]::Create($GzPath)
    $gzipStream = New-Object System.IO.Compression.GZipStream($outStream, [System.IO.Compression.CompressionLevel]::Optimal)
    $inStream.CopyTo($gzipStream)
}
finally {
    if ($gzipStream) { $gzipStream.Dispose() }
    if ($outStream) { $outStream.Dispose() }
    if ($inStream) { $inStream.Dispose() }
}

if (-not (Test-Path $GzPath)) {
    Write-Error "Compression did not produce $GzPath."
    exit 1
}

if (-not $KeepTar) {
    Remove-Item $TarPath -Force
}

$sizeMB = [math]::Round((Get-Item $GzPath).Length / 1MB, 1)
Write-Host "==> Done: $GzPath ($sizeMB MB)"
Write-Host "    Transfer this file and load it elsewhere with: docker load -i $GzPath"
