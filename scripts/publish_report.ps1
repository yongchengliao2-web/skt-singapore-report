param(
  [string]$Message = "Refresh SKT Singapore report"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

Push-Location $ProjectRoot
try {
  & (Join-Path $PSScriptRoot "update_report.ps1")
  if ($LASTEXITCODE -ne 0) {
    throw "Local refresh failed with exit code $LASTEXITCODE"
  }

  $remote = git remote get-url origin 2>$null
  if (!$remote) {
    throw "Git remote origin is not configured. Create or add a GitHub repository first."
  }

  git add .gitignore .nojekyll AGENTS.md README.md config index.html pipelines scripts
  git diff --cached --quiet
  if ($LASTEXITCODE -eq 0) {
    Write-Host "No publishable changes."
  } else {
    git commit -m $Message
  }

  git push origin HEAD:main
  Write-Host "Published to origin main."
} finally {
  Pop-Location
}
