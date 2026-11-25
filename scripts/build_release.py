"""一键打包免环境 Windows 便携版 EXE 与可分发 ZIP / 7z 压缩包。"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = PROJECT_ROOT / "packaging" / "WoWs-Mini-Relpay-Ru.spec"
TUTORIAL_SRC = PROJECT_ROOT / "packaging" / "README.ZH-CN.txt"
DIST_APP_DIR = PROJECT_ROOT / "dist" / "WoWs-Mini-Relpay-Ru"
ZIP_OUT_PATH = PROJECT_ROOT / "dist" / "WoWs-Mini-Relpay-Ru-win64.zip"
SEVENZ_OUT_PATH = PROJECT_ROOT / "dist" / "WoWs-Mini-Relpay-Ru-win64.7z"


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


def main() -> int:
    t0 = time.perf_counter()
    print("[1/4] 正在运行 PyInstaller 构建独立绿色版 EXE ...")
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--distpath",
        str(PROJECT_ROOT / "dist"),
        "--workpath",
        str(PROJECT_ROOT / "build" / "pyinstaller"),
        str(SPEC_PATH),
    ]
    res = subprocess.run(cmd, cwd=PROJECT_ROOT)
    if res.returncode != 0:
        print(f"[错误] PyInstaller 构建失败，退出码: {res.returncode}")
        return res.returncode

    exe_path = DIST_APP_DIR / "WoWs-Mini-Relpay-Ru.exe"
    if not exe_path.is_file():
        print(f"[错误] 未找到构建产物: {exe_path}")
        return 1

    print("[2/4] 正在写入便携版教程文件 README.ZH-CN.txt ...")
    if not TUTORIAL_SRC.is_file():
        print(f"[错误] 缺少教程源文件: {TUTORIAL_SRC}")
        return 1
    shutil.copyfile(TUTORIAL_SRC, DIST_APP_DIR / "README.ZH-CN.txt")

    # 确保便携包干净（不包含开发者个人缓存 data/ 或 output/）
    for extra in ("data", "output"):
        ep = DIST_APP_DIR / extra
        if ep.exists():
            shutil.rmtree(ep)

    print(f"[3/4] 正在打包 ZIP 发布包 -> {ZIP_OUT_PATH.name} ...")
    if ZIP_OUT_PATH.exists():
        ZIP_OUT_PATH.unlink()

    with zipfile.ZipFile(ZIP_OUT_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file_path in sorted(DIST_APP_DIR.rglob("*")):
            if file_path.is_file():
                arcname = Path("WoWs-Mini-Relpay-Ru") / file_path.relative_to(DIST_APP_DIR)
                zf.write(file_path, arcname=str(arcname))

    print(f"[4/4] 正在打包 7z 发布包 -> {SEVENZ_OUT_PATH.name} ...")
    sevenz_exe = _find_7z_exe()
    sevenz_mb: float | None = None
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
                "WoWs-Mini-Relpay-Ru",
            ],
            cwd=PROJECT_ROOT / "dist",
        )
        if zres.returncode != 0:
            print(f"[错误] 7-Zip 打包失败，退出码: {zres.returncode}")
            return zres.returncode
        sevenz_mb = SEVENZ_OUT_PATH.stat().st_size / (1024 * 1024)
    else:
        print("[警告] 未检测到 7-Zip (7z.exe)，跳过 .7z 打包。")

    dir_mb = sum(p.stat().st_size for p in DIST_APP_DIR.rglob("*") if p.is_file()) / (1024 * 1024)
    zip_mb = ZIP_OUT_PATH.stat().st_size / (1024 * 1024)

    # 清理 PyInstaller 中间构建目录 build/，避免误点中间产物报错
    build_dir = PROJECT_ROOT / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir, ignore_errors=True)

    dt = time.perf_counter() - t0
    print(f"\n[打包完成] 耗时 {dt:.1f}s")
    print(f"  绿色版目录 : {DIST_APP_DIR} ({dir_mb:.1f} MB)")
    print(f"  主程序 EXE : {exe_path}")
    print(f"  ZIP 压缩包 : {ZIP_OUT_PATH} ({zip_mb:.1f} MB)")
    if sevenz_mb is not None:
        print(f"  7z  压缩包 : {SEVENZ_OUT_PATH} ({sevenz_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
