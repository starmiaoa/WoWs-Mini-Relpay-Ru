"""WoWs-Mini-Relpay-Ru 四标签页多语言桌面图形界面（PySide6 / Qt6）。

支持：
- 外观主题：默认「跟随系统」（实时响应 Windows 10/11 深浅色切换），亦支持手动固定深色/浅色；
- 完整四语 I18N：简体中文 / 繁體中文 / Русский / English，界面与视频渲染器同步生效；
- 「回放」主页面极简两栏布局，「高级 / 设置 / 关于」分层收纳所有进阶功能。
"""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any
import webbrowser

from PySide6.QtCore import QEvent, QModelIndex, QObject, QRect, QSize, QThread, QTimer, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .config import DEFAULT_DATA_DIR, DEFAULT_OUTPUT_DIR, RenderConfig, get_app_root
from .extractor import (
    DiscoveredClient,
    LocalizationBundle,
    VersionDataStore,
    discover_korabli_clients,
    list_synced_versions,
    load_localization,
    load_version_store,
    locate_client,
    normalize_short_version,
    sync_client_data,
)
from .i18n import (
    SUPPORTED_LANGUAGES,
    detect_system_language,
    normalize_lang,
    tr,
    translate_client_channel,
)
from .parser import parse_replay
from .parser.container import read_replay_meta_only
from .renderer.video import RenderCancelledError, render_timeline_to_video
from .replay_catalog import (
    ReplayListItem,
    collect_korabli_replays_from_paths,
    create_batch_output_dir,
    parse_replay_item,
    scan_replays_in_game_dir,
)


APP_VERSION = "0.1.0"
PROJECT_ROOT = get_app_root()
REPO_URL = "https://github.com/starmiaoa/WoWs-Mini-Relpay-Ru"

RESOLUTION_PRESET_SPECS: list[tuple[str, int, int]] = [
    ("res_1k", 1920, 1080),
    ("res_720p", 1280, 720),
    ("res_2k", 2560, 1440),
    ("res_4k", 3840, 2160),
    ("res_custom", 0, 0),
]

ROLE_PATH_KEY = Qt.ItemDataRole.UserRole + 1
ROLE_REPLAY_ITEM = Qt.ItemDataRole.UserRole + 2
ROLE_CHECKED = Qt.ItemDataRole.UserRole + 3


def detect_system_dark_mode() -> bool:
    """探测操作系统当前是否为深色模式（优先 Qt6 StyleHints，回退 Windows 注册表）。"""
    try:
        hints = QGuiApplication.styleHints()
        if hints is not None:
            scheme = hints.colorScheme()
            if scheme == Qt.ColorScheme.Dark:
                return True
            if scheme == Qt.ColorScheme.Light:
                return False
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return int(val) == 0
        except OSError:
            pass
    return True


def _build_qss(dark: bool) -> str:
    if dark:
        bg = "#101319"
        panel = "#151922"
        card = "#161b24"
        inp = "#0e1117"
        border = "#242b38"
        border_hi = "#354054"
        text = "#edf0f5"
        text_sec = "#9ba5b5"
        muted = "#7b8597"
        btn_bg = "#1d2330"
        btn_hv = "#262e3f"
        tab_bg = "#141821"
        tab_sel = "#1e2533"
        accent = "#16a580"
        accent_hv = "#1ab990"
        accent_soft = "#16443a"
        accent_txt = "#5ef0c9"
        log_bg = "#0c0f14"
        scroll_h = "#2c3445"
    else:
        bg = "#f3f5f8"
        panel = "#ffffff"
        card = "#ffffff"
        inp = "#f8f9fb"
        border = "#dce1ea"
        border_hi = "#bac4d4"
        text = "#1b2230"
        text_sec = "#535f73"
        muted = "#768296"
        btn_bg = "#eef1f6"
        btn_hv = "#e2e7f0"
        tab_bg = "#e8ecf2"
        tab_sel = "#ffffff"
        accent = "#119473"
        accent_hv = "#15ab85"
        accent_soft = "#dff5ee"
        accent_txt = "#0c7a5e"
        log_bg = "#f6f8fb"
        scroll_h = "#c4cddb"

    return f"""
QMainWindow, QWidget#RootWidget, QWidget#TabPage, QWidget#ScrollInner {{
    background-color: {bg};
    color: {text};
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}}

QLabel {{
    color: {text};
    background-color: transparent;
}}

QTabWidget::pane {{
    border: 1px solid {border};
    border-radius: 10px;
    background-color: {bg};
    top: -1px;
}}

QTabBar::tab {{
    background-color: {tab_bg};
    color: {text_sec};
    border: 1px solid {border};
    border-bottom: none;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    padding: 7px 18px;
    margin-right: 4px;
    font-size: 13px;
    font-weight: 500;
}}

QTabBar::tab:hover {{
    color: {text};
}}

QTabBar::tab:selected {{
    background-color: {tab_sel};
    color: {text};
    font-weight: bold;
    border-bottom: 2px solid {accent};
}}

QFrame.PanelCard {{
    background-color: {panel};
    border: 1px solid {border};
    border-radius: 10px;
}}

QFrame#ListPanel {{
    background-color: {panel};
    border: 1px solid {border};
    border-radius: 10px;
}}

QFrame#ListPanel[dragActive="true"] {{
    background-color: {accent_soft};
    border: 2px dashed {accent};
}}

QFrame.GroupCard {{
    background-color: {card};
    border: 1px solid {border};
    border-radius: 9px;
}}

QLabel.CardTitle {{
    color: {text};
    font-size: 14px;
    font-weight: bold;
}}

QLabel.FieldLabel {{
    color: {text_sec};
    font-size: 12px;
    font-weight: 500;
}}

QLabel.Muted {{
    color: {muted};
    font-size: 12px;
}}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: {inp};
    color: {text};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 6px 10px;
    min-height: 22px;
}}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {accent};
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}

QComboBox QAbstractItemView {{
    background-color: {panel};
    color: {text};
    border: 1px solid {border_hi};
    border-radius: 6px;
    padding: 4px;
    selection-background-color: {accent_soft};
    selection-color: {text};
    outline: none;
}}

QPushButton {{
    background-color: {btn_bg};
    color: {text};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 6px 13px;
    font-size: 12.5px;
}}

QPushButton:hover {{
    background-color: {btn_hv};
    border-color: {border_hi};
}}

QPushButton:disabled {{
    color: {muted};
}}

QPushButton.FpsPill {{
    background-color: {inp};
    color: {text_sec};
    border: 1px solid {border};
    border-radius: 6px;
    padding: 5px 0px;
    font-weight: bold;
}}

QPushButton.FpsPill:hover {{
    border-color: {border_hi};
    color: {text};
}}

QPushButton.FpsPill[active="true"] {{
    background-color: {accent_soft};
    color: {accent_txt};
    border: 1px solid {accent};
}}

QPushButton#PrimaryBtn {{
    background-color: {accent};
    color: #ffffff;
    border: none;
    border-radius: 8px;
    font-size: 14.5px;
    font-weight: bold;
    min-height: 42px;
}}

QPushButton#PrimaryBtn:hover {{
    background-color: {accent_hv};
}}

QPushButton#PrimaryBtn:disabled {{
    background-color: {accent_soft};
    color: {muted};
}}

QPushButton#StopBtn {{
    background-color: #c2393c;
    color: #ffffff;
    border: none;
    border-radius: 8px;
    font-size: 14px;
    font-weight: bold;
    min-height: 42px;
}}

QPushButton#StopBtn:hover {{
    background-color: #d64447;
}}

QPushButton.LinkBtn {{
    background: transparent;
    border: none;
    color: {muted};
    padding: 2px 6px;
    font-size: 12px;
}}

QPushButton.LinkBtn:hover {{
    color: {accent_txt};
}}

QRadioButton {{
    color: {text};
    spacing: 6px;
}}

QListWidget {{
    background-color: transparent;
    border: none;
    outline: none;
}}

QScrollArea, QScrollArea > QWidget > QWidget {{
    background-color: {bg};
    border: none;
}}

QPlainTextEdit {{
    background-color: {log_bg};
    color: {text_sec};
    border: 1px solid {border};
    border-radius: 6px;
    font-family: "Consolas", "Microsoft YaHei UI", monospace;
    font-size: 12px;
    padding: 6px;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 7px;
    margin: 4px 2px 4px 0px;
}}

QScrollBar::handle:vertical {{
    background: {scroll_h};
    border-radius: 3px;
    min-height: 30px;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: none;
}}

QProgressBar {{
    background-color: {inp};
    border: none;
    border-radius: 4px;
    height: 7px;
}}

QProgressBar::chunk {{
    background-color: {accent};
    border-radius: 4px;
}}
"""


def _open_folder_in_explorer(folder: Path) -> None:
    target = Path(folder)
    if not target.exists():
        return
    if sys.platform == "win32":
        os.startfile(str(target))  # type: ignore[attr-defined]
    else:
        import subprocess
        subprocess.Popen(["xdg-open", str(target)])


class SmoothCheckBox(QCheckBox):
    """自绘抗锯齿圆角复选框（深浅主题显式配色，不依赖系统暗色主题）。"""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.dark_theme = True
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(26)

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        box = 17
        y = (self.height() - box) // 2
        cb_rect = QRect(1, y, box, box)

        if self.isChecked():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#16a580") if self.isEnabled() else QColor("#6ca697"))
            painter.drawRoundedRect(cb_rect, 4, 4)
            path = QPainterPath()
            path.moveTo(cb_rect.x() + 4.0, cb_rect.y() + 8.7)
            path.lineTo(cb_rect.x() + 7.2, cb_rect.y() + 12.0)
            path.lineTo(cb_rect.x() + 13.0, cb_rect.y() + 5.2)
            pen = QPen(QColor("#ffffff"), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        else:
            border_col = QColor("#3e485c") if self.dark_theme else QColor("#98a4b8")
            bg_col = QColor("#0e1117") if self.dark_theme else QColor("#ffffff")
            painter.setPen(QPen(border_col, 1.4))
            painter.setBrush(bg_col)
            painter.drawRoundedRect(cb_rect, 4, 4)

        text_col = QColor("#edf0f5") if self.dark_theme else QColor("#1b2230")
        if not self.isEnabled():
            text_col.setAlpha(120)
        painter.setPen(text_col)
        painter.setFont(self.font())
        painter.drawText(
            QRect(box + 10, 0, self.width() - box - 10, self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self.text(),
        )


class AboutLogoBadge(QWidget):
    """「关于」页自绘扁平徽标。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(96, 96)

    def paintEvent(self, event: Any) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#149c78"))
        p.drawRoundedRect(4, 4, 88, 88, 18, 18)

        p.setPen(QPen(QColor(255, 255, 255, 190), 3.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(16, 16, 64, 64, 10, 10)

        ship = QPainterPath()
        ship.moveTo(48, 28)
        ship.lineTo(62, 62)
        ship.lineTo(48, 54)
        ship.lineTo(34, 62)
        ship.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ffffff"))
        p.drawPath(ship)


class ReplayRowDelegate(QStyledItemDelegate):
    """自定义回放行绘制：抗锯齿复选勾 + 多语言船名/地图 + 战斗时间（深浅主题自适应）。"""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.dark_theme = True
        self.lang = "zh_CN"

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(option.rect.width(), 48)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        item: ReplayListItem | None = index.data(ROLE_REPLAY_ITEM)
        if item is None:
            return
        checked = bool(index.data(ROLE_CHECKED))
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = option.rect
        bg_rect = rect.adjusted(4, 2, -4, -2)

        if self.dark_theme:
            chk_bg = QColor(24, 72, 62, 120)
            hov_bg = QColor(255, 255, 255, 12)
            sep_col = QColor(255, 255, 255, 11)
            ship_col = QColor("#f5f7fb") if checked else QColor("#e6ebf3")
            map_col = QColor("#9ea8ba")
            time_col = QColor("#778296")
            cb_border = QColor("#3c4659")
            cb_bg = QColor("#0e1117")
            accent = QColor("#22c59b")
        else:
            chk_bg = QColor(22, 165, 128, 28)
            hov_bg = QColor(15, 23, 42, 10)
            sep_col = QColor(15, 23, 42, 16)
            ship_col = QColor("#0f172a")
            map_col = QColor("#4e5b70")
            time_col = QColor("#748296")
            cb_border = QColor("#b3bece")
            cb_bg = QColor("#ffffff")
            accent = QColor("#119473")

        if checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(chk_bg)
            painter.drawRoundedRect(bg_rect, 7, 7)
            painter.setBrush(accent)
            painter.drawRoundedRect(QRect(bg_rect.left(), bg_rect.top() + 8, 3, bg_rect.height() - 16), 1.5, 1.5)
        elif hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(hov_bg)
            painter.drawRoundedRect(bg_rect, 7, 7)

        painter.setPen(QPen(sep_col, 1))
        painter.drawLine(rect.left() + 14, rect.bottom(), rect.right() - 14, rect.bottom())

        cb_size = 18
        cb_x = rect.left() + 18
        cb_y = rect.center().y() - cb_size // 2
        cb_rect = QRect(cb_x, cb_y, cb_size, cb_size)
        if checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#16a580"))
            painter.drawRoundedRect(cb_rect, 4, 4)
            check_path = QPainterPath()
            check_path.moveTo(cb_x + 4.3, cb_y + 9.2)
            check_path.lineTo(cb_x + 7.6, cb_y + 12.6)
            check_path.lineTo(cb_x + 13.8, cb_y + 5.6)
            pen = QPen(QColor("#ffffff"), 2.1, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(check_path)
        else:
            painter.setPen(QPen(cb_border, 1.4))
            painter.setBrush(cb_bg)
            painter.drawRoundedRect(cb_rect, 4, 4)

        content_left = cb_x + cb_size + 14
        content_right = rect.right() - 16
        total_w = max(200, content_right - content_left)
        time_w = 132
        map_w = 165
        ship_w = max(120, total_w - time_w - map_w - 16)

        ship_font = QFont(option.font)
        ship_font.setPointSize(10)
        ship_font.setBold(True)
        painter.setFont(ship_font)
        painter.setPen(ship_col)

        is_dropped = item.source == "手动拖入"
        tag_txt = tr(self.lang, "tag_drop") if is_dropped else ""
        tag_w = 42 if is_dropped else 0
        fm_ship = painter.fontMetrics()
        elided_ship = fm_ship.elidedText(item.ship_display, Qt.TextElideMode.ElideRight, max(40, ship_w - tag_w))
        painter.drawText(
            QRect(content_left, rect.top(), ship_w - tag_w, rect.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            elided_ship,
        )

        if is_dropped:
            sw = fm_ship.horizontalAdvance(elided_ship)
            tx = content_left + sw + 8
            ty = rect.center().y() - 9
            tag_rect = QRect(tx, ty, 36, 18)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(235, 175, 45, 42))
            painter.drawRoundedRect(tag_rect, 4, 4)
            tag_font = QFont(option.font)
            tag_font.setPointSize(8)
            tag_font.setBold(True)
            painter.setFont(tag_font)
            painter.setPen(QColor("#d98a00") if not self.dark_theme else QColor("#ffd166"))
            painter.drawText(tag_rect, Qt.AlignmentFlag.AlignCenter, tag_txt)

        map_font = QFont(option.font)
        map_font.setPointSize(9)
        painter.setFont(map_font)
        painter.setPen(map_col)
        map_x = content_left + ship_w + 8
        elided_map = painter.fontMetrics().elidedText(item.map_display, Qt.TextElideMode.ElideRight, map_w)
        painter.drawText(
            QRect(map_x, rect.top(), map_w, rect.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            elided_map,
        )

        time_font = QFont(option.font)
        time_font.setPointSize(9)
        painter.setFont(time_font)
        painter.setPen(time_col)
        short_time = item.battle_time_str[:16] if len(item.battle_time_str) >= 16 else item.battle_time_str
        painter.drawText(
            QRect(content_right - time_w, rect.top(), time_w, rect.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
            short_time,
        )

        painter.restore()


class AutoSyncWorker(QThread):
    """后台静默/手动同步俄服客户端数据。"""

    log_signal = Signal(str)
    finished_signal = Signal(bool, str, str)

    def __init__(
        self,
        game_dir: Path,
        data_dir: Path,
        i18n_only: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.game_dir = game_dir
        self.data_dir = data_dir
        self.i18n_only = i18n_only

    def run(self) -> None:
        try:
            mode = "i18n-only" if self.i18n_only else "full"
            self.log_signal.emit(f"[Sync] {self.game_dir} ({mode})...")
            t0 = time.perf_counter()
            res = sync_client_data(
                game_dir=self.game_dir,
                data_dir=self.data_dir,
                i18n_only=self.i18n_only,
            )
            dt = time.perf_counter() - t0
            msg = f"v{res.version} ({dt:.1f}s): {res.ship_count} ships, {res.map_count} maps"
            self.log_signal.emit(f"[Sync] Done: {msg}")
            self.finished_signal.emit(True, res.version, msg)
        except Exception as exc:
            self.log_signal.emit(f"[Error] Sync failed: {exc}")
            self.finished_signal.emit(False, "", str(exc))


class BatchRenderWorker(QThread):
    """后台多线程批量渲染（支持四语 I18N 渲染器输出）。"""

    log_signal = Signal(str)
    progress_signal = Signal(int, str, str)
    finished_signal = Signal(str, int, int, bool, list)

    def __init__(
        self,
        items: list[ReplayListItem],
        render_config: RenderConfig,
        data_dir: Path,
        output_root: Path,
        selected_game_dir: Path | None,
        export_keyframes: bool = False,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.items = items
        self.render_config = render_config
        self.lang = normalize_lang(render_config.language)
        self.data_dir = data_dir
        self.output_root = output_root
        self.selected_game_dir = selected_game_dir
        self.export_keyframes = export_keyframes
        self._cancelled = False

    def request_cancel(self) -> None:
        self._cancelled = True

    def _ensure_synced(self, replay_path: Path) -> None:
        synced = set(list_synced_versions(self.data_dir))
        if not self.selected_game_dir or not self.selected_game_dir.is_dir():
            return
        if not synced:
            self.progress_signal.emit(
                0,
                tr(self.lang, "prog_first_sync"),
                tr(self.lang, "prog_first_sync_sub"),
            )
            self.log_signal.emit(f"[AutoSync] {self.selected_game_dir}")
            sync_client_data(game_dir=self.selected_game_dir, data_dir=self.data_dir)
            return
        try:
            meta = read_replay_meta_only(replay_path)
            raw_ver = str(meta.get("clientVersionFromExe") or meta.get("clientVersionFromXml") or "")
            if not raw_ver:
                return
            short_ver = normalize_short_version(raw_ver)
            if short_ver in synced:
                return
            cinfo = locate_client(self.selected_game_dir)
            if cinfo.short_version == short_ver:
                self.progress_signal.emit(
                    0,
                    tr(self.lang, "prog_ver_sync", ver=short_ver),
                    tr(self.lang, "prog_wait"),
                )
                self.log_signal.emit(f"[AutoSync] v{short_ver}")
                sync_client_data(game_dir=self.selected_game_dir, data_dir=self.data_dir)
        except Exception:
            pass

    def run(self) -> None:
        batch_dir = create_batch_output_dir(self.output_root)
        total_items = len(self.items)
        ok_count = 0
        fail_count = 0
        errors: list[str] = []
        out_w, out_h = self.render_config.resolved_output_size()

        self.log_signal.emit(
            f"=== Batch ({total_items} | {out_w}x{out_h} @ {self.render_config.fps}fps | lang={self.lang}) -> {batch_dir} ==="
        )

        for idx, item in enumerate(self.items, start=1):
            if self._cancelled:
                self.log_signal.emit("[Stop] Cancelled by user.")
                break

            title = f"[{idx}/{total_items}] {item.ship_display} · {item.map_display}"
            base_pct = int(((idx - 1) / total_items) * 1000)
            self.progress_signal.emit(base_pct, title, tr(self.lang, "prog_parsing"))
            self.log_signal.emit(f"{title} — {item.filename}")

            try:
                self._ensure_synced(item.path)
                t0 = time.perf_counter()
                timeline, store = parse_replay(item.path, data_dir=self.data_dir)

                out_mp4 = batch_dir / f"{item.path.stem}.mp4"
                dup = 2
                while out_mp4.exists():
                    out_mp4 = batch_dir / f"{item.path.stem}_{dup}.mp4"
                    dup += 1

                kf_dir = (batch_dir / f"{out_mp4.stem}_keyframes") if self.export_keyframes else None

                def _on_frame(done: int, total: int, _idx: int = idx, _title: str = title) -> None:
                    frac = done / max(1, total)
                    overall = int(((_idx - 1 + frac) / total_items) * 1000)
                    self.progress_signal.emit(
                        overall,
                        _title,
                        tr(self.lang, "prog_rendering", pct=int(frac * 100), done=done, total=total),
                    )

                res = render_timeline_to_video(
                    timeline=timeline,
                    store=store,
                    output_mp4=out_mp4,
                    config=self.render_config,
                    keyframes_dir=kf_dir,
                    progress_callback=_on_frame,
                    cancel_check=lambda: self._cancelled,
                )
                dt = time.perf_counter() - t0
                ok_count += 1
                mb = res.video_path.stat().st_size / (1024 * 1024)
                self.log_signal.emit(f"  OK ({dt:.1f}s, {mb:.2f} MB) -> {res.video_path.name}")
            except RenderCancelledError:
                self.log_signal.emit(f"  STOPPED: {item.filename}")
                break
            except Exception as exc:
                fail_count += 1
                err = f"{item.ship_display} ({item.filename}): {exc}"
                errors.append(err)
                self.log_signal.emit(f"  FAILED: {err}")

        try:
            if ok_count == 0 and batch_dir.is_dir() and not any(batch_dir.iterdir()):
                batch_dir.rmdir()
        except OSError:
            pass

        self.finished_signal.emit(str(batch_dir), ok_count, fail_count, self._cancelled, errors)


class MainWindow(QMainWindow):
    """四标签页多语言主窗口（支持跟随系统主题与四语实时切换）。"""

    def __init__(
        self,
        data_dir: Path = DEFAULT_DATA_DIR,
        enable_auto_sync: bool = True,
        lang_override: str | None = None,
        theme_override: str | None = None,
    ) -> None:
        super().__init__()
        self.data_dir = Path(data_dir)
        self.enable_auto_sync = enable_auto_sync
        self.settings_path = self.data_dir / "gui_settings.json"
        self.settings = self._load_settings()

        self.discovered_clients: list[DiscoveredClient] = []
        self.custom_game_dirs: list[str] = list(self.settings.get("custom_game_dirs") or [])
        self.game_replays: list[ReplayListItem] = []
        self.dropped_replays: list[ReplayListItem] = []
        self.checked_paths: set[str] = set()
        self.last_batch_dir: Path | None = None

        self._store_cache: VersionDataStore | None = None
        self._live_loc_cache: dict[str, LocalizationBundle] = {}
        self._sync_worker: AutoSyncWorker | None = None
        self._render_worker: BatchRenderWorker | None = None
        self._updating_res = False
        self._fps_buttons: dict[int, QPushButton] = {}
        self._sync_status_state: tuple[str, str] = ("idle", "")

        # 语言与主题模式（默认：跟随系统主题 + 跟随系统语言，非目标语言回退英语）
        saved_lang_mode = str(lang_override or self.settings.get("language_mode") or "system")
        if saved_lang_mode == "system":
            self.language_mode = "system"
            self.lang = detect_system_language()
        else:
            self.language_mode = normalize_lang(saved_lang_mode)
            self.lang = self.language_mode

        saved_theme = str(theme_override or self.settings.get("theme_mode") or "system")
        if saved_theme not in ("system", "dark", "light"):
            saved_theme = "system"
        self.theme_mode = saved_theme
        self._dark_theme = self._resolve_is_dark(self.theme_mode)

        self.setWindowTitle("WoWs-Mini-Relpay-Ru")
        self.resize(1020, 680)
        self.setMinimumSize(860, 580)
        self.setAcceptDrops(True)

        self._build_ui()
        self._apply_saved_settings()
        self._apply_theme_mode(self.theme_mode)
        self._retranslate_ui()
        self._reload_store_cache()
        self._refresh_clients(select_path=self.settings.get("last_game_dir"))

        # 实时监听操作系统深浅色切换（当选择「跟随系统」时立即响应）
        try:
            hints = QGuiApplication.styleHints()
            if hints is not None:
                hints.colorSchemeChanged.connect(self._on_system_color_scheme_changed)
        except Exception:
            pass

    def _t(self, key: str, **kwargs: Any) -> str:
        return tr(self.lang, key, **kwargs)

    # =========================================================================
    # 配置持久化、系统颜色监听与语言切换
    # =========================================================================

    def _load_settings(self) -> dict[str, Any]:
        if self.settings_path.is_file():
            try:
                return json.loads(self.settings_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_settings(self) -> None:
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            cur = self._current_game_dir()
            payload = {
                "language_mode": self.language_mode,
                "theme_mode": self.theme_mode,
                "last_game_dir": str(cur) if cur else "",
                "custom_game_dirs": self.custom_game_dirs[-8:],
                "output_root": self.output_dir_edit.text().strip() or str(DEFAULT_OUTPUT_DIR),
                "auto_open_folder": self.chk_auto_open.isChecked(),
                "width": self.width_spin.value(),
                "height": self.height_spin.value(),
                "fps": self.fps_spin.value(),
                "show_smoke": self.chk_smoke.isChecked(),
                "show_unspotted_names": self.chk_unspotted.isChecked(),
                "show_spotted_names": self.chk_spotted.isChecked(),
                "show_mini_hp_bars": self.chk_mini_hp.isChecked(),
                "show_chat": self.chk_chat.isChecked(),
                "show_shells": self.chk_shells.isChecked(),
                "show_torpedoes": self.chk_torps.isChecked(),
                "show_planes": self.chk_planes.isChecked(),
                "show_consumable_rings": self.chk_rings.isChecked(),
                "show_dead_markers": self.chk_dead.isChecked(),
                "show_grid": self.chk_grid.isChecked(),
                "export_keyframes": self.chk_keyframes.isChecked(),
                "end_banner_seconds": self.banner_sec_spin.value(),
            }
            self.settings_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def closeEvent(self, event: QEvent) -> None:
        self._save_settings()
        if self._render_worker is not None and self._render_worker.isRunning():
            self._render_worker.request_cancel()
            self._render_worker.wait(5000)
        if self._sync_worker is not None and self._sync_worker.isRunning():
            self._sync_worker.wait(10000)
        super().closeEvent(event)

    def _resolve_is_dark(self, mode: str) -> bool:
        if mode == "dark":
            return True
        if mode == "light":
            return False
        return detect_system_dark_mode()

    def _on_system_color_scheme_changed(self, _scheme: Any = None) -> None:
        if self.theme_mode == "system":
            self._apply_theme_mode("system")

    def _apply_theme_mode(self, mode: str) -> None:
        if mode not in ("system", "dark", "light"):
            mode = "system"
        self.theme_mode = mode
        dark = self._resolve_is_dark(mode)
        self._dark_theme = dark

        app = QApplication.instance()
        if app is not None:
            pal = QPalette()
            if dark:
                pal.setColor(QPalette.ColorRole.Window, QColor("#101319"))
                pal.setColor(QPalette.ColorRole.WindowText, QColor("#edf0f5"))
                pal.setColor(QPalette.ColorRole.Base, QColor("#0e1117"))
                pal.setColor(QPalette.ColorRole.Text, QColor("#edf0f5"))
                pal.setColor(QPalette.ColorRole.Button, QColor("#1d2330"))
                pal.setColor(QPalette.ColorRole.ButtonText, QColor("#edf0f5"))
            else:
                pal.setColor(QPalette.ColorRole.Window, QColor("#f3f5f8"))
                pal.setColor(QPalette.ColorRole.WindowText, QColor("#1b2230"))
                pal.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
                pal.setColor(QPalette.ColorRole.Text, QColor("#1b2230"))
                pal.setColor(QPalette.ColorRole.Button, QColor("#eef1f6"))
                pal.setColor(QPalette.ColorRole.ButtonText, QColor("#1b2230"))
            app.setPalette(pal)
            app.setStyleSheet(_build_qss(dark))

        self.row_delegate.dark_theme = dark
        self.replay_list.viewport().update()
        for chk in self.findChildren(SmoothCheckBox):
            chk.dark_theme = dark
            chk.update()

        for rb, target_mode in (
            (self.rb_system, "system"),
            (self.rb_dark, "dark"),
            (self.rb_light, "light"),
        ):
            rb.blockSignals(True)
            rb.setChecked(mode == target_mode)
            rb.blockSignals(False)

    def _on_theme_selected(self, mode: str) -> None:
        self._apply_theme_mode(mode)
        self._save_settings()

    def _on_language_changed(self, idx: int) -> None:
        if idx < 0:
            return
        mode = str(self.lang_combo.itemData(idx) or "system")
        new_lang = detect_system_language() if mode == "system" else normalize_lang(mode)
        if mode == self.language_mode and new_lang == self.lang:
            return
        self.language_mode = mode
        self.lang = new_lang
        self.row_delegate.lang = self.lang
        self._retranslate_ui()
        # 刷新客户端下拉文案与回放列表语言（船名/舰种/地图名同步切换）
        cur = self._current_game_dir()
        self._populate_client_combo(select_path=str(cur) if cur else None)
        self._scan_replays()
        self._update_settings_client_summary()
        self._save_settings()

    # =========================================================================
    # 四标签页 UI 搭建（回放 | 高级 | 设置 | 关于）
    # =========================================================================

    def _build_ui(self) -> None:
        root = QWidget(self)
        root.setObjectName("RootWidget")
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(0)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_replays_tab(), "")
        self.tabs.addTab(self._build_advanced_tab(), "")
        self.tabs.addTab(self._build_settings_tab(), "")
        self.tabs.addTab(self._build_about_tab(), "")

        corner = QWidget()
        c_lay = QHBoxLayout(corner)
        c_lay.setContentsMargins(0, 0, 6, 4)
        self.lbl_sync_pill = QLabel("")
        self.lbl_sync_pill.setStyleSheet("color: #22c59b; font-size: 12px; font-weight: 500;")
        c_lay.addWidget(self.lbl_sync_pill)
        self.tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)

        outer.addWidget(self.tabs, 1)

    def _build_replays_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("TabPage")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(12)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        self.lbl_game_dir = QLabel()
        self.lbl_game_dir.setProperty("class", "FieldLabel")
        top_row.addWidget(self.lbl_game_dir)

        self.client_combo = QComboBox()
        self.client_combo.currentIndexChanged.connect(self._on_client_changed)
        top_row.addWidget(self.client_combo, 1)

        self.btn_browse_game = QPushButton()
        self.btn_browse_game.clicked.connect(self._on_browse_game_dir)
        top_row.addWidget(self.btn_browse_game)
        lay.addLayout(top_row)

        body = QHBoxLayout()
        body.setSpacing(14)

        self.list_panel = QFrame()
        self.list_panel.setObjectName("ListPanel")
        self.list_panel.setProperty("dragActive", False)
        lp_lay = QVBoxLayout(self.list_panel)
        lp_lay.setContentsMargins(14, 12, 14, 10)
        lp_lay.setSpacing(10)

        tb = QHBoxLayout()
        tb.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.textChanged.connect(self._render_list)
        tb.addWidget(self.search_edit, 1)

        self.btn_toggle_all = QPushButton()
        self.btn_toggle_all.setMinimumWidth(74)
        self.btn_toggle_all.clicked.connect(self._on_toggle_select_all)
        tb.addWidget(self.btn_toggle_all)

        self.btn_import = QPushButton()
        self.btn_import.clicked.connect(self._on_add_external_replays)
        tb.addWidget(self.btn_import)
        lp_lay.addLayout(tb)

        col_hdr = QHBoxLayout()
        col_hdr.setContentsMargins(50, 2, 24, 0)
        col_hdr.setSpacing(8)
        self.lbl_h_ship = QLabel()
        self.lbl_h_ship.setProperty("class", "Muted")
        self.lbl_h_map = QLabel()
        self.lbl_h_map.setProperty("class", "Muted")
        self.lbl_h_map.setFixedWidth(165)
        self.lbl_h_time = QLabel()
        self.lbl_h_time.setProperty("class", "Muted")
        self.lbl_h_time.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.lbl_h_time.setFixedWidth(132)
        col_hdr.addWidget(self.lbl_h_ship, 1)
        col_hdr.addWidget(self.lbl_h_map)
        col_hdr.addWidget(self.lbl_h_time)
        lp_lay.addLayout(col_hdr)

        self.replay_list = QListWidget()
        self.row_delegate = ReplayRowDelegate(self.replay_list)
        self.row_delegate.lang = self.lang
        self.replay_list.setItemDelegate(self.row_delegate)
        self.replay_list.setMouseTracking(True)
        self.replay_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.replay_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.replay_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.replay_list.itemClicked.connect(self._on_row_clicked)
        lp_lay.addWidget(self.replay_list, 1)

        foot = QHBoxLayout()
        foot.setContentsMargins(4, 2, 4, 0)
        self.lbl_list_footer = QLabel()
        self.lbl_list_footer.setProperty("class", "Muted")
        foot.addWidget(self.lbl_list_footer, 1)

        self.btn_refresh = QPushButton()
        self.btn_refresh.setProperty("class", "LinkBtn")
        self.btn_refresh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_refresh.clicked.connect(self._on_manual_refresh)
        foot.addWidget(self.btn_refresh)
        lp_lay.addLayout(foot)

        body.addWidget(self.list_panel, 1)

        side = QFrame()
        side.setProperty("class", "PanelCard")
        side.setFixedWidth(272)
        s_lay = QVBoxLayout(side)
        s_lay.setContentsMargins(16, 16, 16, 16)
        s_lay.setSpacing(14)

        self.lbl_side_export = QLabel()
        self.lbl_side_export.setProperty("class", "CardTitle")
        s_lay.addWidget(self.lbl_side_export)

        res_block = QVBoxLayout()
        res_block.setSpacing(6)
        self.lbl_res = QLabel()
        self.lbl_res.setProperty("class", "FieldLabel")
        res_block.addWidget(self.lbl_res)

        self.res_combo = QComboBox()
        for _key, _w, _h in RESOLUTION_PRESET_SPECS:
            self.res_combo.addItem("")
        self.res_combo.currentIndexChanged.connect(self._on_res_preset_changed)
        res_block.addWidget(self.res_combo)

        self.custom_res_row = QWidget()
        cr_lay = QHBoxLayout(self.custom_res_row)
        cr_lay.setContentsMargins(0, 4, 0, 0)
        cr_lay.setSpacing(6)
        self.width_spin = QSpinBox()
        self.width_spin.setRange(640, 7680)
        self.width_spin.setSingleStep(16)
        self.width_spin.setValue(1920)
        self.height_spin = QSpinBox()
        self.height_spin.setRange(360, 4320)
        self.height_spin.setSingleStep(8)
        self.height_spin.setValue(1080)
        cr_lay.addWidget(self.width_spin, 1)
        cr_lay.addWidget(QLabel("×"))
        cr_lay.addWidget(self.height_spin, 1)
        self.custom_res_row.setVisible(False)
        res_block.addWidget(self.custom_res_row)
        s_lay.addLayout(res_block)

        fps_block = QVBoxLayout()
        fps_block.setSpacing(6)
        self.lbl_fps = QLabel()
        self.lbl_fps.setProperty("class", "FieldLabel")
        fps_block.addWidget(self.lbl_fps)

        fps_row = QHBoxLayout()
        fps_row.setSpacing(6)
        for val in (20, 30, 60):
            btn = QPushButton(f"{val}")
            btn.setProperty("class", "FpsPill")
            btn.setProperty("active", val == 30)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, v=val: self._set_fps(v))
            fps_row.addWidget(btn, 1)
            self._fps_buttons[val] = btn

        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(5, 120)
        self.fps_spin.setValue(30)
        self.fps_spin.setFixedWidth(70)
        self.fps_spin.valueChanged.connect(self._sync_fps_pills)
        fps_row.addWidget(self.fps_spin)
        fps_block.addLayout(fps_row)
        s_lay.addLayout(fps_block)

        self.progress_box = QWidget()
        pb_lay = QVBoxLayout(self.progress_box)
        pb_lay.setContentsMargins(0, 6, 0, 0)
        pb_lay.setSpacing(6)
        self.lbl_prog_title = QLabel("")
        self.lbl_prog_title.setStyleSheet("font-size: 12.5px; font-weight: bold;")
        self.lbl_prog_title.setWordWrap(True)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.lbl_prog_sub = QLabel("")
        self.lbl_prog_sub.setProperty("class", "Muted")
        pb_lay.addWidget(self.lbl_prog_title)
        pb_lay.addWidget(self.progress_bar)
        pb_lay.addWidget(self.lbl_prog_sub)
        self.progress_box.setVisible(False)
        s_lay.addWidget(self.progress_box)

        s_lay.addStretch(1)

        self.lbl_sel_summary = QLabel()
        self.lbl_sel_summary.setProperty("class", "Muted")
        self.lbl_sel_summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s_lay.addWidget(self.lbl_sel_summary)

        self.btn_render = QPushButton()
        self.btn_render.setObjectName("PrimaryBtn")
        self.btn_render.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_render.clicked.connect(self._on_start_batch_render)
        s_lay.addWidget(self.btn_render)

        self.btn_stop = QPushButton()
        self.btn_stop.setObjectName("StopBtn")
        self.btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop.setVisible(False)
        self.btn_stop.clicked.connect(self._on_stop_render)
        s_lay.addWidget(self.btn_stop)

        self.btn_open_out = QPushButton()
        self.btn_open_out.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_out.clicked.connect(self._on_open_output_dir)
        s_lay.addWidget(self.btn_open_out)

        self.lbl_auto_tip = QLabel()
        self.lbl_auto_tip.setProperty("class", "Muted")
        self.lbl_auto_tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_auto_tip.setWordWrap(True)
        s_lay.addWidget(self.lbl_auto_tip)

        body.addWidget(side)
        lay.addLayout(body, 1)
        return page

    def _build_advanced_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("ScrollInner")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(14)

        layer_card = QFrame()
        layer_card.setProperty("class", "GroupCard")
        lc_lay = QVBoxLayout(layer_card)
        lc_lay.setContentsMargins(16, 14, 16, 14)
        lc_lay.setSpacing(10)

        hdr = QHBoxLayout()
        self.lbl_adv_layers = QLabel()
        self.lbl_adv_layers.setProperty("class", "CardTitle")
        hdr.addWidget(self.lbl_adv_layers)
        hdr.addStretch(1)
        self.btn_reset_adv = QPushButton()
        self.btn_reset_adv.clicked.connect(self._reset_advanced_defaults)
        hdr.addWidget(self.btn_reset_adv)
        lc_lay.addLayout(hdr)

        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(8)
        self.chk_smoke = SmoothCheckBox()
        self.chk_unspotted = SmoothCheckBox()
        self.chk_spotted = SmoothCheckBox()
        self.chk_mini_hp = SmoothCheckBox()
        self.chk_chat = SmoothCheckBox()
        self.chk_shells = SmoothCheckBox()
        self.chk_torps = SmoothCheckBox()
        self.chk_planes = SmoothCheckBox()
        self.chk_rings = SmoothCheckBox()
        self.chk_dead = SmoothCheckBox()
        self.chk_grid = SmoothCheckBox()

        for i, chk in enumerate(
            [
                self.chk_smoke, self.chk_unspotted, self.chk_spotted,
                self.chk_mini_hp, self.chk_chat, self.chk_shells,
                self.chk_torps, self.chk_planes, self.chk_rings,
                self.chk_dead, self.chk_grid,
            ]
        ):
            chk.setChecked(True)
            grid.addWidget(chk, i // 3, i % 3)
        lc_lay.addLayout(grid)
        lay.addWidget(layer_card)

        ext_card = QFrame()
        ext_card.setProperty("class", "GroupCard")
        ec_lay = QVBoxLayout(ext_card)
        ec_lay.setContentsMargins(16, 14, 16, 14)
        ec_lay.setSpacing(10)

        self.lbl_adv_extra = QLabel()
        self.lbl_adv_extra.setProperty("class", "CardTitle")
        ec_lay.addWidget(self.lbl_adv_extra)

        ext_row = QHBoxLayout()
        ext_row.setSpacing(20)
        self.chk_keyframes = SmoothCheckBox()
        self.chk_keyframes.setChecked(False)
        ext_row.addWidget(self.chk_keyframes, 1)

        self.lbl_banner_sec = QLabel()
        ext_row.addWidget(self.lbl_banner_sec)
        self.banner_sec_spin = QDoubleSpinBox()
        self.banner_sec_spin.setRange(0.0, 6.0)
        self.banner_sec_spin.setSingleStep(0.5)
        self.banner_sec_spin.setDecimals(1)
        self.banner_sec_spin.setValue(2.5)
        self.banner_sec_spin.setFixedWidth(92)
        ext_row.addWidget(self.banner_sec_spin)
        ec_lay.addLayout(ext_row)
        lay.addWidget(ext_card)

        log_card = QFrame()
        log_card.setProperty("class", "GroupCard")
        lg_lay = QVBoxLayout(log_card)
        lg_lay.setContentsMargins(16, 14, 16, 14)
        lg_lay.setSpacing(8)

        lhdr = QHBoxLayout()
        self.lbl_adv_log = QLabel()
        self.lbl_adv_log.setProperty("class", "CardTitle")
        lhdr.addWidget(self.lbl_adv_log)
        lhdr.addStretch(1)
        self.btn_clear_log = QPushButton()
        self.btn_clear_log.clicked.connect(lambda: self.log_box.clear())
        lhdr.addWidget(self.btn_clear_log)
        lg_lay.addLayout(lhdr)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(180)
        lg_lay.addWidget(self.log_box, 1)
        lay.addWidget(log_card, 1)

        scroll.setWidget(inner)
        return scroll

    def _build_settings_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner.setObjectName("ScrollInner")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(14)

        # 1. 外观与语言
        card_app = QFrame()
        card_app.setProperty("class", "GroupCard")
        a_lay = QVBoxLayout(card_app)
        a_lay.setContentsMargins(16, 14, 16, 14)
        a_lay.setSpacing(12)
        self.lbl_set_appearance = QLabel()
        self.lbl_set_appearance.setProperty("class", "CardTitle")
        a_lay.addWidget(self.lbl_set_appearance)

        lang_row = QHBoxLayout()
        lang_row.setSpacing(12)
        self.lbl_language = QLabel()
        lang_row.addWidget(self.lbl_language)
        self.lang_combo = QComboBox()
        self.lang_combo.setMinimumWidth(230)
        self.lang_combo.addItem("", userData="system")
        for code, native_name in SUPPORTED_LANGUAGES:
            self.lang_combo.addItem(native_name, userData=code)
        lang_idx = 0
        for i in range(self.lang_combo.count()):
            if str(self.lang_combo.itemData(i)) == self.language_mode:
                lang_idx = i
                break
        self.lang_combo.setCurrentIndex(lang_idx)
        self.lang_combo.currentIndexChanged.connect(self._on_language_changed)
        lang_row.addWidget(self.lang_combo)
        lang_row.addStretch(1)
        a_lay.addLayout(lang_row)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(18)
        self.lbl_theme = QLabel()
        theme_row.addWidget(self.lbl_theme)
        self.rb_system = QRadioButton()
        self.rb_dark = QRadioButton()
        self.rb_light = QRadioButton()
        grp = QButtonGroup(self)
        grp.addButton(self.rb_system)
        grp.addButton(self.rb_dark)
        grp.addButton(self.rb_light)
        self.rb_system.toggled.connect(lambda checked: self._on_theme_selected("system") if checked else None)
        self.rb_dark.toggled.connect(lambda checked: self._on_theme_selected("dark") if checked else None)
        self.rb_light.toggled.connect(lambda checked: self._on_theme_selected("light") if checked else None)
        theme_row.addWidget(self.rb_system)
        theme_row.addWidget(self.rb_dark)
        theme_row.addWidget(self.rb_light)
        theme_row.addStretch(1)
        a_lay.addLayout(theme_row)
        lay.addWidget(card_app)

        # 2. 客户端与汉化数据
        card_cli = QFrame()
        card_cli.setProperty("class", "GroupCard")
        c_lay = QVBoxLayout(card_cli)
        c_lay.setContentsMargins(16, 14, 16, 14)
        c_lay.setSpacing(10)
        self.lbl_set_client = QLabel()
        self.lbl_set_client.setProperty("class", "CardTitle")
        c_lay.addWidget(self.lbl_set_client)

        self.lbl_settings_client_info = QLabel()
        self.lbl_settings_client_info.setProperty("class", "Muted")
        self.lbl_settings_client_info.setWordWrap(True)
        c_lay.addWidget(self.lbl_settings_client_info)

        c_btns = QHBoxLayout()
        c_btns.setSpacing(10)
        self.btn_rescan_clients = QPushButton()
        self.btn_rescan_clients.clicked.connect(self._on_manual_refresh)
        self.btn_sync_i18n = QPushButton()
        self.btn_sync_i18n.clicked.connect(lambda: self._start_background_sync(i18n_only=True))
        self.btn_sync_full = QPushButton()
        self.btn_sync_full.clicked.connect(lambda: self._start_background_sync(i18n_only=False))
        c_btns.addWidget(self.btn_rescan_clients)
        c_btns.addWidget(self.btn_sync_i18n)
        c_btns.addWidget(self.btn_sync_full)
        c_btns.addStretch(1)
        c_lay.addLayout(c_btns)
        lay.addWidget(card_cli)

        # 3. 文件与缓存
        card_file = QFrame()
        card_file.setProperty("class", "GroupCard")
        f_lay = QVBoxLayout(card_file)
        f_lay.setContentsMargins(16, 14, 16, 14)
        f_lay.setSpacing(10)
        self.lbl_set_files = QLabel()
        self.lbl_set_files.setProperty("class", "CardTitle")
        f_lay.addWidget(self.lbl_set_files)

        out_row = QHBoxLayout()
        out_row.setSpacing(8)
        self.lbl_output_dir = QLabel()
        out_row.addWidget(self.lbl_output_dir)
        self.output_dir_edit = QLineEdit(str(DEFAULT_OUTPUT_DIR))
        out_row.addWidget(self.output_dir_edit, 1)
        self.btn_chg_out = QPushButton()
        self.btn_chg_out.clicked.connect(self._on_browse_output_dir)
        self.btn_open_out2 = QPushButton()
        self.btn_open_out2.clicked.connect(self._on_open_output_dir)
        out_row.addWidget(self.btn_chg_out)
        out_row.addWidget(self.btn_open_out2)
        f_lay.addLayout(out_row)

        self.chk_auto_open = SmoothCheckBox()
        self.chk_auto_open.setChecked(True)
        f_lay.addWidget(self.chk_auto_open)

        cache_row = QHBoxLayout()
        cache_row.setSpacing(8)
        self.lbl_data_dir = QLabel()
        cache_row.addWidget(self.lbl_data_dir)
        lbl_data_path = QLineEdit(str(self.data_dir))
        lbl_data_path.setReadOnly(True)
        cache_row.addWidget(lbl_data_path, 1)
        self.btn_open_data = QPushButton()
        self.btn_open_data.clicked.connect(lambda: self._open_ensure_dir(self.data_dir))
        self.btn_clear_cache = QPushButton()
        self.btn_clear_cache.clicked.connect(self._on_clear_version_cache)
        cache_row.addWidget(self.btn_open_data)
        cache_row.addWidget(self.btn_clear_cache)
        f_lay.addLayout(cache_row)
        lay.addWidget(card_file)

        lay.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    def _build_about_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("TabPage")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(24, 36, 24, 24)
        lay.setSpacing(12)
        lay.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)

        logo = AboutLogoBadge()
        lay.addWidget(logo, 0, Qt.AlignmentFlag.AlignHCenter)

        name = QLabel("WoWs-Mini-Relpay-Ru")
        name.setStyleSheet("font-size: 20px; font-weight: bold;")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(name)

        self.lbl_about_desc = QLabel()
        self.lbl_about_desc.setProperty("class", "FieldLabel")
        self.lbl_about_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_about_desc)

        self.lbl_about_meta = QLabel()
        self.lbl_about_meta.setProperty("class", "Muted")
        self.lbl_about_meta.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_about_meta)

        lay.addSpacing(10)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)
        self.btn_visit_gh = QPushButton()
        self.btn_visit_gh.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_visit_gh.clicked.connect(lambda: webbrowser.open(REPO_URL))
        self.btn_open_app = QPushButton()
        self.btn_open_app.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_app.clicked.connect(lambda: _open_folder_in_explorer(PROJECT_ROOT))
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_visit_gh)
        btn_row.addWidget(self.btn_open_app)
        btn_row.addStretch(1)
        lay.addLayout(btn_row)

        url_lbl = QLabel(f'<a href="{REPO_URL}" style="color:#16a580; text-decoration:none;">{REPO_URL}</a>')
        url_lbl.setOpenExternalLinks(True)
        url_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(url_lbl)

        lay.addStretch(1)

        self.lbl_about_credit = QLabel()
        self.lbl_about_credit.setProperty("class", "Muted")
        self.lbl_about_credit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.lbl_about_credit)
        return page

    # =========================================================================
    # 动态四语文案刷新
    # =========================================================================

    def _refresh_sync_pill_text(self) -> None:
        kind, ver = self._sync_status_state
        if kind == "ready":
            self.lbl_sync_pill.setStyleSheet("color: #16a580; font-size: 12px; font-weight: 500;")
            self.lbl_sync_pill.setText(self._t("status_ready", ver=ver))
        elif kind == "syncing":
            self.lbl_sync_pill.setStyleSheet("color: #d99516; font-size: 12px; font-weight: 500;")
            self.lbl_sync_pill.setText(self._t("status_syncing"))
        elif kind == "wait":
            self.lbl_sync_pill.setStyleSheet("color: #d99516; font-size: 12px; font-weight: 500;")
            self.lbl_sync_pill.setText(self._t("status_wait_sync", ver=ver))
        elif kind == "fail":
            self.lbl_sync_pill.setStyleSheet("color: #d94c4c; font-size: 12px; font-weight: 500;")
            self.lbl_sync_pill.setText(self._t("status_sync_fail"))
        else:
            self.lbl_sync_pill.setText("")

    def _retranslate_ui(self) -> None:
        self.tabs.setTabText(0, self._t("tab_replays"))
        self.tabs.setTabText(1, self._t("tab_advanced"))
        self.tabs.setTabText(2, self._t("tab_settings"))
        self.tabs.setTabText(3, self._t("tab_about"))

        # Tab 1
        self.lbl_game_dir.setText(self._t("label_game_dir"))
        self.btn_browse_game.setText(self._t("btn_choose_dir"))
        self.search_edit.setPlaceholderText(self._t("search_placeholder"))
        self.btn_import.setText(self._t("btn_import_replays"))
        self.lbl_h_ship.setText(self._t("col_ship"))
        self.lbl_h_map.setText(self._t("col_map"))
        self.lbl_h_time.setText(self._t("col_time"))
        self.btn_refresh.setText(self._t("btn_refresh_list"))
        self.lbl_side_export.setText(self._t("card_export"))
        self.lbl_res.setText(self._t("label_resolution"))
        for i, (key, _w, _h) in enumerate(RESOLUTION_PRESET_SPECS):
            self.res_combo.setItemText(i, self._t(key))
        self.lbl_fps.setText(self._t("label_fps"))
        self.btn_stop.setText(self._t("btn_stop_render"))
        self.btn_open_out.setText(self._t("btn_open_output"))
        self.lbl_auto_tip.setText(self._t("tip_auto_folder"))

        # Tab 2
        self.lbl_adv_layers.setText(self._t("adv_layers_title"))
        self.btn_reset_adv.setText(self._t("btn_reset_defaults"))
        self.chk_smoke.setText(self._t("chk_smoke"))
        self.chk_unspotted.setText(self._t("chk_unspotted"))
        self.chk_spotted.setText(self._t("chk_spotted"))
        self.chk_mini_hp.setText(self._t("chk_mini_hp"))
        self.chk_chat.setText(self._t("chk_chat"))
        self.chk_shells.setText(self._t("chk_shells"))
        self.chk_torps.setText(self._t("chk_torps"))
        self.chk_planes.setText(self._t("chk_planes"))
        self.chk_rings.setText(self._t("chk_rings"))
        self.chk_dead.setText(self._t("chk_dead"))
        self.chk_grid.setText(self._t("chk_grid"))
        self.lbl_adv_extra.setText(self._t("adv_extra_title"))
        self.chk_keyframes.setText(self._t("chk_keyframes"))
        self.lbl_banner_sec.setText(self._t("label_banner_sec"))
        self.banner_sec_spin.setSuffix(self._t("suffix_sec"))
        self.lbl_adv_log.setText(self._t("adv_log_title"))
        self.btn_clear_log.setText(self._t("btn_clear_log"))
        self.log_box.setPlaceholderText(self._t("log_placeholder"))

        # Tab 3
        self.lbl_set_appearance.setText(self._t("set_appearance_title"))
        self.lbl_language.setText(self._t("label_language"))
        sys_code = detect_system_language()
        sys_native = dict(SUPPORTED_LANGUAGES).get(sys_code, "English")
        self.lang_combo.setItemText(0, self._t("lang_system", detected=sys_native))
        self.lbl_theme.setText(self._t("label_theme"))
        self.rb_system.setText(self._t("theme_system"))
        self.rb_dark.setText(self._t("theme_dark"))
        self.rb_light.setText(self._t("theme_light"))
        self.lbl_set_client.setText(self._t("set_client_title"))
        self.btn_rescan_clients.setText(self._t("btn_rescan_clients"))
        self.btn_sync_i18n.setText(self._t("btn_sync_i18n"))
        self.btn_sync_full.setText(self._t("btn_sync_full"))
        self.lbl_set_files.setText(self._t("set_files_title"))
        self.lbl_output_dir.setText(self._t("label_output_dir"))
        self.btn_chg_out.setText(self._t("btn_change_dir"))
        self.btn_open_out2.setText(self._t("btn_open_dir"))
        self.chk_auto_open.setText(self._t("chk_auto_open"))
        self.lbl_data_dir.setText(self._t("label_data_dir"))
        self.btn_open_data.setText(self._t("btn_open_dir"))
        self.btn_clear_cache.setText(self._t("btn_clear_cache"))

        # Tab 4
        self.lbl_about_desc.setText(self._t("about_desc"))
        self.btn_visit_gh.setText(self._t("btn_visit_github"))
        self.btn_open_app.setText(self._t("btn_open_app_dir"))
        self.lbl_about_credit.setText(self._t("about_credit"))

        self._refresh_sync_pill_text()
        self._update_counters()

    # =========================================================================
    # 设置读写与交互回调
    # =========================================================================

    def _append_log(self, text: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.appendPlainText(f"[{ts}] {text}")

    def _apply_saved_settings(self) -> None:
        if self.settings.get("output_root"):
            self.output_dir_edit.setText(str(self.settings["output_root"]))
        self.chk_auto_open.setChecked(bool(self.settings.get("auto_open_folder", True)))

        w = int(self.settings.get("width") or 1920)
        h = int(self.settings.get("height") or 1080)
        self._set_resolution_controls(w, h)
        self._set_fps(int(self.settings.get("fps") or 30))

        self.chk_smoke.setChecked(bool(self.settings.get("show_smoke", True)))
        self.chk_unspotted.setChecked(bool(self.settings.get("show_unspotted_names", True)))
        self.chk_spotted.setChecked(bool(self.settings.get("show_spotted_names", True)))
        self.chk_mini_hp.setChecked(bool(self.settings.get("show_mini_hp_bars", True)))
        self.chk_chat.setChecked(bool(self.settings.get("show_chat", True)))
        self.chk_shells.setChecked(bool(self.settings.get("show_shells", True)))
        self.chk_torps.setChecked(bool(self.settings.get("show_torpedoes", True)))
        self.chk_planes.setChecked(bool(self.settings.get("show_planes", True)))
        self.chk_rings.setChecked(bool(self.settings.get("show_consumable_rings", True)))
        self.chk_dead.setChecked(bool(self.settings.get("show_dead_markers", True)))
        self.chk_grid.setChecked(bool(self.settings.get("show_grid", True)))
        self.chk_keyframes.setChecked(bool(self.settings.get("export_keyframes", False)))
        self.banner_sec_spin.setValue(float(self.settings.get("end_banner_seconds", 2.5)))

    def _reset_advanced_defaults(self) -> None:
        for chk in (
            self.chk_smoke, self.chk_unspotted, self.chk_spotted, self.chk_mini_hp,
            self.chk_chat, self.chk_shells, self.chk_torps, self.chk_planes,
            self.chk_rings, self.chk_dead, self.chk_grid,
        ):
            chk.setChecked(True)
        self.chk_keyframes.setChecked(False)
        self.banner_sec_spin.setValue(2.5)
        self._save_settings()

    def _open_ensure_dir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        _open_folder_in_explorer(path)

    def _on_clear_version_cache(self) -> None:
        vers_dir = self.data_dir / "versions"
        if not vers_dir.is_dir():
            QMessageBox.information(self, self._t("msg_no_cache_title"), self._t("msg_no_cache_body"))
            return
        ans = QMessageBox.question(
            self,
            self._t("msg_confirm_clear_title"),
            self._t("msg_confirm_clear_body", path=vers_dir),
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        shutil.rmtree(vers_dir, ignore_errors=True)
        self._store_cache = None
        self._live_loc_cache.clear()
        self._append_log("[Cache] Cleared version cache.")
        self._on_client_changed()

    def _set_resolution_controls(self, w: int, h: int) -> None:
        self._updating_res = True
        try:
            self.width_spin.setValue(w)
            self.height_spin.setValue(h)
            matched_idx = len(RESOLUTION_PRESET_SPECS) - 1
            for idx, (_key, pw, ph) in enumerate(RESOLUTION_PRESET_SPECS):
                if pw == w and ph == h:
                    matched_idx = idx
                    break
            self.res_combo.setCurrentIndex(matched_idx)
            self.custom_res_row.setVisible(matched_idx == len(RESOLUTION_PRESET_SPECS) - 1)
        finally:
            self._updating_res = False

    def _on_res_preset_changed(self, idx: int) -> None:
        if self._updating_res or idx < 0 or idx >= len(RESOLUTION_PRESET_SPECS):
            return
        _key, pw, ph = RESOLUTION_PRESET_SPECS[idx]
        is_custom = pw == 0 or ph == 0
        self.custom_res_row.setVisible(is_custom)
        if not is_custom:
            self._updating_res = True
            try:
                self.width_spin.setValue(pw)
                self.height_spin.setValue(ph)
            finally:
                self._updating_res = False

    def _set_fps(self, val: int) -> None:
        self.fps_spin.setValue(val)
        self._sync_fps_pills(val)

    def _sync_fps_pills(self, val: int) -> None:
        for pv, btn in self._fps_buttons.items():
            btn.setProperty("active", pv == val)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    # =========================================================================
    # 客户端探测、零等待本地化与后台自动同步
    # =========================================================================

    def _reload_store_cache(self, preferred_version: str | None = None) -> None:
        vers = list_synced_versions(self.data_dir)
        if not vers:
            self._store_cache = None
            return
        target = preferred_version if (preferred_version and preferred_version in vers) else vers[0]
        try:
            self._store_cache = load_version_store(target, data_dir=self.data_dir)
        except Exception:
            self._store_cache = None

    def _get_live_localization(self, game_dir: Path) -> LocalizationBundle | None:
        key = str(game_dir.resolve()).lower() if game_dir.exists() else str(game_dir).lower()
        if key in self._live_loc_cache:
            return self._live_loc_cache[key]
        try:
            info = locate_client(game_dir)
            bundle = load_localization(info)
            self._live_loc_cache[key] = bundle
            return bundle
        except Exception:
            return None

    def _update_settings_client_summary(self) -> None:
        live_cnt = sum(1 for c in self.discovered_clients if c.channel == "live")
        pt_cnt = sum(1 for c in self.discovered_clients if c.channel == "pt")
        vers = list_synced_versions(self.data_dir)
        ver_txt = ", ".join(f"v{v}" for v in vers) if vers else self._t("not_synced")
        cur = self._current_game_dir()
        self.lbl_settings_client_info.setText(
            self._t(
                "set_client_summary",
                live=live_cnt,
                pt=pt_cnt,
                cur=cur if cur else self._t("not_selected"),
                vers=ver_txt,
            )
        )
        self.lbl_about_meta.setText(self._t("about_meta", app_ver=APP_VERSION, vers=ver_txt))

    def _populate_client_combo(self, select_path: str | None = None) -> None:
        self.client_combo.blockSignals(True)
        self.client_combo.clear()

        for c in self.discovered_clients:
            ch = translate_client_channel(c.channel, self.lang)
            self.client_combo.addItem(f"{ch} · {c.game_dir}  (v{c.short_version})", userData=str(c.game_dir))

        known_lower = {str(c.game_dir).lower() for c in self.discovered_clients}
        custom_lbl = self._t("channel_custom")
        for custom in self.custom_game_dirs:
            p = Path(custom)
            if p.is_dir() and str(p.resolve()).lower() not in known_lower:
                self.client_combo.addItem(f"{custom_lbl} · {p}", userData=str(p))

        if self.client_combo.count() == 0:
            self.client_combo.addItem(self._t("no_client_found"), userData="")

        target_idx = 0
        if select_path:
            low_sel = str(Path(select_path)).lower()
            for i in range(self.client_combo.count()):
                ud = str(self.client_combo.itemData(i) or "").lower()
                if ud and ud == low_sel:
                    target_idx = i
                    break
        self.client_combo.setCurrentIndex(target_idx)
        self.client_combo.blockSignals(False)

    def _refresh_clients(self, select_path: str | None = None) -> None:
        self.discovered_clients = discover_korabli_clients(extra_paths=self.custom_game_dirs)
        self._populate_client_combo(select_path=select_path)
        live_cnt = sum(1 for c in self.discovered_clients if c.channel == "live")
        pt_cnt = sum(1 for c in self.discovered_clients if c.channel == "pt")
        self._append_log(f"[Scan] Found {live_cnt} live, {pt_cnt} PT client(s).")
        self._on_client_changed()

    def _current_game_dir(self) -> Path | None:
        s = str(self.client_combo.currentData() or "").strip()
        return Path(s) if s else None

    def _on_client_changed(self) -> None:
        gdir = self._current_game_dir()
        if gdir is None or not gdir.is_dir():
            self.game_replays = []
            self._sync_status_state = ("idle", "")
            self._refresh_sync_pill_text()
            self._update_settings_client_summary()
            self._render_list()
            return

        synced = set(list_synced_versions(self.data_dir))
        matched = next((c for c in self.discovered_clients if str(c.game_dir).lower() == str(gdir).lower()), None)
        if matched and matched.short_version in synced:
            self._reload_store_cache(preferred_version=matched.short_version)
            self._sync_status_state = ("ready", matched.short_version)
            self._refresh_sync_pill_text()
        elif matched:
            self._reload_store_cache()
            if self.enable_auto_sync:
                self._start_background_sync(i18n_only=False)
            else:
                self._sync_status_state = ("wait", matched.short_version)
                self._refresh_sync_pill_text()
        else:
            self._sync_status_state = ("idle", "")
            self._refresh_sync_pill_text()

        self._update_settings_client_summary()
        self._scan_replays()

    def _start_background_sync(self, i18n_only: bool = False) -> None:
        gdir = self._current_game_dir()
        if gdir is None or not gdir.is_dir():
            return
        if self._sync_worker is not None and self._sync_worker.isRunning():
            return
        if self._render_worker is not None and self._render_worker.isRunning():
            return

        self._sync_status_state = ("syncing", "")
        self._refresh_sync_pill_text()
        self.btn_sync_i18n.setEnabled(False)
        self.btn_sync_full.setEnabled(False)
        self._sync_worker = AutoSyncWorker(gdir, self.data_dir, i18n_only=i18n_only, parent=self)
        self._sync_worker.log_signal.connect(self._append_log)
        self._sync_worker.finished_signal.connect(self._on_sync_finished)
        self._sync_worker.start()

    def _on_sync_finished(self, ok: bool, version: str, detail: str) -> None:
        self.btn_sync_i18n.setEnabled(True)
        self.btn_sync_full.setEnabled(True)
        if ok:
            self._live_loc_cache.clear()
            self._reload_store_cache(preferred_version=version)
            self._sync_status_state = ("ready", version)
            self._refresh_sync_pill_text()
            self._update_settings_client_summary()
            self._scan_replays()
        else:
            self._sync_status_state = ("fail", "")
            self._refresh_sync_pill_text()

    def _on_browse_game_dir(self) -> None:
        cur = self._current_game_dir()
        start = str(cur) if cur else r"D:\Games"
        chosen = QFileDialog.getExistingDirectory(self, self._t("dlg_choose_game_dir"), start)
        if not chosen:
            return
        norm = str(Path(chosen).resolve())
        if norm not in self.custom_game_dirs:
            self.custom_game_dirs.append(norm)
        self._refresh_clients(select_path=norm)
        self._save_settings()

    def _on_manual_refresh(self) -> None:
        cur = self._current_game_dir()
        self._refresh_clients(select_path=str(cur) if cur else None)

    # =========================================================================
    # 回放列表、拖拽与选择
    # =========================================================================

    def _scan_replays(self) -> None:
        gdir = self._current_game_dir()
        loc_bundle: LocalizationBundle | None = None
        if self._store_cache is None and gdir is not None and gdir.is_dir():
            loc_bundle = self._get_live_localization(gdir)

        if gdir is None or not gdir.is_dir():
            self.game_replays = []
        else:
            self.game_replays = scan_replays_in_game_dir(
                gdir, store=self._store_cache, loc_bundle=loc_bundle, lang=self.lang
            )
            if not self.game_replays and not (gdir / "replays").is_dir():
                direct = [
                    parse_replay_item(p, store=self._store_cache, source="游戏目录", loc_bundle=loc_bundle, lang=self.lang)
                    for p in gdir.iterdir()
                    if p.is_file() and p.suffix.lower() == ".korablireplay" and p.name.lower() != "temp.korablireplay"
                ]
                direct.sort(key=lambda x: (x.sort_timestamp, x.filename), reverse=True)
                self.game_replays = direct

        if self.dropped_replays:
            self.dropped_replays = [
                parse_replay_item(it.path, store=self._store_cache, source="手动拖入", loc_bundle=loc_bundle, lang=self.lang)
                for it in self.dropped_replays
                if it.path.is_file()
            ]
        self._render_list()

    def _all_items_merged(self) -> list[ReplayListItem]:
        merged: list[ReplayListItem] = []
        seen: set[str] = set()
        for it in self.dropped_replays + self.game_replays:
            key = str(it.path.resolve()).lower() if it.path.exists() else str(it.path).lower()
            if key not in seen:
                seen.add(key)
                merged.append(it)
        return merged

    def _render_list(self) -> None:
        keyword = self.search_edit.text().strip().lower()
        items = self._all_items_merged()
        if keyword:
            items = [
                it for it in items
                if keyword in it.ship_display.lower()
                or keyword in it.map_display.lower()
                or keyword in it.battle_time_str.lower()
                or keyword in it.filename.lower()
            ]

        self.replay_list.clear()
        for it in items:
            key = str(it.path.resolve()).lower() if it.path.exists() else str(it.path).lower()
            lw_item = QListWidgetItem()
            lw_item.setData(ROLE_PATH_KEY, key)
            lw_item.setData(ROLE_REPLAY_ITEM, it)
            lw_item.setData(ROLE_CHECKED, key in self.checked_paths)
            lw_item.setToolTip(
                self._t("tooltip_item", ship=it.ship_display, map=it.map_display, time=it.battle_time_str, file=it.filename)
            )
            self.replay_list.addItem(lw_item)

        self._update_counters()

    def _on_row_clicked(self, lw_item: QListWidgetItem) -> None:
        if self._render_worker is not None and self._render_worker.isRunning():
            return
        key = str(lw_item.data(ROLE_PATH_KEY) or "")
        if not key:
            return
        now_checked = not bool(lw_item.data(ROLE_CHECKED))
        lw_item.setData(ROLE_CHECKED, now_checked)
        if now_checked:
            self.checked_paths.add(key)
        else:
            self.checked_paths.discard(key)
        self._update_counters()

    def _on_toggle_select_all(self) -> None:
        visible_keys = [
            str(self.replay_list.item(i).data(ROLE_PATH_KEY) or "")
            for i in range(self.replay_list.count())
        ]
        if not visible_keys:
            return
        all_selected = all(k in self.checked_paths for k in visible_keys)
        for i in range(self.replay_list.count()):
            lw_item = self.replay_list.item(i)
            key = str(lw_item.data(ROLE_PATH_KEY) or "")
            if all_selected:
                self.checked_paths.discard(key)
                lw_item.setData(ROLE_CHECKED, False)
            else:
                self.checked_paths.add(key)
                lw_item.setData(ROLE_CHECKED, True)
        self._update_counters()

    def _get_selected_items(self) -> list[ReplayListItem]:
        return [
            it for it in self._all_items_merged()
            if (str(it.path.resolve()).lower() if it.path.exists() else str(it.path).lower()) in self.checked_paths
            and it.path.is_file()
        ]

    def _update_counters(self) -> None:
        total = len(self._all_items_merged())
        sel = len(self._get_selected_items())
        self.lbl_list_footer.setText(self._t("footer_replays", n=total))

        visible_cnt = self.replay_list.count()
        vis_sel = sum(1 for i in range(visible_cnt) if bool(self.replay_list.item(i).data(ROLE_CHECKED)))
        self.btn_toggle_all.setText(
            self._t("btn_unselect_all") if (visible_cnt > 0 and vis_sel == visible_cnt) else self._t("btn_select_all")
        )

        is_rendering = self._render_worker is not None and self._render_worker.isRunning()
        if not is_rendering:
            if sel > 0:
                self.lbl_sel_summary.setText(self._t("sel_hint_count", n=sel))
                self.lbl_sel_summary.setStyleSheet("color: #16a580; font-size: 13px; font-weight: bold;")
                self.btn_render.setText(self._t("btn_start_render_n", n=sel))
                self.btn_render.setEnabled(True)
            else:
                self.lbl_sel_summary.setText(self._t("sel_hint_empty"))
                self.lbl_sel_summary.setStyleSheet("")
                self.btn_render.setText(self._t("btn_start_render"))
                self.btn_render.setEnabled(False)

    def _add_external_paths(self, raw_paths: list[Path | str]) -> None:
        valid = collect_korabli_replays_from_paths(raw_paths)
        if not valid:
            QMessageBox.information(self, self._t("msg_no_replays_title"), self._t("msg_no_replays_body"))
            return

        gdir = self._current_game_dir()
        loc_bundle = self._get_live_localization(gdir) if (self._store_cache is None and gdir and gdir.is_dir()) else None
        existing = {str(it.path.resolve()).lower() for it in self.dropped_replays if it.path.exists()}
        for p in valid:
            key = str(p.resolve()).lower()
            self.checked_paths.add(key)
            if key not in existing:
                self.dropped_replays.insert(
                    0,
                    parse_replay_item(p, store=self._store_cache, source="手动拖入", loc_bundle=loc_bundle, lang=self.lang),
                )
                existing.add(key)
        self.tabs.setCurrentIndex(0)
        self._append_log(f"[Import] Added {len(valid)} .korablireplay file(s).")
        self._render_list()

    def _on_add_external_replays(self) -> None:
        cur = self._current_game_dir()
        files, _ = QFileDialog.getOpenFileNames(
            self,
            self._t("dlg_import_replays"),
            str(cur / "replays") if (cur and (cur / "replays").is_dir()) else str(PROJECT_ROOT),
            self._t("dlg_replay_filter"),
        )
        if files:
            self._add_external_paths(files)

    def _set_drag_highlight(self, active: bool) -> None:
        self.list_panel.setProperty("dragActive", active)
        self.list_panel.style().unpolish(self.list_panel)
        self.list_panel.style().polish(self.list_panel)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()
            self._set_drag_highlight(True)
            return
        event.ignore()

    def dragLeaveEvent(self, event: QEvent) -> None:
        self._set_drag_highlight(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_drag_highlight(False)
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self._add_external_paths(paths)
            event.acceptProposedAction()

    # =========================================================================
    # 批量渲染与输出目录打开
    # =========================================================================

    def _on_browse_output_dir(self) -> None:
        cur = self.output_dir_edit.text().strip() or str(DEFAULT_OUTPUT_DIR)
        chosen = QFileDialog.getExistingDirectory(self, self._t("dlg_choose_output_dir"), cur)
        if chosen:
            self.output_dir_edit.setText(chosen)
            self._save_settings()

    def _on_open_output_dir(self) -> None:
        target = self.last_batch_dir if (self.last_batch_dir and self.last_batch_dir.is_dir()) else Path(
            self.output_dir_edit.text().strip() or str(DEFAULT_OUTPUT_DIR)
        )
        target.mkdir(parents=True, exist_ok=True)
        _open_folder_in_explorer(target)

    def _set_rendering_state(self, rendering: bool) -> None:
        for w in (
            self.client_combo, self.btn_browse_game, self.search_edit,
            self.btn_toggle_all, self.btn_import, self.btn_refresh,
            self.res_combo, self.width_spin, self.height_spin, self.fps_spin,
            self.btn_rescan_clients, self.btn_sync_i18n, self.btn_sync_full,
            self.lang_combo,
            *self._fps_buttons.values(),
        ):
            w.setEnabled(not rendering)
        self.btn_render.setVisible(not rendering)
        self.btn_stop.setVisible(rendering)
        self.btn_stop.setEnabled(rendering)
        if rendering:
            self.progress_box.setVisible(True)
        else:
            self._update_counters()

    def _on_start_batch_render(self) -> None:
        items = self._get_selected_items()
        if not items:
            return
        if not list_synced_versions(self.data_dir) and not self._current_game_dir():
            QMessageBox.warning(self, self._t("msg_no_client_title"), self._t("msg_no_client_body"))
            return

        self._save_settings()
        out_root = Path(self.output_dir_edit.text().strip() or str(DEFAULT_OUTPUT_DIR))
        cfg = RenderConfig(
            fps=self.fps_spin.value(),
            output_width=self.width_spin.value(),
            output_height=self.height_spin.value(),
            language=self.lang,
            end_banner_seconds=self.banner_sec_spin.value(),
            show_smoke=self.chk_smoke.isChecked(),
            show_unspotted_names=self.chk_unspotted.isChecked(),
            show_spotted_names=self.chk_spotted.isChecked(),
            show_mini_hp_bars=self.chk_mini_hp.isChecked(),
            show_chat=self.chk_chat.isChecked(),
            show_shells=self.chk_shells.isChecked(),
            show_torpedoes=self.chk_torps.isChecked(),
            show_planes=self.chk_planes.isChecked(),
            show_consumable_rings=self.chk_rings.isChecked(),
            show_dead_markers=self.chk_dead.isChecked(),
            show_grid=self.chk_grid.isChecked(),
        )
        self.progress_bar.setValue(0)
        self.lbl_prog_title.setText(self._t("prog_preparing", n=len(items)))
        self.lbl_prog_sub.setText(f"{cfg.resolved_output_size()[0]}×{cfg.resolved_output_size()[1]} · {cfg.fps}fps")

        self._render_worker = BatchRenderWorker(
            items=items,
            render_config=cfg,
            data_dir=self.data_dir,
            output_root=out_root,
            selected_game_dir=self._current_game_dir(),
            export_keyframes=self.chk_keyframes.isChecked(),
            parent=self,
        )
        self._render_worker.log_signal.connect(self._append_log)
        self._render_worker.progress_signal.connect(self._on_render_progress)
        self._render_worker.finished_signal.connect(self._on_batch_finished)
        self._set_rendering_state(True)
        self._render_worker.start()

    def _on_stop_render(self) -> None:
        if self._render_worker is not None and self._render_worker.isRunning():
            self._render_worker.request_cancel()
            self.btn_stop.setEnabled(False)
            self.lbl_prog_sub.setText(self._t("prog_stopping"))

    def _on_render_progress(self, pct: int, title: str, sub: str) -> None:
        self.progress_bar.setValue(pct)
        self.lbl_prog_title.setText(title)
        self.lbl_prog_sub.setText(sub)

    def _on_batch_finished(
        self,
        batch_dir_str: str,
        ok_count: int,
        fail_count: int,
        cancelled: bool,
        errors: list[str],
    ) -> None:
        batch_dir = Path(batch_dir_str)
        if ok_count > 0 and batch_dir.is_dir():
            self.last_batch_dir = batch_dir

        self._reload_store_cache()
        self._on_client_changed()
        self._set_rendering_state(False)

        if cancelled:
            self.lbl_prog_title.setText(self._t("prog_stopped", ok=ok_count))
            self.lbl_prog_sub.setText("")
        else:
            self.progress_bar.setValue(1000)
            if fail_count:
                self.lbl_prog_title.setText(self._t("prog_done_fail", ok=ok_count, fail=fail_count))
            else:
                self.lbl_prog_title.setText(self._t("prog_done", ok=ok_count))
            self.lbl_prog_sub.setText(batch_dir.name if ok_count > 0 else "")

        if ok_count > 0 and batch_dir.is_dir() and self.chk_auto_open.isChecked():
            _open_folder_in_explorer(batch_dir)

        if errors:
            QMessageBox.warning(
                self,
                self._t("msg_partial_fail_title", n=len(errors)),
                "\n".join(errors[:6]) + self._t("msg_partial_fail_footer"),
            )


def launch_gui(
    data_dir: Path = DEFAULT_DATA_DIR,
    screenshot_path: Path | None = None,
    screenshot_all_tabs_dir: Path | None = None,
    lang: str | None = None,
    theme: str | None = None,
) -> int:
    """启动四标签页多语言 PySide6 桌面图形界面。"""
    app = QApplication.instance()
    created_app = app is None
    if app is None:
        app = QApplication(sys.argv)

    is_shot = screenshot_path is not None or screenshot_all_tabs_dir is not None
    win = MainWindow(
        data_dir=data_dir,
        enable_auto_sync=(not is_shot),
        lang_override=lang,
        theme_override=theme,
    )
    win.show()

    if is_shot:
        def _capture_and_quit() -> None:
            if win.replay_list.count() >= 2:
                win._on_row_clicked(win.replay_list.item(0))
                win._on_row_clicked(win.replay_list.item(1))
            if screenshot_all_tabs_dir is not None:
                out_dir = Path(screenshot_all_tabs_dir)
                out_dir.mkdir(parents=True, exist_ok=True)
                for idx, tag in enumerate(("tab1_replays", "tab2_advanced", "tab3_settings", "tab4_about")):
                    win.tabs.setCurrentIndex(idx)
                    QApplication.processEvents()
                    win.grab().save(str(out_dir / f"{tag}.png"))
                win.tabs.setCurrentIndex(0)
            if screenshot_path is not None:
                Path(screenshot_path).parent.mkdir(parents=True, exist_ok=True)
                QApplication.processEvents()
                win.grab().save(str(screenshot_path))
            win.close()
            app.quit()

        QTimer.singleShot(450, _capture_and_quit)

    return int(app.exec()) if created_app else 0


def main() -> int:
    screenshot: Path | None = None
    tabs_dir: Path | None = None
    lang: str | None = None
    theme: str | None = None
    for flag, attr in (("--screenshot", "s"), ("--screenshot-tabs", "t"), ("--lang", "l"), ("--theme", "th")):
        if flag in sys.argv:
            idx = sys.argv.index(flag)
            if idx + 1 < len(sys.argv):
                val = sys.argv[idx + 1]
                if attr == "s":
                    screenshot = Path(val)
                elif attr == "t":
                    tabs_dir = Path(val)
                elif attr == "l":
                    lang = val
                elif attr == "th":
                    theme = val
    return launch_gui(screenshot_path=screenshot, screenshot_all_tabs_dir=tabs_dir, lang=lang, theme=theme)


if __name__ == "__main__":
    sys.exit(main())
