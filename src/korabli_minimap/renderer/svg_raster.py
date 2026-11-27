"""纯 Python + Pillow 精确 SVG 路径光栅化器（支持 evenodd 镂空与三次贝塞尔曲线，零 NumPy 依赖）。

专门用于将俄服客户端 gui.idx 提取的官方 SVG 舰种/小地图/飞机图标无损渲染为高清晰抗锯齿 RGBA 图像，
无需依赖 Windows 上难安装的原生 Cairo/librsvg DLL 或重型 OpenBLAS 运行时。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw


_TOKEN_RE = re.compile(r"[MmLlHhVvCcSsQqZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")

_NAMED_COLORS: dict[str, tuple[int, int, int]] = {
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "none": (-1, -1, -1),
}


def _parse_color(raw: str | None, opacity: float = 1.0) -> tuple[int, int, int, int] | None:
    if not raw:
        return None
    s = raw.strip().lower()
    if s == "none":
        return None
    if s in _NAMED_COLORS:
        r, g, b = _NAMED_COLORS[s]
        if r < 0:
            return None
    elif s.startswith("#"):
        hx = s[1:]
        if len(hx) == 3:
            r, g, b = int(hx[0] * 2, 16), int(hx[1] * 2, 16), int(hx[2] * 2, 16)
        elif len(hx) == 6:
            r, g, b = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)
        elif len(hx) == 8:
            r, g, b = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)
            opacity *= int(hx[6:8], 16) / 255.0
        else:
            return None
    else:
        return None
    a = max(0, min(255, int(round(255.0 * opacity))))
    return (r, g, b, a)


def _sample_cubic(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    steps: int = 10,
) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1.0 - t
        x = mt**3 * p0[0] + 3 * mt**2 * t * p1[0] + 3 * mt * t**2 * p2[0] + t**3 * p3[0]
        y = mt**3 * p0[1] + 3 * mt**2 * t * p1[1] + 3 * mt * t**2 * p2[1] + t**3 * p3[1]
        pts.append((x, y))
    return pts


def _sample_quad(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    steps: int = 8,
) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for i in range(1, steps + 1):
        t = i / steps
        mt = 1.0 - t
        x = mt**2 * p0[0] + 2 * mt * t * p1[0] + t**2 * p2[0]
        y = mt**2 * p0[1] + 2 * mt * t * p1[1] + t**2 * p2[1]
        pts.append((x, y))
    return pts


def _parse_svg_path_subpaths(d_attr: str) -> list[list[tuple[float, float]]]:
    """将 SVG path `d` 字符串解析为闭合子多边形顶点列表。"""
    tokens = _TOKEN_RE.findall(d_attr)
    subpaths: list[list[tuple[float, float]]] = []
    cur_poly: list[tuple[float, float]] = []

    idx = 0
    n = len(tokens)
    cmd = ""
    cx, cy = 0.0, 0.0
    sx, sy = 0.0, 0.0
    last_ctrl: tuple[float, float] | None = None

    def _is_cmd(tok: str) -> bool:
        return len(tok) == 1 and tok.isalpha()

    while idx < n:
        if _is_cmd(tokens[idx]):
            cmd = tokens[idx]
            idx += 1
        elif not cmd:
            break

        if cmd in ("M", "m"):
            x = float(tokens[idx])
            y = float(tokens[idx + 1])
            idx += 2
            if cmd == "m":
                x += cx
                y += cy
            if cur_poly:
                subpaths.append(cur_poly)
            cur_poly = [(x, y)]
            cx, cy = x, y
            sx, sy = x, y
            last_ctrl = None
            # 后续隐式坐标按 L/l 处理
            cmd = "L" if cmd == "M" else "l"

        elif cmd in ("L", "l"):
            x = float(tokens[idx])
            y = float(tokens[idx + 1])
            idx += 2
            if cmd == "l":
                x += cx
                y += cy
            cur_poly.append((x, y))
            cx, cy = x, y
            last_ctrl = None

        elif cmd in ("H", "h"):
            x = float(tokens[idx])
            idx += 1
            if cmd == "h":
                x += cx
            cur_poly.append((x, cy))
            cx = x
            last_ctrl = None

        elif cmd in ("V", "v"):
            y = float(tokens[idx])
            idx += 1
            if cmd == "v":
                y += cy
            cur_poly.append((cx, y))
            cy = y
            last_ctrl = None

        elif cmd in ("C", "c"):
            x1, y1 = float(tokens[idx]), float(tokens[idx + 1])
            x2, y2 = float(tokens[idx + 2]), float(tokens[idx + 3])
            x, y = float(tokens[idx + 4]), float(tokens[idx + 5])
            idx += 6
            if cmd == "c":
                x1 += cx; y1 += cy
                x2 += cx; y2 += cy
                x += cx; y += cy
            cur_poly.extend(_sample_cubic((cx, cy), (x1, y1), (x2, y2), (x, y)))
            last_ctrl = (x2, y2)
            cx, cy = x, y

        elif cmd in ("S", "s"):
            x2, y2 = float(tokens[idx]), float(tokens[idx + 1])
            x, y = float(tokens[idx + 2]), float(tokens[idx + 3])
            idx += 4
            if cmd == "s":
                x2 += cx; y2 += cy
                x += cx; y += cy
            if last_ctrl is not None:
                x1, y1 = 2 * cx - last_ctrl[0], 2 * cy - last_ctrl[1]
            else:
                x1, y1 = cx, cy
            cur_poly.extend(_sample_cubic((cx, cy), (x1, y1), (x2, y2), (x, y)))
            last_ctrl = (x2, y2)
            cx, cy = x, y

        elif cmd in ("Q", "q"):
            x1, y1 = float(tokens[idx]), float(tokens[idx + 1])
            x, y = float(tokens[idx + 2]), float(tokens[idx + 3])
            idx += 4
            if cmd == "q":
                x1 += cx; y1 += cy
                x += cx; y += cy
            cur_poly.extend(_sample_quad((cx, cy), (x1, y1), (x, y)))
            last_ctrl = (x1, y1)
            cx, cy = x, y

        elif cmd in ("Z", "z"):
            if cur_poly:
                subpaths.append(cur_poly)
                cur_poly = []
            cx, cy = sx, sy
            last_ctrl = None
            cmd = ""
        else:
            idx += 1

    if cur_poly:
        subpaths.append(cur_poly)
    return subpaths


def rasterize_svg(
    svg_path: Path,
    target_w: int,
    target_h: int,
    tint_color: tuple[int, int, int, int] | None = None,
    outline_color: tuple[int, int, int, int] | None = None,
    supersample: int = 6,
) -> Image.Image:
    """将官方 SVG 无损超采样光栅化为目标尺寸 RGBA 图像。

    支持着色替换：
    - 原 SVG 中偏亮前景（白 / #CCCCCC / #5EDABB 等非深灰暗边）在传入 tint_color 时自动着色；
    - 原 SVG 中深灰/黑描边（#333333 / black）在传入 outline_color 时可自定义颜色，否则保留原暗边。
    """
    root = ET.fromstring(svg_path.read_bytes())
    vb = root.attrib.get("viewBox", "")
    if vb:
        vx, vy, vw, vh = [float(p) for p in vb.replace(",", " ").split()[:4]]
    else:
        vx, vy = 0.0, 0.0
        vw = float(root.attrib.get("width", target_w))
        vh = float(root.attrib.get("height", target_h))

    hi_w = max(8, target_w * supersample)
    hi_h = max(8, target_h * supersample)
    sx = hi_w / max(1e-6, vw)
    sy = hi_h / max(1e-6, vh)
    scale = min(sx, sy)
    off_x = (hi_w - vw * scale) * 0.5 - vx * scale
    off_y = (hi_h - vh * scale) * 0.5 - vy * scale

    canvas = Image.new("RGBA", (hi_w, hi_h), (0, 0, 0, 0))

    for elem in root.iter():
        tag = elem.tag.split("}")[-1].lower()
        if tag != "path":
            continue
        d_attr = elem.attrib.get("d", "")
        if not d_attr:
            continue
        fill_op = float(elem.attrib.get("fill-opacity", "1.0") or "1.0")
        raw_col = _parse_color(elem.attrib.get("fill"), opacity=fill_op)
        if raw_col is None or raw_col[3] == 0:
            continue

        # 区分背景暗描边 (#333333 / black) 与前景主体 (white / #cccccc / 彩色)
        luma = 0.299 * raw_col[0] + 0.587 * raw_col[1] + 0.114 * raw_col[2]
        if luma < 72:
            if outline_color is not None:
                eff_a = int(round(outline_color[3] * (raw_col[3] / 255.0)))
                draw_col = (outline_color[0], outline_color[1], outline_color[2], eff_a)
            else:
                draw_col = (14, 24, 30, raw_col[3])
        else:
            if tint_color is not None:
                eff_a = int(round(tint_color[3] * (raw_col[3] / 255.0)))
                draw_col = (tint_color[0], tint_color[1], tint_color[2], eff_a)
            else:
                draw_col = raw_col

        subpaths = _parse_svg_path_subpaths(d_attr)
        if not subpaths:
            continue

        fill_rule = elem.attrib.get("fill-rule", "nonzero").strip().lower()
        acc = Image.new("1", (hi_w, hi_h), 0)
        for poly in subpaths:
            if len(poly) < 3:
                continue
            scaled_pts = [(p[0] * scale + off_x, p[1] * scale + off_y) for p in poly]
            tmp = Image.new("1", (hi_w, hi_h), 0)
            ImageDraw.Draw(tmp).polygon(scaled_pts, fill=1)
            if fill_rule == "evenodd":
                acc = ImageChops.logical_xor(acc, tmp)
            else:
                acc = ImageChops.logical_or(acc, tmp)

        mask = acc.convert("L")
        layer = Image.new("RGBA", (hi_w, hi_h), (0, 0, 0, 0))
        layer.paste(draw_col, (0, 0), mask)
        canvas = Image.alpha_composite(canvas, layer)

    return canvas.resize((target_w, target_h), resample=Image.Resampling.LANCZOS)
