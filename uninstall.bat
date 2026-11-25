@echo off
rem ============================================================
rem  快捷启动台 (QuickLauncher) 卸载脚本
rem  关闭程序, 删除桌面/开机自启快捷方式, 再删除安装目录。
rem ============================================================
setlocal
cd /d "%~dp0"

echo.
echo   快捷启动台 - 卸载
echo   ------------------------
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1"
set RC=%ERRORLEVEL%

echo.
if "%RC%"=="0" (
    echo   卸载完成。
) else (
    echo   卸载没有完成, 请把上面的提示发给开发者。
)
echo.
pause
