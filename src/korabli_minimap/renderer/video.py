"""H.264 MP4 视频高速低内存编码管线（多线程滑动窗口并发 + 异步 GPU/x264 编码 + 零 NumPy 依赖）。"""

from __future__ import annotations

from dataclasses import dataclass, field
import gc
import logging
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
from typing import Callable

import imageio_ffmpeg
from PIL import Image

from ..config import RenderConfig
from ..domain.models import BattleFrame, BattleTimeline
from ..extractor.store import VersionDataStore
from .layers import CANVAS_H, CANVAS_W, MinimapFrameRenderer

logger = logging.getLogger(__name__)


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
    encoder_used: str = "libx264"


# -----------------------------------------------------------------------------
# H.264 编码器自动探测（优先 GPU 硬件编码，自动无感回退 CPU libx264）
# -----------------------------------------------------------------------------

_ENCODER_SPECS: dict[str, tuple[str, list[str]]] = {
    "nvenc": (
        "h264_nvenc",
        [
            "-preset", "p4",
            "-rc", "vbr",
            "-cq", "20",
            "-b:v", "0",
            "-profile:v", "high",
            "-movflags", "+faststart",
        ],
    ),
    "amf": (
        "h264_amf",
        [
            "-quality", "balanced",
            "-rc", "cqp",
            "-qp_i", "20",
            "-qp_p", "20",
            "-profile:v", "high",
            "-movflags", "+faststart",
        ],
    ),
    "qsv": (
        "h264_qsv",
        [
            "-preset", "veryfast",
            "-global_quality", "20",
            "-profile:v", "high",
            "-movflags", "+faststart",
        ],
    ),
    "x264": (
        "libx264",
        [
            "-preset", "veryfast",
            "-crf", "20",
            "-profile:v", "high",
            "-movflags", "+faststart",
            "-tune", "animation",
        ],
    ),
}

_ENCODER_PROBE_CACHE: dict[str, bool] = {}
_ENCODER_PROBE_LOCK = threading.Lock()


def _probe_hw_encoder(codec: str, extra_params: list[str]) -> bool:
    """用极短 3 帧黑屏探测本机显卡/驱动是否真正支持指定硬件编码器（结果进程级缓存）。"""
    with _ENCODER_PROBE_LOCK:
        cached = _ENCODER_PROBE_CACHE.get(codec)
        if cached is not None:
            return cached

    ok = False
    try:
        ff_exe = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [
            ff_exe,
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-f", "lavfi",
            "-i", "color=c=black:s=640x360:r=30:d=0.1",
            "-pix_fmt", "yuv420p",
            "-c:v", codec,
            *extra_params,
            "-f", "null",
            "-",
        ]
        kwargs: dict[str, object] = {
            "capture_output": True,
            "timeout": 8,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        proc = subprocess.run(cmd, **kwargs)  # type: ignore[arg-type]
        ok = proc.returncode == 0
    except Exception:
        ok = False

    with _ENCODER_PROBE_LOCK:
        _ENCODER_PROBE_CACHE[codec] = ok
    return ok


def resolve_video_encoder(preferred: str = "auto") -> tuple[str, list[str]]:
    """根据配置与本机硬件探测返回 `(codec, output_params)`，硬件不可用时回退 `libx264`。"""
    pref = (preferred or "auto").strip().lower()
    if pref in ("x264", "libx264", "cpu"):
        return _ENCODER_SPECS["x264"]

    if pref in ("nvenc", "h264_nvenc"):
        order = ("nvenc",)
    elif pref in ("amf", "h264_amf"):
        order = ("amf",)
    elif pref in ("qsv", "h264_qsv"):
        order = ("qsv",)
    else:
        order = ("nvenc", "amf", "qsv")

    for key in order:
        codec, params = _ENCODER_SPECS[key]
        probe_params = [
            p for i, p in enumerate(params)
            if p != "-movflags" and (i == 0 or params[i - 1] != "-movflags")
        ]
        if _probe_hw_encoder(codec, probe_params):
            return codec, list(params)

    return _ENCODER_SPECS["x264"]


def _resolve_worker_count(requested: int, frame_count: int) -> int:
    """自动选择并发渲染线程数：平衡多核吞吐与低内存峰值（最多 6 线程）。"""
    if frame_count < 48:
        return 1
    if requested > 0:
        return max(1, min(10, requested))
    cpus = os.cpu_count() or 4
    return max(1, min(6, cpus - 2 if cpus >= 6 else cpus))


def _build_ffmpeg_scale_filter(out_w: int, out_h: int) -> str | None:
    """将分辨率缩放/等比补边下沉到 FFmpeg SIMD Lanczos 滤镜，避免在 Python 端搬运 2K/4K 原始帧。"""
    if (out_w, out_h) == (CANVAS_W, CANVAS_H):
        return None
    src_ratio = CANVAS_W / CANVAS_H
    dst_ratio = out_w / out_h
    if abs(src_ratio - dst_ratio) < 0.01:
        return f"scale={out_w}:{out_h}:flags=lanczos"
    return (
        f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2:color=#0a0c10"
    )


# -----------------------------------------------------------------------------
# 帧缩放与关键帧挑选
# -----------------------------------------------------------------------------

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


# -----------------------------------------------------------------------------
# 单帧渲染与低内存滑动窗口并发编码管线
# -----------------------------------------------------------------------------

def _render_single_frame_to_bytes(
    renderer: MinimapFrameRenderer,
    bf: BattleFrame,
    out_w: int,
    out_h: int,
    end_banner_alpha: float = 0.0,
    save_kf_paths: list[Path] | None = None,
) -> bytes:
    buf = renderer._render_into_buf(bf, end_banner_alpha=end_banner_alpha)
    if save_kf_paths:
        kf_img = _resize_frame_to_output(buf, out_w, out_h) if (out_w, out_h) != (CANVAS_W, CANVAS_H) else buf
        for kf_file in save_kf_paths:
            kf_img.save(kf_file)

    return buf.tobytes("raw", "RGB")


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
    """将 BattleTimeline 并发渲染并异步编码为 H.264 MP4 视频。

    采用无死锁有界滑动窗口缓冲，将并发在途帧内存严格限制在 ~100MB 以内，
    兼顾多核满载吞吐与低内存占用。
    """
    cfg = config or RenderConfig()
    out_w, out_h = cfg.resolved_output_size()
    out_path = Path(output_mp4)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    frames = timeline.frames[:max_frames] if max_frames else timeline.frames
    if not frames:
        raise ValueError("回放时间轴为空，无法渲染视频。")

    banner_frames = max(0, int(round(cfg.end_banner_seconds * cfg.fps)))
    planned_total = len(frames) + banner_frames

    keyframe_targets: dict[int, list[Path]] = {}
    saved_keyframes: list[Path] = []
    kf_dir_path = Path(keyframes_dir) if keyframes_dir else None
    if kf_dir_path is not None:
        kf_dir_path.mkdir(parents=True, exist_ok=True)
        for tag, idx in select_representative_keyframes(timeline):
            if idx < len(frames):
                kf_file = kf_dir_path / f"{tag}_f{idx:04d}.png"
                keyframe_targets.setdefault(idx, []).append(kf_file)
                saved_keyframes.append(kf_file)

    renderer = MinimapFrameRenderer(timeline=timeline, store=store, config=cfg)
    codec, enc_params = resolve_video_encoder(cfg.encoder)
    vf_scale = _build_ffmpeg_scale_filter(out_w, out_h)
    vf_args = ["-vf", vf_scale] if vf_scale else []

    # 同步渲染并写入第 0 帧验证硬件编码器，失败时无感回退 libx264
    first_bytes = _render_single_frame_to_bytes(
        renderer,
        frames[0],
        out_w,
        out_h,
        end_banner_alpha=0.0,
        save_kf_paths=keyframe_targets.get(0),
    )

    try:
        test_writer = imageio_ffmpeg.write_frames(
            str(out_path),
            size=(CANVAS_W, CANVAS_H),
            fps=cfg.fps,
            codec=codec,
            quality=None,
            pix_fmt_in="rgb24",
            pix_fmt_out="yuv420p",
            macro_block_size=2,
            output_params=[*vf_args, *enc_params],
        )
        test_writer.send(None)
        test_writer.send(first_bytes)
    except Exception as hw_exc:
        if codec != "libx264":
            logger.warning("硬件编码器 %s 初始化失败，自动回退 libx264: %s", codec, hw_exc)
            codec, enc_params = _ENCODER_SPECS["x264"]
            test_writer = imageio_ffmpeg.write_frames(
                str(out_path),
                size=(CANVAS_W, CANVAS_H),
                fps=cfg.fps,
                codec=codec,
                quality=None,
                pix_fmt_in="rgb24",
                pix_fmt_out="yuv420p",
                macro_block_size=2,
                output_params=[*vf_args, *enc_params],
            )
            test_writer.send(None)
            test_writer.send(first_bytes)
        else:
            raise
    finally:
        del first_bytes

    # 小容量异步写入队列（最多缓存 4 帧 ≈ 25 MB），解耦渲染与管道 I/O
    write_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=4)
    writer_error: list[BaseException] = []

    def _writer_loop() -> None:
        try:
            while True:
                item = write_queue.get()
                if item is None:
                    break
                test_writer.send(item)
                del item
        except BaseException as exc:
            writer_error.append(exc)
        finally:
            try:
                test_writer.close()
            except Exception:
                pass

    writer_thread = threading.Thread(target=_writer_loop, name="ffmpeg-writer", daemon=True)
    writer_thread.start()

    def _push_frame(frame_bytes: bytes) -> None:
        if writer_error:
            raise writer_error[0]
        write_queue.put(frame_bytes)
        if writer_error:
            raise writer_error[0]

    total_written = 1
    cancelled = False
    if progress_callback is not None and total_written == planned_total:
        progress_callback(total_written, planned_total)

    num_frames = len(frames)
    remaining_count = num_frames - 1
    n_workers = _resolve_worker_count(cfg.render_workers, remaining_count)

    try:
        if n_workers <= 1 or remaining_count < 48:
            for i in range(1, num_frames):
                if cancel_check is not None and cancel_check():
                    cancelled = True
                    raise RenderCancelledError("渲染任务已取消。")
                frame_bytes = _render_single_frame_to_bytes(
                    renderer,
                    frames[i],
                    out_w,
                    out_h,
                    end_banner_alpha=0.0,
                    save_kf_paths=keyframe_targets.get(i),
                )
                _push_frame(frame_bytes)
                total_written += 1
                if progress_callback is not None and (total_written % 5 == 0 or total_written == planned_total):
                    progress_callback(total_written, planned_total)
        else:
            # 有界窗口流式并发：
            # - 每个工作线程顺序领取连续 24 帧区间，保证侧边栏/聊天/烟雾缓存 >95% 命中率；
            # - 每帧渲染前仅需等待 `fi < next_to_consume + window_size`，
            #   持有所需帧 `next_to_consume` 的线程永不阻塞（严格无死锁），同时将总缓冲帧硬限制在 window_size 内。
            chunk_size = 24
            window_size = max(12, n_workers * 2 + 4)  # 6 线程 -> 16 帧 ≈ 99 MB 上限
            next_chunk_start = [1]
            chunk_lock = threading.Lock()
            buf_cond = threading.Condition()
            ready: dict[int, bytes] = {}
            next_to_consume = [1]
            stop_event = threading.Event()
            worker_error: list[BaseException] = []

            def _worker_main(worker_ren: MinimapFrameRenderer) -> None:
                try:
                    while not stop_event.is_set():
                        with chunk_lock:
                            start = next_chunk_start[0]
                            if start >= num_frames:
                                return
                            end = min(num_frames, start + chunk_size)
                            next_chunk_start[0] = end

                        for fi in range(start, end):
                            with buf_cond:
                                while (
                                    fi >= next_to_consume[0] + window_size
                                    and not stop_event.is_set()
                                ):
                                    buf_cond.wait(timeout=0.05)
                                if stop_event.is_set():
                                    return

                            if cancel_check is not None and cancel_check():
                                stop_event.set()
                                with buf_cond:
                                    buf_cond.notify_all()
                                return

                            frame_bytes = _render_single_frame_to_bytes(
                                worker_ren,
                                frames[fi],
                                out_w,
                                out_h,
                                end_banner_alpha=0.0,
                                save_kf_paths=keyframe_targets.get(fi),
                            )

                            with buf_cond:
                                if stop_event.is_set():
                                    return
                                ready[fi] = frame_bytes
                                buf_cond.notify_all()
                except BaseException as exc:
                    worker_error.append(exc)
                    stop_event.set()
                    with buf_cond:
                        buf_cond.notify_all()

            worker_threads = [
                threading.Thread(
                    target=_worker_main,
                    args=(renderer.clone_for_worker(),),
                    name=f"minimap-render-{idx}",
                    daemon=True,
                )
                for idx in range(n_workers)
            ]
            for wt in worker_threads:
                wt.start()

            try:
                for expected_idx in range(1, num_frames):
                    if cancel_check is not None and cancel_check():
                        stop_event.set()
                        cancelled = True
                        with buf_cond:
                            buf_cond.notify_all()
                        raise RenderCancelledError("渲染任务已取消。")

                    with buf_cond:
                        while (
                            expected_idx not in ready
                            and not worker_error
                            and not stop_event.is_set()
                        ):
                            buf_cond.wait(timeout=0.05)
                            if cancel_check is not None and cancel_check():
                                stop_event.set()
                                cancelled = True
                                buf_cond.notify_all()
                                break

                        if worker_error:
                            raise worker_error[0]
                        if cancelled or (stop_event.is_set() and expected_idx not in ready):
                            cancelled = True
                            raise RenderCancelledError("渲染任务已取消。")

                        frame_bytes = ready.pop(expected_idx)
                        next_to_consume[0] = expected_idx + 1
                        buf_cond.notify_all()

                    _push_frame(frame_bytes)
                    del frame_bytes
                    total_written += 1
                    if progress_callback is not None and (total_written % 5 == 0 or total_written == planned_total):
                        progress_callback(total_written, planned_total)
            finally:
                stop_event.set()
                with buf_cond:
                    ready.clear()
                    buf_cond.notify_all()
                for wt in worker_threads:
                    wt.join(timeout=5.0)
                worker_threads.clear()

        # 结尾结算横幅：仅渲染淡入前 fade_steps 帧，满不透明度静止帧直接复用缓冲
        if banner_frames > 0 and frames:
            last_bf = frames[-1]
            fade_steps = max(1, min(15, banner_frames // 2))
            full_alpha_bytes: bytes | None = None
            for k in range(banner_frames):
                if cancel_check is not None and cancel_check():
                    cancelled = True
                    raise RenderCancelledError("渲染任务已取消。")
                alpha = min(1.0, (k + 1) / fade_steps)
                if alpha >= 1.0 and full_alpha_bytes is not None:
                    frame_bytes = full_alpha_bytes
                else:
                    kf_paths: list[Path] | None = None
                    if kf_dir_path is not None and (k == banner_frames - 1 or alpha >= 1.0):
                        kf_file = kf_dir_path / "06_result_banner.png"
                        if kf_file not in saved_keyframes:
                            kf_paths = [kf_file]
                            saved_keyframes.append(kf_file)
                    frame_bytes = _render_single_frame_to_bytes(
                        renderer,
                        last_bf,
                        out_w,
                        out_h,
                        end_banner_alpha=alpha,
                        save_kf_paths=kf_paths,
                    )
                    if alpha >= 1.0:
                        full_alpha_bytes = frame_bytes
                _push_frame(frame_bytes)
                total_written += 1
                if progress_callback is not None and (total_written % 5 == 0 or total_written == planned_total):
                    progress_callback(total_written, planned_total)
            full_alpha_bytes = None
    finally:
        write_queue.put(None)
        writer_thread.join()
        del renderer
        gc.collect()
        if cancelled:
            try:
                out_path.unlink(missing_ok=True)
            except OSError:
                pass

    if writer_error:
        raise writer_error[0]

    return RenderOutput(
        video_path=out_path,
        total_frames=total_written,
        fps=cfg.fps,
        width=out_w,
        height=out_h,
        keyframe_paths=saved_keyframes,
        encoder_used=codec,
    )
