"""中文字体加载与缓存管理（优先使用汉化 mod 内置思源黑体，自动回退系统微软雅黑/NotoSansSC）。"""

from __future__ import annotations

from pathlib import Path

from PIL import ImageFont


SYSTEM_REGULAR_CANDIDATES = (
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\NotoSansSC-VF.ttf"),
    Path(r"C:\Windows\Fonts\Deng.ttf"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
)

SYSTEM_BOLD_CANDIDATES = (
    Path(r"C:\Windows\Fonts\msyhbd.ttc"),
    Path(r"C:\Windows\Fonts\Dengb.ttf"),
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
)


class FontManager:
    """统一管理常规/加粗中文字体，避免乱码与豆腐块。"""

    def __init__(
        self,
        version_fonts_dir: Path | None = None,
        regular_override: Path | None = None,
        bold_override: Path | None = None,
    ) -> None:
        self.regular_path = self._pick_font(
            override=regular_override,
            version_dir=version_fonts_dir,
            prefer_tokens=("medium", "regular"),
            fallbacks=SYSTEM_REGULAR_CANDIDATES,
        )
        self.bold_path = self._pick_font(
            override=bold_override,
            version_dir=version_fonts_dir,
            prefer_tokens=("bold",),
            fallbacks=SYSTEM_BOLD_CANDIDATES,
        )
        self._cache: dict[tuple[bool, int], ImageFont.FreeTypeFont] = {}

    @staticmethod
    def _pick_font(
        override: Path | None,
        version_dir: Path | None,
        prefer_tokens: tuple[str, ...],
        fallbacks: tuple[Path, ...],
    ) -> Path | None:
        if override and override.is_file():
            return override
        if version_dir and version_dir.is_dir():
            files = sorted(version_dir.glob("*.ttf")) + sorted(version_dir.glob("*.otf")) + sorted(version_dir.glob("*.ttc"))
            for tok in prefer_tokens:
                for f in files:
                    if tok in f.name.lower():
                        return f
            if files:
                return files[0]
        for cand in fallbacks:
            if cand.is_file():
                return cand
        return None

    def get(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        key = (bold, size)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        path = self.bold_path if bold else self.regular_path
        if path is None:
            path = self.regular_path or self.bold_path
        if path is not None:
            font = ImageFont.truetype(str(path), size=size)
            self._cache[key] = font
            return font
        return ImageFont.load_default()
