param(
  [string]$ProjectName = "skt-singapore-report",
  [switch]$SkipBuild,
  [switch]$NoVerify
)

$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "publish_cloudflare.ps1") `
  -ProjectName $ProjectName `
  -SkipBuild:$SkipBuild `
  -NoVerify:$NoVerify
