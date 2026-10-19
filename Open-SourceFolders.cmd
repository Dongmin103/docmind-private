@echo off
setlocal
set "DocMindSourceFile=%~dp0deployment\source-folders.txt"
if not exist "%DocMindSourceFile%" (
  echo The source folders file was not found.
  echo Please open this command from the complete DOCMIND folder.
  pause
  exit /b 1
)
start "" notepad.exe "%DocMindSourceFile%"
