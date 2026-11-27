@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到开发环境 .venv，请先运行: uv sync --extra dev
    pause
    exit /b 1
)
".venv\Scripts\python.exe" "scripts\build_release.py"
if errorlevel 1 (
    pause
    exit /b 1
)
echo.
echo Nuitka 编译、签名及全套发布包构建完成：
echo   - dist\WoWs-Mini-Relpay-Ru-Setup-win64.exe  (安装包)
echo   - dist\WoWs-Mini-Relpay-Ru-win64.7z        (7z 便携包)
echo   - dist\WoWs-Mini-Relpay-Ru-win64.zip       (ZIP 便携包)
pause
