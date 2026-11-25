"""俄服 GameParams_py3.data / GameParams_py2.data 容错解包与舰船/弹药/消耗品索引构建。"""

from __future__ import annotations

import io
import pickle
import zlib
from typing import Any

from .idx_vfs import KorabliVFS
from .localization import LocalizationBundle


GAMEPARAMS_CANDIDATES = (
    "content/GameParams_py3.data",
    "content/GameParams_py2.data",
    "content/GameParams.data",
)


class _GPStub:
    """容错反序列化占位对象，完整保留 GameParams 的属性与嵌套字典。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def __setstate__(self, state: Any) -> None:
        if isinstance(state, dict):
            self.__dict__.update(state)
        elif isinstance(state, tuple) and len(state) == 2 and isinstance(state[1], dict):
            self.__dict__.update(state[1])


class _TolerantUnpickler(pickle.Unpickler):
    _stub_cache: dict[tuple[str, str], type] = {}

    def find_class(self, module: str, name: str) -> Any:
        # 仅放行基础内建类型，其余游戏自定义类全部映射为安全动态占位类
        if module == "builtins" and name in {
            "dict", "list", "tuple", "set", "frozenset", "bytes", "bytearray",
            "str", "int", "float", "bool", "complex", "slice", "object",
        }:
            return super().find_class(module, name)
        key = (module, name)
        cls = self._stub_cache.get(key)
        if cls is None:
            cls = type(name, (_GPStub,), {"__module__": module})
            self._stub_cache[key] = cls
        return cls


def _decode_game_params_raw(raw: bytes, is_py2: bool) -> dict[str, Any]:
    decompressed = zlib.decompress(raw[::-1])
    if is_py2:
        obj = _TolerantUnpickler(io.BytesIO(decompressed), encoding="latin1").load()
    else:
        obj = _TolerantUnpickler(io.BytesIO(decompressed)).load()

    # 兼容三种顶层结构: (dict, set) / [dict, ...] / {"": dict} / flat dict
    if isinstance(obj, (tuple, list)) and obj:
        obj = obj[0]
    if isinstance(obj, dict):
        if "" in obj and isinstance(obj[""], dict):
            return obj[""]
        return obj
    raise ValueError(f"无法识别的 GameParams 顶层类型: {type(obj).__name__}")


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _extract_ship_max_hp_and_range(ship_obj: Any) -> tuple[int, float]:
    """尝试从船只组件中估算默认船体血量与主炮最大射程（米）。"""
    max_hp = 0
    max_range = 0.0
    d = ship_obj.__dict__ if hasattr(ship_obj, "__dict__") else (ship_obj if isinstance(ship_obj, dict) else {})
    for key, val in d.items():
        if not isinstance(key, str) or val is None:
            continue
        tinfo = _attr(val, "typeinfo")
        comp_type = _attr(tinfo, "type") if tinfo is not None else None
        if comp_type == "Hull" or "Hull" in key:
            hp = _attr(val, "health", 0)
            if isinstance(hp, (int, float)) and hp > max_hp:
                max_hp = int(hp)
        if comp_type == "Artillery" or "Artillery" in key:
            dist = _attr(val, "maxDist", 0.0)
            if isinstance(dist, (int, float)) and dist > max_range:
                max_range = float(dist)
    return max_hp, max_range


def classify_aircraft_minimap_icon(entry: Any, params: dict[str, Any]) -> tuple[str, bool, bool]:
    """严格遵循俄服官方 plane_marker.unbound 规则，将 GameParams Aircraft 映射到小地图 SVG 图标名。

    返回 `(icon_stem, is_consumable, is_airsupport)`，例如：
    - 弹射/巡逻战斗机 -> `("consumables_fighter", True, False)`
    - 水侦 -> `("consumables_scout", True, False)`
    - 航母火箭攻击机 -> `("fighter_he" | "fighter_ap", False, False)`
    - 航母俯冲轰炸机 -> `("bomber_he" | "bomber_ap", False, False)`
    - 航母鱼雷机 -> `("torpedo_regular" | "torpedo_deepwater", False, False)`
    - 航母跳弹机 -> `("skip_he", False, False)`
    - 反潜深弹空袭 -> `("airsupport_bomber_depth_charge", False, True)`
    - 烟雾/间谍空袭 -> `("airsupport_scout_smoker" | "airsupport_scout_spy", False, True)`
    """
    tinfo = _attr(entry, "typeinfo")
    species = str(_attr(tinfo, "species", "") or "")
    raw_sub = _attr(entry, "planeSubtype") or ()
    sub_set = {str(s).lower() for s in raw_sub}

    is_airsupport = "airsupport" in sub_set
    is_consumable = ("consumable" in sub_set) and (not is_airsupport)
    is_smoke = "smoke" in sub_set
    is_spy = "spy" in sub_set
    is_upgrade = bool(_attr(entry, "isUpgraded") or _attr(entry, "isModified"))

    bomb_name = _attr(entry, "bombName")
    bomb_obj = params.get(bomb_name) if isinstance(bomb_name, str) and bomb_name else None
    bomb_ammo = str(_attr(bomb_obj, "ammoType", "") or "").lower() if bomb_obj is not None else ""
    bomb_ti = _attr(bomb_obj, "typeinfo") if bomb_obj is not None else None
    bomb_sp = str(_attr(bomb_ti, "species", "") or "").lower() if bomb_ti is not None else ""
    bomb_deepwater = bool(_attr(bomb_obj, "isDeepWater")) if bomb_obj is not None else False

    # GameParams 中 species="Bomber" 为鱼雷机，species="Dive" 为俯冲轰炸机
    if species == "Scout":
        base = "scout"
    elif species == "Dive":
        base = "bomber"
    elif species == "Bomber":
        base = "torpedo"
    elif species == "Fighter":
        base = "fighter"
    elif species == "Skip":
        base = "skip"
    elif species == "Mine":
        base = "minebomber"
    else:
        base = "auxiliary"

    # 极少数测试拦截机带 airsupport + Fighter，官方无 airsupport_fighter_*.svg，归为巡逻战斗机图标
    if is_airsupport and base == "fighter":
        return "consumables_fighter", True, True

    prefix = "consumables_" if is_consumable else ("airsupport_" if is_airsupport else "")

    ammo_post = ""
    if not is_consumable:
        if base == "bomber":
            if bomb_sp == "depthcharge" or bomb_ammo == "depthcharge":
                ammo_post = "_depth_charge"
            elif bomb_ammo == "ap":
                ammo_post = "_ap"
            else:
                ammo_post = "_he"
        elif base == "torpedo":
            ammo_post = "_deepwater" if bomb_deepwater else "_regular"
        elif base == "fighter":
            if is_upgrade:
                ammo_post = ""
            elif bomb_ammo == "ap":
                ammo_post = "_ap"
            else:
                ammo_post = "_he"
        elif base == "skip":
            ammo_post = "_he"
        elif base == "minebomber":
            ammo_post = "_mine_seamine"

    postfix = ""
    if is_upgrade:
        postfix = "_upgrade"
    elif is_smoke:
        postfix = "_smoker"
    elif is_spy:
        postfix = "_spy"

    return f"{prefix}{base}{ammo_post}{postfix}", is_consumable, is_airsupport


def extract_game_params_catalogs(
    vfs: KorabliVFS,
    loc: LocalizationBundle,
) -> tuple[
    str,
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    """从 VFS 读取 GameParams，提取 ships / projectiles / consumables / aircrafts 四个版本化字典。"""
    gp_path = None
    for cand in GAMEPARAMS_CANDIDATES:
        if vfs.exists(cand):
            gp_path = cand
            break
    if gp_path is None:
        raise FileNotFoundError(f"VFS 中未找到 GameParams 文件，已尝试: {GAMEPARAMS_CANDIDATES}")

    raw = vfs.read_bytes(gp_path, verify_crc=True)
    is_py2 = "py2" in gp_path.lower()
    params = _decode_game_params_raw(raw, is_py2=is_py2)

    ships: dict[str, dict[str, Any]] = {}
    projectiles: dict[str, dict[str, Any]] = {}
    consumables: dict[str, dict[str, Any]] = {}
    aircrafts: dict[str, dict[str, Any]] = {}

    for internal_name, entry in params.items():
        if not isinstance(internal_name, str) or entry is None:
            continue
        tinfo = _attr(entry, "typeinfo")
        if tinfo is None:
            continue
        gp_type = _attr(tinfo, "type", "")
        species = _attr(tinfo, "species", "") or ""
        nation = _attr(tinfo, "nation", "") or ""
        obj_id = _attr(entry, "id")
        if obj_id is None:
            continue

        if gp_type == "Ship":
            index = str(_attr(entry, "index", "") or "")
            level = int(_attr(entry, "level", 0) or 0)
            group = str(_attr(entry, "group", "") or "")
            est_hp, main_range = _extract_ship_max_hp_and_range(entry)
            zh_name = loc.ship_name_zh(index, fallback=internal_name) if index else internal_name
            zh_full = loc.ship_full_name_zh(index, fallback=zh_name) if index else zh_name
            ru_name = loc.ship_name_ru(index, fallback=internal_name) if index else internal_name
            ships[str(obj_id)] = {
                "id": int(obj_id),
                "internal_name": internal_name,
                "index": index,
                "species": str(species),
                "level": level,
                "nation": str(nation),
                "group": group,
                "default_hp": est_hp,
                "main_battery_range_m": round(main_range, 1),
                "zh_name": zh_name,
                "zh_full_name": zh_full,
                "ru_name": ru_name,
            }

        elif gp_type == "Projectile":
            ammo_type = str(_attr(entry, "ammoType", "") or "")
            speed = float(_attr(entry, "speed", 0.0) or 0.0)
            max_dist = float(_attr(entry, "maxDist", 0.0) or 0.0)
            projectiles[str(obj_id)] = {
                "id": int(obj_id),
                "internal_name": internal_name,
                "species": str(species),
                "ammo_type": ammo_type,
                "speed": speed,
                "max_dist": max_dist,
                "is_secondary": "secondary" in internal_name.lower() or species == "Secondary",
            }

        elif gp_type == "Aircraft":
            icon_name, is_cons, is_as = classify_aircraft_minimap_icon(entry, params)
            raw_sub = _attr(entry, "planeSubtype") or ()
            aircrafts[str(obj_id)] = {
                "id": int(obj_id),
                "internal_name": internal_name,
                "species": str(species),
                "subtypes": [str(s) for s in raw_sub],
                "icon_name": icon_name,
                "is_consumable": is_cons,
                "is_airsupport": is_as,
            }

        elif gp_type == "Ability":
            # 遍历所有子变体（默认/金币/特殊）抓取探测半径（BigWorld 单位）和持续时间
            d = entry.__dict__ if hasattr(entry, "__dict__") else (entry if isinstance(entry, dict) else {})
            variants: dict[str, dict[str, Any]] = {}
            for k, v in d.items():
                if k in {"id", "index", "name", "typeinfo"} or v is None:
                    continue
                ctype = _attr(v, "consumableType", "")
                if not ctype:
                    continue
                variants[k] = {
                    "consumable_type": str(ctype),
                    "work_time": float(_attr(v, "workTime", 0.0) or 0.0),
                    "dist_ship_bw": float(_attr(v, "distShip", 0.0) or 0.0),
                    "dist_torpedo_bw": float(_attr(v, "distTorpedo", 0.0) or 0.0),
                    "radius_bw": float(_attr(v, "radius", 0.0) or 0.0),
                }
            if variants:
                index = str(_attr(entry, "index", "") or "")
                consumables[str(obj_id)] = {
                    "id": int(obj_id),
                    "internal_name": internal_name,
                    "index": index,
                    "zh_name": loc.get_zh(f"IDS_DOCK_CONSUME_TITLE_{internal_name.upper()}", internal_name),
                    "variants": variants,
                }

    return gp_path, ships, projectiles, consumables, aircrafts
