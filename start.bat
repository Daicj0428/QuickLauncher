@echo off
cd /d "%~dp0"
rem ---- Prefer the compiled exe, fall back to the Python source ----
if exist "QuickLauncher.exe" (
  start "" "QuickLauncher.exe"
  exit /b 0
)
if exist "dist\QuickLauncher.exe" (
  start "" "dist\QuickLauncher.exe"
  exit /b 0
)
set "PY="
where pythonw.exe >nul 2>nul && set "PY=pythonw.exe"
if not defined PY ( where pyw.exe >nul 2>nul && set "PY=pyw.exe" )
if not defined PY ( where python.exe >nul 2>nul && set "PY=python.exe" )
if not defined PY (
  echo QuickLauncher.exe not found and Python not found.
  echo Please put QuickLauncher.exe in this folder, or install Python 3.10+.
  pause
  exit /b 1
)
start "" %PY% launcher.py
