"""一键同步俄服客户端版本数据、实体定义、地图底图与汉化 mod。"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import DEFAULT_DATA_DIR
from .client_locator import KorabliClientInfo, locate_client
from .game_params import extract_game_params_catalogs
from .idx_vfs import KorabliVFS
from .localization import LocalizationBundle, extract_game_localization_dict, load_localization


@dataclass
class SyncResult:
    """一次客户端同步操作的摘要结果。"""

    version: str
    full_version: str
    build: str
    output_dir: Path
    entity_def_count: int
    ship_count: int
    ship_zh_hit_count: int
    projectile_count: int
    consumable_count: int
    map_count: int
    icon_count: int
    font_files: list[str]
    i18n_sources: list[dict[str, Any]]


def _parse_space_settings(xml_bytes: bytes) -> dict[str, Any]:
    """解析 spaces/<map>/space.settings 的边界与 chunkSize。"""
    root = ET.fromstring(xml_bytes)
    bounds_el = root.find("bounds")
    if bounds_el is None:
        raise ValueError("space.settings 缺少 <bounds> 节点")

    def _read_bound(name: str, default: int) -> int:
        if name in bounds_el.attrib:
            return int(float(bounds_el.attrib[name].strip()))
        child = bounds_el.find(name)
        if child is not None and child.text:
            return int(float(child.text.strip()))
        return default

    min_x = _read_bound("minX", -9)
    max_x = _read_bound("maxX", 8)
    min_y = _read_bound("minY", -9)
    max_y = _read_bound("maxY", 8)

    chunk_el = root.find("chunkSize")
    chunk_size = float(chunk_el.text.strip()) if (chunk_el is not None and chunk_el.text) else 100.0

    # BigWorld 地图可玩区域每边去除 2 个边界 chunk（共减 4）
    space_width = (max_x - min_x + 1 - 4) * chunk_size
    space_height = (max_y - min_y + 1 - 4) * chunk_size

    return {
        "min_x": min_x,
        "max_x": max_x,
        "min_y": min_y,
        "max_y": max_y,
        "chunk_size": chunk_size,
        "space_width": space_width,
        "space_height": space_height,
    }


def _extract_entity_defs(vfs: KorabliVFS, target_dir: Path) -> int:
    """提取 scripts/entities.xml 及全部 scripts/entity_defs/** 文件。"""
    target_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    if vfs.exists("scripts/entities.xml"):
        (target_dir / "entities.xml").write_bytes(vfs.read_bytes("scripts/entities.xml"))
        count += 1

    for entry in vfs.list_prefix("scripts/entity_defs/"):
        rel = entry.path[len("scripts/entity_defs/") :]
        out_path = target_dir / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(vfs.read_bytes(entry.path))
        count += 1

    return count


def _extract_maps(
    vfs: KorabliVFS,
    loc: LocalizationBundle,
    maps_dir: Path,
    map_filter: set[str] | None = None,
) -> int:
    """提取所有（或指定）战斗地图的 minimap.png、minimap_water.png 与 space.settings。"""
    maps_dir.mkdir(parents=True, exist_ok=True)
    settings_entries = vfs.glob("spaces/*/space.settings")
    extracted = 0

    for entry in sorted(settings_entries, key=lambda e: e.path.lower()):
        parts = entry.path.split("/")
        if len(parts) < 3:
            continue
        space_name = parts[1]
        if map_filter and space_name.lower() not in map_filter:
            continue

        minimap_path = f"spaces/{space_name}/minimap.png"
        water_path = f"spaces/{space_name}/minimap_water.png"
        if not vfs.exists(minimap_path):
            # 跳过港区/船坞等无小地图的 space
            continue

        out_map_dir = maps_dir / space_name
        out_map_dir.mkdir(parents=True, exist_ok=True)

        (out_map_dir / "minimap.png").write_bytes(vfs.read_bytes(minimap_path))
        if vfs.exists(water_path):
            (out_map_dir / "minimap_water.png").write_bytes(vfs.read_bytes(water_path))
        bg_path = f"gui/maps_bg/{space_name}.jpg"
        if vfs.exists(bg_path):
            (out_map_dir / "bg.jpg").write_bytes(vfs.read_bytes(bg_path))

        settings_bytes = vfs.read_bytes(entry.path)
        meta = _parse_space_settings(settings_bytes)
        meta["space_name"] = space_name
        meta["zh_name"] = loc.map_name_zh(space_name)
        meta["ru_name"] = loc.map_name_ru(space_name)
        (out_map_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        extracted += 1

    return extracted


def _extract_official_icons(vfs: KorabliVFS, icons_dir: Path) -> int:
    """从俄服 gui.idx 提取官方原版小地图舰种、侧边栏舰种、占点、飞机、击杀及战绩图标。"""
    prefix_map = (
        ("gui/battle_hud/markers/minimap/ship/ship_default_svg/", "minimap_ship", (".svg",)),
        ("gui/battle_hud/markers/minimap/ship/ship_self_svg/", "minimap_self", (".svg",)),
        ("gui/battle_hud/markers/ship_svg/", "hud_ship_svg", (".svg",)),
        ("gui/service_kit/ship_classes/", "hud_ship_png", (".png",)),
        ("gui/battle_hud/markers/ship/", "hud_markers_png", (".png",)),
        ("gui/battle_hud/markers/capture_point_svg/", "capture_point", (".svg",)),
        ("gui/battle_hud/markers/minimap/plane_svg/", "plane", (".svg",)),
        ("gui/battle_hud/icon_frag/", "frag", (".png",)),
        ("gui/battle_hud/damage_widget/", "damage_widget", (".png",)),
    )
    extracted = 0
    for prefix, subdir, exts in prefix_map:
        out_sub = icons_dir / subdir
        out_sub.mkdir(parents=True, exist_ok=True)
        for entry in vfs.list_prefix(prefix):
            low = entry.path.lower()
            if not any(low.endswith(ext) for ext in exts):
                continue
            filename = Path(entry.path).name
            (out_sub / filename).write_bytes(vfs.read_bytes(entry.path, verify_crc=True))
            extracted += 1
    return extracted


def _extract_mod_fonts(loc: LocalizationBundle, fonts_dir: Path) -> list[str]:
    """从汉化 mod 字体包提取中文字体到版本缓存目录（可选，不影响系统字体回退）。"""
    saved: list[str] = []
    if not loc.font_candidates:
        return saved
    fonts_dir.mkdir(parents=True, exist_ok=True)
    seen_names: set[str] = set()
    for mod_path, inner_name in loc.font_candidates:
        filename = Path(inner_name).name
        if filename in seen_names:
            continue
        seen_names.add(filename)
        try:
            with zipfile.ZipFile(mod_path, "r") as zf:
                (fonts_dir / filename).write_bytes(zf.read(inner_name))
            saved.append(filename)
        except OSError:
            continue
    return saved


def sync_client_data(
    game_dir: Path | str,
    data_dir: Path | str = DEFAULT_DATA_DIR,
    preferred_build: str | None = None,
    i18n_only: bool = False,
    map_names: list[str] | None = None,
) -> SyncResult:
    """将指定俄服客户端的版本资产与汉化 mod 同步到 data/versions/<ver>/。"""
    client: KorabliClientInfo = locate_client(game_dir, preferred_build=preferred_build)
    out_dir = Path(data_dir) / "versions" / client.short_version
    out_dir.mkdir(parents=True, exist_ok=True)

    loc = load_localization(client)
    loc_dict = extract_game_localization_dict(loc)
    (out_dir / "localization.json").write_text(
        json.dumps(loc_dict, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 若仅更新汉化，尝试原地更新已存在的 ships.json 和 maps/*/meta.json
    ships_file = out_dir / "ships.json"
    if i18n_only and ships_file.is_file():
        ships: dict[str, dict[str, Any]] = json.loads(ships_file.read_text(encoding="utf-8"))
        zh_hits = 0
        for s in ships.values():
            idx = s.get("index", "")
            if idx:
                s["zh_name"] = loc.ship_name_zh(idx, fallback=s.get("internal_name", idx))
                s["zh_full_name"] = loc.ship_full_name_zh(idx, fallback=s["zh_name"])
                s["ru_name"] = loc.ship_name_ru(idx, fallback=s.get("internal_name", idx))
            if any("\u4e00" <= ch <= "\u9fff" for ch in s.get("zh_name", "")):
                zh_hits += 1
        ships_file.write_text(json.dumps(ships, ensure_ascii=False, indent=2), encoding="utf-8")

        maps_dir = out_dir / "maps"
        map_count = 0
        if maps_dir.is_dir():
            for meta_path in maps_dir.glob("*/meta.json"):
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                sname = meta.get("space_name", meta_path.parent.name)
                meta["zh_name"] = loc.map_name_zh(sname)
                meta["ru_name"] = loc.map_name_ru(sname)
                meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                map_count += 1

        font_files = _extract_mod_fonts(loc, out_dir / "fonts")
        manifest_path = out_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        manifest["i18n_updated_at"] = datetime.now(timezone.utc).isoformat()
        manifest["i18n_sources"] = [asdict(s) for s in loc.sources]
        manifest["ship_zh_hit_count"] = zh_hits
        manifest["font_files"] = font_files
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        return SyncResult(
            version=client.short_version,
            full_version=client.full_version,
            build=client.build,
            output_dir=out_dir,
            entity_def_count=int(manifest.get("entity_def_count", 0)),
            ship_count=len(ships),
            ship_zh_hit_count=zh_hits,
            projectile_count=int(manifest.get("projectile_count", 0)),
            consumable_count=int(manifest.get("consumable_count", 0)),
            map_count=map_count,
            icon_count=int(manifest.get("icon_count", 0)),
            font_files=font_files,
            i18n_sources=[asdict(s) for s in loc.sources],
        )

    # 全量同步：只需扫描 system_data.idx, basecontent.idx, gui.idx 以及 spaces_*.idx（极速）
    idx_names: list[str] | None = None
    needed_idx = [p.name for p in client.idx_dir.glob("*.idx") if (
        p.name.lower() in {"system_data.idx", "basecontent.idx", "gui.idx"}
        or p.name.lower().startswith("spaces_")
    )]
    if needed_idx:
        idx_names = needed_idx
    vfs = KorabliVFS(client.idx_dir, client.pkg_dir, idx_filter=idx_names)

    entity_def_count = _extract_entity_defs(vfs, out_dir / "entity_defs")
    gp_path, ships, projectiles, consumables = extract_game_params_catalogs(vfs, loc)

    (out_dir / "ships.json").write_text(
        json.dumps(ships, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "projectiles.json").write_text(
        json.dumps(projectiles, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "consumables.json").write_text(
        json.dumps(consumables, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    map_filter = {m.replace("spaces/", "").strip("/").lower() for m in map_names} if map_names else None
    map_count = _extract_maps(vfs, loc, out_dir / "maps", map_filter=map_filter)
    icon_count = _extract_official_icons(vfs, out_dir / "icons")
    font_files = _extract_mod_fonts(loc, out_dir / "fonts")

    zh_hits = sum(
        1 for s in ships.values()
        if s.get("zh_name") and (s["zh_name"] != s.get("ru_name") or any("\u4e00" <= c <= "\u9fff" for c in s["zh_name"]))
    )

    manifest = {
        "version": client.short_version,
        "full_version": client.full_version,
        "build": client.build,
        "publisher": client.publisher,
        "game_id": client.game_id,
        "game_dir": str(client.game_dir),
        "game_params_path": gp_path,
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "entity_def_count": entity_def_count,
        "ship_count": len(ships),
        "ship_zh_hit_count": zh_hits,
        "projectile_count": len(projectiles),
        "consumable_count": len(consumables),
        "map_count": map_count,
        "icon_count": icon_count,
        "font_files": font_files,
        "i18n_sources": [asdict(s) for s in loc.sources],
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return SyncResult(
        version=client.short_version,
        full_version=client.full_version,
        build=client.build,
        output_dir=out_dir,
        entity_def_count=entity_def_count,
        ship_count=len(ships),
        ship_zh_hit_count=zh_hits,
        projectile_count=len(projectiles),
        consumable_count=len(consumables),
        map_count=map_count,
        icon_count=icon_count,
        font_files=font_files,
        i18n_sources=[asdict(s) for s in loc.sources],
    )
