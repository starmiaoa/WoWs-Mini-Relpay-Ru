"""H.264 MP4 视频编码管线（支持多分辨率缩放与实时进度/取消回调）与代表性关键帧自动抽取器。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import imageio_ffmpeg
import numpy as np
from PIL import Image

from ..config import RenderConfig
from ..domain.models import BattleFrame, BattleTimeline
from ..extractor.store import VersionDataStore
from .layers import CANVAS_H, CANVAS_W, MinimapFrameRenderer


class RenderCancelledError(RuntimeError):
    """用户主动取消当前渲染任务时抛出。"""


@dataclass
class RenderOutput:
    """渲染完成后的产出路径与摘要信息。"""

    video_path: Path
    total_frames: int
    fps: int
    width: int
    height: int
    keyframe_paths: list[Path] = field(default_factory=list)


def _resize_frame_to_output(
    img: Image.Image,
    target_w: int,
    target_h: int,
    pad_rgb: tuple[int, int, int] = (10, 12, 16),
) -> Image.Image:
    """将 1920×1080 母版帧高质量缩放到目标分辨率；若宽高比非 16:9 则等比居中补黑边防拉伸变形。"""
    if img.size == (target_w, target_h):
        return img
    src_w, src_h = img.size
    src_ratio = src_w / src_h
    dst_ratio = target_w / target_h
    if abs(src_ratio - dst_ratio) < 0.01:
        return img.resize((target_w, target_h), resample=Image.Resampling.LANCZOS)

    scale = min(target_w / src_w, target_h / src_h)
    nw = max(2, round(src_w * scale))
    nh = max(2, round(src_h * scale))
    fitted = img.resize((nw, nh), resample=Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (target_w, target_h), pad_rgb)
    canvas.paste(fitted, ((target_w - nw) // 2, (target_h - nh) // 2))
    return canvas


def select_representative_keyframes(timeline: BattleTimeline) -> list[tuple[str, int]]:
    """自动挑选最具代表性的 5+ 个战斗关键帧（开局、烟雾峰值、灭点峰值、激烈交战、终局）。"""
    frames = timeline.frames
    if not frames:
        return []
    n = len(frames)

    idx_opening = min(n - 1, max(5, min(25, n // 20)))
    idx_smoke = max(
        range(n),
        key=lambda i: (sum(len(s.points) for s in frames[i].smokes), len(frames[i].smokes)),
    )

    def _unspot_score(f: BattleFrame) -> tuple[int, int]:
        unsp = sum(1 for s in f.ships.values() if s.is_alive and s.has_been_spotted and not s.is_spotted)
        return (unsp, len(f.smokes))

    idx_unspot = max(range(n), key=lambda i: _unspot_score(frames[i]))

    def _combat_score(i: int) -> tuple[int, int, int]:
        f = frames[i]
        has_mid_kills = 1 if (3 <= len(f.kills) <= max(4, len(frames[-1].kills) - 2)) else 0
        action = len(f.shells) + len(f.torpedoes) * 3 + len(f.smokes) * 5
        return (has_mid_kills, action, len(f.kills))

    idx_combat = max(range(n), key=_combat_score)
    idx_final = max(0, n - 2)

    return [
        ("01_opening", idx_opening),
        ("02_smoke_peak", idx_smoke),
        ("03_unspotted_peak", idx_unspot),
        ("04_mid_combat", idx_combat),
        ("05_endgame", idx_final),
    ]


def render_timeline_to_video(
    timeline: BattleTimeline,
    store: VersionDataStore,
    output_mp4: Path | str,
    config: RenderConfig | None = None,
    keyframes_dir: Path | str | None = None,
    max_frames: int | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> RenderOutput:
    """将 BattleTimeline 逐帧渲染为 H.264 MP4 视频，支持自定义分辨率、帧率、进度回调与取消。"""
    cfg = config or RenderConfig()
    out_w, out_h = cfg.resolved_output_size()
    out_path = Path(output_mp4)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    renderer = MinimapFrameRenderer(timeline=timeline, store=store, config=cfg)
    frames = timeline.frames[:max_frames] if max_frames else timeline.frames
    if not frames:
        raise ValueError("回放时间轴为空，无法渲染视频。")

    banner_frames = max(0, int(round(cfg.end_banner_seconds * cfg.fps)))
    planned_total = len(frames) + banner_frames

    keyframe_targets: dict[int, list[str]] = {}
    saved_keyframes: list[Path] = []
    kf_dir_path = Path(keyframes_dir) if keyframes_dir else None
    if kf_dir_path is not None:
        kf_dir_path.mkdir(parents=True, exist_ok=True)
        for tag, idx in select_representative_keyframes(timeline):
            if idx < len(frames):
                keyframe_targets.setdefault(idx, []).append(tag)

    writer = imageio_ffmpeg.write_frames(
        str(out_path),
        size=(out_w, out_h),
        fps=cfg.fps,
        codec="libx264",
        pix_fmt_in="rgb24",
        pix_fmt_out="yuv420p",
        macro_block_size=2,
        output_params=[
            "-preset", "fast",
            "-crf", "20",
            "-profile:v", "high",
            "-movflags", "+faststart",
            "-tune", "animation",
        ],
    )
    writer.send(None)

    total_written = 0
    cancelled = False
    try:
        for i, bf in enumerate(frames):
            if cancel_check is not None and cancel_check():
                cancelled = True
                raise RenderCancelledError("渲染任务已取消。")

            img = renderer.render_frame(bf, end_banner_alpha=0.0)
            if (out_w, out_h) != (CANVAS_W, CANVAS_H):
                img = _resize_frame_to_output(img, out_w, out_h)
            if kf_dir_path is not None and i in keyframe_targets:
                for tag in keyframe_targets[i]:
                    kf_file = kf_dir_path / f"{tag}_f{i:04d}.png"
                    img.save(kf_file)
                    saved_keyframes.append(kf_file)
            writer.send(np.asarray(img, dtype=np.uint8))
            total_written += 1
            if progress_callback is not None and (total_written % 5 == 0 or total_written == planned_total):
                progress_callback(total_written, planned_total)

        if banner_frames > 0 and frames:
            last_bf = frames[-1]
            fade_steps = max(1, min(15, banner_frames // 2))
            for k in range(banner_frames):
                if cancel_check is not None and cancel_check():
                    cancelled = True
                    raise RenderCancelledError("渲染任务已取消。")
                alpha = min(1.0, (k + 1) / fade_steps)
                img = renderer.render_frame(last_bf, end_banner_alpha=alpha)
                if (out_w, out_h) != (CANVAS_W, CANVAS_H):
                    img = _resize_frame_to_output(img, out_w, out_h)
                if kf_dir_path is not None and k == banner_frames - 1:
                    kf_file = kf_dir_path / "06_result_banner.png"
                    img.save(kf_file)
                    saved_keyframes.append(kf_file)
                writer.send(np.asarray(img, dtype=np.uint8))
                total_written += 1
                if progress_callback is not None and (total_written % 5 == 0 or total_written == planned_total):
                    progress_callback(total_written, planned_total)
    finally:
        writer.close()
        if cancelled:
            try:
                out_path.unlink(missing_ok=True)
            except OSError:
                pass

    return RenderOutput(
        video_path=out_path,
        total_frames=total_written,
        fps=cfg.fps,
        width=out_w,
        height=out_h,
        keyframe_paths=saved_keyframes,
    )
