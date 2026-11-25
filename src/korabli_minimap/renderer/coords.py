"""BigWorld 世界坐标 (x, z) 与小地图像素坐标 (px, py) 精确换算。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MapProjection:
    """单张战斗地图的坐标投影参数（1 BigWorld 单位 = 30 米）。"""

    minimap_size: int
    space_width: float
    space_height: float

    @property
    def scale_x(self) -> float:
        return self.minimap_size / max(1.0, self.space_width)

    @property
    def scale_y(self) -> float:
        return self.minimap_size / max(1.0, self.space_height)

    @property
    def scale_r(self) -> float:
        return (self.scale_x + self.scale_y) * 0.5

    def world_to_pixel(self, x: float, z: float) -> tuple[float, float]:
        """将 BigWorld (x, z) 转换为小地图像素坐标（z 轴取反，北方朝上）。"""
        half = self.minimap_size * 0.5
        px = x * self.scale_x + half
        py = (-z) * self.scale_y + half
        return px, py

    def radius_bw_to_pixel(self, radius_bw: float) -> float:
        return radius_bw * self.scale_r

    @classmethod
    def from_map_meta(cls, meta: dict[str, Any], minimap_size: int = 760) -> "MapProjection":
        sw = float(meta.get("space_width", 1400.0) or 1400.0)
        sh = float(meta.get("space_height", sw) or sw)
        return cls(minimap_size=minimap_size, space_width=sw, space_height=sh)
