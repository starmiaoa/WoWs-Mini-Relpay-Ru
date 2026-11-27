"""1080p 黑色半透明玻璃风 + Tactical-Tech 侧边长条血条 + 局内聊天区 扁平化小地图渲染引擎。

画布尺寸: 1920 × 1080（标准 1K Full HD，默认 30fps）
核心视觉特性:
1. 底层柔化呈现每张地图官方 1920×1080 场景大图，外层玻璃面板采用 ~60% 不透明度黑色半透明玻璃质感；
2. 左右团队标题栏整格作为恒定绿色/红色总血量进度条动态变化（保留存活/总人数与总血量数字）；
3. 左右舰队列表完整复刻 BattleFrame Tactical-Tech 样式（5px 侧边色条、官方水平舰种图标、橙色发光点亮灯、
   [军团]玩家名 + 次行等级与中文船名、连续长条 100-70 绿 / 70-30 黄 / 30-0 红血条、移除行内击杀角标），
   并且双方沉没船只自动下沉排序至列表底部、缩进并大幅降低二层底框透明度，一眼区分灭点与阵亡；
4. 居中保留 820×820 高清小地图（整数 82px 网格、原版双行倒计时、细边框比分条、方/菱形占领点、单层融合烟雾、
   官方空心灭点图标 + 无黑框描边防重叠中文船名）与正下方 860×122 宽幅局内聊天区。

高性能管线说明:
- 主画布与小地图工作在 RGB 空间（免除每帧全屏 1920×1080 RGBA 拷贝与 RGBA->RGB 转换）；
- 小地图描边船名、侧边栏船只行卡与面板、占领点、烟雾、主角战报、局内聊天与击杀播报均实施增量脏检查与位图缓存；
- 烟雾外轮廓采用局部包围盒双半径差值（ImageChops.subtract），彻底消除整图 5×5 MaxFilter 排序滤波瓶颈；
- 支持 `clone_for_worker()` 零拷贝共享静态底图与预热位图，实现多核线程安全并行渲染。
"""

from __future__ import annotations

import math
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from ..config import RenderConfig
from ..domain.models import (
    BattleFrame,
    BattleTimeline,
    RELATION_ALLY,
    RELATION_DIVISION,
    RELATION_ENEMY,
    RELATION_SELF,
    ShipMeta,
    ShipState,
)
from ..extractor.store import VersionDataStore
from ..i18n import (
    normalize_lang,
    tr,
    translate_chat_channel,
    translate_result_banner,
)
from .coords import MapProjection
from .fonts import FontManager
from .icons import GameIconFactory


# 标准 1K Full HD 画布几何常量 (1920 × 1080)
CANVAS_W = 1920
CANVAS_H = 1080

# 顶部黑色半透明 HUD 条 (y: 12..72)
TOP_Y = 12
TOP_H = 60

# 三栏对称水平边界：16 + 498 + 16 + 860 + 16 + 498 + 16 = 1920
LEFT_X = 16
LEFT_W = 498
CENTER_X = 530
CENTER_W = 860               # 40px 左侧 A-J + 820px 小地图 = 860px
RIGHT_X = 1406
RIGHT_W = 498

# 中央小地图框架 (y: 82..936, 高 854)
MAP_FRAME_Y = 82
MAP_HDR_TOP = 34
MAP_HDR_LEFT = 40
MINIMAP_SIZE = 820           # 10×10 网格每格严格 82.0px 整数像素
MINIMAP_X = CENTER_X + MAP_HDR_LEFT   # 570
MINIMAP_Y = MAP_FRAME_Y + MAP_HDR_TOP # 116 (116 + 820 = 936)
MAP_FRAME_BOTTOM = MINIMAP_Y + MINIMAP_SIZE  # 936

# 中央底部专属局内聊天区域 (y: 946..1068, 宽 860 × 高 122)
CHAT_Y = 946
CHAT_H = 122

# 左右 Tactical-Tech 舰队面板 (y: 82..678, 高 596)
EARS_Y = 82
EARS_H = 596
EARS_HEADER_H = 36
EARS_ROW_H = 42
EARS_ROW_GAP = 4
EARS_START_Y = EARS_Y + EARS_HEADER_H + 6  # 124 (124 + 12*46 - 4 = 672, 底留 6px -> 678)

# 左下主角战报 (y: 688..936, 高 248，与小地图底边精确对齐) + 左底图例 (y: 946..1068, 高 122，与聊天栏精确对齐)
SELF_CARD_Y = 688
SELF_CARD_H = 248
LEGEND_Y = 946
LEGEND_H = 122

# 右下官方击杀播报 (y: 688..1068, 宽 498 × 高 380)
KILL_CARD_Y = 688
KILL_CARD_H = 380

ROMAN_TIERS = {
    1: "I", 2: "II", 3: "III", 4: "IV", 5: "V",
    6: "VI", 7: "VII", 8: "VIII", 9: "IX", 10: "X", 11: "★",
}

SPECIES_ORDER = {
    "AirCarrier": 0,
    "Battleship": 1,
    "Cruiser": 2,
    "Destroyer": 3,
    "Submarine": 4,
    "Auxiliary": 5,
}


def _fmt_mmss(seconds: int | None) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def _fmt_num(n: int) -> str:
    return f"{int(n):,}"


def _truncate_text(draw: ImageDraw.ImageDraw, text: str, font: Any, max_width: int) -> str:
    if not text or max_width <= 8:
        return ""
    bbox = draw.textbbox((0, 0), text, font=font)
    if (bbox[2] - bbox[0]) <= max_width:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        cand = text[:mid] + ell
        b = draw.textbbox((0, 0), cand, font=font)
        if (b[2] - b[0]) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + ell if lo > 0 else ell


def _cover_resize(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """等比裁切铺满画布（CSS object-fit: cover），不拉伸变形。"""
    src_w, src_h = img.size
    scale = max(target_w / max(1, src_w), target_h / max(1, src_h))
    nw = max(target_w, round(src_w * scale))
    nh = max(target_h, round(src_h * scale))
    resized = img.resize((nw, nh), resample=Image.Resampling.LANCZOS)
    x0 = (nw - target_w) // 2
    y0 = (nh - target_h) // 2
    return resized.crop((x0, y0, x0 + target_w, y0 + target_h))


def _blit_rgba(dst_rgb: Image.Image, src_rgba: Image.Image, x: int, y: int) -> None:
    """将 RGBA 贴图按 alpha 混合粘贴到不透明 RGB 目标图（自动裁剪负坐标越界）。"""
    dw, dh = dst_rgb.size
    sw, sh = src_rgba.size
    if x >= dw or y >= dh or x + sw <= 0 or y + sh <= 0:
        return
    dst_rgb.paste(src_rgba, (x, y), src_rgba)


class MinimapFrameRenderer:
    """1920×1080 黑色半透明玻璃风 + Tactical-Tech 长条血条 + 局内聊天区 高速逐帧渲染器。"""

    def __init__(
        self,
        timeline: BattleTimeline,
        store: VersionDataStore,
        config: RenderConfig | None = None,
        *,
        _shared_from: MinimapFrameRenderer | None = None,
    ) -> None:
        self.timeline = timeline
        self.store = store
        self.config = config or RenderConfig()
        self.theme = self.config.theme
        self.lang = normalize_lang(self.config.language)

        # 每个工作线程独占自己的 FreeType FontManager（FT_Face 非线程安全）
        self.fonts = FontManager(
            version_fonts_dir=store.fonts_dir,
            regular_override=self.config.font_path,
            bold_override=self.config.bold_font_path,
        )

        if _shared_from is not None:
            # 零拷贝共享不可变静态底图、投影、名单与预热位图，仅隔离线程局部可变缓存
            self._ship_names_by_vid = _shared_from._ship_names_by_vid
            self.icons = _shared_from.icons.clone()
            self.proj = _shared_from.proj
            self.ally_roster = _shared_from.ally_roster
            self.enemy_roster = _shared_from.enemy_roster
            self.base_minimap = _shared_from.base_minimap
            self.base_canvas = _shared_from.base_canvas
            self._base_minimap_rgb = _shared_from._base_minimap_rgb
            self._base_canvas_rgb = _shared_from._base_canvas_rgb
            self._ally_ear_bg_rgb = _shared_from._ally_ear_bg_rgb
            self._enemy_ear_bg_rgb = _shared_from._enemy_ear_bg_rgb
            self._top_hud_bg_rgb = _shared_from._top_hud_bg_rgb
            self._chat_bg_rgb = _shared_from._chat_bg_rgb
            self._kill_bg_rgb = _shared_from._kill_bg_rgb
            self._self_card_base_rgb = _shared_from._self_card_base_rgb
            self._minimap_label_cache = _shared_from._minimap_label_cache
            self._ear_row_base_cache: dict[tuple[int, bool, bool, bool], Image.Image] = dict(
                _shared_from._ear_row_base_cache
            )
            self._ear_hdr_cache: dict[tuple[bool, int, int], Image.Image] = {}
        else:
            # 预缓存本局所有舰船在当前渲染语言下的名称（O(1) 查询，零逐帧开销）
            self._ship_names_by_vid = {
                vid: store.get_ship_name(m.ship_params_id, lang=self.lang, fallback=m.zh_name)
                for vid, m in timeline.ships_meta.items()
            }
            self.icons = GameIconFactory(icons_dir=store.icons_dir)

            map_meta = store.get_map_meta(timeline.map_space)
            self.proj = MapProjection.from_map_meta(map_meta, minimap_size=MINIMAP_SIZE)

            self.ally_roster = sorted(
                [m for m in timeline.ships_meta.values() if m.team_id == timeline.ally_team_id],
                key=lambda m: (SPECIES_ORDER.get(m.species, 9), -m.level, m.zh_name),
            )
            self.enemy_roster = sorted(
                [m for m in timeline.ships_meta.values() if m.team_id == timeline.enemy_team_id],
                key=lambda m: (SPECIES_ORDER.get(m.species, 9), -m.level, m.zh_name),
            )

            self._base_minimap_rgb = self._build_base_minimap().convert("RGB")
            self._base_canvas_rgb = self._build_base_canvas().convert("RGB")
            self.base_minimap = self._base_minimap_rgb
            self.base_canvas = self._base_canvas_rgb

            # 预裁剪各独立面板的不透明底图（RGB，避免每帧整画布合成与转换）
            self._ally_ear_bg_rgb = self._base_canvas_rgb.crop((LEFT_X, EARS_Y, LEFT_X + LEFT_W, EARS_Y + EARS_H))
            self._enemy_ear_bg_rgb = self._base_canvas_rgb.crop((RIGHT_X, EARS_Y, RIGHT_X + RIGHT_W, EARS_Y + EARS_H))
            self._top_hud_bg_rgb = self._base_canvas_rgb.crop((CENTER_X, TOP_Y, CENTER_X + CENTER_W, TOP_Y + TOP_H))
            self._chat_bg_rgb = self._base_canvas_rgb.crop((CENTER_X, CHAT_Y, CENTER_X + CENTER_W, CHAT_Y + CHAT_H))
            self._kill_bg_rgb = self._base_canvas_rgb.crop((RIGHT_X, KILL_CARD_Y, RIGHT_X + RIGHT_W, KILL_CARD_Y + KILL_CARD_H))
            self._self_card_base_rgb = self._build_self_stats_base()

            # 预热本局全部 24 艘舰船的小地图描边船名位图（只读，多线程零锁共享）
            self._minimap_label_cache: dict[tuple[int, bool], tuple[Image.Image, int, int, int, int]] = {}
            self._prewarm_minimap_labels()

            # 预热本局舰船侧边栏静态行底图与基础 SVG 图标
            self._ear_row_base_cache = {}
            self._ear_hdr_cache = {}
            self._prewarm_ear_rows_and_icons()

        # 线程局部持久主画布缓冲（视频流模式就地增量覆写，免除每帧 6.2MB 整画布拷贝）
        self._canvas_buf: Image.Image = self._base_canvas_rgb.copy()
        self._canvas_has_ally_ear_key: Any = None
        self._canvas_has_enemy_ear_key: Any = None
        self._canvas_has_self_key: Any = None
        self._canvas_has_chat_key: Any = None
        self._canvas_has_kill_key: Any = None

        # 线程局部帧间增量缓存（顺序/分块渲染时命中率 > 90%）
        self._zones_cache_key: Any = None
        self._zones_cache_img: Image.Image | None = None
        self._smoke_cache_key: Any = None
        self._smoke_cache_val: tuple[int, int, Image.Image] | None = None
        self._ally_ear_panel: Image.Image = self._ally_ear_bg_rgb.copy()
        self._ally_ear_hdr_key: Any = None
        self._ally_ear_row_sigs: list[Any] = [None] * 12
        self._ally_ear_cache_key: Any = None
        self._enemy_ear_panel: Image.Image = self._enemy_ear_bg_rgb.copy()
        self._enemy_ear_hdr_key: Any = None
        self._enemy_ear_row_sigs: list[Any] = [None] * 12
        self._enemy_ear_cache_key: Any = None
        self._self_cache_key: Any = None
        self._self_cache_img: Image.Image | None = None
        self._chat_cache_key: Any = None
        self._chat_cache_img: Image.Image | None = None
        self._kill_cache_key: Any = None
        self._kill_cache_img: Image.Image | None = None

    def clone_for_worker(self) -> MinimapFrameRenderer:
        """克隆供并发工作线程使用的轻量渲染器实例（共享只读静态贴图与预热位图）。"""
        return MinimapFrameRenderer(
            timeline=self.timeline,
            store=self.store,
            config=self.config,
            _shared_from=self,
        )

    def _t(self, key: str, **kwargs: Any) -> str:
        return tr(self.lang, key, **kwargs)

    def _ship_name(self, meta: ShipMeta | None) -> str:
        if meta is None:
            return self._t("hud_unknown_ship")
        return self._ship_names_by_vid.get(meta.vehicle_id, meta.zh_name)

    def _relation_color(self, relation: int) -> tuple[int, int, int, int]:
        if relation == RELATION_SELF:
            return self.theme.self_color
        if relation == RELATION_DIVISION:
            return self.theme.division_color
        if relation == RELATION_ALLY:
            return self.theme.ally_color
        return self.theme.enemy_color

    def _team_color(self, team_id: int) -> tuple[int, int, int, int]:
        if team_id == self.timeline.ally_team_id:
            return self.theme.ally_color
        if team_id == self.timeline.enemy_team_id:
            return self.theme.enemy_color
        return self.theme.neutral_color

    def hp_threshold_color(self, ratio: float) -> tuple[int, int, int, int]:
        """用户指定单船血条颜色规范：100%-70% 绿，70%-30% 黄，30%-0% 红。"""
        if ratio >= 0.70:
            return self.theme.hp_green
        if ratio >= 0.30:
            return self.theme.hp_yellow
        return self.theme.hp_red

    def sort_roster_for_frame(self, roster: list[ShipMeta], frame: BattleFrame) -> list[ShipMeta]:
        """存活舰船保持原舰种/等级排序置顶，沉没舰船自动下移至列表底部。"""
        # roster 已按 (SPECIES_ORDER, -level, zh_name) 预排好，仅需线性分区即可保序
        alive: list[ShipMeta] = []
        dead: list[ShipMeta] = []
        ships = frame.ships
        for m in roster:
            st = ships.get(m.vehicle_id)
            if st is None or st.is_alive:
                alive.append(m)
            else:
                dead.append(m)
        if not dead:
            return alive
        alive.extend(dead)
        return alive

    def _build_base_minimap(self) -> Image.Image:
        mdir = self.store.get_map_dir(self.timeline.map_space)
        water_path = mdir / "minimap_water.png" if mdir else None
        land_path = mdir / "minimap.png" if mdir else None

        if water_path and water_path.is_file():
            water = Image.open(water_path).convert("RGBA").resize((MINIMAP_SIZE, MINIMAP_SIZE), Image.Resampling.LANCZOS)
        else:
            water = Image.new("RGBA", (MINIMAP_SIZE, MINIMAP_SIZE), (14, 24, 32, 255))

        veil = Image.new("RGBA", (MINIMAP_SIZE, MINIMAP_SIZE), self.theme.minimap_veil)
        base = Image.alpha_composite(water, veil)

        if self.config.show_grid:
            grid_layer = Image.new("RGBA", (MINIMAP_SIZE, MINIMAP_SIZE), (0, 0, 0, 0))
            gd = ImageDraw.Draw(grid_layer)
            step = MINIMAP_SIZE // 10  # 82px 精确整数步进
            for i in range(1, 10):
                pos = i * step
                gd.line([(pos, 0), (pos, MINIMAP_SIZE - 1)], fill=self.theme.grid_line, width=1)
                gd.line([(0, pos), (MINIMAP_SIZE - 1, pos)], fill=self.theme.grid_line, width=1)
            gd.rectangle([0, 0, MINIMAP_SIZE - 1, MINIMAP_SIZE - 1], outline=self.theme.grid_border, width=1)
            base = Image.alpha_composite(base, grid_layer)

        if land_path and land_path.is_file():
            land = Image.open(land_path).convert("RGBA").resize((MINIMAP_SIZE, MINIMAP_SIZE), Image.Resampling.LANCZOS)
            base = Image.alpha_composite(base, land)

        return base

    def _draw_glass_box(
        self,
        layer: Image.Image,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        fill: tuple[int, int, int, int] | None = None,
        border: tuple[int, int, int, int] | None = None,
    ) -> None:
        """绘制锐利直角 1px 中性细框的 60% 黑色半透明玻璃面板。"""
        d = ImageDraw.Draw(layer)
        d.rectangle(
            [x0, y0, x1, y1],
            fill=fill or self.theme.glass_panel,
            outline=border or self.theme.glass_border,
            width=1,
        )

    def _build_base_canvas(self) -> Image.Image:
        """构建底层场景大图 + 60% 黑色半透明玻璃面板骨架。"""
        mdir = self.store.get_map_dir(self.timeline.map_space)
        bg_path = mdir / "bg.jpg" if mdir else None
        water_path = mdir / "minimap_water.png" if mdir else None

        if bg_path and bg_path.is_file():
            raw_bg = Image.open(bg_path).convert("RGBA")
            scenic = _cover_resize(raw_bg, CANVAS_W, CANVAS_H).filter(ImageFilter.GaussianBlur(radius=3.5))
        elif water_path and water_path.is_file():
            scenic = Image.open(water_path).convert("RGBA").resize((CANVAS_W, CANVAS_H), Image.Resampling.BILINEAR)
        else:
            scenic = Image.new("RGBA", (CANVAS_W, CANVAS_H), self.theme.canvas_fallback_bg)

        veil = Image.new("RGBA", (CANVAS_W, CANVAS_H), self.theme.backdrop_veil)
        canvas = Image.alpha_composite(scenic, veil)

        # 叠加所有 60% 不透明度黑色半透明玻璃面板
        glass = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
        # 顶部三段 HUD 框
        self._draw_glass_box(glass, LEFT_X, TOP_Y, LEFT_X + LEFT_W, TOP_Y + TOP_H)
        self._draw_glass_box(glass, CENTER_X, TOP_Y, CENTER_X + CENTER_W, TOP_Y + TOP_H)
        self._draw_glass_box(glass, RIGHT_X, TOP_Y, RIGHT_X + RIGHT_W, TOP_Y + TOP_H)
        # 左侧：我方舰队外框 + 主角战报 + 图例
        self._draw_glass_box(glass, LEFT_X, EARS_Y, LEFT_X + LEFT_W, EARS_Y + EARS_H)
        self._draw_glass_box(glass, LEFT_X, SELF_CARD_Y, LEFT_X + LEFT_W, SELF_CARD_Y + SELF_CARD_H)
        self._draw_glass_box(glass, LEFT_X, LEGEND_Y, LEFT_X + LEFT_W, LEGEND_Y + LEGEND_H)
        # 中央：小地图外框 + 局内聊天区
        self._draw_glass_box(glass, CENTER_X, MAP_FRAME_Y, CENTER_X + CENTER_W, MAP_FRAME_BOTTOM)
        self._draw_glass_box(glass, CENTER_X, CHAT_Y, CENTER_X + CENTER_W, CHAT_Y + CHAT_H)
        # 右侧：敌方舰队外框 + 击杀播报
        self._draw_glass_box(glass, RIGHT_X, EARS_Y, RIGHT_X + RIGHT_W, EARS_Y + EARS_H)
        self._draw_glass_box(glass, RIGHT_X, KILL_CARD_Y, RIGHT_X + RIGHT_W, KILL_CARD_Y + KILL_CARD_H)

        # 非团队总血条面板的标题栏分隔条（团队标题栏改为整格动态绿/红总血条，在逐帧绘制）
        gd = ImageDraw.Draw(glass)
        for bx, bw, by, bh in (
            (LEFT_X, LEFT_W, SELF_CARD_Y, 32),
            (RIGHT_X, RIGHT_W, KILL_CARD_Y, 32),
            (CENTER_X, CENTER_W, CHAT_Y, 28),
            (LEFT_X, LEFT_W, LEGEND_Y, 28),
        ):
            gd.rectangle([bx + 1, by + 1, bx + bw - 1, by + bh], fill=self.theme.glass_header)
            gd.line([(bx + 1, by + bh), (bx + bw - 1, by + bh)], fill=self.theme.hairline, width=1)

        canvas = Image.alpha_composite(canvas, glass)
        d = ImageDraw.Draw(canvas)

        # 小地图 1..10 与 A..J 坐标刻度
        grid_font = self.fonts.get(16, bold=True)
        step = MINIMAP_SIZE / 10.0
        for i in range(10):
            cx = MINIMAP_X + (i + 0.5) * step
            cy = MAP_FRAME_Y + MAP_HDR_TOP * 0.52
            d.text((cx, cy), str(i + 1), font=grid_font, fill=self.theme.text_primary, anchor="mm")
            lx = CENTER_X + MAP_HDR_LEFT * 0.52
            ly = MINIMAP_Y + (i + 0.5) * step
            d.text((lx, ly), chr(ord("A") + i), font=grid_font, fill=self.theme.text_primary, anchor="mm")

        # 顶部左侧地图名与模式（支持四语 I18N）
        map_title = self.store.get_map_name(self.timeline.map_space, lang=self.lang)
        btype_txt = self.store.get_battle_type_name(self.timeline.battle_type, lang=self.lang)
        scen_txt = self.store.get_scenario_name(self.timeline.scenario, lang=self.lang)
        d.text(
            (LEFT_X + 16, TOP_Y + 10),
            _truncate_text(d, map_title, self.fonts.get(20, bold=True), LEFT_W - 32),
            font=self.fonts.get(20, bold=True),
            fill=self.theme.text_primary,
        )
        mode_txt = f"{btype_txt} · {scen_txt}"
        d.text(
            (LEFT_X + 16, TOP_Y + 37),
            _truncate_text(d, mode_txt, self.fonts.get(13, bold=False), LEFT_W - 32),
            font=self.fonts.get(13, bold=False),
            fill=self.theme.text_secondary,
        )

        # 顶部右侧项目名称、版本与仓库链接
        d.text(
            (RIGHT_X + 16, TOP_Y + 16),
            f"v{self.timeline.short_version} · {self.config.fps}fps",
            font=self.fonts.get(12, bold=False),
            fill=self.theme.text_muted,
        )
        d.text(
            (RIGHT_X + RIGHT_W - 16, TOP_Y + 10),
            "WoWs-Mini-Relpay-Ru",
            font=self.fonts.get(19, bold=True),
            fill=self.theme.text_primary,
            anchor="ra",
        )
        d.text(
            (RIGHT_X + RIGHT_W - 16, TOP_Y + 37),
            "https://github.com/starmiaoa/WoWs-Mini-Relpay-Ru",
            font=self.fonts.get(13, bold=False),
            fill=self.theme.text_secondary,
            anchor="ra",
        )

        # 左下角图例面板静态绘制
        self._draw_legend_panel(canvas, d)

        return canvas

    def _build_self_stats_base(self) -> Image.Image:
        """预烘焙左下主角战报卡片的静态标题、标签、官方图标与分割线。"""
        base = self._base_canvas_rgb.crop((LEFT_X, SELF_CARD_Y, LEFT_X + LEFT_W, SELF_CARD_Y + SELF_CARD_H))
        d = ImageDraw.Draw(base, "RGBA")
        x = 16
        inner_w = LEFT_W - 32
        own_meta = self.timeline.ships_meta.get(self.timeline.player_vehicle_id)
        title = (
            self._t("hud_self_title_ship", ship=self._ship_name(own_meta))
            if own_meta
            else self._t("hud_self_title")
        )
        d.text(
            (x, 16),
            _truncate_text(d, title, self.fonts.get(15, bold=True), inner_w),
            font=self.fonts.get(15, bold=True),
            fill=self.theme.text_primary,
            anchor="lm",
        )

        static_rows = [
            (self._t("hud_dmg_caused"), "caused"),
            (self._t("hud_dmg_potential"), "blocked"),
            (self._t("hud_dmg_spotting"), "assisted"),
            (self._t("hud_kills"), "frag"),
        ]
        cy = 108
        for idx, (label, icon_kind) in enumerate(static_rows):
            lbl_font = self.fonts.get(17 if idx == 0 else 14, bold=(idx == 0))
            d.text(
                (x, cy),
                label,
                font=lbl_font,
                fill=self.theme.text_primary if idx == 0 else self.theme.text_secondary,
                anchor="lm",
            )
            lb = d.textbbox((x, cy), label, font=lbl_font, anchor="lm")
            ic = (
                self.icons.get_frag_icon(0, height=17)
                if icon_kind == "frag"
                else self.icons.get_damage_counter_icon(icon_kind, size=20 if idx == 0 else 17)
            )
            if ic is not None:
                _blit_rgba(base, ic, lb[2] + 10, round(cy - ic.height / 2))
            if idx < len(static_rows) - 1:
                d.line([(x, cy + 19), (x + inner_w, cy + 19)], fill=self.theme.hairline, width=1)
            cy += 38 if idx == 0 else 34

        return base

    def _prewarm_minimap_labels(self) -> None:
        """预渲染本局所有舰船在点/灭点描边中文船名位图，消除每帧 FreeType 重复光栅化。"""
        dummy = ImageDraw.Draw(Image.new("RGBA", (1, 1), (0, 0, 0, 0)))
        spotted_font = self.fonts.get(12, bold=True)
        unspot_font = self.fonts.get(12, bold=False)

        for vid, meta in self.timeline.ships_meta.items():
            label = self._ship_name(meta)
            for is_spotted in (True, False):
                font = spotted_font if is_spotted else unspot_font
                if is_spotted:
                    txt_col = self._relation_color(meta.relation)
                    stroke_col = self.theme.text_stroke
                else:
                    txt_col = (
                        self.theme.unspotted_enemy_text
                        if meta.relation == RELATION_ENEMY
                        else self.theme.unspotted_ally_text
                    )
                    stroke_col = (4, 6, 9, 225)

                tb = dummy.textbbox((0, 0), label, font=font, stroke_width=2)
                tw = max(1, tb[2] - tb[0])
                th = max(1, tb[3] - tb[1])
                pad = 2
                sprite = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
                sd = ImageDraw.Draw(sprite)
                sd.text(
                    (-tb[0] + pad, -tb[1] + pad),
                    label,
                    font=font,
                    fill=txt_col,
                    stroke_width=2,
                    stroke_fill=stroke_col,
                )
                # 当绘制在 (lx, ly) 时，sprite 左上角偏移量为 (tb[0] - pad, -pad)
                self._minimap_label_cache[(vid, is_spotted)] = (sprite, tw, th, tb[0] - pad, -pad)

    def _prewarm_ear_rows_and_icons(self) -> None:
        """预热常用舰种图标与侧边栏静态基础行贴图，减少并发工作线程冷启动开销。"""
        for is_ally, roster in ((True, self.ally_roster), (False, self.enemy_roster)):
            w = LEFT_W if is_ally else RIGHT_W
            for meta in roster[:12]:
                # 预热常用状态（存活无灯、存活亮灯；敌方再预热灭点无灯）
                self._get_ear_row_base(meta, w, is_ally, True, False, False)
                self._get_ear_row_base(meta, w, is_ally, True, False, True)
                if not is_ally:
                    self._get_ear_row_base(meta, w, is_ally, True, True, False)
                # 预热官方小地图基础舰种图标（仅未旋转 base，避免多线程重复光栅 SVG）
                is_self = meta.relation == RELATION_SELF
                self.icons._get_minimap_ship_base(
                    meta.species,
                    "self" if is_self else "spotted",
                    self._relation_color(meta.relation),
                    24 if is_self else 23,
                )
                unsp_col = (
                    self.theme.unspotted_enemy_icon
                    if meta.relation == RELATION_ENEMY
                    else self.theme.unspotted_ally_icon
                )
                self.icons._get_minimap_ship_base(meta.species, "unspotted", unsp_col, 23)
                self.icons._get_minimap_ship_base(
                    meta.species,
                    "self_dead" if is_self else "dead",
                    self.theme.dead_icon,
                    20,
                )

    def _draw_legend_panel(self, canvas: Image.Image, d: ImageDraw.ImageDraw) -> None:
        x = LEFT_X + 16
        y = LEGEND_Y
        d.text((x, y + 14), self._t("hud_legend_title"), font=self.fonts.get(14, bold=True), fill=self.theme.text_secondary, anchor="lm")

        font = self.fonts.get(14, bold=False)
        lbl_unsp = self._t("hud_legend_unspotted")
        lbl_smoke = self._t("hud_legend_smoke")
        lbl_spot = self._t("hud_legend_spotted")

        # 第一行：灭点 + 烟雾区 + 被点亮
        r1y = y + 54
        cur_x = x
        unsp_ic = self.icons.get_minimap_ship_icon("Cruiser", self.theme.unspotted_enemy_icon, 0.0, state="unspotted", height=18)
        canvas.alpha_composite(unsp_ic, (cur_x, r1y - 9))
        cur_x += unsp_ic.width + 8
        d.text((cur_x, r1y), lbl_unsp, font=font, fill=self.theme.text_primary, anchor="lm")
        tb = d.textbbox((cur_x, r1y), lbl_unsp, font=font, anchor="lm")
        cur_x = tb[2] + 24

        d.ellipse([cur_x, r1y - 7, cur_x + 14, r1y + 7], fill=self.theme.smoke_fill, outline=self.theme.smoke_outline, width=1)
        cur_x += 22
        d.text((cur_x, r1y), lbl_smoke, font=font, fill=self.theme.text_primary, anchor="lm")
        tb = d.textbbox((cur_x, r1y), lbl_smoke, font=font, anchor="lm")
        cur_x = tb[2] + 24

        d.ellipse([cur_x - 3, r1y - 6, cur_x + 9, r1y + 6], fill=self.theme.spot_lamp_glow)
        d.ellipse([cur_x, r1y - 3, cur_x + 6, r1y + 3], fill=self.theme.spot_lamp_on)
        cur_x += 15
        d.text((cur_x, r1y), lbl_spot, font=font, fill=self.theme.text_primary, anchor="lm")

        # 第二行：血条三色阈值清晰标示 (100-70 绿 / 70-30 黄 / 30-0 红)
        r2y = y + 92
        cur_x = x
        for col, lbl in (
            (self.theme.hp_green, "100-70%"),
            (self.theme.hp_yellow, "70-30%"),
            (self.theme.hp_red, "30-0%"),
        ):
            d.rectangle([cur_x, r2y - 5, cur_x + 26, r2y + 5], fill=col)
            cur_x += 34
            d.text((cur_x, r2y), lbl, font=font, fill=self.theme.text_secondary, anchor="lm")
            tb = d.textbbox((cur_x, r2y), lbl, font=font, anchor="lm")
            cur_x = tb[2] + 28

    # =========================================================================
    # 主逐帧渲染入口（全 RGB + 持久画布就地增量更新，零全屏拷贝与转换）
    # =========================================================================

    def _render_into_buf(self, frame: BattleFrame, end_banner_alpha: float = 0.0) -> Image.Image:
        canvas = self._canvas_buf

        # 1. 小地图图层（带占领点增量缓存的 RGB 基底）
        minimap = self._get_minimap_with_zones(frame)
        if self.config.show_smoke and frame.smokes:
            self._composite_smokes(minimap, frame)
        if self.config.show_consumable_rings:
            self._draw_consumable_rings(minimap, frame)
        if self.config.show_dead_markers:
            self._draw_dead_ships(minimap, frame)
        if self.config.show_shells and frame.shells:
            self._draw_shells(minimap, frame)
        if self.config.show_torpedoes and frame.torpedoes:
            self._draw_torpedoes(minimap, frame)
        self._draw_live_ships_and_labels(minimap, frame)
        if self.config.show_planes and (frame.planes or frame.fighter_wards):
            self._draw_planes(minimap, frame)

        if end_banner_alpha > 0.01:
            self._composite_end_banner(minimap, frame, end_banner_alpha)

        canvas.paste(minimap, (MINIMAP_X, MINIMAP_Y))

        # 2. 顶部原版计分与双行倒计时（仅重置 860×60 顶部 HUD 区域）
        canvas.paste(self._top_hud_bg_rgb, (CENTER_X, TOP_Y))
        draw = ImageDraw.Draw(canvas, "RGBA")
        self._draw_top_hud(draw, frame)

        # 3. 左右 Tactical-Tech 舰队面板（逐行槽位脏检查 + 仅脏区重绘）
        self._draw_tactical_ears(canvas, frame, self.ally_roster, x=LEFT_X, w=LEFT_W, is_ally=True)
        self._draw_tactical_ears(canvas, frame, self.enemy_roster, x=RIGHT_X, w=RIGHT_W, is_ally=False)

        # 4. 左下主角战报 + 中下局内聊天 + 右下官方击杀播报（脏检查：无变化时直接跳过贴图）
        self._draw_self_stats_widget(canvas, frame)
        if self.config.show_chat:
            self._draw_chat_panel(canvas, frame)
        self._draw_kill_feed(canvas, frame)

        return canvas

    def render_frame(self, frame: BattleFrame, end_banner_alpha: float = 0.0) -> Image.Image:
        """渲染单帧并返回独立安全的 RGB PIL Image 副本。"""
        return self._render_into_buf(frame, end_banner_alpha=end_banner_alpha).copy()

    def render_frame_rgb_bytes(
        self, frame: BattleFrame, end_banner_alpha: float = 0.0
    ) -> bytes:
        """高速视频流接口：就地增量更新持久画布，零 NumPy 依赖直接输出紧凑 RGB24 原始字节。"""
        buf = self._render_into_buf(frame, end_banner_alpha=end_banner_alpha)
        return buf.tobytes("raw", "RGB")

    # =========================================================================
    # 小地图各图层
    # =========================================================================

    def _get_minimap_with_zones(self, frame: BattleFrame) -> Image.Image:
        if not frame.capture_zones:
            return self._base_minimap_rgb.copy()

        zkey = tuple(
            (
                z.label,
                round(z.x, 1),
                round(z.z, 1),
                round(z.radius_bw, 1),
                z.owner_team,
                z.invader_team,
                round(z.progress, 3),
                z.is_contested,
            )
            for z in frame.capture_zones
        )
        if zkey == self._zones_cache_key and self._zones_cache_img is not None:
            return self._zones_cache_img.copy()

        overlay = Image.new("RGBA", (MINIMAP_SIZE, MINIMAP_SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        label_font = self.fonts.get(18, bold=True)

        for z in frame.capture_zones:
            px, py = self.proj.world_to_pixel(z.x, z.z)
            r = max(16.0, self.proj.radius_bw_to_pixel(z.radius_bw))
            owner_col = self._team_color(z.owner_team)

            fill_alpha = 46 if z.owner_team != -1 else 36
            ring_alpha = 220 if z.owner_team != -1 else 198
            bbox = [px - r, py - r, px + r, py + r]
            d.ellipse(
                bbox,
                fill=(owner_col[0], owner_col[1], owner_col[2], fill_alpha),
                outline=(owner_col[0], owner_col[1], owner_col[2], ring_alpha),
                width=2,
            )

            is_capturing = z.progress > 0.005 and z.invader_team != -1
            if is_capturing:
                inv_col = self._team_color(z.invader_team)
                end_ang = -90.0 + 360.0 * min(1.0, z.progress)
                d.arc(bbox, start=-90.0, end=end_ang, fill=inv_col, width=4)
                dr = 17.0
                diamond = [(px, py - dr), (px + dr, py), (px, py + dr), (px - dr, py)]
                d.polygon(diamond, fill=(10, 12, 16, 178), outline=inv_col, width=2)
                d.text((px, py), z.label, font=label_font, fill=inv_col, anchor="mm")
            else:
                hs = 13.0
                border_col = self.theme.timer_warning if z.is_contested else owner_col
                d.rectangle([px - hs, py - hs, px + hs, py + hs], fill=(10, 12, 16, 150), outline=border_col, width=2)
                d.text((px, py), z.label, font=label_font, fill=border_col, anchor="mm")

        cached = self._base_minimap_rgb.copy()
        cached.paste(overlay, (0, 0), overlay)
        self._zones_cache_key = zkey
        self._zones_cache_img = cached
        return cached.copy()

    def _composite_smokes(self, minimap: Image.Image, frame: BattleFrame) -> None:
        smoke_key = tuple((sm.entity_id, round(sm.radius_bw, 2), sm.points) for sm in frame.smokes)
        if smoke_key == self._smoke_cache_key:
            if self._smoke_cache_val is not None:
                bx0, by0, smoke_rgba = self._smoke_cache_val
                minimap.paste(smoke_rgba, (bx0, by0), smoke_rgba)
            return

        circles: list[tuple[float, float, float]] = []
        min_x = float(MINIMAP_SIZE)
        min_y = float(MINIMAP_SIZE)
        max_x = 0.0
        max_y = 0.0

        for sm in frame.smokes:
            r = max(6.5, self.proj.radius_bw_to_pixel(sm.radius_bw))
            for wx, wz in sm.points:
                px, py = self.proj.world_to_pixel(wx, wz)
                if -r - 3 <= px <= MINIMAP_SIZE + r + 3 and -r - 3 <= py <= MINIMAP_SIZE + r + 3:
                    circles.append((px, py, r))
                    if px - r < min_x:
                        min_x = px - r
                    if py - r < min_y:
                        min_y = py - r
                    if px + r > max_x:
                        max_x = px + r
                    if py + r > max_y:
                        max_y = py + r

        if not circles:
            self._smoke_cache_key = smoke_key
            self._smoke_cache_val = None
            return

        # 仅在紧凑包围盒内绘制双半径圆并用 C 级 ImageChops.subtract 求 2px 融合轮廓（比 MaxFilter(5) 快 ~80 倍）
        pad = 4
        bx0 = max(0, int(math.floor(min_x)) - pad)
        by0 = max(0, int(math.floor(min_y)) - pad)
        bx1 = min(MINIMAP_SIZE, int(math.ceil(max_x)) + pad)
        by1 = min(MINIMAP_SIZE, int(math.ceil(max_y)) + pad)
        bw = bx1 - bx0
        bh = by1 - by0
        if bw <= 0 or bh <= 0:
            self._smoke_cache_key = smoke_key
            self._smoke_cache_val = None
            return

        inner = Image.new("L", (bw, bh), 0)
        outer = Image.new("L", (bw, bh), 0)
        idraw = ImageDraw.Draw(inner)
        odraw = ImageDraw.Draw(outer)
        for px, py, r in circles:
            lx = px - bx0
            ly = py - by0
            idraw.ellipse([lx - r, ly - r, lx + r, ly + r], fill=255)
            ro = r + 2.0
            odraw.ellipse([lx - ro, ly - ro, lx + ro, ly + ro], fill=255)

        rim = ImageChops.subtract(outer, inner)
        smoke_rgba = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        smoke_rgba.paste(self.theme.smoke_fill, (0, 0), inner)
        smoke_rgba.paste(self.theme.smoke_outline, (0, 0), rim)

        self._smoke_cache_key = smoke_key
        self._smoke_cache_val = (bx0, by0, smoke_rgba)
        minimap.paste(smoke_rgba, (bx0, by0), smoke_rgba)

    def _draw_consumable_rings(self, minimap: Image.Image, frame: BattleFrame) -> None:
        rings: list[tuple[float, float, float, tuple[int, int, int, int]]] = []
        for ship in frame.ships.values():
            if not ship.is_alive or not ship.active_consumables:
                continue
            px, py = self.proj.world_to_pixel(ship.x, ship.z)
            for c in ship.active_consumables:
                if c.radius_bw <= 0:
                    continue
                r = self.proj.radius_bw_to_pixel(c.radius_bw)
                col = self.theme.radar_ring if c.consumable_type == "rls" else self.theme.hydro_ring
                rings.append((px, py, r, col))
        if not rings:
            return
        d = ImageDraw.Draw(minimap, "RGBA")
        for px, py, r, col in rings:
            d.ellipse([px - r, py - r, px + r, py + r], outline=col, width=2)

    def _draw_dead_ships(self, minimap: Image.Image, frame: BattleFrame) -> None:
        ships_meta = self.timeline.ships_meta
        for vid, st in frame.ships.items():
            if st.is_alive or not st.has_been_spotted:
                continue
            meta = ships_meta.get(vid)
            if meta is None:
                continue
            px, py = self.proj.world_to_pixel(st.x, st.z)
            dead_state = "self_dead" if meta.relation == RELATION_SELF else "dead"
            icon = self.icons.get_minimap_ship_icon(
                species=meta.species,
                color=self.theme.dead_icon,
                yaw_rad=st.yaw_rad,
                state=dead_state,
                height=20,
            )
            _blit_rgba(minimap, icon, round(px - icon.width / 2), round(py - icon.height / 2))

    def _draw_shells(self, minimap: Image.Image, frame: BattleFrame) -> None:
        d = ImageDraw.Draw(minimap, "RGBA")
        ap_col = self.theme.shell_ap
        cs_col = self.theme.shell_cs
        he_col = self.theme.shell_he
        sec_a = self.theme.shell_secondary_alpha
        for sh in frame.shells:
            x0, y0 = self.proj.world_to_pixel(sh.x0, sh.z0)
            x1, y1 = self.proj.world_to_pixel(sh.x1, sh.z1)
            if sh.ammo_type == "AP":
                base_col = ap_col
            elif sh.ammo_type == "CS":
                base_col = cs_col
            else:
                base_col = he_col
            col = (base_col[0], base_col[1], base_col[2], sec_a if sh.is_secondary else base_col[3])
            d.line([(x0, y0), (x1, y1)], fill=col, width=1 if sh.is_secondary else 2)

    def _draw_torpedoes(self, minimap: Image.Image, frame: BattleFrame) -> None:
        d = ImageDraw.Draw(minimap, "RGBA")
        for t in frame.torpedoes:
            px, py = self.proj.world_to_pixel(t.x, t.z)
            if not (0 <= px <= MINIMAP_SIZE and 0 <= py <= MINIMAP_SIZE):
                continue
            col = self._relation_color(t.relation)
            d.ellipse([px - 2.8, py - 2.8, px + 2.8, py + 2.8], fill=col)

    def _get_minimap_label_sprite(
        self, meta: ShipMeta, is_spotted: bool
    ) -> tuple[Image.Image, int, int, int, int]:
        key = (meta.vehicle_id, is_spotted)
        cached = self._minimap_label_cache.get(key)
        if cached is not None:
            return cached

        font = self.fonts.get(12, bold=is_spotted)
        label = self._ship_name(meta)
        if is_spotted:
            txt_col = self._relation_color(meta.relation)
            stroke_col = self.theme.text_stroke
        else:
            txt_col = (
                self.theme.unspotted_enemy_text
                if meta.relation == RELATION_ENEMY
                else self.theme.unspotted_ally_text
            )
            stroke_col = (4, 6, 9, 225)

        dummy = ImageDraw.Draw(Image.new("RGBA", (1, 1), (0, 0, 0, 0)))
        tb = dummy.textbbox((0, 0), label, font=font, stroke_width=2)
        tw = max(1, tb[2] - tb[0])
        th = max(1, tb[3] - tb[1])
        pad = 2
        sprite = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
        ImageDraw.Draw(sprite).text(
            (-tb[0] + pad, -tb[1] + pad),
            label,
            font=font,
            fill=txt_col,
            stroke_width=2,
            stroke_fill=stroke_col,
        )
        res = (sprite, tw, th, -pad, -pad)
        self._minimap_label_cache[key] = res
        return res

    def _draw_live_ships_and_labels(self, minimap: Image.Image, frame: BattleFrame) -> None:
        unspotted_ships: list[tuple[ShipMeta, ShipState]] = []
        spotted_ships: list[tuple[ShipMeta, ShipState]] = []
        ships_meta = self.timeline.ships_meta

        for vid, st in frame.ships.items():
            if not st.is_alive or not st.has_been_spotted:
                continue
            meta = ships_meta.get(vid)
            if meta is None:
                continue
            if st.is_spotted:
                spotted_ships.append((meta, st))
            else:
                unspotted_ships.append((meta, st))

        spotted_ships.sort(key=lambda pair: (1 if pair[0].relation == RELATION_SELF else 0, pair[0].vehicle_id))

        d = ImageDraw.Draw(minimap, "RGBA")
        label_requests: list[tuple[int, ShipMeta, ShipState, float, float, bool]] = []

        # 1. 灭点舰船（俄服官方空心 *_last_visible.svg）
        for meta, st in unspotted_ships:
            px, py = self.proj.world_to_pixel(st.x, st.z)
            icon_col = (
                self.theme.unspotted_enemy_icon
                if meta.relation == RELATION_ENEMY
                else self.theme.unspotted_ally_icon
            )
            icon = self.icons.get_minimap_ship_icon(
                species=meta.species,
                color=icon_col,
                yaw_rad=st.yaw_rad,
                state="unspotted",
                height=23,
            )
            _blit_rgba(minimap, icon, round(px - icon.width / 2), round(py - icon.height / 2))
            if self.config.show_unspotted_names:
                label_requests.append((2, meta, st, px, py, False))

        # 2. 在点舰船（俄服官方实心 SVG + 100-70-30 连续细血条 + 橙色点亮方点）
        for meta, st in spotted_ships:
            px, py = self.proj.world_to_pixel(st.x, st.z)
            is_self = meta.relation == RELATION_SELF
            icon_col = self._relation_color(meta.relation)
            icon = self.icons.get_minimap_ship_icon(
                species=meta.species,
                color=icon_col,
                yaw_rad=st.yaw_rad,
                state="self" if is_self else "spotted",
                height=24 if is_self else 23,
            )
            _blit_rgba(minimap, icon, round(px - icon.width / 2), round(py - icon.height / 2))

            has_bar = self.config.show_mini_hp_bars and st.max_hp > 0 and (st.in_aoi or is_self)
            if has_bar:
                bar_w = 42
                bar_h = 5
                bx = round(px - bar_w / 2)
                by = round(py + 12)
                ratio = max(0.0, min(1.0, st.hp / st.max_hp))
                bar_col = self.hp_threshold_color(ratio)
                d.rectangle([bx, by, bx + bar_w, by + bar_h], fill=(8, 10, 14, 195), outline=(bar_col[0], bar_col[1], bar_col[2], 155), width=1)
                fill_w = max(1, round((bar_w - 1) * ratio)) if ratio > 0 else 0
                if fill_w > 0:
                    d.rectangle([bx + 1, by + 1, bx + fill_w, by + bar_h - 1], fill=bar_col)
                if meta.relation != RELATION_ENEMY and st.is_detected_by_enemy:
                    d.rectangle([bx - 8, by, bx - 3, by + 5], fill=self.theme.spot_lamp_on)
            elif meta.relation != RELATION_ENEMY and st.is_detected_by_enemy:
                d.rectangle([px - 16, py + 10, px - 11, py + 15], fill=self.theme.spot_lamp_on)

            if self.config.show_spotted_names:
                prio = 0 if is_self else (1 if meta.relation == RELATION_ENEMY else 3)
                label_requests.append((prio, meta, st, px, py, True))

        # 3. 预缓存无黑框细描边中文船名：始终固定居中锚定在船只图标（及迷你血条）正下方，
        #    不做上下翻转避让，杜绝多船靠近时船名突然漂移到船只图标上方或跨帧闪烁；
        #    按优先级从低到高绘制（友军 -> 灭点 -> 在点敌军 -> 主角置顶）。
        label_requests.sort(key=lambda item: (-item[0], item[4], item[3]))
        for _prio, meta, st, px, py, is_spotted in label_requests:
            sprite, tw, th, dx, dy = self._get_minimap_label_sprite(meta, is_spotted)

            has_bar = is_spotted and self.config.show_mini_hp_bars and st.max_hp > 0 and (st.in_aoi or meta.relation == RELATION_SELF)
            base_down = 19 if has_bar else 14
            cx0 = max(2.0, min(MINIMAP_SIZE - tw - 2.0, px - tw * 0.5))
            cy0 = max(2.0, min(MINIMAP_SIZE - th - 2.0, py + base_down))

            lx, ly = round(cx0), round(cy0)
            _blit_rgba(minimap, sprite, lx + dx, ly + dy)

    def _draw_planes(self, minimap: Image.Image, frame: BattleFrame) -> None:
        # 1. 巡逻战斗机圆形防空拦截圈（对应官方 ward_fighters_marker.unbound，淡透明阵营色圆）
        if frame.fighter_wards:
            d = ImageDraw.Draw(minimap, "RGBA")
            for w in frame.fighter_wards:
                px, py = self.proj.world_to_pixel(w.x, w.z)
                r = max(8.0, self.proj.radius_bw_to_pixel(w.radius_bw))
                if not (-r <= px <= MINIMAP_SIZE + r and -r <= py <= MINIMAP_SIZE + r):
                    continue
                col = self._relation_color(w.relation)
                d.ellipse(
                    [px - r, py - r, px + r, py + r],
                    fill=(col[0], col[1], col[2], 24),
                    outline=(col[0], col[1], col[2], 120),
                    width=1,
                )

        # 2. 官方原版飞机/战斗机/水侦/空袭图标（消耗品机先画，航母攻击编队置顶，0° 垂直清晰显示）
        ordered = sorted(frame.planes, key=lambda p: (0 if p.is_consumable else 1, p.plane_id))
        for p in ordered:
            px, py = self.proj.world_to_pixel(p.x, p.z)
            if not (-20 <= px <= MINIMAP_SIZE + 20 and -20 <= py <= MINIMAP_SIZE + 20):
                continue
            col = self._relation_color(p.relation)
            icon = self.icons.get_plane_icon(
                color=col,
                yaw_rad=p.yaw_rad,
                returning=(p.purpose == 1),
                size=20,
                icon_name=p.icon_name,
                rotate=False,
            )
            _blit_rgba(minimap, icon, round(px - icon.width / 2), round(py - icon.height / 2))

    def _composite_end_banner(
        self, minimap: Image.Image, frame: BattleFrame, alpha_scale: float
    ) -> None:
        overlay = Image.new("RGBA", (MINIMAP_SIZE, MINIMAP_SIZE), (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        a = max(0.0, min(1.0, alpha_scale))

        d.rectangle([0, 0, MINIMAP_SIZE, MINIMAP_SIZE], fill=(6, 8, 12, int(155 * a)))
        if self.timeline.result_zh == "胜利":
            accent = self.theme.ally_color
        elif self.timeline.result_zh == "失败":
            accent = self.theme.enemy_color
        else:
            accent = self.theme.neutral_color

        cy = MINIMAP_SIZE // 2
        d.rectangle(
            [160, cy - 72, MINIMAP_SIZE - 160, cy + 70],
            fill=(10, 12, 16, int(210 * a)),
            outline=(accent[0], accent[1], accent[2], int(215 * a)),
            width=1,
        )
        d.line([(160, cy - 72), (MINIMAP_SIZE - 160, cy - 72)], fill=(accent[0], accent[1], accent[2], int(255 * a)), width=3)

        d.text(
            (MINIMAP_SIZE // 2, cy - 16),
            translate_result_banner(self.timeline.result_zh, self.lang),
            font=self.fonts.get(50 if self.lang in ("ru", "en") else 54, bold=True),
            fill=(255, 255, 255, int(255 * a)),
            anchor="mm",
        )
        d.text(
            (MINIMAP_SIZE // 2, cy + 36),
            self._t("hud_final_score", ally=frame.ally_score, enemy=frame.enemy_score),
            font=self.fonts.get(22, bold=True),
            fill=(accent[0], accent[1], accent[2], int(245 * a)),
            anchor="mm",
        )
        minimap.paste(overlay, (0, 0), overlay)

    # =========================================================================
    # 顶部 HUD（原版双行倒计时 + 细边框双向比分条 + 双行 TTW）
    # =========================================================================

    def _draw_top_hud(self, d: ImageDraw.ImageDraw, frame: BattleFrame) -> None:
        mm, ss = divmod(max(0, int(frame.time_left)), 60)
        t_col = self.theme.timer_warning if frame.time_left <= 120 else self.theme.text_primary
        t_font = self.fonts.get(21, bold=True)
        timer_cx = CENTER_X + 28
        d.text((timer_cx, TOP_Y + 18), f"{mm:02d}", font=t_font, fill=t_col, anchor="mm")
        d.text((timer_cx, TOP_Y + 42), f"{ss:02d}", font=t_font, fill=t_col, anchor="mm")

        bar_y0, bar_y1 = TOP_Y + 10, TOP_Y + 50
        mid_x = CENTER_X + CENTER_W // 2 - 4
        score_half_gap = 82
        bar_len = 280
        win_sc = max(1, frame.win_score)

        # 我方比分框
        a_x1 = mid_x - score_half_gap
        a_x0 = a_x1 - bar_len
        d.rectangle([a_x0, bar_y0, a_x1, bar_y1], fill=(6, 8, 12, 200), outline=(94, 218, 187, 138), width=1)
        a_fill = round((bar_len - 2) * min(1.0, max(0.0, frame.ally_score / win_sc)))
        if a_fill > 0:
            d.rectangle([a_x1 - 1 - a_fill, bar_y0 + 1, a_x1 - 1, bar_y1 - 1], fill=self.theme.ally_color)

        # 敌方比分框
        e_x0 = mid_x + score_half_gap
        e_x1 = e_x0 + bar_len
        d.rectangle([e_x0, bar_y0, e_x1, bar_y1], fill=(6, 8, 12, 200), outline=(242, 87, 85, 138), width=1)
        e_fill = round((bar_len - 2) * min(1.0, max(0.0, frame.enemy_score / win_sc)))
        if e_fill > 0:
            d.rectangle([e_x0 + 1, bar_y0 + 1, e_x0 + 1 + e_fill, bar_y1 - 1], fill=self.theme.enemy_color)

        # 中央大比分
        d.text(
            (mid_x, (bar_y0 + bar_y1) // 2),
            f"{frame.ally_score} : {frame.enemy_score}",
            font=self.fonts.get(29, bold=True),
            fill=self.theme.text_primary,
            anchor="mm",
        )

        # 右侧双行 TTW
        ttw_font = self.fonts.get(15, bold=True)
        ttw_x = e_x1 + 14
        d.text((ttw_x, TOP_Y + 20), _fmt_mmss(frame.ally_ttw_sec), font=ttw_font, fill=self.theme.ally_color, anchor="lm")
        d.text((ttw_x, TOP_Y + 41), _fmt_mmss(frame.enemy_ttw_sec), font=ttw_font, fill=self.theme.enemy_color, anchor="lm")

    # =========================================================================
    # 左右 Tactical-Tech 舰队面板（单行位图缓存 + 整栏脏检查）
    # =========================================================================

    def _get_ear_header_rgba(
        self,
        w: int,
        is_ally: bool,
        alive_cnt: int,
        total_cnt: int,
        cur_total_hp: int,
        max_total_hp: int,
    ) -> Image.Image:
        key = (is_ally, alive_cnt, cur_total_hp)
        cached = self._ear_hdr_cache.get(key)
        if cached is not None:
            return cached

        hdr_font = self.fonts.get(15, bold=True)
        tot_font = self.fonts.get(15, bold=True)
        hdr = Image.new("RGBA", (w, EARS_HEADER_H + 1), (0, 0, 0, 0))
        rd = ImageDraw.Draw(hdr)

        tot_ratio = max(0.0, min(1.0, cur_total_hp / max(1, max_total_hp)))
        hdr_x0, hdr_y0 = 1, 1
        hdr_x1, hdr_y1 = w - 1, EARS_HEADER_H
        hdr_span = hdr_x1 - hdr_x0
        rd.rectangle([hdr_x0, hdr_y0, hdr_x1, hdr_y1], fill=self.theme.team_hp_track)

        bar_fill = self.theme.team_hp_ally_fill if is_ally else self.theme.team_hp_enemy_fill
        bar_edge_hi = (128, 236, 200, 220) if is_ally else (252, 128, 126, 220)
        fill_w = round(hdr_span * tot_ratio)
        if fill_w > 0:
            if is_ally:
                rd.rectangle([hdr_x0, hdr_y0, hdr_x0 + fill_w, hdr_y1], fill=bar_fill)
                if fill_w < hdr_span:
                    rd.line([(hdr_x0 + fill_w, hdr_y0), (hdr_x0 + fill_w, hdr_y1)], fill=bar_edge_hi, width=2)
            else:
                fx0 = hdr_x1 - fill_w
                rd.rectangle([fx0, hdr_y0, hdr_x1, hdr_y1], fill=bar_fill)
                if fill_w < hdr_span:
                    rd.line([(fx0, hdr_y0), (fx0, hdr_y1)], fill=bar_edge_hi, width=2)

        rd.line([(hdr_x0, hdr_y1), (hdr_x1, hdr_y1)], fill=self.theme.hairline, width=1)

        hdr_cy = (hdr_y0 + hdr_y1) // 2
        txt_stroke = (6, 8, 12, 205)
        hp_txt = self._t("hud_total_hp", hp=_fmt_num(cur_total_hp))
        if is_ally:
            title = f"{self._t('hud_allies')}  {alive_cnt}/{total_cnt}"
            rd.text((14, hdr_cy), title, font=hdr_font, fill=self.theme.text_primary, anchor="lm", stroke_width=1, stroke_fill=txt_stroke)
            rd.text((w - 14, hdr_cy), hp_txt, font=tot_font, fill=self.theme.text_primary, anchor="rm", stroke_width=1, stroke_fill=txt_stroke)
        else:
            title = f"{self._t('hud_enemies')}  {alive_cnt}/{total_cnt}"
            rd.text((14, hdr_cy), hp_txt, font=tot_font, fill=self.theme.text_primary, anchor="lm", stroke_width=1, stroke_fill=txt_stroke)
            rd.text((w - 14, hdr_cy), title, font=hdr_font, fill=self.theme.text_primary, anchor="rm", stroke_width=1, stroke_fill=txt_stroke)

        # 限制缓存体积避免长局无限增长
        if len(self._ear_hdr_cache) > 64:
            self._ear_hdr_cache.clear()
        self._ear_hdr_cache[key] = hdr
        return hdr

    def _get_ear_row_base(
        self,
        meta: ShipMeta,
        w: int,
        is_ally: bool,
        is_alive: bool,
        is_unspotted_enemy: bool,
        lamp_on: bool,
    ) -> Image.Image:
        """缓存单船行底图（含 5px 侧边条、舰种图标、点亮灯、截断玩家名与船名、血条底槽）。"""
        key = (meta.vehicle_id, is_alive, is_unspotted_enemy, lamp_on if is_alive else False)
        cached = self._ear_row_base_cache.get(key)
        if cached is not None:
            return cached

        row_pad_x = 8
        full_row_w = w - row_pad_x * 2      # 482px
        dead_row_w = full_row_w - 136       # 346px
        hp_bar_w = 132
        hp_bar_h = 13
        row_w = full_row_w if is_alive else dead_row_w

        row_rgba = Image.new("RGBA", (row_w + 1, EARS_ROW_H + 1), (0, 0, 0, 0))
        rd = ImageDraw.Draw(row_rgba)

        if not is_alive:
            row_fill = self.theme.glass_row_dead
            row_border = (255, 255, 255, 16)
        elif meta.relation == RELATION_SELF:
            row_fill = self.theme.glass_row_self
            row_border = self.theme.accent_gold
        elif is_unspotted_enemy:
            row_fill = self.theme.glass_row_unspotted
            row_border = (255, 255, 255, 26)
        else:
            row_fill = self.theme.glass_row
            row_border = (255, 255, 255, 30)

        rd.rectangle([0, 0, row_w, EARS_ROW_H], fill=row_fill, outline=row_border, width=1)

        if not is_alive:
            edge_col = (70, 74, 82, 95)
        elif meta.relation == RELATION_SELF:
            edge_col = self.theme.self_color
        elif meta.relation == RELATION_DIVISION:
            edge_col = self.theme.division_color
        elif is_ally:
            edge_col = self.theme.ally_color
        else:
            edge_col = self.theme.enemy_color if not is_unspotted_enemy else (196, 78, 76, 215)

        if is_ally:
            rd.rectangle([0, 0, 5, EARS_ROW_H], fill=edge_col)
        else:
            rd.rectangle([row_w - 5, 0, row_w, EARS_ROW_H], fill=edge_col)

        if not is_alive:
            icon_col = self.theme.dead_icon
            nick_col = self.theme.dead_text
            ship_col = (98, 104, 114, 140)
        elif meta.relation in (RELATION_SELF, RELATION_DIVISION):
            icon_col = self.theme.division_color if meta.relation == RELATION_DIVISION else self.theme.self_color
            nick_col = self.theme.division_color
            ship_col = self.theme.text_ship_sub
        elif is_ally or not is_unspotted_enemy:
            icon_col = (244, 246, 250, 248)
            nick_col = self.theme.text_primary
            ship_col = self.theme.text_ship_sub
        else:
            icon_col = (196, 202, 212, 220)
            nick_col = (210, 216, 224, 238)
            ship_col = (168, 176, 188, 226)

        cls_icon = self.icons.get_hud_class_icon(
            species=meta.species,
            color=icon_col,
            size=26,
            flip_left=(not is_ally),
        )

        nick_font = self.fonts.get(14, bold=True)
        ship_font = self.fonts.get(13, bold=False)
        full_nick = f"[{meta.clan_tag}]{meta.player_name}" if meta.clan_tag else meta.player_name
        tier_txt = ROMAN_TIERS.get(meta.level, str(meta.level))
        ship_disp = self._ship_name(meta)
        ship_sub = f"{tier_txt} {ship_disp}" if is_ally else f"{ship_disp} {tier_txt}"

        if is_ally:
            row_rgba.alpha_composite(cls_icon, (10, (EARS_ROW_H - 26) // 2))
            text_left = 42
            text_right = (row_w - hp_bar_w - 18) if is_alive else (row_w - 12)

            nick_x = text_left
            if is_alive:
                ly_c = 13
                if lamp_on:
                    rd.ellipse([nick_x - 3, ly_c - 6, nick_x + 9, ly_c + 6], fill=self.theme.spot_lamp_glow)
                    rd.ellipse([nick_x, ly_c - 3, nick_x + 6, ly_c + 3], fill=self.theme.spot_lamp_on)
                else:
                    rd.ellipse([nick_x, ly_c - 3, nick_x + 6, ly_c + 3], fill=self.theme.spot_lamp_off)
                nick_x += 13
            rd.text(
                (nick_x, 5),
                _truncate_text(rd, full_nick, nick_font, max(24, text_right - nick_x)),
                font=nick_font,
                fill=nick_col,
            )
            rd.text(
                (text_left, 23),
                _truncate_text(rd, ship_sub, ship_font, max(24, text_right - text_left)),
                font=ship_font,
                fill=ship_col,
            )

            if is_alive:
                bx1 = row_w - 10
                bx0 = bx1 - hp_bar_w
                by0 = (EARS_ROW_H - hp_bar_h) // 2
                by1 = by0 + hp_bar_h
                rd.rectangle([bx0, by0, bx1, by1], fill=self.theme.hp_track, outline=self.theme.hp_border, width=1)
        else:
            row_rgba.alpha_composite(cls_icon, (row_w - 10 - 26, (EARS_ROW_H - 26) // 2))
            text_right = row_w - 42
            text_left = (hp_bar_w + 18) if is_alive else 12

            nick_r = text_right
            if is_alive:
                ly_c = 13
                lx = nick_r - 6
                if lamp_on:
                    rd.ellipse([lx - 3, ly_c - 6, lx + 9, ly_c + 6], fill=self.theme.spot_lamp_glow)
                    rd.ellipse([lx, ly_c - 3, lx + 6, ly_c + 3], fill=self.theme.spot_lamp_on)
                else:
                    rd.ellipse([lx, ly_c - 3, lx + 6, ly_c + 3], fill=self.theme.spot_lamp_off)
                nick_r -= 13
            rd.text(
                (nick_r, 5),
                _truncate_text(rd, full_nick, nick_font, max(24, nick_r - text_left)),
                font=nick_font,
                fill=nick_col,
                anchor="ra",
            )
            rd.text(
                (text_right, 23),
                _truncate_text(rd, ship_sub, ship_font, max(24, text_right - text_left)),
                font=ship_font,
                fill=ship_col,
                anchor="ra",
            )

            if is_alive:
                bx0 = 10
                bx1 = bx0 + hp_bar_w
                by0 = (EARS_ROW_H - hp_bar_h) // 2
                by1 = by0 + hp_bar_h
                rd.rectangle([bx0, by0, bx1, by1], fill=self.theme.hp_track, outline=self.theme.hp_border, width=1)

        self._ear_row_base_cache[key] = row_rgba
        return row_rgba

    def _draw_tactical_ears(
        self,
        canvas: Image.Image,
        frame: BattleFrame,
        roster: list[ShipMeta],
        x: int,
        w: int,
        is_ally: bool,
    ) -> None:
        ships = frame.ships
        alive_cnt = 0
        cur_total_hp = 0
        max_total_hp = 0
        for m in roster:
            max_total_hp += m.max_hp
            st = ships.get(m.vehicle_id)
            if st is not None and st.is_alive:
                alive_cnt += 1
                if st.hp > 0:
                    cur_total_hp += st.hp
        max_total_hp = max(1, max_total_hp)

        ordered_roster = self.sort_roster_for_frame(roster, frame)
        hp_bar_w = 132
        hp_bar_h = 13

        row_specs: list[tuple[ShipMeta, bool, bool, bool, int, tuple[int, int, int, int]]] = []
        sig_rows: list[tuple[int, bool, bool, bool, int, tuple[int, int, int, int]]] = []
        for meta in ordered_roster[:12]:
            st = ships.get(meta.vehicle_id)
            is_alive = st.is_alive if st else True
            is_spotted = bool(st and st.is_spotted)
            is_unspotted_enemy = is_alive and (not is_ally) and (not is_spotted)
            lamp_on = bool(st and (st.is_detected_by_enemy if is_ally else st.is_spotted)) if is_alive else False
            if is_alive:
                hp = st.hp if st else meta.max_hp
                max_hp = (st.max_hp if st and st.max_hp > 0 else meta.max_hp) or 1
                ratio = max(0.0, min(1.0, hp / max_hp))
                fw = max(2, round((hp_bar_w - 2) * ratio)) if ratio > 0 else 0
                hp_col = self.hp_threshold_color(ratio)
            else:
                fw = 0
                hp_col = self.theme.hp_red
            row_specs.append((meta, is_alive, is_unspotted_enemy, lamp_on, fw, hp_col))
            sig_rows.append((meta.vehicle_id, is_alive, is_unspotted_enemy, lamp_on, fw, hp_col))

        panel_sig = (alive_cnt, cur_total_hp, tuple(sig_rows))
        if is_ally:
            if panel_sig == self._canvas_has_ally_ear_key:
                return
            ear_panel = self._ally_ear_panel
            bg_rgb = self._ally_ear_bg_rgb
            prev_hdr_key = self._ally_ear_hdr_key
            prev_row_sigs = self._ally_ear_row_sigs
        else:
            if panel_sig == self._canvas_has_enemy_ear_key:
                return
            ear_panel = self._enemy_ear_panel
            bg_rgb = self._enemy_ear_bg_rgb
            prev_hdr_key = self._enemy_ear_hdr_key
            prev_row_sigs = self._enemy_ear_row_sigs

        # 1. 仅当团队人数或总血量变化时重绘顶栏
        hdr_key = (alive_cnt, cur_total_hp)
        if hdr_key != prev_hdr_key:
            hdr_bg = bg_rgb.crop((0, 0, w, EARS_HEADER_H + 1))
            hdr_rgba = self._get_ear_header_rgba(w, is_ally, alive_cnt, len(roster), cur_total_hp, max_total_hp)
            hdr_bg.paste(hdr_rgba, (0, 0), hdr_rgba)
            ear_panel.paste(hdr_bg, (0, 0))
            if is_ally:
                self._ally_ear_hdr_key = hdr_key
            else:
                self._enemy_ear_hdr_key = hdr_key

        # 2. 逐行槽位脏检查：仅重绘状态/血量/排序发生变化的 1-2 行（跳过未变化的 10+ 行！）
        row_pad_x = 8
        full_row_w = w - row_pad_x * 2      # 482px
        dead_row_w = full_row_w - 136       # 346px
        row_start_y = EARS_HEADER_H + 6     # 42
        pd = ImageDraw.Draw(ear_panel)

        num_rows = len(row_specs)
        for idx in range(12):
            ry = row_start_y + idx * (EARS_ROW_H + EARS_ROW_GAP)
            cur_sig = sig_rows[idx] if idx < num_rows else None
            if cur_sig == prev_row_sigs[idx]:
                continue
            prev_row_sigs[idx] = cur_sig

            # 用底图局部行切片擦除旧行（高度 EARS_ROW_H + 1 覆盖 1px 底边框）
            strip = bg_rgb.crop((0, ry, w, ry + EARS_ROW_H + 1))
            ear_panel.paste(strip, (0, ry))
            if cur_sig is None:
                continue

            meta, is_alive, is_unspotted_enemy, lamp_on, fw, hp_col = row_specs[idx]
            if is_alive:
                rx0 = row_pad_x
                rx1 = rx0 + full_row_w
            elif is_ally:
                rx0 = row_pad_x
                rx1 = rx0 + dead_row_w
            else:
                rx1 = w - row_pad_x
                rx0 = rx1 - dead_row_w

            row_rgba = self._get_ear_row_base(meta, w, is_ally, is_alive, is_unspotted_enemy, lamp_on)
            ear_panel.paste(row_rgba, (rx0, ry), row_rgba)

            if is_alive and fw > 0:
                by0 = ry + (EARS_ROW_H - hp_bar_h) // 2
                by1 = by0 + hp_bar_h
                if is_ally:
                    bx1 = rx1 - 10
                    bx0 = bx1 - hp_bar_w
                    pd.rectangle([bx0 + 1, by0 + 1, bx0 + 1 + fw, by1 - 1], fill=hp_col[:3])
                else:
                    bx0 = rx0 + 10
                    bx1 = bx0 + hp_bar_w
                    pd.rectangle([bx1 - 1 - fw, by0 + 1, bx1 - 1, by1 - 1], fill=hp_col[:3])

        if is_ally:
            self._ally_ear_cache_key = panel_sig
            self._canvas_has_ally_ear_key = panel_sig
        else:
            self._enemy_ear_cache_key = panel_sig
            self._canvas_has_enemy_ear_key = panel_sig
        canvas.paste(ear_panel, (x, EARS_Y))

    # =========================================================================
    # 左下主角战报（静态底图预烘焙 + 增量缓存）
    # =========================================================================

    def _draw_self_stats_widget(self, canvas: Image.Image, frame: BattleFrame) -> None:
        own_meta = self.timeline.ships_meta.get(self.timeline.player_vehicle_id)
        own_st = frame.ships.get(self.timeline.player_vehicle_id)

        hp = own_st.hp if (own_st and own_st.is_alive) else 0
        max_hp = (own_st.max_hp if own_st and own_st.max_hp > 0 else (own_meta.max_hp if own_meta else 1)) or 1
        heal_lim = own_st.healable_hp if (own_st and own_st.is_alive) else 0
        ss = frame.self_stats

        skey = (hp, max_hp, heal_lim, ss.enemy_damage, ss.potential_damage, ss.spotting_damage, ss.kills)
        if skey == self._canvas_has_self_key:
            return
        if skey == self._self_cache_key and self._self_cache_img is not None:
            canvas.paste(self._self_cache_img, (LEFT_X, SELF_CARD_Y))
            self._canvas_has_self_key = skey
            return

        panel = self._self_card_base_rgb.copy()
        d = ImageDraw.Draw(panel, "RGBA")
        x = 16
        inner_w = LEFT_W - 32

        ratio = max(0.0, min(1.0, hp / max_hp))
        heal_ratio = max(ratio, min(1.0, heal_lim / max_hp)) if heal_lim > hp else ratio

        hp_col = self.hp_threshold_color(ratio) if hp > 0 else self.theme.dead_text
        hp_cur_txt = _fmt_num(hp) if hp > 0 else self._t("hud_sunk")
        hp_max_txt = f" / {_fmt_num(max_hp)}"
        f14 = self.fonts.get(14, bold=False)
        max_b = d.textbbox((0, 0), hp_max_txt, font=f14)
        max_w = max_b[2] - max_b[0]
        d.text((x + inner_w, 48), hp_max_txt, font=f14, fill=self.theme.text_secondary, anchor="rm")
        d.text((x + inner_w - max_w, 47), hp_cur_txt, font=self.fonts.get(16, bold=True), fill=hp_col, anchor="rm")
        d.text((x, 48), self._t("hud_hp_pct", pct=int(round(ratio * 100))), font=f14, fill=self.theme.text_secondary, anchor="lm")

        # 大号纯扁平连续三色矩形血条（严禁剪影；100-70 绿 / 70-30 黄 / 30-0 红）
        bx0, bx1 = x, x + inner_w
        by0, by1 = 63, 79
        d.rectangle([bx0, by0, bx1, by1], fill=self.theme.hp_track, outline=self.theme.hp_border, width=1)
        bar_span = bx1 - bx0 - 2
        if heal_ratio > ratio:
            hw = round(bar_span * heal_ratio)
            d.rectangle([bx0 + 1, by0 + 1, bx0 + 1 + hw, by1 - 1], fill=self.theme.hp_healable)
        if ratio > 0:
            fw = max(2, round(bar_span * ratio))
            d.rectangle([bx0 + 1, by0 + 1, bx0 + 1 + fw, by1 - 1], fill=hp_col)

        val_rows = (
            (_fmt_num(ss.enemy_damage), 24, self.theme.text_primary, 108),
            (_fmt_num(ss.potential_damage), 18, self.theme.text_primary, 146),
            (_fmt_num(ss.spotting_damage), 18, self.theme.text_primary, 180),
            (str(ss.kills), 19, self.theme.division_color, 214),
        )
        for val_str, val_sz, val_col, cy in val_rows:
            d.text((x + inner_w, cy), val_str, font=self.fonts.get(val_sz, bold=True), fill=val_col, anchor="rm")

        self._self_cache_key = skey
        self._self_cache_img = panel
        canvas.paste(panel, (LEFT_X, SELF_CARD_Y))
        self._canvas_has_self_key = skey

    # =========================================================================
    # 中央底部专属局内聊天区域（内容变化时才重绘，缓存命中直接贴图）
    # =========================================================================

    def _draw_chat_panel(self, canvas: Image.Image, frame: BattleFrame) -> None:
        recent = frame.chat_messages[-4:]
        ckey = (
            len(frame.chat_messages),
            tuple((cm.time_left, cm.channel, cm.sender_vehicle_id, cm.sender_name, cm.text) for cm in recent),
        )
        if ckey == self._canvas_has_chat_key:
            return
        if ckey == self._chat_cache_key and self._chat_cache_img is not None:
            canvas.paste(self._chat_cache_img, (CENTER_X, CHAT_Y))
            self._canvas_has_chat_key = ckey
            return

        panel = self._chat_bg_rgb.copy()
        d = ImageDraw.Draw(panel, "RGBA")
        x = 16

        d.text((x, 14), self._t("hud_chat_title", n=len(frame.chat_messages)), font=self.fonts.get(14, bold=True), fill=self.theme.text_secondary, anchor="lm")
        d.text(
            (CENTER_W - 16, 14),
            self._t("hud_chat_sub"),
            font=self.fonts.get(12, bold=False),
            fill=self.theme.text_muted,
            anchor="rm",
        )

        if not recent:
            d.text(
                (CENTER_W // 2, 74),
                self._t("hud_chat_empty"),
                font=self.fonts.get(14, bold=False),
                fill=self.theme.text_muted,
                anchor="mm",
            )
        else:
            time_font = self.fonts.get(13, bold=False)
            meta_font = self.fonts.get(14, bold=True)
            msg_font = self.fonts.get(14, bold=False)
            ry = 42

            for cm in recent:
                cx = x
                t_str = _fmt_mmss(cm.time_left)
                d.text((cx, ry), t_str, font=time_font, fill=self.theme.text_muted, anchor="lm")
                cx += 46

                if cm.channel == "battle_team":
                    ch_col = self.theme.ally_color
                elif "division" in cm.channel or "prebattle" in cm.channel:
                    ch_col = self.theme.division_color
                else:
                    ch_col = self.theme.accent_gold
                ch_txt = f"[{translate_chat_channel(cm.channel, self.lang)}]"
                d.text((cx, ry), ch_txt, font=meta_font, fill=ch_col, anchor="lm")
                cb = d.textbbox((cx, ry), ch_txt, font=meta_font, anchor="lm")
                cx = cb[2] + 8

                sender_col = (
                    self._relation_color(cm.sender_relation)
                    if cm.sender_relation in (RELATION_SELF, RELATION_ALLY, RELATION_ENEMY, RELATION_DIVISION)
                    else self.theme.text_secondary
                )
                sender_ship = (
                    self._ship_names_by_vid.get(cm.sender_vehicle_id, cm.sender_ship_zh)
                    if cm.sender_vehicle_id is not None
                    else cm.sender_ship_zh
                )
                ship_part = f"({sender_ship})" if sender_ship else ""
                sender_str = f"{cm.sender_name}{ship_part}:"
                d.text((cx, ry), sender_str, font=meta_font, fill=sender_col, anchor="lm")
                sb = d.textbbox((cx, ry), sender_str, font=meta_font, anchor="lm")
                cx = sb[2] + 9

                rem_w = (CENTER_W - 16) - cx
                if rem_w > 24:
                    msg_txt = _truncate_text(d, cm.text, msg_font, rem_w)
                    d.text((cx, ry), msg_txt, font=msg_font, fill=self.theme.text_primary, anchor="lm")
                ry += 22

        self._chat_cache_key = ckey
        self._chat_cache_img = panel
        canvas.paste(panel, (CENTER_X, CHAT_Y))
        self._canvas_has_chat_key = ckey

    # =========================================================================
    # 右下官方击杀播报（内容变化时才重绘，缓存命中直接贴图）
    # =========================================================================

    def _draw_kill_feed(self, canvas: Image.Image, frame: BattleFrame) -> None:
        recent = frame.kills[-12:]
        kkey = (
            len(frame.kills),
            tuple((ev.killer_vehicle_id, ev.victim_vehicle_id, ev.death_type_id) for ev in recent),
        )
        if kkey == self._canvas_has_kill_key:
            return
        if kkey == self._kill_cache_key and self._kill_cache_img is not None:
            canvas.paste(self._kill_cache_img, (RIGHT_X, KILL_CARD_Y))
            self._canvas_has_kill_key = kkey
            return

        panel = self._kill_bg_rgb.copy()
        d = ImageDraw.Draw(panel, "RGBA")
        x = 16
        inner_w = RIGHT_W - 32
        d.text((x, 16), self._t("hud_killfeed_title", n=len(frame.kills)), font=self.fonts.get(15, bold=True), fill=self.theme.text_primary, anchor="lm")

        if not recent:
            d.text(
                (RIGHT_W // 2, KILL_CARD_H // 2),
                self._t("hud_killfeed_empty"),
                font=self.fonts.get(14, bold=False),
                fill=self.theme.text_muted,
                anchor="mm",
            )
        else:
            row_font = self.fonts.get(14, bold=True)
            cx = RIGHT_W // 2
            ry = 48

            for ev in reversed(recent):
                km = self.timeline.ships_meta.get(ev.killer_vehicle_id)
                vm = self.timeline.ships_meta.get(ev.victim_vehicle_id)
                k_name = self._ship_name(km)
                v_name = self._ship_name(vm)
                k_col = self._relation_color(km.relation) if km else self.theme.text_secondary
                v_col = self._relation_color(vm.relation) if vm else self.theme.text_secondary

                frag_ic = self.icons.get_frag_icon(ev.death_type_id, height=17)
                if frag_ic is not None:
                    _blit_rgba(panel, frag_ic, round(cx - frag_ic.width / 2), round(ry - frag_ic.height / 2))
                    half_gap = max(18, frag_ic.width // 2 + 8)
                else:
                    d.text((cx, ry), ev.death_type_zh, font=self.fonts.get(12, bold=True), fill=self.theme.text_secondary, anchor="mm")
                    half_gap = 22

                k_icon_x = cx - half_gap - 22
                if km is not None:
                    ki = self.icons.get_hud_class_icon(km.species, k_col, size=22, flip_left=False)
                    _blit_rgba(panel, ki, k_icon_x, round(ry - ki.height / 2))
                k_max_w = max(60, k_icon_x - x - 6)
                d.text((k_icon_x - 6, ry), _truncate_text(d, k_name, row_font, k_max_w), font=row_font, fill=k_col, anchor="rm")

                v_icon_x = cx + half_gap
                if vm is not None:
                    vi = self.icons.get_hud_class_icon(vm.species, v_col, size=22, flip_left=True)
                    _blit_rgba(panel, vi, v_icon_x, round(ry - vi.height / 2))
                v_txt_x = v_icon_x + 26
                v_max_w = max(60, (RIGHT_W - 16) - v_txt_x)
                d.text((v_txt_x, ry), _truncate_text(d, v_name, row_font, v_max_w), font=row_font, fill=v_col, anchor="lm")

                d.line([(x, ry + 14), (x + inner_w, ry + 14)], fill=(255, 255, 255, 16), width=1)
                ry += 28

        self._kill_cache_key = kkey
        self._kill_cache_img = panel
        canvas.paste(panel, (RIGHT_X, KILL_CARD_Y))
        self._canvas_has_kill_key = kkey
