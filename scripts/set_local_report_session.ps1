param(
  [SecureString]$SessionToken
)

$ErrorActionPreference = "Stop"
$SecretDir = Join-Path $env:LOCALAPPDATA "SKTReport"
$SecretPath = Join-Path $SecretDir "report-session.clixml"

if (-not $SessionToken) {
  $SessionToken = Read-Host "SKT report session token" -AsSecureString
}

New-Item -ItemType Directory -Force -Path $SecretDir | Out-Null
$credential = [PSCredential]::new("skt-report-session", $SessionToken)
$credential | Export-Clixml -LiteralPath $SecretPath
Write-Host "Encrypted local report session saved for the current Windows user."
