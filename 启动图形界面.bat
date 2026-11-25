@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "dist\WoWs-Mini-Relpay-Ru\WoWs-Mini-Relpay-Ru.exe" (
    start "" "dist\WoWs-Mini-Relpay-Ru\WoWs-Mini-Relpay-Ru.exe"
) else if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m korabli_minimap.gui
) else (
    echo [提示] 未检测到打包好的 EXE 或本地 .venv 环境。
    echo        - 普通玩家请直接前往 GitHub Releases 下载免安装绿色版压缩包，解压后双击 WoWs-Mini-Relpay-Ru.exe 即可使用；
    echo        - 开发者请先运行 uv sync 初始化虚拟环境。
    pause
)
