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


def extract_game_params_catalogs(
    vfs: KorabliVFS,
    loc: LocalizationBundle,
) -> tuple[str, dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """从 VFS 读取 GameParams，提取 ships / projectiles / consumables 三个版本化字典。"""
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

    return gp_path, ships, projectiles, consumables
