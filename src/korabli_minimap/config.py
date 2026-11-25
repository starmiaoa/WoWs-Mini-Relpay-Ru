"""全局配置、黑色半透明玻璃面板配色、Tactical-Tech 血条阈值与渲染开关。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys


def get_app_root() -> Path:
    """返回应用根目录：PyInstaller 打包后为 EXE 所在目录，开发模式为项目根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


DEFAULT_GAME_DIR = Path(r"D:\Games\Korabli")
DEFAULT_DATA_DIR = get_app_root() / "data"
DEFAULT_OUTPUT_DIR = get_app_root() / "output"


@dataclass(frozen=True)
class ThemeColors:
    """黑色半透明玻璃风（60% 外面板透明度）+ Tactical-Tech 血条色板。"""

    # 黑色半透明玻璃表面与锐利中性边框
    canvas_fallback_bg: tuple[int, int, int, int] = (10, 12, 16, 255)
    backdrop_veil: tuple[int, int, int, int] = (6, 8, 11, 138)          # 适度压暗底层地图场景，透出柔和光影
    glass_panel: tuple[int, int, int, int] = (10, 12, 16, 153)          # 主面板背景 ~60% 不透明度 (153/255)
    glass_header: tuple[int, int, int, int] = (16, 18, 23, 168)         # 面板标题栏 ~66% 不透明度
    glass_row: tuple[int, int, int, int] = (14, 16, 21, 206)            # 存活单船行（保持高对比深色卡片质感）
    glass_row_self: tuple[int, int, int, int] = (24, 27, 34, 224)       # 主角存活行高亮
    glass_row_unspotted: tuple[int, int, int, int] = (14, 16, 21, 182)  # 存活灭点敌舰行（明显区别于沉没行）
    glass_row_dead: tuple[int, int, int, int] = (10, 12, 16, 46)        # 沉没行极低二层透明度，一眼区分阵亡与灭点
    glass_border: tuple[int, int, int, int] = (255, 255, 255, 48)       # 1px 清晰冷灰外框
    hairline: tuple[int, int, int, int] = (255, 255, 255, 24)           # 内部极细分割线
    accent_gold: tuple[int, int, int, int] = (218, 168, 72, 215)        # 暖金高亮细边

    # 阵营语义色
    self_color: tuple[int, int, int, int] = (255, 255, 255, 255)        # #ffffff
    division_color: tuple[int, int, int, int] = (255, 217, 102, 255)    # #ffd966
    ally_color: tuple[int, int, int, int] = (94, 218, 187, 255)         # #5edabb (Tactical-Tech 绿)
    enemy_color: tuple[int, int, int, int] = (242, 87, 85, 255)         # #f25755 (Tactical-Tech 红)
    neutral_color: tuple[int, int, int, int] = (212, 218, 224, 255)

    # 团队总血量满格填充条固定颜色（不随血量百分比变色）
    team_hp_ally_fill: tuple[int, int, int, int] = (14, 156, 114, 232)  # 团队总血条恒定祖母绿
    team_hp_enemy_fill: tuple[int, int, int, int] = (176, 46, 48, 232)  # 团队总血条恒定深绯红
    team_hp_track: tuple[int, int, int, int] = (8, 10, 14, 150)

    # Tactical-Tech 连续长条血条三段阈值色（100%-70% 绿, 70%-30% 黄, 30%-0% 红）
    hp_green: tuple[int, int, int, int] = (94, 218, 187, 255)           # #5EDABB  (>= 70%)
    hp_yellow: tuple[int, int, int, int] = (255, 217, 102, 255)         # #FFD966  (30% ~ 70%)
    hp_red: tuple[int, int, int, int] = (242, 87, 85, 255)              # #F25755  (< 30%)
    hp_track: tuple[int, int, int, int] = (6, 8, 11, 200)
    hp_border: tuple[int, int, int, int] = (255, 255, 255, 50)
    hp_healable: tuple[int, int, int, int] = (228, 232, 238, 180)

    # 点亮灯（Tactical-Tech #ff9f1a 发光橙灯 / #444 隐蔽暗灯）
    spot_lamp_on: tuple[int, int, int, int] = (255, 159, 26, 255)
    spot_lamp_glow: tuple[int, int, int, int] = (255, 140, 20, 110)
    spot_lamp_off: tuple[int, int, int, int] = (74, 78, 86, 195)

    # 灭点状态色
    unspotted_ally_icon: tuple[int, int, int, int] = (156, 224, 204, 215)
    unspotted_enemy_icon: tuple[int, int, int, int] = (224, 228, 234, 220)
    unspotted_enemy_text: tuple[int, int, int, int] = (244, 178, 166, 240)
    unspotted_ally_text: tuple[int, int, int, int] = (180, 232, 214, 235)

    # 沉没置灰
    dead_icon: tuple[int, int, int, int] = (108, 114, 124, 130)
    dead_text: tuple[int, int, int, int] = (112, 118, 128, 160)

    # 文本色
    text_primary: tuple[int, int, int, int] = (244, 246, 250, 255)
    text_ship_sub: tuple[int, int, int, int] = (210, 216, 224, 248)
    text_secondary: tuple[int, int, int, int] = (164, 170, 180, 255)
    text_muted: tuple[int, int, int, int] = (114, 120, 130, 255)
    text_stroke: tuple[int, int, int, int] = (4, 6, 9, 240)
    timer_warning: tuple[int, int, int, int] = (250, 175, 10, 255)

    # 地图 10×10 网格与底图暗化
    minimap_veil: tuple[int, int, int, int] = (6, 8, 12, 72)
    grid_line: tuple[int, int, int, int] = (255, 255, 255, 48)
    grid_border: tuple[int, int, int, int] = (255, 255, 255, 76)

    # 地图烟雾（单层融合轮廓半透明白雾）
    smoke_fill: tuple[int, int, int, int] = (232, 240, 248, 96)
    smoke_outline: tuple[int, int, int, int] = (248, 252, 255, 215)

    # 弹道配色
    shell_he: tuple[int, int, int, int] = (247, 167, 47, 230)
    shell_ap: tuple[int, int, int, int] = (228, 234, 240, 225)
    shell_cs: tuple[int, int, int, int] = (255, 52, 52, 230)
    shell_secondary_alpha: int = 90

    # 消耗品探测圈
    radar_ring: tuple[int, int, int, int] = (255, 210, 88, 200)
    hydro_ring: tuple[int, int, int, int] = (92, 224, 255, 195)


@dataclass
class RenderConfig:
    """渲染输出参数与功能开关。"""

    fps: int = 30                      # 默认 30fps = 30 倍速（1K 1920x1080 高清输出）
    output_width: int = 1920           # 输出视频宽度（默认 1K 1920）
    output_height: int = 1080          # 输出视频高度（默认 1K 1080）
    language: str = "zh_CN"            # 渲染器界面语言: zh_CN / zh_TW / ru / en
    encoder: str = "auto"              # 视频编码器: auto (优先 NVENC/AMF/QSV，回退 libx264) | x264 | nvenc | amf | qsv
    render_workers: int = 0            # 并发渲染线程数: 0 = 自动按 CPU 核心数自适应
    seconds_per_frame: float = 1.0
    end_banner_seconds: float = 2.5

    # 核心开关
    show_smoke: bool = True
    show_unspotted_names: bool = True
    show_spotted_names: bool = True
    show_mini_hp_bars: bool = True
    show_shells: bool = True
    show_torpedoes: bool = True
    show_planes: bool = True
    show_consumable_rings: bool = True
    show_dead_markers: bool = True
    show_grid: bool = True
    show_chat: bool = True

    theme: ThemeColors = field(default_factory=ThemeColors)
    font_path: Path | None = None
    bold_font_path: Path | None = None

    def resolved_output_size(self) -> tuple[int, int]:
        """返回偶数对齐的合法输出分辨率（兼容 H.264 yuv420p）。"""
        w = max(320, int(self.output_width))
        h = max(240, int(self.output_height))
        w = w - (w % 2)
        h = h - (h % 2)
        return w, h
