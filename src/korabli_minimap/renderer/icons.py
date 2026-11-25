r"""官方原版《战舰世界》(俄服 gui.idx) 舰种、灭点、沉没、飞机、击杀与战绩图标加载工厂。

优先直接光栅化从 D:\Games\Korabli 的 gui.idx 提取的官方原版 SVG/PNG 资源：
- 小地图在点舰种: icons/minimap_ship/{destroyer,cruiser,battleship,aircarrier,submarine,auxiliary}.svg
- 小地图灭点舰种: icons/minimap_ship/{species}_last_visible.svg（原版空心镂空样式）
- 小地图沉没舰种: icons/minimap_ship/{species}_dead.svg（原版半透明沉没样式）
- 小地图主角图标: icons/minimap_self/alive.svg 与 dead.svg
- 侧边栏 & 击杀播报舰种: icons/hud_ship_svg/{species}.svg（官方水平单/双斜杠原版外形）
- 击杀武器图标: icons/frag/icon_frag_*.png
- 伤害统计图标: icons/damage_widget/icon_counter_*.png

当处于无客户端缓存的纯单元测试环境时，内置与官方 SVG 几何完全一致的矢量兜底绘制。
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

from .svg_raster import rasterize_svg


SPECIES_TO_FILE = {
    "Destroyer": "destroyer",
    "Cruiser": "cruiser",
    "Battleship": "battleship",
    "AirCarrier": "aircarrier",
    "Submarine": "submarine",
    "Auxiliary": "auxiliary",
}

DEATH_ID_TO_FRAG_ICON = {
    0: "frags.png",
    1: "icon_frag_main_caliber.png",
    2: "icon_frag_atba.png",
    3: "icon_frag_torpedo.png",
    4: "icon_frag_bomb.png",
    5: "icon_frag_torpedo.png",
    6: "icon_frag_burning.png",
    7: "icon_frag_ram.png",
    8: "icon_frag_flood.png",
    9: "icon_frag_main_caliber.png",
    10: "icon_frag_secondary_caliber.png",
    11: "icon_frag_naval_mine.png",
    12: "icon_frag_rocket.png",
    13: "icon_frag_depthbomb.png",
    14: "icon_frag_flood.png",
    15: "icon_frag_rocket.png",
    16: "icon_frag_main_caliber.png",
    17: "icon_frag_main_caliber.png",
    18: "icon_frag_main_caliber.png",
    19: "icon_frag_main_caliber.png",
    20: "icon_frag_bomb.png",
    21: "icon_frag_atba.png",
    22: "icon_frag_skip.png",
    23: "icon_frag_torpedo.png",
    24: "icon_frag_missile.png",
    25: "icon_frag_bomb.png",
    28: "icon_frag_depthbomb.png",
}


class GameIconFactory:
    """加载并缓存俄服客户端原版官方图标。"""

    def __init__(self, icons_dir: Path | None = None) -> None:
        self.icons_dir = icons_dir
        self._minimap_ship_base: dict[tuple[str, str, tuple[int, int, int, int], int], Image.Image] = {}
        self._minimap_ship_rot: dict[tuple[str, str, tuple[int, int, int, int], int, int], Image.Image] = {}
        self._hud_ship_cache: dict[tuple[str, tuple[int, int, int, int], int, bool], Image.Image] = {}
        self._plane_cache: dict[tuple[tuple[int, int, int, int], bool, int, int], Image.Image] = {}
        self._frag_cache: dict[tuple[int, int], Image.Image | None] = {}
        self._counter_cache: dict[tuple[str, int], Image.Image | None] = {}

    # ------------------------------------------------------------------
    # 1. 小地图舰种图标（在点 / 灭点空心 / 沉没 / 主角）
    # ------------------------------------------------------------------

    def get_minimap_ship_icon(
        self,
        species: str,
        color: tuple[int, int, int, int],
        yaw_rad: float,
        state: str = "spotted",   # "spotted" | "unspotted" | "dead" | "self" | "self_dead"
        height: int = 22,
    ) -> Image.Image:
        """获取按航向旋转后的官方原版小地图舰种图标。"""
        deg = round((-math.degrees(yaw_rad)) / 3.0) * 3 % 360
        rkey = (species, state, color, height, deg)
        cached = self._minimap_ship_rot.get(rkey)
        if cached is not None:
            return cached

        base = self._get_minimap_ship_base(species, state, color, height)
        rotated = base.rotate(deg, resample=Image.Resampling.BICUBIC, expand=True)
        self._minimap_ship_rot[rkey] = rotated
        return rotated

    def _get_minimap_ship_base(
        self,
        species: str,
        state: str,
        color: tuple[int, int, int, int],
        height: int,
    ) -> Image.Image:
        bkey = (species, state, color, height)
        cached = self._minimap_ship_base.get(bkey)
        if cached is not None:
            return cached

        slug = SPECIES_TO_FILE.get(species, "cruiser")
        svg_path: Path | None = None
        if self.icons_dir and self.icons_dir.is_dir():
            if state == "self":
                cand = self.icons_dir / "minimap_self" / "alive.svg"
            elif state == "self_dead":
                cand = self.icons_dir / "minimap_self" / "dead.svg"
            elif state == "unspotted":
                cand = self.icons_dir / "minimap_ship" / f"{slug}_last_visible.svg"
            elif state == "dead":
                cand = self.icons_dir / "minimap_ship" / f"{slug}_dead.svg"
            else:
                cand = self.icons_dir / "minimap_ship" / f"{slug}.svg"
            if cand.is_file():
                svg_path = cand

        # 官方原版 viewBox 比例: 15×20 (普通) / 16×20 (主角)
        target_w = max(12, round(height * (0.80 if state.startswith("self") else 0.75)))
        if svg_path is not None:
            outline = (10, 22, 28, 220) if state != "dead" else (12, 24, 30, 110)
            img = rasterize_svg(
                svg_path=svg_path,
                target_w=target_w,
                target_h=height,
                tint_color=color,
                outline_color=outline,
                supersample=6,
            )
        else:
            img = self._fallback_minimap_ship(slug, state, color, target_w, height)

        self._minimap_ship_base[bkey] = img
        return img

    @staticmethod
    def _fallback_minimap_ship(
        slug: str,
        state: str,
        color: tuple[int, int, int, int],
        w: int,
        h: int,
    ) -> Image.Image:
        """与俄服官方 15×20 SVG 几何严格一致的纯 Python 兜底绘制（用于无客户端缓存测试）。"""
        ss = 6
        img = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        sx = (w * ss) / 15.0
        sy = (h * ss) / 20.0

        def pt(x: float, y: float) -> tuple[float, float]:
            return (x * sx, y * sy)

        outline_bg = (14, 24, 30, 220)
        hollow = state == "unspotted"

        if state == "self":
            pts = [pt(7.5, 1), pt(14, 19), pt(7.5, 14.5), pt(1, 19)]
            d.polygon(pts, fill=color, outline=outline_bg, width=ss)
        elif slug == "destroyer":
            d.polygon([pt(7.5, 2), pt(12, 18), pt(3, 18)], fill=outline_bg)
            d.polygon(
                [pt(7.5, 5), pt(10.75, 17), pt(4.25, 17)],
                fill=None if hollow else color,
                outline=color if hollow else None,
                width=max(2, ss),
            )
        elif slug == "submarine":
            d.polygon([pt(7.5, 2), pt(12, 18), pt(3, 18)], fill=outline_bg)
            d.polygon([pt(7.5, 5), pt(9.94, 14), pt(5.06, 14)], fill=color)
            d.polygon([pt(10.2, 15), pt(10.75, 17), pt(4.25, 17), pt(4.8, 15)], fill=color)
        elif slug == "battleship":
            d.polygon([pt(7.5, 2.5), pt(12, 7.6), pt(12, 18), pt(3, 18), pt(3, 7.6)], fill=outline_bg)
            d.polygon([pt(4, 8), pt(7.5, 4), pt(11, 8), pt(11, 11)], fill=color)
            d.polygon([pt(4, 9.46), pt(4, 11), pt(11, 14), pt(11, 12.46)], fill=color)
            d.polygon([pt(4, 12.5), pt(11, 15.5), pt(11, 17), pt(4, 17)], fill=color)
        elif slug == "aircarrier":
            d.polygon([pt(7.5, 2.5), pt(12, 7.6), pt(12, 18), pt(3, 18), pt(3, 7.6)], fill=outline_bg)
            d.polygon([pt(11, 8), pt(7.5, 4), pt(4, 8), pt(4, 9), pt(11, 9)], fill=color)
            d.rectangle([pt(4, 10), pt(7, 17)], fill=color)
            d.rectangle([pt(8, 10), pt(11, 17)], fill=color)
        else:  # cruiser
            d.polygon([pt(7.5, 2.5), pt(12, 7.6), pt(12, 18), pt(3, 18), pt(3, 7.6)], fill=outline_bg)
            d.polygon([pt(11, 8), pt(7.5, 4), pt(4, 8), pt(4, 9), pt(11, 12)], fill=color)
            d.polygon([pt(4, 10.5), pt(11, 13.5), pt(11, 17), pt(4, 17)], fill=color)

        return img.resize((w, h), resample=Image.Resampling.LANCZOS)

    # ------------------------------------------------------------------
    # 2. 侧边舰队面板 & 击杀播报用官方水平舰种图标
    # ------------------------------------------------------------------

    def get_hud_class_icon(
        self,
        species: str,
        color: tuple[int, int, int, int],
        size: int = 22,
        flip_left: bool = False,
    ) -> Image.Image:
        """获取侧边队伍列表与击杀播报用的官方原版水平舰种图标（可水平镜像朝左）。"""
        key = (species, color, size, flip_left)
        cached = self._hud_ship_cache.get(key)
        if cached is not None:
            return cached

        slug = SPECIES_TO_FILE.get(species, "cruiser")
        svg_path: Path | None = None
        if self.icons_dir and self.icons_dir.is_dir():
            cand = self.icons_dir / "hud_ship_svg" / f"{slug}.svg"
            if cand.is_file():
                svg_path = cand

        if svg_path is not None:
            img = rasterize_svg(
                svg_path=svg_path,
                target_w=size,
                target_h=size,
                tint_color=color,
                outline_color=(6, 16, 22, 140),
                supersample=6,
            )
        else:
            # 兜底：将垂直官方小地图图标顺时针旋转 90° 朝右
            up = self._get_minimap_ship_base(species, "spotted", color, size)
            img = up.rotate(-90, resample=Image.Resampling.BICUBIC, expand=True).resize(
                (size, size), resample=Image.Resampling.LANCZOS
            )

        if flip_left:
            img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)

        self._hud_ship_cache[key] = img
        return img

    # ------------------------------------------------------------------
    # 3. 航空中队小图标
    # ------------------------------------------------------------------

    def get_plane_icon(
        self,
        color: tuple[int, int, int, int],
        yaw_rad: float,
        returning: bool = False,
        size: int = 16,
    ) -> Image.Image:
        deg = round((-math.degrees(yaw_rad)) / 6.0) * 6 % 360
        adj_col = (color[0], color[1], color[2], 120 if returning else 240)
        key = (adj_col, returning, size, deg)
        cached = self._plane_cache.get(key)
        if cached is not None:
            return cached

        svg_path: Path | None = None
        if self.icons_dir and self.icons_dir.is_dir():
            cand = self.icons_dir / "plane" / "bomber_he_ally.svg"
            if cand.is_file():
                svg_path = cand

        if svg_path is not None:
            base = rasterize_svg(
                svg_path=svg_path,
                target_w=size,
                target_h=size,
                tint_color=adj_col,
                outline_color=(10, 20, 26, 160 if returning else 230),
                supersample=4,
            )
        else:
            ss = 4
            w = h = size * ss
            tmp = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            d = ImageDraw.Draw(tmp)
            cx = w * 0.5
            pts = [
                (cx, h * 0.14), (w * 0.88, h * 0.62), (cx + w * 0.1, h * 0.56),
                (cx + w * 0.14, h * 0.86), (cx, h * 0.76), (cx - w * 0.14, h * 0.86),
                (cx - w * 0.1, h * 0.56), (w * 0.12, h * 0.62),
            ]
            d.polygon(pts, fill=adj_col, outline=(12, 18, 28, 180), width=max(1, ss))
            base = tmp.resize((size, size), resample=Image.Resampling.LANCZOS)

        rot = base.rotate(deg, resample=Image.Resampling.BICUBIC, expand=True)
        self._plane_cache[key] = rot
        return rot

    # ------------------------------------------------------------------
    # 4. 官方击杀方式图标 (icon_frag_*.png) 与战绩图标 (icon_counter_*.png)
    # ------------------------------------------------------------------

    def get_frag_icon(self, death_type_id: int, height: int = 16) -> Image.Image | None:
        key = (death_type_id, height)
        if key in self._frag_cache:
            return self._frag_cache[key]

        img: Image.Image | None = None
        if self.icons_dir and self.icons_dir.is_dir():
            fname = DEATH_ID_TO_FRAG_ICON.get(death_type_id, "frags.png")
            cand = self.icons_dir / "frag" / fname
            if not cand.is_file():
                cand = self.icons_dir / "frag" / "frags.png"
            if cand.is_file():
                raw = Image.open(cand).convert("RGBA")
                # 裁剪多余透明边缘并按目标高度等比缩放
                bbox = raw.getbbox()
                if bbox:
                    raw = raw.crop(bbox)
                ratio = height / max(1, raw.height)
                tw = max(8, round(raw.width * ratio))
                img = raw.resize((tw, height), resample=Image.Resampling.LANCZOS)

        self._frag_cache[key] = img
        return img

    def get_damage_counter_icon(self, kind: str, size: int = 16) -> Image.Image | None:
        """kind: 'caused' | 'blocked' | 'assisted'。"""
        key = (kind, size)
        if key in self._counter_cache:
            return self._counter_cache[key]

        name_map = {
            "caused": "icon_counter_caused_damage.png",
            "blocked": "icon_counter_blocked_damage.png",
            "assisted": "icon_counter_assisted_damage.png",
        }
        img: Image.Image | None = None
        if self.icons_dir and self.icons_dir.is_dir():
            cand = self.icons_dir / "damage_widget" / name_map.get(kind, "")
            if cand.is_file():
                raw = Image.open(cand).convert("RGBA")
                bbox = raw.getbbox()
                if bbox:
                    raw = raw.crop(bbox)
                scale = size / max(1, max(raw.width, raw.height))
                img = raw.resize(
                    (max(8, round(raw.width * scale)), max(8, round(raw.height * scale))),
                    resample=Image.Resampling.LANCZOS,
                )

        self._counter_cache[key] = img
        return img


# 向后兼容别名
FlatIconFactory = GameIconFactory
