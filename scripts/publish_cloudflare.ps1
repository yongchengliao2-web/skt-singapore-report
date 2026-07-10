param(
  [string]$ProjectName = "skt-singapore-report",
  [string]$Branch = "main",
  [string]$CompatibilityDate = "2026-07-10",
  [switch]$CreateProject,
  [switch]$SkipBuild,
  [switch]$NoVerify
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$DeployDir = Join-Path $ProjectRoot "cloudflare_pages"
$LogDir = Join-Path $ProjectRoot "publish_logs"
$RunStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogDir "cloudflare_publish_$RunStamp.log"
$BundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
$Python = if (Test-Path $BundledPython) { $BundledPython } else { "python" }
$Npx = "npx.cmd"
$PageUrl = "https://$ProjectName.pages.dev/"

function Write-Step {
  param([string]$Message)
  $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $Message"
  Write-Host $line
  Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

function Invoke-LoggedNative {
  param(
    [string]$FilePath,
    [string[]]$Arguments,
    [string]$WorkingDirectory = $ProjectRoot
  )

  Write-Step "RUN $FilePath $($Arguments -join ' ')"
  Push-Location $WorkingDirectory
  try {
    & $FilePath @Arguments 2>&1 | Tee-Object -FilePath $LogPath -Append
    $exitCode = $LASTEXITCODE
  } finally {
    Pop-Location
  }
  if ($exitCode -ne 0) {
    throw "Command failed with exit code ${exitCode}: $FilePath"
  }
}

function Reset-DeployDir {
  $resolvedRoot = (Resolve-Path $ProjectRoot).Path
  $parent = Split-Path -Parent $DeployDir
  $resolvedParent = (Resolve-Path $parent).Path
  if ($resolvedParent -ne $resolvedRoot) {
    throw "Refusing to clear deploy directory outside project root: $DeployDir"
  }

  if (Test-Path $DeployDir) {
    Remove-Item -LiteralPath $DeployDir -Recurse -Force
  }
  New-Item -ItemType Directory -Force -Path $DeployDir | Out-Null
}

function Test-LivePage {
  $code = & curl.exe -L -s -o NUL -w "%{http_code}" $PageUrl
  Write-Step "VERIFY HTTP $code $PageUrl"
  if ($code -ne "200") {
    throw "Cloudflare page verification failed: HTTP $code"
  }
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Write-Step "START SKT Cloudflare Pages publish"
Write-Step "PROJECT $ProjectName"
Write-Step "URL $PageUrl"

if (-not $env:CLOUDFLARE_API_TOKEN) {
  throw "CLOUDFLARE_API_TOKEN is not set."
}

if (-not $SkipBuild) {
  Invoke-LoggedNative -FilePath $Python -Arguments @("pipelines\build_skt_alignment.py")
} else {
  Write-Step "SKIP build"
}

$IndexPath = Join-Path $ProjectRoot "index.html"
if (!(Test-Path $IndexPath)) {
  throw "Expected public index was not generated: $IndexPath"
}

Reset-DeployDir
Copy-Item -LiteralPath $IndexPath -Destination (Join-Path $DeployDir "index.html") -Force
Set-Content -LiteralPath (Join-Path $DeployDir "_headers") -Value @"
/*
  Cache-Control: no-store
"@ -Encoding UTF8
Write-Step "PREPARED $DeployDir"

if ($CreateProject) {
  try {
    Invoke-LoggedNative -FilePath $Npx -Arguments @(
      "wrangler", "pages", "project", "create", $ProjectName,
      "--production-branch", $Branch,
      "--compatibility-date", $CompatibilityDate
    )
  } catch {
    Write-Step "PROJECT create skipped or failed; continuing to deploy. $($_.Exception.Message)"
  }
}

Invoke-LoggedNative -FilePath $Npx -Arguments @(
  "wrangler", "pages", "deploy", $DeployDir,
  "--project-name", $ProjectName,
  "--branch", $Branch,
  "--commit-dirty=true"
)

if (-not $NoVerify) {
  Test-LivePage
}

Write-Step "DONE $PageUrl"
