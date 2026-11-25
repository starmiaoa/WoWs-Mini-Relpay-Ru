"""俄服《战舰世界》(Mir Korabley) 客户端目录与版本号自动探测。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import string
import sys
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class DiscoveredClient:
    """自动探测到的俄服客户端条目（含正式服与 PT 测试服）。"""

    game_dir: Path
    channel: str           # "live" 或 "pt"
    channel_zh: str        # "正式服" 或 "PT测试服"
    short_version: str
    full_version: str
    build: str
    game_id: str
    source: str            # 发现来源（LGC / 注册表 / 磁盘扫描 / 手动选择）

    @property
    def replays_dir(self) -> Path:
        return self.game_dir / "replays"

    @property
    def display_label(self) -> str:
        return f"[{self.channel_zh}] {self.game_dir}  (v{self.short_version})"


@dataclass(frozen=True)
class KorabliClientInfo:
    """已定位的俄服客户端目录与版本元数据。"""

    game_dir: Path
    full_version: str      # 例: "26.11.1.0.8871067"
    short_version: str     # 例: "26.11.1"
    build: str             # 例: "8871067"
    publisher: str         # 例: "lesta"
    game_id: str           # 例: "MK.RU.PRODUCTION"
    bin_dir: Path
    idx_dir: Path
    pkg_dir: Path
    res_dir: Path
    mods_dir: Path
    res_mods_dir: Path
    lk_mods_dir: Path


def _parse_game_info(game_info_path: Path) -> tuple[str, str, str, str]:
    """解析 game_info.xml，返回 (full_version, build, publisher, game_id)。"""
    tree = ET.parse(game_info_path)
    root = tree.getroot()
    game_el = root.find("game")
    publisher = (
        root.attrib.get("wgc_publisher_id")
        or (game_el.attrib.get("publisher") if game_el is not None else None)
        or "lesta"
    )
    game_id = ""
    full_version = ""

    if game_el is not None:
        id_el = game_el.find("id")
        if id_el is not None and id_el.text:
            game_id = id_el.text.strip()
        # Lesta game_info.xml: <part_versions><version name="client" installed="26.11.1.0.8871067" .../>
        part_versions = game_el.find("part_versions")
        if part_versions is not None:
            for ver_el in part_versions.findall("version"):
                if ver_el.attrib.get("name") == "client" and ver_el.attrib.get("installed"):
                    full_version = ver_el.attrib["installed"].strip()
                    break
            if not full_version:
                for ver_el in part_versions.findall("version"):
                    if ver_el.attrib.get("installed"):
                        full_version = ver_el.attrib["installed"].strip()
                        break
        if not full_version:
            installed = game_el.find("version_installed")
            if installed is not None and installed.text:
                full_version = installed.text.strip()
        if not full_version:
            parts = game_el.find("installed_parts")
            if parts is not None:
                for part in parts.findall("part"):
                    if part.attrib.get("name") == "client" and part.attrib.get("version"):
                        full_version = part.attrib["version"].strip()
                        break

    build = ""
    if full_version:
        segs = [s.strip() for s in full_version.replace(",", ".").split(".") if s.strip()]
        if segs and segs[-1].isdigit() and len(segs[-1]) >= 6:
            build = segs[-1]
    return full_version, build, publisher, game_id


def normalize_short_version(version_str: str) -> str:
    """将 '26.11.1.0.8871067' 或 '26,11,1,8871067' 归一化为 '26.11.1'。"""
    cleaned = version_str.strip().replace(",", ".")
    segs = [s.strip() for s in cleaned.split(".") if s.strip()]
    if len(segs) >= 3:
        return ".".join(segs[:3])
    return cleaned


def locate_client(game_dir: Path | str, preferred_build: str | None = None) -> KorabliClientInfo:
    """探测俄服游戏根目录，定位当前或指定 build 的资源与 mod 路径。"""
    root = Path(game_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"未找到俄服游戏目录: {root}")

    game_info_path = root / "game_info.xml"
    full_version = ""
    info_build = ""
    publisher = "lesta"
    game_id = "MK.RU.PRODUCTION"

    if game_info_path.is_file():
        full_version, info_build, publisher, game_id = _parse_game_info(game_info_path)

    bin_root = root / "bin"
    if not bin_root.is_dir():
        raise FileNotFoundError(f"缺少 bin 目录，确认是否为俄服游戏目录: {root}")

    numeric_builds = sorted(
        [p.name for p in bin_root.iterdir() if p.is_dir() and p.name.isdigit()],
        key=lambda x: int(x),
        reverse=True,
    )
    if not numeric_builds:
        raise FileNotFoundError(f"在 {bin_root} 下未找到任何有效 build 子目录")

    if preferred_build:
        if preferred_build not in numeric_builds:
            raise FileNotFoundError(
                f"指定的 build {preferred_build} 不存在，可用 build: {', '.join(numeric_builds)}"
            )
        build = preferred_build
    elif info_build and info_build in numeric_builds:
        build = info_build
    else:
        build = numeric_builds[0]

    if not full_version:
        full_version = f"unknown.{build}"
    short_version = normalize_short_version(full_version)

    bin_dir = bin_root / build
    idx_dir = bin_dir / "idx"
    pkg_dir = root / "res_packages"
    if not idx_dir.is_dir():
        raise FileNotFoundError(f"缺少 idx 索引目录: {idx_dir}")
    if not pkg_dir.is_dir():
        raise FileNotFoundError(f"缺少 res_packages 目录: {pkg_dir}")

    return KorabliClientInfo(
        game_dir=root,
        full_version=full_version,
        short_version=short_version,
        build=build,
        publisher=publisher,
        game_id=game_id,
        bin_dir=bin_dir,
        idx_dir=idx_dir,
        pkg_dir=pkg_dir,
        res_dir=bin_dir / "res",
        mods_dir=bin_dir / "mods",
        res_mods_dir=bin_dir / "res_mods",
        lk_mods_dir=bin_dir / "lk_mods",
    )


def _classify_channel(game_dir: Path, game_id: str, hint: str = "") -> tuple[str, str]:
    """区分俄服正式服 (live) 与公测服 (pt)。"""
    combined = f"{game_id} {hint} {game_dir.name}".upper()
    pt_markers = (".PT", "_PT", " PT", "PT.", "PUBLIC_TEST", "TEST", "TST")
    if any(m in combined for m in pt_markers):
        return "pt", "PT测试服"
    return "live", "正式服"


def _is_lesta_korabli_client(info: KorabliClientInfo, allow_unbranded: bool = False) -> bool:
    """校验是否为俄服 Mir Korabley / Lesta 客户端（排除 WG 其他区服目录误扫）。"""
    pub = info.publisher.lower()
    gid = info.game_id.upper()
    if "lesta" in pub or gid.startswith("MK.") or ".RU." in gid or ".PT." in gid:
        return True
    if (info.game_dir / "Korabli.exe").is_file() or (info.game_dir / "korabli.exe").is_file():
        return True
    if "korabli" in info.game_dir.name.lower():
        return True
    return allow_unbranded


def _collect_lgc_candidates() -> list[tuple[Path, str, str]]:
    """从 Lesta Game Center (ProgramData) 读取已安装的正式服与 PT 服目录。"""
    results: list[tuple[Path, str, str]] = []
    prog_data = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    lgc_root = prog_data / "Lesta" / "GameCenter"

    # 1. %ProgramData%\Lesta\GameCenter\apps\<app_id>\<hash>
    apps_dir = lgc_root / "apps"
    if apps_dir.is_dir():
        for app_sub in sorted(apps_dir.iterdir()):
            if not app_sub.is_dir():
                continue
            app_name = app_sub.name.lower()
            if not (app_name.startswith("mk.") or app_name.startswith("wows.") or "korabli" in app_name):
                continue
            for f in app_sub.iterdir():
                if not f.is_file():
                    continue
                try:
                    raw = f.read_text(encoding="utf-8", errors="ignore").strip()
                except OSError:
                    continue
                if raw:
                    results.append((Path(raw), f"LGC ({app_sub.name})", app_sub.name))

    # 2. %ProgramData%\Lesta\GameCenter\preferences.xml
    pref_xml = lgc_root / "preferences.xml"
    if pref_xml.is_file():
        try:
            tree = ET.parse(pref_xml)
            root = tree.getroot()
            for wd in root.findall(".//working_dir"):
                if wd.text and wd.text.strip():
                    results.append((Path(wd.text.strip()), "LGC 配置", ""))
            for sel in root.findall(".//selectedGames/*"):
                tag = sel.tag.upper()
                if sel.text and sel.text.strip() and ("MK" in tag or "WOWS" in tag or "KORABLI" in tag):
                    results.append((Path(sel.text.strip()), f"LGC ({sel.tag})", sel.tag))
        except Exception:
            pass

    return results


def _collect_registry_candidates() -> list[tuple[Path, str, str]]:
    """从 Windows 卸载注册表读取俄服与 PT 服安装路径。"""
    results: list[tuple[Path, str, str]] = []
    if sys.platform != "win32":
        return results
    try:
        import winreg
    except ImportError:
        return results

    roots = [
        (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    keywords = ("мир кораблей", "mir korabley", "korabli", "общий тест")
    for hkey, sub_path in roots:
        try:
            with winreg.OpenKey(hkey, sub_path) as key:
                count = winreg.QueryInfoKey(key)[0]
                for i in range(count):
                    try:
                        sub_name = winreg.EnumKey(key, i)
                        with winreg.OpenKey(key, sub_name) as sk:
                            try:
                                disp, _ = winreg.QueryValueEx(sk, "DisplayName")
                            except OSError:
                                disp = ""
                            try:
                                loc, _ = winreg.QueryValueEx(sk, "InstallLocation")
                            except OSError:
                                loc = ""
                            if not loc:
                                continue
                            low_disp = str(disp).lower()
                            low_loc = str(loc).lower()
                            if any(k in low_disp or k in low_loc for k in keywords):
                                results.append((Path(str(loc).strip()), "系统注册表", str(disp)))
                    except OSError:
                        continue
        except OSError:
            continue
    return results


def _safe_is_dir(p: Path) -> bool:
    try:
        return p.is_dir()
    except OSError:
        return False


def _collect_disk_candidates() -> list[tuple[Path, str, str]]:
    """扫描常见盘符默认游戏目录（兼容正式服与 PT 测试服，容错 RAW/光驱/离线卷）。"""
    results: list[tuple[Path, str, str]] = []
    rel_dirs = (
        r"Games\Korabli",
        r"Games\Korabli_PT",
        r"Games\Korabli PT",
        r"Games\Mir_Korabley",
        r"Games\Mir_Korabley_PT",
        r"Games\Mir Korabley",
        r"Games\Mir Korabley PT",
        r"Games\World_of_Warships_RU_PT",
        r"Lesta\Korabli",
        r"Lesta\Korabli_PT",
    )
    for ch in string.ascii_uppercase:
        root = Path(f"{ch}:\\")
        if not _safe_is_dir(root):
            continue
        for rel in rel_dirs:
            p = root / rel
            if _safe_is_dir(p):
                results.append((p, "磁盘自动扫描", p.name))
    return results


def discover_korabli_clients(extra_paths: list[Path | str] | None = None) -> list[DiscoveredClient]:
    """自动探测本机全部俄服正式服与 PT 测试服客户端目录。"""
    raw_candidates: list[tuple[Path, str, str, bool]] = []
    for p, src, hint in _collect_lgc_candidates():
        raw_candidates.append((p, src, hint, False))
    for p, src, hint in _collect_registry_candidates():
        raw_candidates.append((p, src, hint, False))
    for p, src, hint in _collect_disk_candidates():
        raw_candidates.append((p, src, hint, False))
    for ep in extra_paths or []:
        raw_candidates.append((Path(ep), "手动选择", Path(ep).name, True))

    seen: set[str] = set()
    found: list[DiscoveredClient] = []

    for cand_path, source, hint, allow_unbranded in raw_candidates:
        try:
            resolved = cand_path.resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen or not resolved.is_dir():
            continue
        try:
            info = locate_client(resolved)
        except Exception:
            continue
        if not _is_lesta_korabli_client(info, allow_unbranded=allow_unbranded):
            continue

        seen.add(key)
        channel, channel_zh = _classify_channel(info.game_dir, info.game_id, hint=hint)
        found.append(
            DiscoveredClient(
                game_dir=info.game_dir,
                channel=channel,
                channel_zh=channel_zh,
                short_version=info.short_version,
                full_version=info.full_version,
                build=info.build,
                game_id=info.game_id,
                source=source,
            )
        )

    # 正式服优先，其次 PT 测试服，再按版本号降序
    found.sort(key=lambda c: (0 if c.channel == "live" else 1, c.short_version, str(c.game_dir).lower()))
    return found
