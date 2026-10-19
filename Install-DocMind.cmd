@echo off
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\deployment\Install-DocMindCpuServer.ps1"
if errorlevel 1 (
  echo DOCMIND installation stopped. See the error above.
  pause
  exit /b 1
)
pause
