"""俄服 .korablireplay 文件扫描、文件名元数据解析（战舰中文名/战斗时间/地图）与批次目录管理。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Iterable

from .extractor.localization import LocalizationBundle
from .extractor.store import VersionDataStore
from .i18n import (
    clean_english_ship_name,
    format_english_map_name,
    normalize_lang,
    to_traditional_zh,
    translate_species_short,
)
from .parser.container import read_replay_meta_only


ROMAN_TIERS = {
    1: "I", 2: "II", 3: "III", 4: "IV", 5: "V",
    6: "VI", 7: "VII", 8: "VIII", 9: "IX", 10: "X", 11: "★",
}

SPECIES_ZH = {
    "AirCarrier": "航母",
    "Battleship": "战列",
    "Cruiser": "巡洋",
    "Destroyer": "驱逐",
    "Submarine": "潜艇",
    "Auxiliary": "支援",
}

# 标准俄服回放文件名格式:
#   20261121_164854_PFSC211-La-Rochelle_47_Sleeping_Giant.korablireplay
#   20261120_192217_PRSD110-Pr-24-Khabarovsk_35_NE_north_winter.korablireplay
_RE_STANDARD = re.compile(r"^(\d{8})_(\d{6})_([A-Z]{2,5}\d{3})-(.+?)_(\d{2}_.+)$")
_RE_GENERIC_MAP = re.compile(r"^(\d{8})_(\d{6})_([A-Z]{2,5}\d{3})-([^_]+)_(.+)$")
_RE_DATE_ONLY = re.compile(r"^(\d{8})_(\d{6})_")


@dataclass(frozen=True)
class ReplayListItem:
    """GUI 回放列表中的单条回放摘要。"""

    path: Path
    filename: str
    battle_time_str: str      # "2026-11-21 16:48:54"
    sort_timestamp: float     # Unix 时间戳，供最新优先排序
    ship_index: str           # 例: "PFSC211"
    ship_display: str         # 例: "★ 拉罗谢尔 [巡洋]"
    ship_slug: str            # 例: "La-Rochelle"
    map_space: str            # 例: "47_Sleeping_Giant"
    map_display: str          # 例: "沉睡的渤海湾"
    file_size_kb: int
    source: str               # "游戏目录" / "手动拖入"


def _format_ship_display(
    ship_index: str,
    ship_slug: str,
    store: VersionDataStore | None,
    loc_bundle: LocalizationBundle | None = None,
    lang: str = "zh_CN",
) -> str:
    """结合汉化/内置多语言词库按目标语言格式化战舰显示名。"""
    code = normalize_lang(lang)
    if store is not None and ship_index:
        info = store.get_ship_by_index(ship_index)
        if info is not None:
            tier = ROMAN_TIERS.get(int(info.get("level") or 0), "")
            name = store.get_ship_name_from_dict(info, lang=code, fallback=ship_slug or ship_index)
            sp = translate_species_short(str(info.get("species") or ""), code)
            prefix = f"{tier} " if tier else ""
            suffix = f" ({sp})" if sp else ""
            return f"{prefix}{name}{suffix}"

    # 首次启动尚未完成全量缓存时，直接从客户端 .mo 即时解析目标语言船名
    if loc_bundle is not None and ship_index:
        if code == "zh_CN":
            zh = loc_bundle.ship_name_zh(ship_index, fallback="")
            if zh and zh != ship_index:
                return zh
        elif code == "zh_TW":
            zh = loc_bundle.ship_name_zh(ship_index, fallback="")
            if zh and zh != ship_index:
                return to_traditional_zh(zh)
        elif code == "ru":
            ru = loc_bundle.ship_name_ru(ship_index, fallback="")
            if ru and ru != ship_index:
                return ru
        else:
            ru = loc_bundle.ship_name_ru(ship_index, fallback="")
            en = clean_english_ship_name(ship_slug or ship_index, ru)
            if en:
                return en

    cleaned = ship_slug.replace("-", " ").replace("_", " ").strip()
    if code == "en" and cleaned:
        return clean_english_ship_name(ship_slug)
    if cleaned and ship_index:
        return f"{cleaned} ({ship_index})"
    return cleaned or ship_index or "Unknown"


def _parse_datetime_from_digits(date_str: str, time_str: str) -> tuple[str, float]:
    try:
        dt = datetime.strptime(f"{date_str}_{time_str}", "%Y%m%d_%H%M%S")
        return dt.strftime("%Y-%m-%d %H:%M:%S"), dt.timestamp()
    except ValueError:
        return f"{date_str} {time_str}", 0.0


def _parse_datetime_from_meta(meta_dt: str) -> tuple[str, float] | None:
    """解析回放 JSON 元数据中的 dateTime（通常为 '21.11.2026 16:48:54'）。"""
    txt = meta_dt.strip()
    for fmt in ("%d.%m.%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M:%S"):
        try:
            dt = datetime.strptime(txt, fmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S"), dt.timestamp()
        except ValueError:
            continue
    return None


def parse_replay_item(
    replay_path: Path | str,
    store: VersionDataStore | None = None,
    source: str = "游戏目录",
    loc_bundle: LocalizationBundle | None = None,
    lang: str = "zh_CN",
) -> ReplayListItem:
    """优先根据 .korablireplay 文件名解析驾驶战舰、战斗时间与地图；非标准文件名自动回退快速读取头部 JSON。"""
    path = Path(replay_path)
    stem = path.stem
    try:
        st = path.stat()
        size_kb = max(1, round(st.st_size / 1024))
        mtime = st.st_mtime
    except OSError:
        size_kb = 0
        mtime = 0.0

    battle_time_str = ""
    sort_ts = mtime
    ship_index = ""
    ship_slug = ""
    map_space = ""

    m = _RE_STANDARD.match(stem) or _RE_GENERIC_MAP.match(stem)
    if m:
        d_s, t_s, ship_index, ship_slug, map_space = m.groups()
        battle_time_str, sort_ts = _parse_datetime_from_digits(d_s, t_s)
    else:
        dm = _RE_DATE_ONLY.match(stem)
        if dm:
            battle_time_str, sort_ts = _parse_datetime_from_digits(dm.group(1), dm.group(2))

        # 非标准文件名（如用户重命名或拖入的文件），快速读取 <2KB 头部 JSON 获取船名/时间/地图
        try:
            meta = read_replay_meta_only(path)
            if not battle_time_str and meta.get("dateTime"):
                parsed_dt = _parse_datetime_from_meta(str(meta["dateTime"]))
                if parsed_dt:
                    battle_time_str, sort_ts = parsed_dt
            raw_ship = str(meta.get("playerVehicle") or "")
            if raw_ship:
                parts = raw_ship.split("-", 1)
                ship_index = parts[0].strip()
                ship_slug = parts[1].strip() if len(parts) > 1 else ship_index
            raw_map = str(meta.get("mapName") or "")
            if raw_map:
                map_space = raw_map.replace("spaces/", "").strip("/")
        except Exception:
            pass

    if not battle_time_str:
        if mtime > 0:
            battle_time_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
        else:
            battle_time_str = "未知时间"

    code = normalize_lang(lang)
    ship_display = _format_ship_display(ship_index, ship_slug, store, loc_bundle=loc_bundle, lang=code)
    if map_space:
        if store is not None:
            map_display = store.get_map_name(map_space, lang=code)
        elif loc_bundle is not None:
            if code == "zh_CN":
                map_display = loc_bundle.map_name_zh(map_space, fallback=map_space)
            elif code == "zh_TW":
                map_display = to_traditional_zh(loc_bundle.map_name_zh(map_space, fallback=map_space))
            elif code == "ru":
                map_display = loc_bundle.map_name_ru(map_space, fallback=map_space)
            else:
                map_display = format_english_map_name(map_space)
        else:
            map_display = format_english_map_name(map_space) if code == "en" else map_space
    else:
        map_display = "Unknown" if code == "en" else ("Неизв." if code == "ru" else "未知地图")

    return ReplayListItem(
        path=path,
        filename=path.name,
        battle_time_str=battle_time_str,
        sort_timestamp=sort_ts,
        ship_index=ship_index,
        ship_display=ship_display,
        ship_slug=ship_slug,
        map_space=map_space,
        map_display=map_display,
        file_size_kb=size_kb,
        source=source,
    )


def scan_replays_in_game_dir(
    game_dir: Path | str,
    store: VersionDataStore | None = None,
    loc_bundle: LocalizationBundle | None = None,
    lang: str = "zh_CN",
) -> list[ReplayListItem]:
    """扫描俄服游戏目录 replays/ 下的全部 .korablireplay 文件（含版本子目录，严格过滤后缀）。"""
    replays_root = Path(game_dir) / "replays"
    if not replays_root.is_dir():
        return []

    found_paths: list[Path] = []
    # 扫描 replays/ 根目录及 1 层版本子目录（如 replays/26.11.1.0/）
    try:
        for entry in replays_root.iterdir():
            if entry.is_file() and entry.suffix.lower() == ".korablireplay":
                if entry.name.lower() != "temp.korablireplay":
                    found_paths.append(entry)
            elif entry.is_dir():
                try:
                    for sub in entry.iterdir():
                        if sub.is_file() and sub.suffix.lower() == ".korablireplay":
                            if sub.name.lower() != "temp.korablireplay":
                                found_paths.append(sub)
                except OSError:
                    continue
    except OSError:
        return []

    items = [parse_replay_item(p, store=store, source="游戏目录", loc_bundle=loc_bundle, lang=lang) for p in found_paths]
    items.sort(key=lambda x: (x.sort_timestamp, x.filename), reverse=True)
    return items


def collect_korabli_replays_from_paths(paths: Iterable[Path | str]) -> list[Path]:
    """从拖入/选择的文件与文件夹列表中严格筛选全部 .korablireplay 文件并去重。"""
    collected: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        if not p.is_file() or p.suffix.lower() != ".korablireplay":
            return
        try:
            key = str(p.resolve()).lower()
        except OSError:
            key = str(p).lower()
        if key not in seen:
            seen.add(key)
            collected.append(p)

    for raw in paths:
        p = Path(raw)
        if p.is_file():
            _add(p)
        elif p.is_dir():
            try:
                for child in p.rglob("*.korablireplay"):
                    if child.name.lower() != "temp.korablireplay":
                        _add(child)
            except OSError:
                continue

    return collected


def create_batch_output_dir(
    base_output_dir: Path | str,
    now: datetime | None = None,
) -> Path:
    """按本次生成时间创建独立输出文件夹（如 output/2026-11-25_12-50-08）。"""
    base = Path(base_output_dir)
    base.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime("%Y-%m-%d_%H-%M-%S")
    target = base / stamp
    suffix = 2
    while target.exists():
        target = base / f"{stamp}_{suffix:02d}"
        suffix += 1
    target.mkdir(parents=True, exist_ok=True)
    return target
