param(
  [string]$ProjectName = "skt-singapore-report",
  [switch]$FetchDms,
  [switch]$NoVerify
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$Python = if (Test-Path $BundledPython) { $BundledPython } else { "python" }
$SecretPath = Join-Path $env:LOCALAPPDATA "SKTReport\report-session.clixml"
$BaseUrl = "https://$ProjectName.pages.dev/"

if (!(Test-Path $SecretPath)) {
  throw "Local report session is missing. Run scripts\set_local_report_session.ps1 once."
}

Push-Location $ProjectRoot
try {
  $materialArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "update_material_analysis.ps1"))
  if ($FetchDms) { $materialArgs += "-FetchDms" }
  & powershell.exe @materialArgs
  if ($LASTEXITCODE -ne 0) {
    throw "Material page build failed with exit code $LASTEXITCODE"
  }

  $credential = Import-Clixml -LiteralPath $SecretPath
  $env:SKT_REPORT_SESSION_TOKEN = $credential.GetNetworkCredential().Password
  try {
    & $Python "scripts\download_protected_assets.py" `
      --base-url $BaseUrl `
      --asset "/=site/index.html" `
      --asset "/skt-onsite-offsite-alignment.html=site/skt-onsite-offsite-alignment.html"
    if ($LASTEXITCODE -ne 0) {
      throw "Could not preserve the live main report. Material deployment was cancelled."
    }
  } finally {
    Remove-Item Env:SKT_REPORT_SESSION_TOKEN -ErrorAction SilentlyContinue
  }

  $publishArgs = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $PSScriptRoot "publish_cloudflare.ps1"),
    "-ProjectName", $ProjectName,
    "-SkipBuild"
  )
  if ($NoVerify) { $publishArgs += "-NoVerify" }
  & powershell.exe @publishArgs
  if ($LASTEXITCODE -ne 0) {
    throw "Material deployment failed with exit code $LASTEXITCODE"
  }
} finally {
  Pop-Location
}
