param(
  [switch]$Open,
  [switch]$FetchDms
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$Python = if (Test-Path $BundledPython) { $BundledPython } else { "python" }

function Invoke-CheckedNative {
  param(
    [string]$FilePath,
    [string[]]$Arguments,
    [string]$FailureMessage
  )

  $previousErrorActionPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = "Continue"
    & $FilePath @Arguments
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }
  if ($exitCode -ne 0) {
    throw "$FailureMessage with exit code $exitCode"
  }
}

Push-Location $ProjectRoot
try {
  Write-Host "Refreshing SKT Singapore report from Google Sheet..."
  Write-Host "Refreshing SKT platform GMV and SKU units from DMS..."
  try {
    Invoke-CheckedNative -FilePath $Python `
      -Arguments @("pipelines\fetch_skt_dms_commerce.py") `
      -FailureMessage "DMS commerce refresh failed"
  } catch {
    Write-Warning "DMS commerce refresh failed. Continue with the latest DMS cache or Google Sheet fallback."
  }
  Write-Host "Refreshing SKT platform GMV, orders, and sales units from BigQuery..."
  Invoke-CheckedNative -FilePath $Python `
    -Arguments @("tools\fetch_skt_bq_platform_daily.py", "--require-live") `
    -FailureMessage "BigQuery platform cache refresh failed"
  Write-Host "Refreshing SKT onsite voucher cache from BigQuery..."
  Invoke-CheckedNative -FilePath $Python `
    -Arguments @("tools\fetch_skt_voucher_cost.py", "--require-live") `
    -FailureMessage "Voucher cache refresh failed"
  Invoke-CheckedNative -FilePath $Python `
    -Arguments @("pipelines\build_skt_alignment.py") `
    -FailureMessage "Report build failed"

  if ($FetchDms) {
    Write-Host "Refreshing SKT DMS material cache..."
    try {
      Invoke-CheckedNative -FilePath $Python `
        -Arguments @("pipelines\fetch_skt_dms_materials.py") `
        -FailureMessage "DMS refresh failed"
    } catch {
      Write-Warning "DMS refresh failed. Continue with latest local cache and offsite data."
    }
  }

  Write-Host "Building SKT material analysis page and snapshots..."
  Invoke-CheckedNative -FilePath "powershell.exe" `
    -Arguments @(
      "-NoProfile", "-ExecutionPolicy", "Bypass",
      "-File", (Join-Path $PSScriptRoot "update_material_analysis.ps1")
    ) `
    -FailureMessage "Material analysis build failed"

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
