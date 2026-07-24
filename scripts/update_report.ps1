param(
  [switch]$Open,
  [switch]$FetchDms
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$Python = if (Test-Path $BundledPython) { $BundledPython } else { "python" }

Push-Location $ProjectRoot
try {
  Write-Host "Refreshing SKT Singapore report from Google Sheet..."
  & $Python "pipelines\build_skt_alignment.py"
  if ($LASTEXITCODE -ne 0) {
    throw "Report build failed with exit code $LASTEXITCODE"
  }

  if ($FetchDms) {
    Write-Host "Refreshing SKT DMS material cache..."
    & $Python "pipelines\fetch_skt_dms_materials.py"
    if ($LASTEXITCODE -ne 0) {
      Write-Warning "DMS refresh failed. Continue with latest local cache and offsite data."
    }
  }

  Write-Host "Building SKT material analysis page and snapshots..."
  & powershell.exe -NoProfile -ExecutionPolicy Bypass `
    -File (Join-Path $PSScriptRoot "update_material_analysis.ps1")
  if ($LASTEXITCODE -ne 0) {
    throw "Material analysis build failed with exit code $LASTEXITCODE"
  }

  $IndexPath = Join-Path $ProjectRoot "index.html"
  if (!(Test-Path $IndexPath)) {
    throw "Expected public index was not generated: $IndexPath"
  }

  Write-Host "Done."
  Write-Host "Public page: $IndexPath"
  Write-Host "Local copy:  $(Join-Path $ProjectRoot 'site\index.html')"
  Write-Host "Material page: $(Join-Path $ProjectRoot 'site\skt-material-analysis.html')"

  if ($Open) {
    Start-Process $IndexPath
  }
} finally {
  Pop-Location
}
