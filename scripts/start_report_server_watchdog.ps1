param(
  [int]$Port = 8917,
  [string]$Bind = "127.0.0.1"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$SiteDir = Join-Path $ProjectRoot "site"
$LogDir = Join-Path $ProjectRoot "output\logs"
$Python = "C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if (!(Test-Path $Python)) {
  $Python = "python"
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$WatchdogLog = Join-Path $LogDir "skt_report_server_watchdog.log"
$StdoutLog = Join-Path $LogDir "skt_report_server_stdout.log"
$StderrLog = Join-Path $LogDir "skt_report_server_stderr.log"

function Write-Log {
  param([string]$Message)
  $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  Add-Content -LiteralPath $WatchdogLog -Value "[$timestamp] $Message" -Encoding UTF8
}

Write-Log "watchdog started for $Bind`:$Port, site=$SiteDir"

while ($true) {
  try {
    $args = @(
      "-m", "http.server", "$Port",
      "--bind", $Bind,
      "--directory", $SiteDir
    )
    $process = Start-Process -FilePath $Python `
      -ArgumentList $args `
      -WorkingDirectory $ProjectRoot `
      -WindowStyle Hidden `
      -RedirectStandardOutput $StdoutLog `
      -RedirectStandardError $StderrLog `
      -PassThru

    Write-Log "server started pid=$($process.Id)"
    $process.WaitForExit()
    Write-Log "server exited pid=$($process.Id) code=$($process.ExitCode); restarting in 2s"
  } catch {
    Write-Log "server start failed: $($_.Exception.Message); retrying in 2s"
  }

  Start-Sleep -Seconds 2
}
