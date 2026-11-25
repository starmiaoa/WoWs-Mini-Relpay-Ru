"""一键打包免环境 Windows 便携版 EXE 与可分发 ZIP 压缩包。"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = PROJECT_ROOT / "packaging" / "WoWs-Mini-Relpay-Ru.spec"
DIST_APP_DIR = PROJECT_ROOT / "dist" / "WoWs-Mini-Relpay-Ru"
ZIP_OUT_PATH = PROJECT_ROOT / "dist" / "WoWs-Mini-Relpay-Ru-win64.zip"

PORTABLE_README = """============================================================
  WoWs-Mini-Relpay-Ru — 俄服《战舰世界》小地图回放渲染工具
  免安装绿色版（无需 Python / uv / FFmpeg）
============================================================

【使用步骤】
1. 请先将整个压缩包解压到任意文件夹（不要直接在压缩软件里双击）。
2. 双击运行 WoWs-Mini-Relpay-Ru.exe 打开图形界面。
3. 程序会自动识别本机的俄服正式服与 PT 测试服目录，也支持点击
   “手动选择目录...”自由指定，并自动列出 replays 目录下的全部
   .korablireplay 回放文件（显示中文船名、战斗时间与地图）。
4. 首次使用时，点击“同步客户端数据”（或直接勾选回放点“开始批量生成”，
   程序会自动同步约 3~5 秒），即可提取本地客户端地图、官方图标与汉化 mod 船名。
5. 也可以将任意 .korablireplay 文件直接拖入窗口添加并自动勾选，
   自由设置分辨率（默认 1920x1080）与帧率（默认 30fps）批量生成。
6. 每次生成都会按当前时间在 output 目录下创建独立文件夹并自动打开。

【俄服大版本更新 / 汉化 mod 更新后】
- 游戏更新后，在界面右上角点击“同步客户端数据”即可自动适配新版本；
- 仅更新汉化 mod 船名时，点击“仅同步汉化”即可（约 1 秒）。

【项目地址】
https://github.com/starmiaoa/WoWs-Mini-Relpay-Ru
"""


def main() -> int:
    t0 = time.perf_counter()
    print("[1/3] 正在运行 PyInstaller 构建独立绿色版 EXE ...")
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

    print("[2/3] 正在写入便携版使用说明...")
    (DIST_APP_DIR / "使用说明.txt").write_text(PORTABLE_README, encoding="utf-8")

    # 确保便携包干净（不包含开发者个人缓存 data/ 或 output/）
    for extra in ("data", "output"):
        ep = DIST_APP_DIR / extra
        if ep.exists():
            shutil.rmtree(ep)

    print(f"[3/3] 正在打包发布压缩包 -> {ZIP_OUT_PATH.name} ...")
    if ZIP_OUT_PATH.exists():
        ZIP_OUT_PATH.unlink()

    with zipfile.ZipFile(ZIP_OUT_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file_path in sorted(DIST_APP_DIR.rglob("*")):
            if file_path.is_file():
                arcname = Path("WoWs-Mini-Relpay-Ru") / file_path.relative_to(DIST_APP_DIR)
                zf.write(file_path, arcname=str(arcname))

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
    print(f"  发布压缩包 : {ZIP_OUT_PATH} ({zip_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
