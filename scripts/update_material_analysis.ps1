param(
  [switch]$FetchDms,
  [switch]$Open
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$Python = if (Test-Path $BundledPython) { $BundledPython } else { "python" }

Push-Location $ProjectRoot
try {
  if ($FetchDms) {
    Write-Host "Refreshing SKT DMS material cache..."
    & $Python "pipelines\fetch_skt_dms_materials.py"
    if ($LASTEXITCODE -ne 0) {
      Write-Warning "DMS refresh failed. Continue with latest local cache and offsite data."
    }
  }

  Write-Host "Building SKT material analysis page..."
  & $Python "pipelines\build_skt_material_analysis.py"
  if ($LASTEXITCODE -ne 0) {
    throw "Material analysis build failed with exit code $LASTEXITCODE"
  }

  $PagePath = Join-Path $ProjectRoot "skt-material-analysis.html"
  if (!(Test-Path $PagePath)) {
    throw "Expected material page was not generated: $PagePath"
  }

  Write-Host "Done."
  Write-Host "Material page: $PagePath"

  if ($Open) {
    Start-Process $PagePath
  }
} finally {
  Pop-Location
}
