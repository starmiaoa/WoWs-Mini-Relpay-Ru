"""俄服汉化 mod 与内置多语言词库加载器。

严格遵循客户端 paths.xml 的 VFS 挂载优先级自动定位汉化 .mo：
1. bin/<build>/lk_mods/*.mkmod -> texts/ru/LC_MESSAGES/*.mo
2. bin/<build>/res_mods/texts/ru/LC_MESSAGES/global.mo（散装汉化兼容）
3. bin/<build>/mods/*.mkmod -> texts/ru/LC_MESSAGES/*.mo（如澪刻 aa_lk_i18n_pack.mkmod）
4. bin/<build>/res/texts/ru/LC_MESSAGES/global.mo（客户端内置俄语兜底）
"""

from __future__ import annotations

import gettext
import hashlib
import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .client_locator import KorabliClientInfo


MO_TARGET = "texts/ru/lc_messages/global.mo"
MO_PREFIX = "texts/ru/lc_messages/"


@dataclass
class LocalizationSource:
    """记录单个已加载 .mo 来源以便溯源与更新校验。"""

    kind: str       # "mkmod" | "res_mods" | "builtin"
    path: str
    inner_path: str
    sha256: str
    key_count: int


@dataclass
class LocalizationBundle:
    """整合后的简体中文（汉化 mod）与内置俄语双语查询表。"""

    zh_catalog: dict[str, str] = field(default_factory=dict)
    ru_catalog: dict[str, str] = field(default_factory=dict)
    sources: list[LocalizationSource] = field(default_factory=list)
    font_candidates: list[tuple[Path, str]] = field(default_factory=list)  # (mkmod_path, inner_ttf)

    def get_zh(self, key: str, default: str = "") -> str:
        val = self.zh_catalog.get(key)
        if val:
            return val
        val = self.ru_catalog.get(key)
        if val:
            return val
        return default

    def get_ru(self, key: str, default: str = "") -> str:
        return self.ru_catalog.get(key, default)

    def ship_name_zh(self, ship_index: str, fallback: str = "") -> str:
        return self.get_zh(f"IDS_{ship_index}", fallback or ship_index)

    def ship_full_name_zh(self, ship_index: str, fallback: str = "") -> str:
        return self.get_zh(f"IDS_{ship_index}_FULL", self.ship_name_zh(ship_index, fallback))

    def ship_name_ru(self, ship_index: str, fallback: str = "") -> str:
        return self.get_ru(f"IDS_{ship_index}", fallback or ship_index)

    def map_name_zh(self, space_name: str, fallback: str = "") -> str:
        """地图键格式为 IDS_SPACES/<MAP_UPPER>（带斜杠）。"""
        clean = space_name.replace("spaces/", "").strip("/")
        key = f"IDS_SPACES/{clean.upper()}"
        return self.get_zh(key, fallback or clean)

    def map_name_ru(self, space_name: str, fallback: str = "") -> str:
        clean = space_name.replace("spaces/", "").strip("/")
        key = f"IDS_SPACES/{clean.upper()}"
        return self.get_ru(key, fallback or clean)


def _parse_mo_bytes(raw: bytes) -> dict[str, str]:
    """用标准库 GNUTranslations 解析 .mo 二进制词库，提取全部字符串键值。"""
    trans = gettext.GNUTranslations(io.BytesIO(raw))
    result: dict[str, str] = {}
    for k, v in trans._catalog.items():  # type: ignore[attr-defined]
        if isinstance(k, str) and k and isinstance(v, str):
            result[k] = v
    return result


def _scan_mkmod_dir(
    mkmod_dir: Path,
    catalog: dict[str, str],
    sources: list[LocalizationSource],
    font_candidates: list[tuple[Path, str]],
) -> bool:
    """扫描目录内全部 .mkmod（ZIP）文件，按文件名顺序叠加 .mo 词库与字体候选。"""
    if not mkmod_dir.is_dir():
        return False

    found_global_mo = False
    for mod_path in sorted(mkmod_dir.glob("*.mkmod")):
        if not zipfile.is_zipfile(mod_path):
            continue
        with zipfile.ZipFile(mod_path, "r") as zf:
            names = zf.namelist()
            # 优先收集字体包内的思源黑体等中文字体
            for name in names:
                low = name.lower()
                if low.endswith((".ttf", ".otf", ".ttc")) and (
                    "sourcehan" in low or "noto" in low or "cn" in low or "zh" in low
                ):
                    font_candidates.append((mod_path, name))

            # 先加载 global.mo，再加载扩展 .mo（如 zzz_lk_*.mo）
            mo_entries = [n for n in names if n.replace("\\", "/").lower().startswith(MO_PREFIX) and n.lower().endswith(".mo")]
            mo_entries.sort(key=lambda n: (0 if n.replace("\\", "/").lower() == MO_TARGET else 1, n.lower()))

            for inner in mo_entries:
                raw = zf.read(inner)
                parsed = _parse_mo_bytes(raw)
                if not parsed:
                    continue
                is_global = inner.replace("\\", "/").lower() == MO_TARGET
                # 只在检测到包含汉字或属于 global/lk 扩展包时覆盖中文表
                sample_text = "".join(list(parsed.values())[:200])
                has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in sample_text)
                if not has_cjk and "lk_" not in mod_path.name.lower():
                    continue
                catalog.update(parsed)
                if is_global:
                    found_global_mo = True
                sources.append(
                    LocalizationSource(
                        kind="mkmod",
                        path=str(mod_path),
                        inner_path=inner,
                        sha256=hashlib.sha256(raw).hexdigest(),
                        key_count=len(parsed),
                    )
                )
    return found_global_mo


def load_localization(client: KorabliClientInfo) -> LocalizationBundle:
    """从俄服客户端自动加载汉化 mod（中文）与内置俄语词库。"""
    bundle = LocalizationBundle()

    # 1. 内置俄语词库作为基准和双语对照
    builtin_mo = client.res_dir / "texts" / "ru" / "LC_MESSAGES" / "global.mo"
    if builtin_mo.is_file():
        raw = builtin_mo.read_bytes()
        bundle.ru_catalog = _parse_mo_bytes(raw)
        bundle.sources.append(
            LocalizationSource(
                kind="builtin",
                path=str(builtin_mo),
                inner_path="texts/ru/LC_MESSAGES/global.mo",
                sha256=hashlib.sha256(raw).hexdigest(),
                key_count=len(bundle.ru_catalog),
            )
        )

    # 2. 按从低到高的挂载优先级叠加汉化 mod（后者覆盖前者，最终最高优先级生效）：
    #    mods/*.mkmod -> res_mods/texts/.../global.mo -> lk_mods/*.mkmod
    _scan_mkmod_dir(client.mods_dir, bundle.zh_catalog, bundle.sources, bundle.font_candidates)

    loose_mo = client.res_mods_dir / "texts" / "ru" / "LC_MESSAGES" / "global.mo"
    if loose_mo.is_file():
        raw = loose_mo.read_bytes()
        parsed = _parse_mo_bytes(raw)
        sample_text = "".join(list(parsed.values())[:200])
        if any("\u4e00" <= ch <= "\u9fff" for ch in sample_text):
            bundle.zh_catalog.update(parsed)
            bundle.sources.append(
                LocalizationSource(
                    kind="res_mods",
                    path=str(loose_mo),
                    inner_path="texts/ru/LC_MESSAGES/global.mo",
                    sha256=hashlib.sha256(raw).hexdigest(),
                    key_count=len(parsed),
                )
            )

    _scan_mkmod_dir(client.lk_mods_dir, bundle.zh_catalog, bundle.sources, bundle.font_candidates)

    return bundle


def extract_game_localization_dict(bundle: LocalizationBundle) -> dict[str, dict[str, str]]:
    """提取地图、战斗模式、场景与常见 UI 文案映射，写入本地缓存。"""
    maps_zh: dict[str, str] = {}
    maps_ru: dict[str, str] = {}
    modes_zh: dict[str, str] = {}
    scenarios_zh: dict[str, str] = {}

    all_keys = set(bundle.zh_catalog.keys()) | set(bundle.ru_catalog.keys())
    for k in all_keys:
        if k.startswith("IDS_SPACES/") and not k.endswith(("_DESCR", "_TOOLTIP")):
            space_key = k.split("/", 1)[1]
            maps_zh[space_key] = bundle.get_zh(k, space_key)
            maps_ru[space_key] = bundle.get_ru(k, space_key)
        elif k.startswith("IDS_GAMEMODE_") and k.endswith("_TITLE"):
            mode_key = k[len("IDS_GAMEMODE_") : -len("_TITLE")]
            modes_zh[mode_key] = bundle.get_zh(k, mode_key)
        elif k.startswith("IDS_SCENARIO_") and not k.endswith(("_DESCR", "_TOOLTIP")):
            scen_key = k[len("IDS_SCENARIO_") :]
            scenarios_zh[scen_key] = bundle.get_zh(k, scen_key)

    # 常见战斗队列类型中文名称
    battle_types_keys = {
        "RandomBattle": "IDS_RANDOMBATTLE",
        "CooperativeBattle": "IDS_COOPERATIVEBATTLE",
        "RankedBattle": "IDS_RANKEDBATTLE",
        "ClanBattle": "IDS_CLANBATTLE",
        "BrawlBattle": "IDS_BRAWLBATTLE",
        "PVEBattle": "IDS_PVEBATTLE",
        "TrainingBattle": "IDS_TRAININGBATTLE",
        "EventBattle": "IDS_EVENTBATTLE",
    }
    battle_types_zh: dict[str, str] = {}
    for btype, ids_key in battle_types_keys.items():
        battle_types_zh[btype] = bundle.get_zh(ids_key, btype)

    return {
        "maps_zh": dict(sorted(maps_zh.items())),
        "maps_ru": dict(sorted(maps_ru.items())),
        "modes_zh": dict(sorted(modes_zh.items())),
        "scenarios_zh": dict(sorted(scenarios_zh.items())),
        "battle_types_zh": battle_types_zh,
    }
