"""GitHub Release 在线检查更新与一键快速拉取更新模块（零额外第三方依赖，按需懒加载）。

支持两种运行形态的自动快速更新：
1. 安装包版（存在 Inno Setup unins000.exe）：优先拉取签名版 `*-Setup-win64.exe` 并自动静默覆盖升级与重启；
2. 绿色便携版（ZIP / 7z 解压运行）：优先拉取 `*-win64.zip`，本地校验解压后由 UTF-8 PowerShell
   独立脚本等待主进程退出、保留 `data/` 与 `output/` 无损覆盖更新并自动重启程序。
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Callable
import urllib.error
import urllib.request
import zipfile

from . import __version__ as CURRENT_VERSION
from .config import get_app_root, is_compiled_app, is_installed_app


GITHUB_OWNER = "starmiaoa"
GITHUB_REPO = "WoWs-Mini-Relpay-Ru"
RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
RELEASES_LATEST_WEB_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE_URL = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases"


class UpdateCancelledError(RuntimeError):
    """用户主动取消更新下载时抛出。"""


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int = 0


@dataclass(frozen=True)
class UpdateInfo:
    current_version: str
    latest_version: str
    tag_name: str
    has_update: bool
    html_url: str
    release_name: str
    notes: str
    published_at: str
    selected_asset: ReleaseAsset | None
    update_mode: str  # "installer" | "portable" | "manual"
    assets: tuple[ReleaseAsset, ...] = ()


def parse_version_tuple(version_str: str) -> tuple[int, ...]:
    """将版本号（如 'v0.2.1' / '0.2.0' / '0.3'）解析为可安全比较的整数元组。"""
    cleaned = (version_str or "").strip()
    if cleaned.lower().startswith("v"):
        cleaned = cleaned[1:].strip()
    m = re.match(r"^(\d+(?:\.\d+)*)", cleaned)
    if not m:
        return (0,)
    return tuple(int(part) for part in m.group(1).split("."))


def is_newer_version(remote_version: str, current_version: str) -> bool:
    """判断远端版本号是否严格高于当前版本号。"""
    r = parse_version_tuple(remote_version)
    c = parse_version_tuple(current_version)
    width = max(len(r), len(c))
    r_pad = r + (0,) * (width - len(r))
    c_pad = c + (0,) * (width - len(c))
    return r_pad > c_pad


def select_best_asset(
    assets: list[ReleaseAsset] | tuple[ReleaseAsset, ...],
    *,
    compiled: bool | None = None,
    installed: bool | None = None,
) -> tuple[ReleaseAsset | None, str]:
    """按当前运行形态（安装版 / 便携版 / 开发版）挑选最合适的快速更新产物。"""
    if compiled is None:
        compiled = is_compiled_app()
    if installed is None:
        installed = is_installed_app()

    installer_asset = next(
        (a for a in assets if a.name.lower().endswith(".exe") and "setup" in a.name.lower()),
        None,
    )
    if installer_asset is None:
        installer_asset = next((a for a in assets if a.name.lower().endswith(".exe")), None)

    zip_asset = next(
        (a for a in assets if a.name.lower().endswith(".zip") and "win64" in a.name.lower()),
        None,
    )
    if zip_asset is None:
        zip_asset = next((a for a in assets if a.name.lower().endswith(".zip")), None)

    if not compiled:
        return (installer_asset or zip_asset), "manual"

    if installed:
        if installer_asset is not None:
            return installer_asset, "installer"
        if zip_asset is not None:
            return zip_asset, "portable"
        return None, "manual"

    # 绿色便携版优先拉取 ZIP 做原地无损覆盖（保留 data/ 与 output/）
    if zip_asset is not None:
        return zip_asset, "portable"
    if installer_asset is not None:
        return installer_asset, "installer"
    return None, "manual"


def _fetch_release_via_redirect(timeout: float, current_version: str) -> dict[str, object]:
    """当 GitHub API 触发匿名限流（403）时，通过 releases/latest 302 跳转兜底获取最新版本号。"""
    req = urllib.request.Request(
        RELEASES_LATEST_WEB_URL,
        headers={"User-Agent": f"WoWs-Mini-Relpay-Ru/{current_version}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        final_url = resp.geturl()
    m = re.search(r"/releases/tag/([^/?#]+)", final_url)
    if not m:
        raise RuntimeError(f"无法从 GitHub 发布页解析最新版本号: {final_url}")
    tag = m.group(1)
    dl_base = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/download/{tag}"
    return {
        "tag_name": tag,
        "name": tag,
        "html_url": final_url,
        "body": "",
        "published_at": "",
        "assets": [
            {
                "name": "WoWs-Mini-Relpay-Ru-Setup-win64.exe",
                "browser_download_url": f"{dl_base}/WoWs-Mini-Relpay-Ru-Setup-win64.exe",
                "size": 0,
            },
            {
                "name": "WoWs-Mini-Relpay-Ru-win64.zip",
                "browser_download_url": f"{dl_base}/WoWs-Mini-Relpay-Ru-win64.zip",
                "size": 0,
            },
        ],
    }


def check_for_updates(
    current_version: str = CURRENT_VERSION,
    timeout: float = 12.0,
    *,
    compiled: bool | None = None,
    installed: bool | None = None,
) -> UpdateInfo:
    """查询 GitHub 最新 Release 并返回结构化更新信息。"""
    req = urllib.request.Request(
        RELEASES_API_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"WoWs-Mini-Relpay-Ru/{current_version}",
        },
    )
    data: dict[str, object]
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise RuntimeError("GitHub API 返回格式异常。")
            data = parsed
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            data = _fetch_release_via_redirect(timeout=timeout, current_version=current_version)
        else:
            raise RuntimeError(f"GitHub 返回 HTTP {exc.code}: {exc.reason}") from exc
    except Exception as exc:
        raise RuntimeError(f"连接 GitHub 失败: {exc}") from exc

    tag = str(data.get("tag_name") or "").strip()
    latest_ver = tag.lstrip("vV").strip() or "0.0.0"
    html_url = str(data.get("html_url") or RELEASES_PAGE_URL).strip()
    release_name = str(data.get("name") or tag).strip()
    notes = str(data.get("body") or "").strip()
    published_at = str(data.get("published_at") or "").strip()

    raw_assets = data.get("assets") or []
    assets_list: list[ReleaseAsset] = []
    if isinstance(raw_assets, list):
        for item in raw_assets:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            url = str(item.get("browser_download_url") or "").strip()
            sz = int(item.get("size") or 0)
            if name and url:
                assets_list.append(ReleaseAsset(name=name, download_url=url, size=sz))

    chosen, mode = select_best_asset(assets_list, compiled=compiled, installed=installed)
    has_upd = is_newer_version(latest_ver, current_version)
    return UpdateInfo(
        current_version=current_version,
        latest_version=latest_ver,
        tag_name=tag or f"v{latest_ver}",
        has_update=has_upd,
        html_url=html_url,
        release_name=release_name,
        notes=notes,
        published_at=published_at,
        selected_asset=chosen,
        update_mode=mode,
        assets=tuple(assets_list),
    )


def download_release_asset(
    asset: ReleaseAsset,
    dest_dir: Path | None = None,
    *,
    timeout: float = 30.0,
    progress_callback: Callable[[int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> Path:
    """流式下载 Release 更新包，支持实时字节进度与安全取消。"""
    target_dir = dest_dir or Path(tempfile.mkdtemp(prefix="wows_minimap_upd_"))
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = target_dir / asset.name
    part_path = target_dir / f"{asset.name}.part"

    req = urllib.request.Request(
        asset.download_url,
        headers={"User-Agent": f"WoWs-Mini-Relpay-Ru/{CURRENT_VERSION}"},
    )
    downloaded = 0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            hdr_len = resp.headers.get("Content-Length")
            total = int(hdr_len) if (hdr_len and hdr_len.isdigit()) else int(asset.size or 0)
            with part_path.open("wb") as fh:
                while True:
                    if cancel_check is not None and cancel_check():
                        raise UpdateCancelledError("已取消更新下载。")
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback is not None:
                        progress_callback(downloaded, total)
        if downloaded <= 0:
            raise RuntimeError("下载文件为空，请稍后重试。")
        part_path.replace(final_path)
        return final_path
    except BaseException:
        part_path.unlink(missing_ok=True)
        raise


def validate_downloaded_package(pkg_path: Path, update_mode: str) -> None:
    """校验下载完成的安装包或便携压缩包完整性。"""
    if not pkg_path.is_file() or pkg_path.stat().st_size <= 0:
        raise RuntimeError(f"更新文件不存在或为空: {pkg_path}")
    if update_mode == "installer" or pkg_path.suffix.lower() == ".exe":
        with pkg_path.open("rb") as fh:
            if fh.read(2) != b"MZ":
                raise RuntimeError("下载的安装包格式异常（非有效 Windows 可执行文件）。")
        return
    if pkg_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(pkg_path, "r") as zf:
            bad = zf.testzip()
            if bad is not None:
                raise RuntimeError(f"下载的压缩包已损坏: {bad}")
            names = zf.namelist()
            if not any(Path(n).name.lower() == "wows-mini-relpay-ru.exe" for n in names):
                raise RuntimeError("压缩包内未找到 WoWs-Mini-Relpay-Ru.exe。")


def _locate_extracted_app_dir(extract_root: Path) -> Path:
    for cand in extract_root.rglob("WoWs-Mini-Relpay-Ru.exe"):
        if cand.is_file():
            return cand.parent
    raise RuntimeError("解压后的更新目录中未找到 WoWs-Mini-Relpay-Ru.exe。")


def prepare_portable_update_script(
    zip_path: Path,
    app_dir: Path,
    parent_pid: int,
    exe_name: str = "WoWs-Mini-Relpay-Ru.exe",
) -> Path:
    """解压便携版 ZIP 并生成 UTF-8 PowerShell 覆盖升级脚本（完整兼容中文路径，保留 data/ 与 output/）。"""
    staging_root = zip_path.parent
    extract_dir = staging_root / "extracted"
    if extract_dir.exists():
        shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)

    src_app_dir = _locate_extracted_app_dir(extract_dir)
    ps_script = staging_root / "apply_update.ps1"

    def _ps_quote(p: Path | str) -> str:
        return "'" + str(p).replace("'", "''") + "'"

    content = f"""$ErrorActionPreference = 'Stop'
$parentPid = {int(parent_pid)}
$srcDir = {_ps_quote(src_app_dir)}
$appDir = {_ps_quote(app_dir)}
$staging = {_ps_quote(staging_root)}
$exePath = Join-Path $appDir {_ps_quote(exe_name)}

try {{
    $proc = Get-Process -Id $parentPid -ErrorAction SilentlyContinue
    if ($proc) {{
        Wait-Process -Id $parentPid -Timeout 25 -ErrorAction SilentlyContinue
    }}
}} catch {{}}
Start-Sleep -Milliseconds 500

$skipNames = @('data', 'output')
Get-ChildItem -Path $srcDir -Force | ForEach-Object {{
    if ($skipNames -contains $_.Name.ToLower()) {{ return }}
    $dest = Join-Path $appDir $_.Name
    if ($_.PSIsContainer) {{
        Copy-Item -Path $_.FullName -Destination $appDir -Recurse -Force
    }} else {{
        for ($retry = 0; $retry -lt 8; $retry++) {{
            try {{
                Copy-Item -Path $_.FullName -Destination $dest -Force
                break
            }} catch {{
                Start-Sleep -Milliseconds 350
            }}
        }}
    }}
}}

if (Test-Path $exePath) {{
    Start-Process -FilePath $exePath -WorkingDirectory $appDir
}}
Start-Sleep -Milliseconds 600
Remove-Item -Path $staging -Recurse -Force -ErrorAction SilentlyContinue
"""
    # 写入 UTF-8 with BOM，确保 Windows PowerShell 5.1 正确识别中文/俄文路径
    ps_script.write_text(content, encoding="utf-8-sig")
    return ps_script


def apply_downloaded_update(pkg_path: Path, update_mode: str, app_dir: Path | None = None) -> None:
    """触发下载好的更新包执行安装或便携覆盖升级（调用方随后应立即退出主进程）。"""
    validate_downloaded_package(pkg_path, update_mode)
    if sys.platform != "win32":
        raise RuntimeError("快速拉取更新仅支持 Windows 平台。")

    creationflags = 0x08000000 | 0x00000008  # CREATE_NO_WINDOW | DETACHED_PROCESS
    if update_mode == "installer":
        subprocess.Popen(
            [
                str(pkg_path),
                "/SP-",
                "/SILENT",
                "/CLOSEAPPLICATIONS",
                "/RESTARTAPPLICATIONS",
            ],
            close_fds=True,
        )
        return

    if update_mode == "portable":
        target_dir = app_dir or get_app_root()
        ps_script = prepare_portable_update_script(
            zip_path=pkg_path,
            app_dir=target_dir,
            parent_pid=os.getpid(),
        )
        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-File",
                str(ps_script),
            ],
            creationflags=creationflags,
            close_fds=True,
        )
        return

    raise RuntimeError(f"当前运行模式不支持自动覆盖更新: {update_mode}")
