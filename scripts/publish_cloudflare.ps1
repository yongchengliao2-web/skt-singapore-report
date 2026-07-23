param(
  [string]$ProjectName = "skt-singapore-report",
  [string]$Branch = "main",
  [string]$CompatibilityDate = "2026-07-10",
  [switch]$CreateProject,
  [switch]$SkipBuild,
  [switch]$NoVerify,
  [switch]$FetchDms
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
$PasswordWorkerPath = Join-Path $ProjectRoot "scripts\cloudflare_password_worker.js"

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
  $previousErrorActionPreference = $ErrorActionPreference
  Push-Location $WorkingDirectory
  try {
    # Native tools may write warnings and progress to stderr even when they succeed.
    $ErrorActionPreference = "Continue"
    & $FilePath @Arguments 2>&1 | Tee-Object -FilePath $LogPath -Append
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorActionPreference
    Pop-Location
  }
  if ($exitCode -ne 0) {
    throw "Command failed with exit code ${exitCode}: $FilePath"
  }
}

function Invoke-LoggedNativeWithRetry {
  param(
    [string]$FilePath,
    [string[]]$Arguments,
    [string]$WorkingDirectory = $ProjectRoot,
    [int]$MaxAttempts = 3
  )

  for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
    try {
      Invoke-LoggedNative -FilePath $FilePath -Arguments $Arguments -WorkingDirectory $WorkingDirectory
      return
    } catch {
      if ($attempt -ge $MaxAttempts) {
        throw
      }
      $delaySeconds = $attempt * 6
      Write-Step "RETRY $attempt/$MaxAttempts after ${delaySeconds}s: $($_.Exception.Message)"
      Start-Sleep -Seconds $delaySeconds
    }
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
  for ($attempt = 1; $attempt -le 12; $attempt++) {
    $code = & curl.exe -L -s -o NUL -w "%{http_code}" $PageUrl
    Write-Step "VERIFY password gate HTTP $code $PageUrl (attempt $attempt/12)"
    if ($code -eq "401") {
      $body = (& curl.exe -L -s $PageUrl) -join "`n"
      if ($body -notmatch 'action="/__auth"') {
        throw "Cloudflare password gate returned HTTP 401 without the login page."
      }
      if ($body -match "const DATA =|PAGE_DATA|library_rows") {
        throw "Cloudflare password gate leaked report data before authentication."
      }
      Write-Step "VERIFY password gate active; unauthenticated report data is blocked"
      return
    }
    if ($attempt -lt 12) {
      Start-Sleep -Seconds 5
    }
  }
  throw "Cloudflare password gate verification failed: expected HTTP 401, received HTTP $code"
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
Write-Step "START SKT Cloudflare Pages publish"
Write-Step "PROJECT $ProjectName"
Write-Step "URL $PageUrl"

if (-not $env:CLOUDFLARE_API_TOKEN) {
  throw "CLOUDFLARE_API_TOKEN is not set."
}

if (-not $SkipBuild) {
  $updateArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $ProjectRoot "scripts\update_report.ps1"))
  if ($FetchDms) {
    $updateArgs += "-FetchDms"
  }
  Invoke-LoggedNative -FilePath "powershell.exe" -Arguments $updateArgs
} else {
  Write-Step "SKIP build"
}

$IndexPath = Join-Path $ProjectRoot "index.html"
if (!(Test-Path $IndexPath)) {
  throw "Expected public index was not generated: $IndexPath"
}
$SiteDir = Join-Path $ProjectRoot "site"
if (!(Test-Path $SiteDir)) {
  throw "Expected site directory was not generated: $SiteDir"
}
if (!(Test-Path $PasswordWorkerPath)) {
  throw "Refusing to publish without the password gate: $PasswordWorkerPath"
}

Reset-DeployDir
Get-ChildItem -LiteralPath $SiteDir -Force | ForEach-Object {
  Copy-Item -LiteralPath $_.FullName -Destination $DeployDir -Recurse -Force
}
Copy-Item -LiteralPath $PasswordWorkerPath -Destination (Join-Path $DeployDir "_worker.js") -Force
Set-Content -LiteralPath (Join-Path $DeployDir "_headers") -Value @"
/*
  Cache-Control: no-store
"@ -Encoding UTF8
Write-Step "PREPARED password-protected bundle $DeployDir"

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

Invoke-LoggedNativeWithRetry -FilePath $Npx -Arguments @(
  "wrangler", "pages", "deploy", $DeployDir,
  "--project-name", $ProjectName,
  "--branch", $Branch,
  "--commit-dirty=true"
)

if (-not $NoVerify) {
  Test-LivePage
}

Write-Step "DONE $PageUrl"
