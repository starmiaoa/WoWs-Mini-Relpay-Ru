# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 便携绿色版 (--onedir) 打包配置。

打包产物无需目标电脑安装 Python、uv 或 FFmpeg，解压后双击 WoWs-Mini-Relpay-Ru.exe 即可运行。
"""

from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

SPEC_DIR = Path(SPECPATH).resolve()
PROJECT_ROOT = SPEC_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"

# 打包 imageio_ffmpeg 内置独立 ffmpeg-win-x86_64.exe 二进制及 zhconv 繁简词表
ffmpeg_datas = collect_data_files("imageio_ffmpeg", includes=["binaries/*"]) + collect_data_files("zhconv")

# 剔除未使用的巨型 Qt 子模块与测试/科学计算模块，显著减小安装包体积
EXCLUDES = [
    "pytest",
    "_pytest",
    "unittest",
    "tkinter",
    "matplotlib",
    "scipy",
    "pandas",
    "PySide6.QtNetwork",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickWidgets",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtDBus",
    "PySide6.QtPrintSupport",
    "PySide6.QtConcurrent",
    "PySide6.QtXml",
    "PySide6.QtPositioning",
    "PySide6.QtBluetooth",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtWebSockets",
    "PySide6.QtWebChannel",
    "PySide6.QtNfc",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtStateMachine",
    "PySide6.QtHelp",
    "PySide6.QtDesigner",
    "PySide6.QtUiTools",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
]

a = Analysis(
    [str(SPEC_DIR / "entry_gui.py")],
    pathex=[str(SRC_DIR)],
    binaries=[],
    datas=ffmpeg_datas,
    hiddenimports=[
        "korabli_minimap",
        "korabli_minimap.gui",
        "korabli_minimap.cli",
        "imageio_ffmpeg",
        "Crypto.Cipher.Blowfish",
        "msgpack",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WoWs-Mini-Relpay-Ru",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="WoWs-Mini-Relpay-Ru",
)
