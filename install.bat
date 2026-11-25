@echo off
rem ============================================================
rem  快捷启动台 (QuickLauncher) 安装脚本
rem  把程序装到当前用户目录, 创建桌面快捷方式, 可选开机自启。
rem  不需要管理员权限, 也不会写系统目录。
rem ============================================================
setlocal
cd /d "%~dp0"

echo.
echo   快捷启动台 - 安装
echo   ------------------------
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
set RC=%ERRORLEVEL%

echo.
if "%RC%"=="0" (
    echo   安装完成。
) else (
    echo   安装没有完成, 请把上面的提示发给开发者。
)
echo.
pause
