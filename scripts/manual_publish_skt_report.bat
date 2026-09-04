@echo off
setlocal

cd /d "%~dp0\.."

echo Refreshing and publishing SKT Singapore report and materials...
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\publish_report.ps1" -FetchDms
if errorlevel 1 (
  echo.
  echo Publish failed. Please check the latest log under publish_logs.
  pause
  exit /b 1
)

echo.
echo Publish completed. Opening online report and materials...
start "" "https://skt-singapore-report.pages.dev/"
start "" "https://skt-singapore-report.pages.dev/skt-material-analysis.html"

echo.
pause
