"""使用 Nuitka 一键编译免环境 Windows 独立版 EXE、Authenticode 签名并打包 ZIP / 7z 发布包。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tomllib
import zipfile

from sign_binary import sign_executable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENTRY_SCRIPT = PROJECT_ROOT / "packaging" / "entry_gui.py"
ICON_PATH = PROJECT_ROOT / "packaging" / "app_icon.ico"
TUTORIAL_SRC = PROJECT_ROOT / "packaging" / "README.ZH-CN.txt"
INNO_SCRIPT = PROJECT_ROOT / "packaging" / "installer.iss"
PYPROJECT_PATH = PROJECT_ROOT / "pyproject.toml"
BUILD_ROOT = PROJECT_ROOT / "build" / "nuitka"
DIST_ROOT = PROJECT_ROOT / "dist"
DIST_APP_DIR = DIST_ROOT / "WoWs-Mini-Relpay-Ru"
ZIP_OUT_PATH = DIST_ROOT / "WoWs-Mini-Relpay-Ru-win64.zip"
SEVENZ_OUT_PATH = DIST_ROOT / "WoWs-Mini-Relpay-Ru-win64.7z"
SETUP_EXE_OUT_PATH = DIST_ROOT / "WoWs-Mini-Relpay-Ru-Setup-win64.exe"

PUBLISHER = "starmiaoa"
PRODUCT_NAME = "WoWs-Mini-Relpay-Ru"


def _read_project_version() -> str:
    data = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    return str(data["project"]["version"]).strip()


def _to_win_four_part_version(ver: str) -> str:
    nums = [p for p in ver.split(".") if p.isdigit()]
    while len(nums) < 4:
        nums.append("0")
    return ".".join(nums[:4])


def _find_7z_exe() -> str | None:
    for name in ("7z", "7za"):
        hit = shutil.which(name)
        if hit:
            return hit
    for cand in (
        Path(r"C:\Program Files\7-Zip\7z.exe"),
        Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
    ):
        if cand.is_file():
            return str(cand)
    return None


def _find_iscc_exe() -> str | None:
    for name in ("ISCC", "iscc"):
        hit = shutil.which(name)
        if hit:
            return hit
    local_app = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ]
    if local_app:
        candidates.append(Path(local_app) / "Programs" / "Inno Setup 6" / "ISCC.exe")
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    return None


def _has_msvc() -> bool:
    if shutil.which("cl"):
        return True
    vswhere = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")
    if vswhere.is_file():
        res = subprocess.run(
            [str(vswhere), "-latest", "-products", "*", "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
            capture_output=True,
            text=True,
        )
        if res.returncode == 0 and res.stdout.strip():
            return True
    return False


def _ensure_icon() -> Path:
    if not ICON_PATH.is_file():
        from generate_icon import main as gen_icon_main

        gen_icon_main()
    return ICON_PATH


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nuitka 编译、代码签名与双格式压缩打包脚本")
    parser.add_argument("--no-sign", action="store_true", help="跳过 Authenticode 数字签名")
    parser.add_argument("--allow-unsigned", action="store_true", help="未配置证书时允许无签名通过（CI Fork 模式）")
    parser.add_argument("--skip-archives", action="store_true", help="仅编译与签名，跳过 zip/7z 打包")
    parser.add_argument("--compiler", choices=["auto", "msvc", "mingw64"], default="auto", help="C 编译器选择")
    args = parser.parse_args(argv)

    t0 = time.perf_counter()
    app_ver = _read_project_version()
    win_ver = _to_win_four_part_version(app_ver)
    icon_path = _ensure_icon()

    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    DIST_ROOT.mkdir(parents=True, exist_ok=True)

    # 裁剪未使用的重型模块，最大化缩减包体、内存与编译耗时
    excluded_modules = [
        "numpy",
        "scipy",
        "matplotlib",
        "tkinter",
        "unittest",
        "pytest",
        "PyInstaller",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickWidgets",
        "PySide6.QtSql",
        "PySide6.QtNetwork",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.QtOpenGL",
        "PySide6.QtOpenGLWidgets",
        "PySide6.QtPrintSupport",
        "PySide6.QtSvg",
        "PySide6.QtSvgWidgets",
        "PySide6.QtDesigner",
        "PySide6.QtHelp",
        "PySide6.QtCharts",
        "PySide6.QtPdf",
        "PySide6.QtPositioning",
        "PySide6.QtSensors",
        "PySide6.QtBluetooth",
        "PySide6.QtNfc",
        "PySide6.QtRemoteObjects",
        "PySide6.QtScxml",
        "PySide6.QtSpatialAudio",
        "PySide6.QtStateMachine",
        "PySide6.QtTextToSpeech",
        "PySide6.QtWebChannel",
        "PySide6.QtWebSockets",
    ]

    use_mingw = (args.compiler == "mingw64") or (args.compiler == "auto" and not _has_msvc())
    compiler_label = "MinGW64 (winlibs)" if use_mingw else "MSVC"

    print(f"[1/6] 正在使用 Nuitka ({compiler_label}) 编译 {PRODUCT_NAME} v{app_ver} ...")
    cmd = [
        sys.executable,
        "-m",
        "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        "--windows-console-mode=disable",
        "--enable-plugin=pyside6",
        "--include-qt-plugins=platforms,styles,imageformats",
        # 显式编译完整业务包（确保 GUI 懒加载的 parser/renderer/sync 子模块全部编入原生 C）
        "--include-package=korabli_minimap",
        "--include-package=Crypto.Cipher",
        "--include-package-data=imageio_ffmpeg",
        "--include-package-data=zhconv",
        f"--include-data-files={icon_path}=app_icon.ico",
        f"--windows-icon-from-ico={icon_path}",
        f"--company-name={PUBLISHER}",
        f"--product-name={PRODUCT_NAME}",
        "--file-description=Mir Korabley Minimap Replay Renderer",
        f"--file-version={win_ver}",
        f"--product-version={win_ver}",
        f"--copyright=Copyright (c) 2026 {PUBLISHER}",
        f"--output-dir={BUILD_ROOT}",
        f"--output-filename={PRODUCT_NAME}.exe",
        "--remove-output",
    ]
    if use_mingw:
        cmd.append("--mingw64")
    else:
        cmd.append("--msvc=latest")

    for mod in excluded_modules:
        cmd.append(f"--nofollow-import-to={mod}")

    cmd.append(str(ENTRY_SCRIPT))

    res = subprocess.run(cmd, cwd=PROJECT_ROOT)
    if res.returncode != 0:
        print(f"[错误] Nuitka 编译失败，退出码: {res.returncode}")
        return res.returncode

    nuitka_dist_dir = BUILD_ROOT / f"{ENTRY_SCRIPT.stem}.dist"
    if not nuitka_dist_dir.is_dir():
        print(f"[错误] 未找到 Nuitka 输出目录: {nuitka_dist_dir}")
        return 1

    # 将编译产物移至最终 dist/WoWs-Mini-Relpay-Ru/
    if DIST_APP_DIR.exists():
        shutil.rmtree(DIST_APP_DIR)
    shutil.move(str(nuitka_dist_dir), str(DIST_APP_DIR))

    exe_path = DIST_APP_DIR / f"{PRODUCT_NAME}.exe"
    if not exe_path.is_file():
        print(f"[错误] 未找到编译产物: {exe_path}")
        return 1

    print("[2/6] 正在写入便携版教程文件 README.ZH-CN.txt ...")
    if not TUTORIAL_SRC.is_file():
        print(f"[错误] 缺少教程源文件: {TUTORIAL_SRC}")
        return 1
    shutil.copyfile(TUTORIAL_SRC, DIST_APP_DIR / "README.ZH-CN.txt")

    # 确保便携包干净（不含个人缓存 data/ 或 output/）
    for extra in ("data", "output"):
        ep = DIST_APP_DIR / extra
        if ep.exists():
            shutil.rmtree(ep)

    print(f"[3/6] 正在对 {exe_path.name} 执行 Authenticode 数字签名 (提供方: {PUBLISHER}) ...")
    if not args.no_sign:
        sig_info = sign_executable(
            exe_path,
            auto_init_self_signed=(not args.allow_unsigned),
            allow_unsigned=args.allow_unsigned,
            publisher=PUBLISHER,
        )
        if sig_info:
            print(f"  签名提供方 : {sig_info.get('SignerSubject')}")
            print(f"  证书指纹   : {sig_info.get('SignerThumbprint')}")
            print(f"  时间戳服务 : {sig_info.get('TimeStamper') or '(离线无时间戳)'}")
    else:
        print("  已通过 --no-sign 跳过签名。")

    zip_mb: float | None = None
    sevenz_mb: float | None = None
    setup_mb: float | None = None
    if not args.skip_archives:
        print(f"[4/6] 正在打包 ZIP 便携包 -> {ZIP_OUT_PATH.name} ...")
        if ZIP_OUT_PATH.exists():
            ZIP_OUT_PATH.unlink()
        with zipfile.ZipFile(ZIP_OUT_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            for file_path in sorted(DIST_APP_DIR.rglob("*")):
                if file_path.is_file():
                    arcname = Path(PRODUCT_NAME) / file_path.relative_to(DIST_APP_DIR)
                    zf.write(file_path, arcname=str(arcname))
        zip_mb = ZIP_OUT_PATH.stat().st_size / (1024 * 1024)

        print(f"[5/6] 正在打包 7z 便携包 -> {SEVENZ_OUT_PATH.name} ...")
        sevenz_exe = _find_7z_exe()
        if sevenz_exe:
            if SEVENZ_OUT_PATH.exists():
                SEVENZ_OUT_PATH.unlink()
            zres = subprocess.run(
                [
                    sevenz_exe,
                    "a",
                    "-t7z",
                    "-m0=lzma2",
                    "-mx=9",
                    "-mmt=on",
                    str(SEVENZ_OUT_PATH),
                    PRODUCT_NAME,
                ],
                cwd=DIST_ROOT,
            )
            if zres.returncode != 0:
                print(f"[错误] 7-Zip 打包失败，退出码: {zres.returncode}")
                return zres.returncode
            sevenz_mb = SEVENZ_OUT_PATH.stat().st_size / (1024 * 1024)
        else:
            print("[警告] 未检测到 7-Zip (7z.exe)，跳过 .7z 打包。")

        print(f"[6/6] 正在使用 Inno Setup 编译并签名 EXE 安装包 -> {SETUP_EXE_OUT_PATH.name} ...")
        iscc_exe = _find_iscc_exe()
        if iscc_exe and INNO_SCRIPT.is_file():
            if SETUP_EXE_OUT_PATH.exists():
                SETUP_EXE_OUT_PATH.unlink()
            ires = subprocess.run(
                [
                    iscc_exe,
                    f"/DMyAppVersion={app_ver}",
                    f"/DMyDistDir={DIST_APP_DIR}",
                    f"/DMyOutputDir={DIST_ROOT}",
                    str(INNO_SCRIPT),
                ],
                cwd=PROJECT_ROOT,
            )
            if ires.returncode != 0:
                print(f"[错误] Inno Setup 编译安装包失败，退出码: {ires.returncode}")
                return ires.returncode
            if not SETUP_EXE_OUT_PATH.is_file():
                print(f"[错误] 未找到生成的安装包: {SETUP_EXE_OUT_PATH}")
                return 1
            if not args.no_sign:
                setup_sig = sign_executable(
                    SETUP_EXE_OUT_PATH,
                    auto_init_self_signed=(not args.allow_unsigned),
                    allow_unsigned=args.allow_unsigned,
                    publisher=PUBLISHER,
                )
                if setup_sig:
                    print(f"  安装包签名 : {setup_sig.get('SignerSubject')}")
            setup_mb = SETUP_EXE_OUT_PATH.stat().st_size / (1024 * 1024)
        else:
            print("[警告] 未检测到 Inno Setup (ISCC.exe)，跳过 EXE 安装包编译。")

    # 清理 Nuitka 中间构建目录 build/，保持工作区干净
    build_dir = PROJECT_ROOT / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir, ignore_errors=True)

    dir_mb = sum(p.stat().st_size for p in DIST_APP_DIR.rglob("*") if p.is_file()) / (1024 * 1024)
    dt = time.perf_counter() - t0
    print(f"\n[Nuitka 打包完成] v{app_ver} | 耗时 {dt:.1f}s")
    print(f"  独立版目录 : {DIST_APP_DIR} ({dir_mb:.1f} MB)")
    print(f"  主程序 EXE : {exe_path}")
    if zip_mb is not None:
        print(f"  ZIP 便携包 : {ZIP_OUT_PATH} ({zip_mb:.1f} MB)")
    if sevenz_mb is not None:
        print(f"  7z  便携包 : {SEVENZ_OUT_PATH} ({sevenz_mb:.1f} MB)")
    if setup_mb is not None:
        print(f"  EXE 安装包 : {SETUP_EXE_OUT_PATH} ({setup_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
