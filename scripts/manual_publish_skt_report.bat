@echo off
setlocal

cd /d "%~dp0\.."

echo Refreshing and publishing SKT Singapore material page...
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\publish_material_cloudflare.ps1" -FetchDms
if errorlevel 1 (
  echo.
  echo Publish failed. Please check the latest log under publish_logs.
  pause
  exit /b 1
)

echo.
echo Publish completed. Opening online pages...
start "" "https://skt-singapore-report.pages.dev/"
start "" "https://skt-singapore-report.pages.dev/skt-material-analysis.html"

echo.
pause
