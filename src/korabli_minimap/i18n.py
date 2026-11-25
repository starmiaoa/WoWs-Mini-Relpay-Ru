"""四语国际化（I18N）核心模块：支持简体中文 (zh_CN)、繁體中文 (zh_TW)、Русский (ru)、English (en)。

同时服务于：
1. 桌面图形界面（GUI 全部标签页、按钮、提示、设置与弹窗）；
2. 视频渲染引擎（小地图 HUD 团队栏、主角战报、图例、聊天区、击杀播报、终局横幅、舰船/地图/模式名称）。
"""

from __future__ import annotations

import ctypes
import locale
import os
import re
import sys
from typing import Any

try:
    import zhconv as _zhconv
except ImportError:
    _zhconv = None


DEFAULT_LANG = "zh_CN"

SUPPORTED_LANGUAGES: list[tuple[str, str]] = [
    ("zh_CN", "简体中文"),
    ("zh_TW", "繁體中文"),
    ("ru", "Русский"),
    ("en", "English"),
]

VALID_LANG_CODES = {code for code, _ in SUPPORTED_LANGUAGES}


def normalize_lang(lang: str | None) -> str:
    if not lang:
        return DEFAULT_LANG
    cleaned = lang.strip().replace("-", "_")
    if cleaned.lower() in ("system", "auto"):
        return detect_system_language()
    if cleaned in VALID_LANG_CODES:
        return cleaned
    low = cleaned.lower()
    if low in ("zh", "zh_cn", "zh_hans", "cn", "chs"):
        return "zh_CN"
    if low in ("zh_tw", "zh_hk", "zh_hant", "tw", "cht"):
        return "zh_TW"
    if low.startswith("ru"):
        return "ru"
    if low.startswith("en"):
        return "en"
    return "en"


def map_locale_to_lang(locale_str: str | None) -> str:
    """将系统语言标识映射到支持的目标语言（zh_CN/zh_TW/ru/en），非目标语言统一回退英语。"""
    if not locale_str:
        return "en"
    raw = locale_str.strip().replace("-", "_").lower()
    if raw.startswith("zh") or "chinese" in raw:
        if any(tag in raw for tag in ("tw", "hk", "mo", "hant", "cht", "traditional")):
            return "zh_TW"
        return "zh_CN"
    if raw.startswith("ru") or "russian" in raw:
        return "ru"
    if raw.startswith("en") or "english" in raw:
        return "en"
    return "en"


def map_windows_lcid_to_lang(lcid: int) -> str:
    """将 Windows UI 语言 LCID 映射到目标语言，不在目标范围内一律返回英语 (en)。"""
    primary = lcid & 0x3FF
    sublang = (lcid >> 10) & 0x3F
    if primary == 0x04:  # Chinese
        # 0x01=TW, 0x03=HK, 0x05=MO -> 繁体; 0x02=CN, 0x04=SG -> 简体
        if sublang in (0x01, 0x03, 0x05):
            return "zh_TW"
        return "zh_CN"
    if primary == 0x19:  # Russian
        return "ru"
    if primary == 0x09:  # English
        return "en"
    return "en"


def detect_system_language() -> str:
    """自动识别当前操作系统界面语言（支持简/繁/俄/英，其余非目标语言统一回退英语）。"""
    if sys.platform == "win32":
        try:
            lcid = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
            if lcid > 0:
                return map_windows_lcid_to_lang(lcid)
        except Exception:
            pass

    for env_key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        val = os.environ.get(env_key)
        if val:
            return map_locale_to_lang(val.split(".")[0])

    try:
        loc = locale.getlocale()[0]
        if loc:
            return map_locale_to_lang(loc)
    except Exception:
        pass

    return "en"


def to_traditional_zh(text: str) -> str:
    """将简体中文精准转换为繁体中文（zh-tw）。"""
    if not text or _zhconv is None:
        return text
    return str(_zhconv.convert(text, "zh-tw"))


_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
_INDEX_PREFIX_RE = re.compile(r"^[A-Z]{2,5}\d{3}[_-]")
_YEAR_SUFFIX_RE = re.compile(r"(?:[ _](?:19|20)\d{2})$")
_PR_PREFIX_RE = re.compile(r"^Pr[ _]\d+[ _]", re.IGNORECASE)


def clean_english_ship_name(internal_name: str, ru_name: str = "") -> str:
    """为英文渲染生成自然可读的战舰英文名（优先保留拉丁官方名，俄文船名从内部代号清洗）。"""
    if ru_name and not _CYRILLIC_RE.search(ru_name):
        return ru_name.strip()
    if internal_name:
        s = _INDEX_PREFIX_RE.sub("", internal_name.strip())
        s = _PR_PREFIX_RE.sub("", s)
        s = s.replace("_", " ").replace("-", " ")
        s = _YEAR_SUFFIX_RE.sub("", s).strip()
        if s:
            return s
    return ru_name or internal_name or "Unknown"


# 常见俄服/战舰世界战斗地图英文标准名称（其余新地图自动按标题化清洗兜底）
ENGLISH_MAP_NAMES: dict[str, str] = {
    "01_SOLOMONS": "Solomon Islands",
    "02_TEARS_OF_THE_DESERT": "Tears of the Desert",
    "04_ARCHIPELAGO": "Archipelago",
    "05_RING": "Ring",
    "08_NE_PASSAGE": "Strait",
    "10_NE_BIG_RACE": "Big Race",
    "13_OC_NEW_DAWN": "New Dawn",
    "14_ATLANTIC": "Atlantic",
    "15_NE_NORTH": "North",
    "16_OC_BEES_TO_HONEY": "Hotspot",
    "17_NA_FAULT_LINE": "Fault Line",
    "18_NE_ICE_ISLANDS": "Islands of Ice",
    "19_OC_PREY": "Trap",
    "20_NE_TWO_BROTHERS": "Two Brothers",
    "22_TACTICAL": "Estuary",
    "23_SHARDS": "Shards",
    "25_SEA_HOPE": "Sea of Fortune",
    "28_NAVAL_MISSION": "Tears of the Desert",
    "33_NEW_TIERRA": "Land of Fire",
    "34_OC_ISLANDS": "Islands",
    "35_NE_NORTH_WINTER": "Northern Lights",
    "37_RIDGE": "Mountain Range",
    "38_CANADA": "Shatter",
    "40_Okinawa": "Okinawa",
    "40_OKINAWA": "Okinawa",
    "41_CONQUEST": "Trident",
    "42_NEIGHBORS": "Neighbors",
    "44_PATH_WARRIOR": "Warrior's Path",
    "45_ZIGZAG": "Loop",
    "46_ESTUARY": "Estuary",
    "47_SLEEPING_GIANT": "Sleeping Giant",
    "48_GREECE": "Greece",
    "50_GOLD_HARBOR": "Haven",
    "51_NORTHERN_WATERS": "Northern Waters",
    "52_CRASH_ZONE_ALPHA": "Crash Zone Alpha",
    "53_SHORESIDE": "Riposte",
    "54_FAROE": "Faroe Islands",
    "55_SEYCHELLES": "Seychelles",
    "56_KAMCHATKA": "Kamchatka",
}


def format_english_map_name(space_name: str) -> str:
    clean = space_name.replace("spaces/", "").strip("/")
    key = clean.upper()
    if key in ENGLISH_MAP_NAMES:
        return ENGLISH_MAP_NAMES[key]
    # 兜底清洗如 "47_Sleeping_Giant" / "15_NE_north" -> "Sleeping Giant"
    stripped = re.sub(r"^\d+_(?:NE_|OC_|NA_|S\d+_)?", "", clean, flags=re.IGNORECASE)
    return stripped.replace("_", " ").strip().title() or clean


# =============================================================================
# 战斗类型 / 模式 / 频道 / 胜负 / 舰种四语字典
# =============================================================================

BATTLE_TYPE_I18N: dict[str, dict[str, str]] = {
    "RandomBattle": {"zh_CN": "随机战", "zh_TW": "隨機戰", "ru": "Случайный бой", "en": "Random Battle"},
    "CooperativeBattle": {"zh_CN": "联合作战", "zh_TW": "聯合作戰", "ru": "Кооперативный бой", "en": "Co-op Battle"},
    "RankedBattle": {"zh_CN": "排位战", "zh_TW": "排位戰", "ru": "Ранговый бой", "en": "Ranked Battle"},
    "ClanBattle": {"zh_CN": "军团战", "zh_TW": "軍團戰", "ru": "Клановый бой", "en": "Clan Battle"},
    "BrawlBattle": {"zh_CN": "对决", "zh_TW": "對決", "ru": "Блиц", "en": "Brawl"},
    "PVEBattle": {"zh_CN": "行动", "zh_TW": "行動", "ru": "Операция", "en": "Operation"},
    "TrainingBattle": {"zh_CN": "训练房", "zh_TW": "訓練房", "ru": "Тренировочный бой", "en": "Training"},
    "EventBattle": {"zh_CN": "活动模式", "zh_TW": "活動模式", "ru": "Особый бой", "en": "Event Battle"},
}

MODE_I18N: dict[str, dict[str, str]] = {
    "DOMINATION": {"zh_CN": "制海权", "zh_TW": "制海權", "ru": "Превосходство", "en": "Domination"},
    "STANDART": {"zh_CN": "标准战", "zh_TW": "標準戰", "ru": "Стандартный бой", "en": "Standard Battle"},
    "EPICENTER": {"zh_CN": "震中", "zh_TW": "震央", "ru": "Эпицентр", "en": "Epicenter"},
    "ARMS_RACE": {"zh_CN": "军备竞赛", "zh_TW": "軍備競賽", "ru": "Гонка вооружений", "en": "Arms Race"},
    "AIRSHIP": {"zh_CN": "飞艇护航", "zh_TW": "飛艇護航", "ru": "Сопровождение дирижаблей", "en": "Airship Escort"},
}

CHAT_CHANNEL_I18N: dict[str, dict[str, str]] = {
    "battle_common": {"zh_CN": "全部", "zh_TW": "全部", "ru": "Все", "en": "All"},
    "battle_team": {"zh_CN": "队友", "zh_TW": "隊友", "ru": "Команда", "en": "Team"},
    "battle_prebattle": {"zh_CN": "小队", "zh_TW": "小隊", "ru": "Отряд", "en": "Div"},
    "battle_division": {"zh_CN": "小队", "zh_TW": "小隊", "ru": "Отряд", "en": "Div"},
}

SPECIES_SHORT_I18N: dict[str, dict[str, str]] = {
    "AirCarrier": {"zh_CN": "航母", "zh_TW": "航母", "ru": "АВ", "en": "CV"},
    "Battleship": {"zh_CN": "战列", "zh_TW": "戰艦", "ru": "ЛК", "en": "BB"},
    "Cruiser": {"zh_CN": "巡洋", "zh_TW": "巡洋", "ru": "КР", "en": "CA"},
    "Destroyer": {"zh_CN": "驱逐", "zh_TW": "驅逐", "ru": "ЭМ", "en": "DD"},
    "Submarine": {"zh_CN": "潜艇", "zh_TW": "潛艦", "ru": "ПЛ", "en": "SS"},
    "Auxiliary": {"zh_CN": "支援", "zh_TW": "支援", "ru": "Всп.", "en": "Aux"},
}

RESULT_KEY_MAP = {
    "胜利": "result_victory",
    "失败": "result_defeat",
    "平局": "result_draw",
    "战斗结束": "result_ended",
}


def translate_species_short(species: str, lang: str) -> str:
    code = normalize_lang(lang)
    entry = SPECIES_SHORT_I18N.get(species)
    if entry:
        return entry.get(code, entry["zh_CN"])
    return ""


def translate_chat_channel(channel: str, lang: str) -> str:
    code = normalize_lang(lang)
    entry = CHAT_CHANNEL_I18N.get(channel)
    if entry:
        return entry.get(code, entry["zh_CN"])
    if "team" in channel:
        return CHAT_CHANNEL_I18N["battle_team"][code]
    if "division" in channel or "prebattle" in channel:
        return CHAT_CHANNEL_I18N["battle_division"][code]
    return CHAT_CHANNEL_I18N["battle_common"][code]


def translate_result_banner(result_zh: str, lang: str) -> str:
    key = RESULT_KEY_MAP.get(result_zh, "result_ended")
    return tr(lang, key)


def translate_client_channel(channel: str, lang: str) -> str:
    return tr(lang, "channel_pt" if channel == "pt" else "channel_live")


# =============================================================================
# 全量四语文案表（渲染器 + GUI）
# =============================================================================

STRINGS: dict[str, dict[str, str]] = {
    "zh_CN": {
        # ---- 渲染器 HUD ----
        "hud_allies": "我方舰队",
        "hud_enemies": "敌方舰队",
        "hud_total_hp": "总血量 {hp}",
        "hud_legend_title": "图例与血条色标",
        "hud_legend_unspotted": "灭点",
        "hud_legend_smoke": "烟雾区",
        "hud_legend_spotted": "被点亮",
        "hud_self_title": "主角战报",
        "hud_self_title_ship": "主角战报 · {ship}",
        "hud_hp_pct": "生命值 ({pct}%)",
        "hud_sunk": "已沉没",
        "hud_dmg_caused": "造成伤害",
        "hud_dmg_potential": "潜在伤害",
        "hud_dmg_spotting": "侦察伤害",
        "hud_kills": "击沉敌舰",
        "hud_chat_title": "局内聊天（{n}）",
        "hud_chat_sub": "实时同步俄服全部 / 队友 / 小队频道",
        "hud_chat_empty": "暂无局内聊天记录",
        "hud_killfeed_title": "击杀播报（累计 {n}）",
        "hud_killfeed_empty": "暂无击沉记录",
        "hud_unknown_ship": "未知",
        "hud_final_score": "最终比分  {ally} : {enemy}",
        "result_victory": "胜利",
        "result_defeat": "失败",
        "result_draw": "平局",
        "result_ended": "战斗结束",
        # ---- GUI 标签页 ----
        "tab_replays": "回放",
        "tab_advanced": "高级",
        "tab_settings": "设置",
        "tab_about": "关于",
        # ---- GUI 通用 / 顶部 ----
        "channel_live": "正式服",
        "channel_pt": "PT测试服",
        "channel_custom": "自定义",
        "source_game": "游戏目录",
        "source_drop": "手动拖入",
        "tag_drop": "拖入",
        "status_ready": "● v{ver} 已就绪",
        "status_syncing": "◌ 正在同步数据...",
        "status_wait_sync": "◌ 准备同步 v{ver}",
        "status_sync_fail": "● 同步失败",
        "no_client_found": "未检测到俄服客户端，请点击右侧“选择目录”",
        "label_game_dir": "游戏目录",
        "btn_choose_dir": "选择目录",
        "dlg_choose_game_dir": "选择俄服《战舰世界》正式服或 PT 测试服游戏目录",
        # ---- Tab 1: 回放 ----
        "search_placeholder": "搜索战舰、地图或日期...",
        "btn_select_all": "全选",
        "btn_unselect_all": "取消全选",
        "btn_import_replays": "导入回放",
        "dlg_import_replays": "导入俄服回放文件",
        "dlg_replay_filter": "俄服战舰世界回放 (*.korablireplay)",
        "col_ship": "驾驶战舰",
        "col_map": "地图",
        "col_time": "战斗时间",
        "footer_replays": "共 {n} 场回放 · 可直接将 .korablireplay 文件拖入窗口",
        "btn_refresh_list": "刷新列表",
        "card_export": "导出设置",
        "label_resolution": "分辨率",
        "res_1k": "1920 × 1080  (1K)",
        "res_720p": "1280 × 720   (720p)",
        "res_2k": "2560 × 1440  (2K)",
        "res_4k": "3840 × 2160  (4K)",
        "res_custom": "自定义分辨率",
        "label_fps": "帧率 (FPS)",
        "sel_hint_empty": "请勾选左侧回放，或拖入文件",
        "sel_hint_count": "已选择 {n} 场回放",
        "btn_start_render": "开始生成",
        "btn_start_render_n": "开始生成 ({n} 场)",
        "btn_stop_render": "停止生成",
        "btn_open_output": "打开输出目录",
        "tip_auto_folder": "生成后按时间建文件夹并自动打开",
        "prog_preparing": "准备生成 {n} 场回放...",
        "prog_parsing": "正在解析回放...",
        "prog_rendering": "渲染中 {pct}% ({done}/{total} 帧)",
        "prog_stopping": "正在安全停止...",
        "prog_stopped": "已停止（已完成 {ok} 场）",
        "prog_done": "生成完成：成功 {ok} 场",
        "prog_done_fail": "生成完成：成功 {ok} 场，失败 {fail} 场",
        "prog_first_sync": "正在首次同步客户端资源...",
        "prog_first_sync_sub": "仅需几秒，后续无需重复同步",
        "prog_ver_sync": "正在同步 v{ver} 客户端资源...",
        "prog_wait": "请稍候",
        "tooltip_item": "{ship} · {map}\n时间：{time}\n文件：{file}",
        # ---- Tab 2: 高级 ----
        "adv_layers_title": "小地图与画面图层",
        "btn_reset_defaults": "恢复默认",
        "chk_smoke": "显示地图烟雾",
        "chk_unspotted": "显示灭点船只名称",
        "chk_spotted": "显示在点船只名称",
        "chk_mini_hp": "显示小地图迷你血条",
        "chk_chat": "显示底部局内聊天区域",
        "chk_shells": "显示主炮炮弹轨迹",
        "chk_torps": "显示鱼雷轨迹",
        "chk_planes": "显示航空中队",
        "chk_rings": "显示雷达 / 水听探测圈",
        "chk_dead": "显示沉没残骸图标",
        "chk_grid": "显示 10×10 战术网格",
        "adv_extra_title": "导出附加选项",
        "chk_keyframes": "同时导出代表性关键帧截图（开局 / 烟雾 / 灭点 / 交战 / 终局）",
        "label_banner_sec": "终局结算停留：",
        "suffix_sec": " 秒",
        "adv_log_title": "运行与排查日志",
        "btn_clear_log": "清空日志",
        "log_placeholder": "同步与渲染详细日志将记录在此处...",
        # ---- Tab 3: 设置 ----
        "set_appearance_title": "外观与语言",
        "label_language": "界面与渲染语言：",
        "lang_system": "跟随系统 ({detected})",
        "label_theme": "界面主题：",
        "theme_system": "跟随系统",
        "theme_dark": "深色",
        "theme_light": "浅色",
        "set_client_title": "客户端与汉化数据",
        "set_client_summary": "已识别客户端：正式服 {live} 个、PT 测试服 {pt} 个\n当前选中目录：{cur}\n已缓存数据版本：{vers}",
        "not_selected": "未选择",
        "not_synced": "尚未同步",
        "btn_rescan_clients": "重新扫描本机客户端 (含 PT)",
        "btn_sync_i18n": "仅更新汉化词库",
        "btn_sync_full": "重新同步当前客户端数据",
        "set_files_title": "文件与缓存",
        "label_output_dir": "视频输出目录：",
        "btn_change_dir": "更改目录",
        "btn_open_dir": "打开目录",
        "dlg_choose_output_dir": "选择视频输出根目录",
        "chk_auto_open": "每次批量生成完成后自动打开本次时间戳文件夹",
        "label_data_dir": "本地数据目录：",
        "btn_clear_cache": "清除版本缓存",
        # ---- Tab 4: 关于 ----
        "about_desc": "俄服《战舰世界》（Mir Korabley / Lesta）扁平化小地图回放批量渲染工具",
        "about_meta": "应用版本：{app_ver}  ·  已同步俄服版本：{vers}  ·  内置 FFmpeg",
        "btn_visit_github": "访问 GitHub 仓库",
        "btn_open_app_dir": "打开程序目录",
        "about_credit": "视觉与渲染管线参考 WoWs-Builder-Team/minimap_renderer · 汉化词库自动读取客户端本地化 mod",
        # ---- 弹窗 ----
        "msg_no_replays_title": "未发现回放文件",
        "msg_no_replays_body": "仅支持 .korablireplay 后缀的俄服回放文件。",
        "msg_no_client_title": "未选择游戏目录",
        "msg_no_client_body": "请先选择俄服游戏目录，以便自动读取地图与本地化数据。",
        "msg_no_cache_title": "无缓存可清理",
        "msg_no_cache_body": "当前没有已同步的版本数据缓存。",
        "msg_confirm_clear_title": "确认清除缓存",
        "msg_confirm_clear_body": "将删除本地同步缓存目录：\n{path}\n\n下次使用时会自动从游戏目录重新同步（不会影响游戏本体与已生成视频）。是否继续？",
        "msg_partial_fail_title": "部分回放渲染失败（{n} 场）",
        "msg_partial_fail_footer": "\n\n完整排查记录可在「高级」选项卡底部的运行日志中查看。",
    },
    "zh_TW": {
        # ---- 渲染器 HUD ----
        "hud_allies": "我方艦隊",
        "hud_enemies": "敵方艦隊",
        "hud_total_hp": "總血量 {hp}",
        "hud_legend_title": "圖例與血條色標",
        "hud_legend_unspotted": "滅點",
        "hud_legend_smoke": "煙霧區",
        "hud_legend_spotted": "被點亮",
        "hud_self_title": "主角戰報",
        "hud_self_title_ship": "主角戰報 · {ship}",
        "hud_hp_pct": "生命值 ({pct}%)",
        "hud_sunk": "已沉沒",
        "hud_dmg_caused": "造成傷害",
        "hud_dmg_potential": "潛在傷害",
        "hud_dmg_spotting": "偵察傷害",
        "hud_kills": "擊沉敵艦",
        "hud_chat_title": "局內聊天（{n}）",
        "hud_chat_sub": "即時同步俄服全部 / 隊友 / 小隊頻道",
        "hud_chat_empty": "暫無局內聊天紀錄",
        "hud_killfeed_title": "擊殺播報（累計 {n}）",
        "hud_killfeed_empty": "暫無擊沉紀錄",
        "hud_unknown_ship": "未知",
        "hud_final_score": "最終比分  {ally} : {enemy}",
        "result_victory": "勝利",
        "result_defeat": "失敗",
        "result_draw": "平手",
        "result_ended": "戰鬥結束",
        # ---- GUI 标签页 ----
        "tab_replays": "重播",
        "tab_advanced": "進階",
        "tab_settings": "設定",
        "tab_about": "關於",
        # ---- GUI 通用 / 顶部 ----
        "channel_live": "正式服",
        "channel_pt": "PT測試服",
        "channel_custom": "自訂",
        "source_game": "遊戲目錄",
        "source_drop": "手動拖入",
        "tag_drop": "拖入",
        "status_ready": "● v{ver} 已就緒",
        "status_syncing": "◌ 正在同步資料...",
        "status_wait_sync": "◌ 準備同步 v{ver}",
        "status_sync_fail": "● 同步失敗",
        "no_client_found": "未偵測到俄服客戶端，請點擊右側「選擇目錄」",
        "label_game_dir": "遊戲目錄",
        "btn_choose_dir": "選擇目錄",
        "dlg_choose_game_dir": "選擇俄服《戰艦世界》正式服或 PT 測試服遊戲目錄",
        # ---- Tab 1 ----
        "search_placeholder": "搜尋戰艦、地圖或日期...",
        "btn_select_all": "全選",
        "btn_unselect_all": "取消全選",
        "btn_import_replays": "匯入重播",
        "dlg_import_replays": "匯入俄服重播檔案",
        "dlg_replay_filter": "俄服戰艦世界重播 (*.korablireplay)",
        "col_ship": "駕駛戰艦",
        "col_map": "地圖",
        "col_time": "戰鬥時間",
        "footer_replays": "共 {n} 場重播 · 可直接將 .korablireplay 檔案拖入視窗",
        "btn_refresh_list": "重新整理",
        "card_export": "匯出設定",
        "label_resolution": "解析度",
        "res_1k": "1920 × 1080  (1K)",
        "res_720p": "1280 × 720   (720p)",
        "res_2k": "2560 × 1440  (2K)",
        "res_4k": "3840 × 2160  (4K)",
        "res_custom": "自訂解析度",
        "label_fps": "影格率 (FPS)",
        "sel_hint_empty": "請勾選左側重播，或拖入檔案",
        "sel_hint_count": "已選擇 {n} 場重播",
        "btn_start_render": "開始產生",
        "btn_start_render_n": "開始產生 ({n} 場)",
        "btn_stop_render": "停止產生",
        "btn_open_output": "開啟輸出資料夾",
        "tip_auto_folder": "產生後依時間建立資料夾並自動開啟",
        "prog_preparing": "準備產生 {n} 場重播...",
        "prog_parsing": "正在解析重播...",
        "prog_rendering": "渲染中 {pct}% ({done}/{total} 影格)",
        "prog_stopping": "正在安全停止...",
        "prog_stopped": "已停止（已完成 {ok} 場）",
        "prog_done": "產生完成：成功 {ok} 場",
        "prog_done_fail": "產生完成：成功 {ok} 場，失敗 {fail} 場",
        "prog_first_sync": "正在首次同步客戶端資源...",
        "prog_first_sync_sub": "僅需幾秒，後續無需重複同步",
        "prog_ver_sync": "正在同步 v{ver} 客戶端資源...",
        "prog_wait": "請稍候",
        "tooltip_item": "{ship} · {map}\n時間：{time}\n檔案：{file}",
        # ---- Tab 2 ----
        "adv_layers_title": "小地圖與畫面圖層",
        "btn_reset_defaults": "恢復預設",
        "chk_smoke": "顯示地圖煙霧",
        "chk_unspotted": "顯示滅點船隻名稱",
        "chk_spotted": "顯示在點船隻名稱",
        "chk_mini_hp": "顯示小地圖迷你血條",
        "chk_chat": "顯示底部局內聊天區域",
        "chk_shells": "顯示主砲砲彈軌跡",
        "chk_torps": "顯示魚雷軌跡",
        "chk_planes": "顯示航空中隊",
        "chk_rings": "顯示雷達 / 水聽探測圈",
        "chk_dead": "顯示沉沒殘骸圖示",
        "chk_grid": "顯示 10×10 戰術網格",
        "adv_extra_title": "匯出附加選項",
        "chk_keyframes": "同時匯出代表性關鍵影格截圖（開局 / 煙霧 / 滅點 / 交戰 / 終局）",
        "label_banner_sec": "終局結算停留：",
        "suffix_sec": " 秒",
        "adv_log_title": "執行與排查紀錄",
        "btn_clear_log": "清空紀錄",
        "log_placeholder": "同步與渲染詳細紀錄將顯示於此處...",
        # ---- Tab 3 ----
        "set_appearance_title": "外觀與語言",
        "label_language": "介面與渲染語言：",
        "lang_system": "跟隨系統 ({detected})",
        "label_theme": "介面主題：",
        "theme_system": "跟隨系統",
        "theme_dark": "深色",
        "theme_light": "淺色",
        "set_client_title": "客戶端與中文化資料",
        "set_client_summary": "已識別客戶端：正式服 {live} 個、PT 測試服 {pt} 個\n目前選中目錄：{cur}\n已快取資料版本：{vers}",
        "not_selected": "未選擇",
        "not_synced": "尚未同步",
        "btn_rescan_clients": "重新掃描本機客戶端 (含 PT)",
        "btn_sync_i18n": "僅更新中文化詞庫",
        "btn_sync_full": "重新同步目前客戶端資料",
        "set_files_title": "檔案與快取",
        "label_output_dir": "影片輸出目錄：",
        "btn_change_dir": "變更目錄",
        "btn_open_dir": "開啟目錄",
        "dlg_choose_output_dir": "選擇影片輸出根目錄",
        "chk_auto_open": "每次批次產生完成後自動開啟本次時間戳資料夾",
        "label_data_dir": "本機資料目錄：",
        "btn_clear_cache": "清除版本快取",
        # ---- Tab 4 ----
        "about_desc": "俄服《戰艦世界》（Mir Korabley / Lesta）扁平化小地圖重播批次渲染工具",
        "about_meta": "應用版本：{app_ver}  ·  已同步俄服版本：{vers}  ·  內建 FFmpeg",
        "btn_visit_github": "前往 GitHub 儲存庫",
        "btn_open_app_dir": "開啟程式目錄",
        "about_credit": "視覺與渲染管線參考 WoWs-Builder-Team/minimap_renderer · 詞庫自動讀取客戶端本地化 mod",
        # ---- 弹窗 ----
        "msg_no_replays_title": "未發現重播檔案",
        "msg_no_replays_body": "僅支援 .korablireplay 副檔名的俄服重播檔案。",
        "msg_no_client_title": "未選擇遊戲目錄",
        "msg_no_client_body": "請先選擇俄服遊戲目錄，以便自動讀取地圖與本地化資料。",
        "msg_no_cache_title": "無快取可清理",
        "msg_no_cache_body": "目前沒有已同步的版本資料快取。",
        "msg_confirm_clear_title": "確認清除快取",
        "msg_confirm_clear_body": "將刪除本機同步快取目錄：\n{path}\n\n下次使用時會自動從遊戲目錄重新同步（不影響遊戲本體與已產生的影片）。是否繼續？",
        "msg_partial_fail_title": "部分重播渲染失敗（{n} 場）",
        "msg_partial_fail_footer": "\n\n完整排查紀錄可在「進階」分頁底部的執行紀錄中查看。",
    },
    "ru": {
        # ---- Renderer HUD ----
        "hud_allies": "Союзники",
        "hud_enemies": "Противники",
        "hud_total_hp": "ХП {hp}",
        "hud_legend_title": "Легенда и шкала ХП",
        "hud_legend_unspotted": "Пропал",
        "hud_legend_smoke": "Дымы",
        "hud_legend_spotted": "Обнаружен",
        "hud_self_title": "Сводка боя",
        "hud_self_title_ship": "Сводка · {ship}",
        "hud_hp_pct": "Боеспособность ({pct}%)",
        "hud_sunk": "Потоплен",
        "hud_dmg_caused": "Урон",
        "hud_dmg_potential": "Потенц. урон",
        "hud_dmg_spotting": "Разведданные",
        "hud_kills": "Потоплено",
        "hud_chat_title": "Боевой чат ({n})",
        "hud_chat_sub": "Все / Команда / Отряд",
        "hud_chat_empty": "Сообщений пока нет",
        "hud_killfeed_title": "Уничтожения ({n})",
        "hud_killfeed_empty": "Уничтожений пока нет",
        "hud_unknown_ship": "Неизв.",
        "hud_final_score": "Итоговый счёт  {ally} : {enemy}",
        "result_victory": "ПОБЕДА",
        "result_defeat": "ПОРАЖЕНИЕ",
        "result_draw": "НИЧЬЯ",
        "result_ended": "БОЙ ЗАВЕРШЁН",
        # ---- GUI Tabs ----
        "tab_replays": "Реплеи",
        "tab_advanced": "Дополнительно",
        "tab_settings": "Настройки",
        "tab_about": "О программе",
        # ---- GUI General ----
        "channel_live": "Основа",
        "channel_pt": "Общий тест (PT)",
        "channel_custom": "Папка",
        "source_game": "Клиент",
        "source_drop": "Добавлен",
        "tag_drop": "Файл",
        "status_ready": "● v{ver} готов",
        "status_syncing": "◌ Синхронизация...",
        "status_wait_sync": "◌ Ожидает v{ver}",
        "status_sync_fail": "● Ошибка синхр.",
        "no_client_found": "Клиент Мир кораблей не найден — нажмите «Выбрать»",
        "label_game_dir": "Клиент",
        "btn_choose_dir": "Выбрать",
        "dlg_choose_game_dir": "Выберите папку клиента Мир кораблей (основной или Общий тест)",
        # ---- Tab 1 ----
        "search_placeholder": "Поиск корабля, карты или даты...",
        "btn_select_all": "Все",
        "btn_unselect_all": "Снять",
        "btn_import_replays": "Добавить",
        "dlg_import_replays": "Выберите реплеи Мир кораблей",
        "dlg_replay_filter": "Реплеи Мир кораблей (*.korablireplay)",
        "col_ship": "Корабль",
        "col_map": "Карта",
        "col_time": "Дата боя",
        "footer_replays": "Реплеев: {n} · Можно перетащить файлы .korablireplay в окно",
        "btn_refresh_list": "Обновить",
        "card_export": "Параметры видео",
        "label_resolution": "Разрешение",
        "res_1k": "1920 × 1080  (1K)",
        "res_720p": "1280 × 720   (720p)",
        "res_2k": "2560 × 1440  (2K)",
        "res_4k": "3840 × 2160  (4K)",
        "res_custom": "Своё разрешение",
        "label_fps": "Частота (FPS)",
        "sel_hint_empty": "Отметьте реплеи или перетащите файлы",
        "sel_hint_count": "Выбрано реплеев: {n}",
        "btn_start_render": "Создать видео",
        "btn_start_render_n": "Создать ({n})",
        "btn_stop_render": "Остановить",
        "btn_open_output": "Открыть папку вывода",
        "tip_auto_folder": "Автопапка по дате и открытие после рендера",
        "prog_preparing": "Подготовка ({n})...",
        "prog_parsing": "Разбор реплея...",
        "prog_rendering": "Рендер {pct}% ({done}/{total})",
        "prog_stopping": "Остановка...",
        "prog_stopped": "Остановлено (готово: {ok})",
        "prog_done": "Готово: успешно {ok}",
        "prog_done_fail": "Готово: успешно {ok}, ошибок {fail}",
        "prog_first_sync": "Первичная синхронизация клиента...",
        "prog_first_sync_sub": "Займёт несколько секунд",
        "prog_ver_sync": "Синхронизация v{ver}...",
        "prog_wait": "Пожалуйста, подождите",
        "tooltip_item": "{ship} · {map}\nВремя: {time}\nФайл: {file}",
        # ---- Tab 2 ----
        "adv_layers_title": "Слои миникарты и интерфейса",
        "btn_reset_defaults": "Сбросить",
        "chk_smoke": "Отображать дымы",
        "chk_unspotted": "Названия пропавших кораблей",
        "chk_spotted": "Названия видимых кораблей",
        "chk_mini_hp": "Полосы ХП на миникарте",
        "chk_chat": "Панель боевого чата",
        "chk_shells": "Трассеры снарядов ГК",
        "chk_torps": "Торпеды",
        "chk_planes": "Эскадрильи авиации",
        "chk_rings": "Радиусы РЛС / ГАП",
        "chk_dead": "Отметки потопленных кораблей",
        "chk_grid": "Сетка 10×10",
        "adv_extra_title": "Дополнительно при экспорте",
        "chk_keyframes": "Сохранять ключевые кадры PNG (старт / дымы / бой / финал)",
        "label_banner_sec": "Баннер итога:",
        "suffix_sec": " сек",
        "adv_log_title": "Журнал событий",
        "btn_clear_log": "Очистить",
        "log_placeholder": "Подробный журнал синхронизации и рендера...",
        # ---- Tab 3 ----
        "set_appearance_title": "Внешний вид и язык",
        "label_language": "Язык интерфейса и видео:",
        "lang_system": "Как в системе ({detected})",
        "label_theme": "Тема оформления:",
        "theme_system": "Как в системе",
        "theme_dark": "Тёмная",
        "theme_light": "Светлая",
        "set_client_title": "Клиент и данные локализации",
        "set_client_summary": "Найдено клиентов: основа {live}, PT {pt}\nТекущая папка: {cur}\nСинхронизированные версии: {vers}",
        "not_selected": "Не выбрано",
        "not_synced": "Нет данных",
        "btn_rescan_clients": "Найти клиенты (вкл. PT)",
        "btn_sync_i18n": "Обновить локализацию",
        "btn_sync_full": "Пересинхронизировать клиент",
        "set_files_title": "Файлы и кэш",
        "label_output_dir": "Папка вывода:",
        "btn_change_dir": "Изменить",
        "btn_open_dir": "Открыть",
        "dlg_choose_output_dir": "Выберите корневую папку для видео",
        "chk_auto_open": "Автоматически открывать папку после завершения рендера",
        "label_data_dir": "Папка кэша:",
        "btn_clear_cache": "Очистить кэш версий",
        # ---- Tab 4 ----
        "about_desc": "Рендерер миникарты реплеев Мир кораблей (Lesta) в плоском стиле",
        "about_meta": "Версия: {app_ver}  ·  Клиент: {vers}  ·  Встроенный FFmpeg",
        "btn_visit_github": "Репозиторий GitHub",
        "btn_open_app_dir": "Папка программы",
        "about_credit": "Вдохновлено WoWs-Builder-Team/minimap_renderer · Адаптировано для Мир кораблей",
        # ---- Dialogs ----
        "msg_no_replays_title": "Реплеи не найдены",
        "msg_no_replays_body": "Поддерживаются только файлы реплеев с расширением .korablireplay.",
        "msg_no_client_title": "Не выбран клиент",
        "msg_no_client_body": "Сначала выберите папку клиента Мир кораблей для загрузки карт и данных.",
        "msg_no_cache_title": "Кэш пуст",
        "msg_no_cache_body": "Локальный кэш версий отсутствует.",
        "msg_confirm_clear_title": "Очистка кэша",
        "msg_confirm_clear_body": "Удалить папку кэша версий:\n{path}\n\nПри следующем запуске данные синхронизируются автоматически. Продолжить?",
        "msg_partial_fail_title": "Ошибки рендера ({n})",
        "msg_partial_fail_footer": "\n\nПодробности смотрите в журнале на вкладке «Дополнительно».",
    },
    "en": {
        # ---- Renderer HUD ----
        "hud_allies": "Allies",
        "hud_enemies": "Enemies",
        "hud_total_hp": "Total HP {hp}",
        "hud_legend_title": "Legend & HP Scale",
        "hud_legend_unspotted": "Last Seen",
        "hud_legend_smoke": "Smoke",
        "hud_legend_spotted": "Spotted",
        "hud_self_title": "Battle Report",
        "hud_self_title_ship": "Report · {ship}",
        "hud_hp_pct": "HP ({pct}%)",
        "hud_sunk": "Sunk",
        "hud_dmg_caused": "Damage",
        "hud_dmg_potential": "Potential",
        "hud_dmg_spotting": "Spotting",
        "hud_kills": "Destroyed",
        "hud_chat_title": "Battle Chat ({n})",
        "hud_chat_sub": "All / Team / Division Channels",
        "hud_chat_empty": "No battle chat messages yet",
        "hud_killfeed_title": "Kill Feed ({n})",
        "hud_killfeed_empty": "No ships destroyed yet",
        "hud_unknown_ship": "Unknown",
        "hud_final_score": "Final Score  {ally} : {enemy}",
        "result_victory": "VICTORY",
        "result_defeat": "DEFEAT",
        "result_draw": "DRAW",
        "result_ended": "BATTLE ENDED",
        # ---- GUI Tabs ----
        "tab_replays": "Replays",
        "tab_advanced": "Advanced",
        "tab_settings": "Settings",
        "tab_about": "About",
        # ---- GUI General ----
        "channel_live": "Live",
        "channel_pt": "Public Test",
        "channel_custom": "Custom",
        "source_game": "Game",
        "source_drop": "Dropped",
        "tag_drop": "Drop",
        "status_ready": "● v{ver} ready",
        "status_syncing": "◌ Syncing data...",
        "status_wait_sync": "◌ Pending v{ver}",
        "status_sync_fail": "● Sync failed",
        "no_client_found": "No Mir Korabley client found — click Browse",
        "label_game_dir": "Client",
        "btn_choose_dir": "Browse",
        "dlg_choose_game_dir": "Select Mir Korabley Live or Public Test folder",
        # ---- Tab 1 ----
        "search_placeholder": "Search ship, map, or date...",
        "btn_select_all": "All",
        "btn_unselect_all": "None",
        "btn_import_replays": "Import",
        "dlg_import_replays": "Import Mir Korabley Replays",
        "dlg_replay_filter": "Mir Korabley Replays (*.korablireplay)",
        "col_ship": "Ship",
        "col_map": "Map",
        "col_time": "Battle Time",
        "footer_replays": "{n} replays · Drop .korablireplay files anywhere in this window",
        "btn_refresh_list": "Refresh",
        "card_export": "Export",
        "label_resolution": "Resolution",
        "res_1k": "1920 × 1080  (1K)",
        "res_720p": "1280 × 720   (720p)",
        "res_2k": "2560 × 1440  (2K)",
        "res_4k": "3840 × 2160  (4K)",
        "res_custom": "Custom",
        "label_fps": "Frame Rate (FPS)",
        "sel_hint_empty": "Select replays or drop files here",
        "sel_hint_count": "{n} replay(s) selected",
        "btn_start_render": "Render",
        "btn_start_render_n": "Render ({n})",
        "btn_stop_render": "Stop",
        "btn_open_output": "Open Output Folder",
        "tip_auto_folder": "Saves into a timestamped folder & opens it",
        "prog_preparing": "Preparing {n} replay(s)...",
        "prog_parsing": "Parsing replay...",
        "prog_rendering": "Rendering {pct}% ({done}/{total})",
        "prog_stopping": "Stopping...",
        "prog_stopped": "Stopped ({ok} completed)",
        "prog_done": "Done: {ok} rendered",
        "prog_done_fail": "Done: {ok} rendered, {fail} failed",
        "prog_first_sync": "Syncing client assets...",
        "prog_first_sync_sub": "Takes a few seconds on first run",
        "prog_ver_sync": "Syncing v{ver} client assets...",
        "prog_wait": "Please wait",
        "tooltip_item": "{ship} · {map}\nTime: {time}\nFile: {file}",
        # ---- Tab 2 ----
        "adv_layers_title": "Minimap & Overlay Layers",
        "btn_reset_defaults": "Reset",
        "chk_smoke": "Smoke screens",
        "chk_unspotted": "Last-known ship names",
        "chk_spotted": "Spotted ship names",
        "chk_mini_hp": "Minimap mini HP bars",
        "chk_chat": "Battle chat panel",
        "chk_shells": "Main battery tracers",
        "chk_torps": "Torpedoes",
        "chk_planes": "Aircraft squadrons",
        "chk_rings": "Radar / Hydro rings",
        "chk_dead": "Sunk ship markers",
        "chk_grid": "10×10 tactical grid",
        "adv_extra_title": "Additional Export Options",
        "chk_keyframes": "Also export representative PNG keyframes",
        "label_banner_sec": "End banner:",
        "suffix_sec": " s",
        "adv_log_title": "Activity & Diagnostic Log",
        "btn_clear_log": "Clear",
        "log_placeholder": "Sync and render diagnostic logs appear here...",
        # ---- Tab 3 ----
        "set_appearance_title": "Appearance & Language",
        "label_language": "UI & Renderer Language:",
        "lang_system": "Follow System ({detected})",
        "label_theme": "Theme:",
        "theme_system": "Follow System",
        "theme_dark": "Dark",
        "theme_light": "Light",
        "set_client_title": "Client & Localization Data",
        "set_client_summary": "Detected clients: {live} Live, {pt} Public Test\nSelected folder: {cur}\nCached versions: {vers}",
        "not_selected": "None",
        "not_synced": "Not synced",
        "btn_rescan_clients": "Rescan Clients (incl. PT)",
        "btn_sync_i18n": "Sync Localization Only",
        "btn_sync_full": "Resync Client Assets",
        "set_files_title": "Files & Cache",
        "label_output_dir": "Output Folder:",
        "btn_change_dir": "Change",
        "btn_open_dir": "Open",
        "dlg_choose_output_dir": "Select Output Root Folder",
        "chk_auto_open": "Automatically open the timestamped folder when finished",
        "label_data_dir": "Cache Folder:",
        "btn_clear_cache": "Clear Version Cache",
        # ---- Tab 4 ----
        "about_desc": "Flat-style Minimap Replay Renderer for Mir Korabley (Lesta / RU)",
        "about_meta": "Version: {app_ver}  ·  Synced Client: {vers}  ·  Bundled FFmpeg",
        "btn_visit_github": "Open GitHub Repository",
        "btn_open_app_dir": "Open App Folder",
        "about_credit": "Inspired by WoWs-Builder-Team/minimap_renderer · Built specifically for Mir Korabley",
        # ---- Dialogs ----
        "msg_no_replays_title": "No Replays Found",
        "msg_no_replays_body": "Only .korablireplay files are supported.",
        "msg_no_client_title": "No Game Folder Selected",
        "msg_no_client_body": "Please select a Mir Korabley game folder first.",
        "msg_no_cache_title": "Cache Empty",
        "msg_no_cache_body": "No cached client versions found.",
        "msg_confirm_clear_title": "Clear Version Cache",
        "msg_confirm_clear_body": "Remove cached version folder:\n{path}\n\nIt will automatically resync on next use. Continue?",
        "msg_partial_fail_title": "Some Replays Failed ({n})",
        "msg_partial_fail_footer": "\n\nSee the log under the Advanced tab for details.",
    },
}


def tr(lang: str, key: str, **kwargs: Any) -> str:
    """按语言代码获取本地化字符串并支持安全格式化占位符。"""
    code = normalize_lang(lang)
    table = STRINGS.get(code) or STRINGS[DEFAULT_LANG]
    template = table.get(key) or STRINGS[DEFAULT_LANG].get(key, key)
    if kwargs:
        try:
            return template.format(**kwargs)
        except Exception:
            return template
    return template
