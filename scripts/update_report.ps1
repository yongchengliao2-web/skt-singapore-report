param(
  [switch]$Open
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

  $IndexPath = Join-Path $ProjectRoot "index.html"
  if (!(Test-Path $IndexPath)) {
    throw "Expected public index was not generated: $IndexPath"
  }

  Write-Host "Done."
  Write-Host "Public page: $IndexPath"
  Write-Host "Local copy:  $(Join-Path $ProjectRoot 'site\index.html')"

  if ($Open) {
    Start-Process $IndexPath
  }
} finally {
  Pop-Location
}
