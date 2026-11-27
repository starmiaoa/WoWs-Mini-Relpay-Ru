"""GitHub 在线检查更新与快速拉取更新单元测试。"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest import mock
import urllib.error
import zipfile

import pytest

from korabli_minimap.updater import (
    ReleaseAsset,
    UpdateCancelledError,
    check_for_updates,
    download_release_asset,
    is_newer_version,
    parse_version_tuple,
    prepare_portable_update_script,
    select_best_asset,
    validate_downloaded_package,
)


def test_version_parsing_and_comparison() -> None:
    assert parse_version_tuple("v0.2.1") == (0, 2, 1)
    assert parse_version_tuple("0.2.0") == (0, 2, 0)
    assert parse_version_tuple("v1.0") == (1, 0)

    assert is_newer_version("v0.2.1", "0.2.0") is True
    assert is_newer_version("0.3.0", "0.2.1") is True
    assert is_newer_version("v0.2.1", "0.2.1") is False
    assert is_newer_version("0.2.0", "0.2.1") is False


def test_select_best_asset_by_runtime_mode() -> None:
    assets = [
        ReleaseAsset("WoWs-Mini-Relpay-Ru-win64.7z", "https://example.com/a.7z", 50_000_000),
        ReleaseAsset("WoWs-Mini-Relpay-Ru-win64.zip", "https://example.com/a.zip", 76_000_000),
        ReleaseAsset("WoWs-Mini-Relpay-Ru-Setup-win64.exe", "https://example.com/setup.exe", 52_000_000),
    ]
    # 便携版优先拉取 ZIP 做原地无损覆盖
    a_port, m_port = select_best_asset(assets, compiled=True, installed=False)
    assert m_port == "portable" and a_port is not None and a_port.name.endswith(".zip")

    # 安装版优先拉取 Setup.exe 做静默原地升级
    a_inst, m_inst = select_best_asset(assets, compiled=True, installed=True)
    assert m_inst == "installer" and a_inst is not None and a_inst.name.endswith(".exe")

    # 开发源码模式仅提示手动查看发布页
    _a_dev, m_dev = select_best_asset(assets, compiled=False, installed=False)
    assert m_dev == "manual"


def test_check_for_updates_api_and_403_redirect_fallback() -> None:
    payload = {
        "tag_name": "v0.2.5",
        "name": "v0.2.5",
        "html_url": "https://github.com/starmiaoa/WoWs-Mini-Relpay-Ru/releases/tag/v0.2.5",
        "body": "- Fast update support",
        "published_at": "2026-11-28T00:00:00Z",
        "assets": [
            {
                "name": "WoWs-Mini-Relpay-Ru-win64.zip",
                "browser_download_url": "https://example.com/app.zip",
                "size": 12345,
            }
        ],
    }

    class _FakeResp:
        def __init__(self, data: bytes, url: str = "") -> None:
            self._buf = io.BytesIO(data)
            self._url = url

        def read(self, n: int = -1) -> bytes:
            return self._buf.read(n)

        def geturl(self) -> str:
            return self._url

        def __enter__(self) -> "_FakeResp":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    with mock.patch("urllib.request.urlopen", return_value=_FakeResp(json.dumps(payload).encode("utf-8"))):
        info = check_for_updates(current_version="0.2.1", compiled=True, installed=False)
    assert info.has_update is True
    assert info.latest_version == "0.2.5"
    assert info.update_mode == "portable"
    assert info.selected_asset is not None and info.selected_asset.name.endswith(".zip")

    # 模拟 403 API 匿名限流，验证自动回退 releases/latest 跳转解析
    http_403 = urllib.error.HTTPError("https://api.github.com/...", 403, "rate limit", hdrs=None, fp=None)  # type: ignore[arg-type]
    redirect_resp = _FakeResp(b"", url="https://github.com/starmiaoa/WoWs-Mini-Relpay-Ru/releases/tag/v0.2.6")
    with mock.patch("urllib.request.urlopen", side_effect=[http_403, redirect_resp]):
        info2 = check_for_updates(current_version="0.2.1", compiled=True, installed=True)
    assert info2.has_update is True
    assert info2.latest_version == "0.2.6"
    assert info2.update_mode == "installer"


def test_download_validate_and_portable_unicode_script(tmp_path: Path) -> None:
    # 构造一个含 WoWs-Mini-Relpay-Ru.exe 的测试 ZIP
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("WoWs-Mini-Relpay-Ru/WoWs-Mini-Relpay-Ru.exe", b"MZ" + b"\x00" * 64)
        zf.writestr("WoWs-Mini-Relpay-Ru/README.ZH-CN.txt", "hello")
    raw_zip = zip_buf.getvalue()

    class _StreamResp:
        headers = {"Content-Length": str(len(raw_zip))}

        def __init__(self) -> None:
            self._bio = io.BytesIO(raw_zip)

        def read(self, n: int = -1) -> bytes:
            return self._bio.read(n)

        def __enter__(self) -> "_StreamResp":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    asset = ReleaseAsset("WoWs-Mini-Relpay-Ru-win64.zip", "https://example.com/a.zip", len(raw_zip))
    prog: list[tuple[int, int]] = []
    with mock.patch("urllib.request.urlopen", return_value=_StreamResp()):
        out = download_release_asset(asset, dest_dir=tmp_path / "dl", progress_callback=lambda d, t: prog.append((d, t)))
    assert out.is_file() and prog and prog[-1] == (len(raw_zip), len(raw_zip))
    validate_downloaded_package(out, "portable")

    # 校验中文路径便携更新脚本生成（保留 data/ 与 output/）
    app_dir = tmp_path / "中文目录" / "WoWs-Mini-Relpay-Ru"
    app_dir.mkdir(parents=True)
    ps1 = prepare_portable_update_script(out, app_dir=app_dir, parent_pid=99999)
    assert ps1.is_file()
    txt = ps1.read_text(encoding="utf-8-sig")
    assert "中文目录" in txt
    assert "'data', 'output'" in txt

    # 校验取消下载会清理临时文件并抛出 UpdateCancelledError
    with mock.patch("urllib.request.urlopen", return_value=_StreamResp()):
        with pytest.raises(UpdateCancelledError):
            download_release_asset(asset, dest_dir=tmp_path / "dl_cancel", cancel_check=lambda: True)
    assert not (tmp_path / "dl_cancel" / f"{asset.name}.part").exists()
