"""版本化数据仓库访问接口：根据回放版本号自动定位对应的定义、舰船、弹药、地图与汉化。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import DEFAULT_DATA_DIR
from ..i18n import (
    BATTLE_TYPE_I18N,
    MODE_I18N,
    clean_english_ship_name,
    format_english_map_name,
    normalize_lang,
    to_traditional_zh,
)
from .client_locator import normalize_short_version


@dataclass
class VersionDataStore:
    """加载单一客户端版本的提取结果，提供统一只读查询。"""

    version: str
    root_dir: Path
    manifest: dict[str, Any]
    ships: dict[str, dict[str, Any]]
    projectiles: dict[str, dict[str, Any]]
    consumables: dict[str, dict[str, Any]]
    localization: dict[str, dict[str, str]]
    _ships_by_index: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _ships_by_internal: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        for s in self.ships.values():
            idx = str(s.get("index") or "").upper()
            if idx and idx not in self._ships_by_index:
                self._ships_by_index[idx] = s
            iname = str(s.get("internal_name") or "").upper()
            if iname and iname not in self._ships_by_internal:
                self._ships_by_internal[iname] = s

    @property
    def entity_defs_dir(self) -> Path:
        return self.root_dir / "entity_defs"

    @property
    def maps_dir(self) -> Path:
        return self.root_dir / "maps"

    @property
    def fonts_dir(self) -> Path:
        return self.root_dir / "fonts"

    @property
    def icons_dir(self) -> Path:
        return self.root_dir / "icons"

    def get_ship(self, ship_params_id: int | str) -> dict[str, Any] | None:
        return self.ships.get(str(ship_params_id))

    def get_ship_by_index(self, ship_index: str) -> dict[str, Any] | None:
        """按舰船索引号（如 PFSC211）或内部代号快速检索舰船汉化信息。"""
        key = ship_index.strip().upper()
        if not key:
            return None
        hit = self._ships_by_index.get(key)
        if hit is not None:
            return hit
        hit = self._ships_by_internal.get(key)
        if hit is not None:
            return hit
        # 兼容如 "PFSC211-La-Rochelle" -> "PFSC211_LA_ROCHELLE"
        norm = key.replace("-", "_")
        if norm in self._ships_by_internal:
            return self._ships_by_internal[norm]
        prefix = norm.split("_")[0]
        return self._ships_by_index.get(prefix)

    def get_ship_name_from_dict(
        self,
        ship: dict[str, Any] | None,
        lang: str = "zh_CN",
        fallback: str = "",
    ) -> str:
        if not ship:
            return fallback
        code = normalize_lang(lang)
        zh = str(ship.get("zh_name") or "")
        ru = str(ship.get("ru_name") or "")
        iname = str(ship.get("internal_name") or "")
        if code == "zh_CN":
            return zh or ru or iname or fallback
        if code == "zh_TW":
            base = zh or ru or iname or fallback
            return to_traditional_zh(base)
        if code == "ru":
            return ru or clean_english_ship_name(iname, ru) or zh or fallback
        # en
        return clean_english_ship_name(iname, ru) or ru or zh or fallback

    def get_ship_name(self, ship_params_id: int | str, lang: str = "zh_CN", fallback: str = "") -> str:
        ship = self.get_ship(ship_params_id)
        if ship:
            return self.get_ship_name_from_dict(ship, lang=lang, fallback=fallback)
        return fallback or f"#{ship_params_id}"

    def get_ship_name_zh(self, ship_params_id: int | str, fallback: str = "") -> str:
        ship = self.get_ship(ship_params_id)
        if ship:
            return ship.get("zh_name") or ship.get("ru_name") or ship.get("internal_name") or fallback
        return fallback or f"未知战舰#{ship_params_id}"

    def get_projectile(self, proj_id: int | str) -> dict[str, Any] | None:
        return self.projectiles.get(str(proj_id))

    def get_consumable(self, ability_id: int | str) -> dict[str, Any] | None:
        return self.consumables.get(str(ability_id))

    def get_map_dir(self, map_name: str) -> Path | None:
        clean = map_name.replace("spaces/", "").strip("/")
        direct = self.maps_dir / clean
        if direct.is_dir():
            return direct
        low = clean.lower()
        if self.maps_dir.is_dir():
            for child in self.maps_dir.iterdir():
                if child.is_dir() and child.name.lower() == low:
                    return child
        return None

    def get_map_meta(self, map_name: str) -> dict[str, Any]:
        mdir = self.get_map_dir(map_name)
        if mdir is not None and (mdir / "meta.json").is_file():
            return json.loads((mdir / "meta.json").read_text(encoding="utf-8"))
        clean = map_name.replace("spaces/", "").strip("/")
        return {
            "space_name": clean,
            "min_x": -9,
            "max_x": 8,
            "min_y": -9,
            "max_y": 8,
            "chunk_size": 100.0,
            "space_width": 1400.0,
            "space_height": 1400.0,
            "zh_name": self.get_map_name_zh(clean),
            "ru_name": clean,
        }

    def get_map_name(self, map_name: str, lang: str = "zh_CN") -> str:
        clean = map_name.replace("spaces/", "").strip("/")
        key = clean.upper()
        code = normalize_lang(lang)
        if code == "zh_CN":
            return self.localization.get("maps_zh", {}).get(key, clean)
        if code == "zh_TW":
            zh = self.localization.get("maps_zh", {}).get(key, clean)
            return to_traditional_zh(zh)
        if code == "ru":
            return self.localization.get("maps_ru", {}).get(key) or self.localization.get("maps_zh", {}).get(key, clean)
        return format_english_map_name(clean)

    def get_map_name_zh(self, map_name: str) -> str:
        return self.get_map_name(map_name, lang="zh_CN")

    def get_battle_type_name(self, game_type: str, lang: str = "zh_CN") -> str:
        code = normalize_lang(lang)
        if code == "zh_CN":
            hit = self.localization.get("battle_types_zh", {}).get(game_type)
            if hit:
                return hit
        entry = BATTLE_TYPE_I18N.get(game_type)
        if entry:
            return entry.get(code, entry["zh_CN"])
        zh = self.localization.get("battle_types_zh", {}).get(game_type, game_type)
        return to_traditional_zh(zh) if code == "zh_TW" else zh

    def get_battle_type_zh(self, game_type: str) -> str:
        return self.get_battle_type_name(game_type, lang="zh_CN")

    def get_scenario_name(self, scenario: str, lang: str = "zh_CN") -> str:
        code = normalize_lang(lang)
        key = scenario.upper()
        if code in ("zh_CN", "zh_TW"):
            zh = self.get_scenario_zh(scenario)
            return to_traditional_zh(zh) if code == "zh_TW" else zh
        if "DOMINATION" in key:
            return MODE_I18N["DOMINATION"][code]
        if "STANDARD" in key or "STANDART" in key:
            return MODE_I18N["STANDART"][code]
        if "EPICENTER" in key:
            return MODE_I18N["EPICENTER"][code]
        if "ARMS" in key:
            return MODE_I18N["ARMS_RACE"][code]
        if "AIRSHIP" in key:
            return MODE_I18N["AIRSHIP"][code]
        return scenario.replace("_", " ").strip().title()

    def get_scenario_zh(self, scenario: str) -> str:
        scenarios = self.localization.get("scenarios_zh", {})
        key = scenario.upper()
        if key in scenarios:
            return scenarios[key]
        # 常见回退规则（例如 domination_4point -> DOMINATION）
        modes = self.localization.get("modes_zh", {})
        prefix = key.split("_")[0]
        if prefix in modes:
            return modes[prefix]
        if "DOMINATION" in key:
            return modes.get("DOMINATION", "制海权")
        if "STANDARD" in key or "STANDART" in key:
            return modes.get("STANDART", "标准战")
        if "EPICENTER" in key:
            return modes.get("EPICENTER", "震中")
        if "ARMS" in key:
            return modes.get("ARMS_RACE", "军备竞赛")
        return scenario


def _version_tuple(ver: str) -> tuple[int, ...]:
    nums: list[int] = []
    for seg in ver.replace(",", ".").split("."):
        if seg.isdigit():
            nums.append(int(seg))
    return tuple(nums)


def list_synced_versions(data_dir: Path | str = DEFAULT_DATA_DIR) -> list[str]:
    versions_dir = Path(data_dir) / "versions"
    if not versions_dir.is_dir():
        return []
    found = [p.name for p in versions_dir.iterdir() if p.is_dir() and (p / "manifest.json").is_file()]
    return sorted(found, key=_version_tuple, reverse=True)


def load_version_store(
    replay_version: str | None = None,
    data_dir: Path | str = DEFAULT_DATA_DIR,
) -> VersionDataStore:
    """根据回放版本号加载精确匹配或最近邻的已同步俄服版本数据。"""
    versions_dir = Path(data_dir) / "versions"
    available = list_synced_versions(data_dir)
    if not available:
        raise FileNotFoundError(
            f"在 {versions_dir} 中未找到任何已同步的俄服客户端数据，请先运行 `korabli-minimap sync`。"
        )

    chosen: str | None = None
    if replay_version:
        target = normalize_short_version(replay_version)
        if target in available:
            chosen = target
        else:
            # 寻找同主次版本或版本号距离最近的可用定义（便于小热修兼容）
            target_t = _version_tuple(target)
            def _dist(v: str) -> tuple[int, int, int]:
                vt = _version_tuple(v)
                a = abs((vt[0] if len(vt) > 0 else 0) - (target_t[0] if len(target_t) > 0 else 0))
                b = abs((vt[1] if len(vt) > 1 else 0) - (target_t[1] if len(target_t) > 1 else 0))
                c = abs((vt[2] if len(vt) > 2 else 0) - (target_t[2] if len(target_t) > 2 else 0))
                return (a, b, c)
            chosen = min(available, key=_dist)
    else:
        chosen = available[0]

    root = versions_dir / chosen
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    ships = json.loads((root / "ships.json").read_text(encoding="utf-8"))
    projectiles = json.loads((root / "projectiles.json").read_text(encoding="utf-8"))
    consumables = json.loads((root / "consumables.json").read_text(encoding="utf-8"))
    localization = json.loads((root / "localization.json").read_text(encoding="utf-8"))

    return VersionDataStore(
        version=chosen,
        root_dir=root,
        manifest=manifest,
        ships=ships,
        projectiles=projectiles,
        consumables=consumables,
        localization=localization,
    )
