@echo off
rem ---- Build QuickLauncher.exe with PyInstaller ----
rem Usage: build.bat
rem Output: dist\QuickLauncher.exe
setlocal
cd /d "%~dp0"

where python.exe >nul 2>nul
if errorlevel 1 (
  echo Python not found. Install Python 3.10+ first.
  pause
  exit /b 1
)

python -c "import PyInstaller" 2>nul
if errorlevel 1 (
  echo Installing PyInstaller ...
  python -m pip install pyinstaller || goto :fail
)

echo Building ...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name QuickLauncher --icon icon.ico ^
  --hidden-import pystray._win32 ^
  --hidden-import comtypes --hidden-import comtypes.client ^
  --collect-submodules pycaw launcher.py || goto :fail

echo.
echo Done: dist\QuickLauncher.exe
echo Copy it next to icon.ico / README.txt, or use install.bat.
pause
exit /b 0

:fail
echo.
echo Build failed. See the messages above.
pause
exit /b 1
