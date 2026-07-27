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
$GitHubRepository = if ($env:SKT_GITHUB_REPOSITORY) { $env:SKT_GITHUB_REPOSITORY } else { "yongchengliao2-web/skt-singapore-report" }
$MainWorkflow = "refresh-main-report.yml"
$MaxReconcileAttempts = 3
$ActiveRunStatuses = @("queued", "in_progress", "requested", "waiting", "pending")

if (!(Test-Path $SecretPath)) {
  throw "Local report session is missing. Run scripts\set_local_report_session.ps1 once."
}

if (!(Get-Command "gh.exe" -ErrorAction SilentlyContinue)) {
  throw "GitHub CLI is required to prevent main and material deployments from overwriting each other."
}

function Get-MainRefreshRuns {
  $json = & gh.exe run list `
    --repo $GitHubRepository `
    --workflow $MainWorkflow `
    --limit 20 `
    --json databaseId,status,conclusion,startedAt,updatedAt,displayTitle 2>$null
  if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect the SKT main-report workflow. Material deployment was cancelled."
  }
  if (!$json) { return @() }
  return @($json | ConvertFrom-Json)
}

function Wait-MainRefreshIdle {
  param([int]$TimeoutMinutes = 30)

  $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
  while ((Get-Date) -lt $deadline) {
    $activeRuns = @(Get-MainRefreshRuns | Where-Object { $ActiveRunStatuses -contains $_.status })
    if ($activeRuns.Count -eq 0) { return }
    $labels = ($activeRuns | ForEach-Object { "$($_.databaseId):$($_.status)" }) -join ", "
    Write-Host "Main report refresh is active ($labels). Waiting before material deployment..."
    Start-Sleep -Seconds 15
  }
  throw "Main report refresh did not become idle within $TimeoutMinutes minutes."
}

function Get-LatestMainRefreshMarker {
  $latest = Get-MainRefreshRuns | Sort-Object startedAt -Descending | Select-Object -First 1
  if (!$latest) { return "none" }
  return "$($latest.databaseId)|$($latest.updatedAt)|$($latest.status)|$($latest.conclusion)"
}

function Save-LiveMainReport {
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
    $credential = $null
  }
}

function Publish-MaterialBundle {
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
}

Push-Location $ProjectRoot
try {
  $materialArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "update_material_analysis.ps1"))
  if ($FetchDms) { $materialArgs += "-FetchDms" }
  & powershell.exe @materialArgs
  if ($LASTEXITCODE -ne 0) {
    throw "Material page build failed with exit code $LASTEXITCODE"
  }

  $reconciled = $false
  for ($attempt = 1; $attempt -le $MaxReconcileAttempts; $attempt++) {
    Wait-MainRefreshIdle
    $mainMarkerBefore = Get-LatestMainRefreshMarker
    Write-Host "Preserving the latest live main report before material publish (attempt $attempt/$MaxReconcileAttempts)..."
    Save-LiveMainReport
    Publish-MaterialBundle

    Wait-MainRefreshIdle
    $mainMarkerAfter = Get-LatestMainRefreshMarker
    if ($mainMarkerAfter -eq $mainMarkerBefore) {
      $reconciled = $true
      break
    }

    Write-Warning "A main-report refresh overlapped material deployment. Re-preserving the latest main report and publishing material again."
  }

  if (!$reconciled) {
    throw "Main and material deployments kept overlapping after $MaxReconcileAttempts attempts."
  }
} finally {
  Pop-Location
}
