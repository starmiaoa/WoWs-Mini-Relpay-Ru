"""生成多分辨率 Windows 应用图标 packaging/app_icon.ico（与关于页祖母绿战舰徽标一致）。"""

from __future__ import annotations

from pathlib import Path
from PIL import Image, ImageDraw


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ICON_OUT = PROJECT_ROOT / "packaging" / "app_icon.ico"


def render_badge(size: int = 512) -> Image.Image:
    """高倍超采样绘制圆角祖母绿战术底盘 + 白色战舰箭头。"""
    ss = 4
    s = size * ss
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    pad = int(s * 0.04)
    radius = int(s * 0.19)
    d.rounded_rectangle([pad, pad, s - pad, s - pad], radius=radius, fill=(20, 156, 120, 255))

    inner_pad = int(s * 0.165)
    inner_r = int(s * 0.105)
    stroke_w = max(4, int(s * 0.032))
    d.rounded_rectangle(
        [inner_pad, inner_pad, s - inner_pad, s - inner_pad],
        radius=inner_r,
        outline=(255, 255, 255, 200),
        width=stroke_w,
    )

    # 居中白色战舰小地图箭头
    cx = s * 0.5
    ship = [
        (cx, s * 0.275),
        (s * 0.655, s * 0.655),
        (cx, s * 0.565),
        (s * 0.345, s * 0.655),
    ]
    d.polygon(ship, fill=(255, 255, 255, 255))

    return img.resize((size, size), resample=Image.Resampling.LANCZOS)


def main() -> int:
    ICON_OUT.parent.mkdir(parents=True, exist_ok=True)
    master = render_badge(512)
    # 覆盖 Windows 10/11 100%~200% DPI 任务栏与资源管理器全档位，使用原生 BMP/DIB 编码确保最佳兼容性
    sizes = [(16, 16), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]
    master.save(ICON_OUT, format="ICO", bitmap_format="bmp", sizes=sizes)
    print(f"Generated {ICON_OUT} ({ICON_OUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
