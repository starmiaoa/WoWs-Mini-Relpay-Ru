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
echo 可直接将 dist\WoWs-Mini-Relpay-Ru-win64.zip 上传到 GitHub Releases 供无 Python 环境用户解压即用！
pause
