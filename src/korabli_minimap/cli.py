"""俄服《战舰世界》小地图回放渲染工具中文命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import DEFAULT_DATA_DIR, DEFAULT_GAME_DIR, RenderConfig
from .extractor import list_synced_versions, sync_client_data
from .parser import parse_replay
from .renderer import render_timeline_to_video


def _cmd_sync(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    res = sync_client_data(
        game_dir=args.game_dir,
        data_dir=args.data_dir,
        preferred_build=args.build,
        i18n_only=args.i18n_only,
        map_names=args.maps,
    )
    dt = time.perf_counter() - t0
    print(f"[同步完成] 耗时 {dt:.2f}s")
    print(f"  客户端版本 : {res.version} (完整: {res.full_version}, build: {res.build})")
    print(f"  输出目录   : {res.output_dir}")
    print(f"  实体定义数 : {res.entity_def_count}")
    print(f"  战舰总数   : {res.ship_count}（中文词条命中: {res.ship_zh_hit_count}）")
    print(f"  弹药/消耗品: {res.projectile_count} / {res.consumable_count}")
    print(f"  提取地图数 : {res.map_count}")
    print(f"  中文字体   : {', '.join(res.font_files) if res.font_files else '使用系统字体'}")
    print(f"  汉化来源数 : {len(res.i18n_sources)}")
    for src in res.i18n_sources:
        print(f"    - [{src['kind']}] {src['path']} -> {src['inner_path']} ({src['key_count']} 条)")
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    tl, _store = parse_replay(args.replay, data_dir=args.data_dir)
    dt = time.perf_counter() - t0
    print(f"[解析完成] 耗时 {dt:.2f}s")
    print(f"  回放版本 : {tl.client_version}（使用本地定义 v{tl.short_version}）")
    print(f"  战斗地图 : {tl.map_zh_name} ({tl.map_space})")
    print(f"  模式/结果: {tl.battle_type_zh} · {tl.scenario_zh} | {tl.result_zh}")
    print(f"  录制玩家 : {tl.player_name} | 帧数: {len(tl.frames)}")
    print("  诊断统计 :", json.dumps(tl.diagnostics, ensure_ascii=False))
    print("\n  参战舰船（中文名）：")
    for m in sorted(tl.ships_meta.values(), key=lambda x: (x.team_id, -x.level, x.species)):
        side = "我方" if m.team_id == tl.ally_team_id else "敌方"
        tag = " [自己]" if m.vehicle_id == tl.player_vehicle_id else ""
        print(f"    [{side}] Lv{m.level:2d} {m.species:10s} {m.zh_name:14s} ({m.player_name}){tag}")
    return 0


def _parse_resolution(res_str: str) -> tuple[int, int]:
    parts = res_str.lower().replace("×", "x").split("x")
    if len(parts) != 2 or not parts[0].strip().isdigit() or not parts[1].strip().isdigit():
        raise argparse.ArgumentTypeError(f"分辨率格式应为 宽x高（如 1920x1080），收到: {res_str}")
    return int(parts[0].strip()), int(parts[1].strip())


def _cmd_render(args: argparse.Namespace) -> int:
    t0 = time.perf_counter()
    tl, store = parse_replay(args.replay, data_dir=args.data_dir)
    t1 = time.perf_counter()

    out_w, out_h = _parse_resolution(args.resolution)
    out_mp4 = Path(args.output) if args.output else Path("output") / (Path(args.replay).stem + ".mp4")
    cfg = RenderConfig(
        fps=args.fps,
        output_width=out_w,
        output_height=out_h,
        language=args.lang,
        show_smoke=not args.no_smoke,
        show_unspotted_names=not args.no_unspotted_names,
        show_spotted_names=not args.no_spotted_names,
        show_shells=not args.no_shells,
        show_torpedoes=not args.no_torpedoes,
        show_planes=not args.no_planes,
        show_chat=not args.no_chat,
    )
    res = render_timeline_to_video(
        timeline=tl,
        store=store,
        output_mp4=out_mp4,
        config=cfg,
        keyframes_dir=args.keyframes_dir,
        max_frames=args.max_frames,
    )
    t2 = time.perf_counter()
    print(f"[渲染完成] 解析 {t1 - t0:.2f}s | 渲染+编码 {t2 - t1:.2f}s | 总耗时 {t2 - t0:.2f}s")
    print(f"  输出视频 : {res.video_path} ({res.width}x{res.height}, {res.total_frames} 帧 @ {res.fps}fps)")
    print(f"  地图/模式: {tl.map_zh_name} · {tl.battle_type_zh} ({tl.result_zh})")
    if res.keyframe_paths:
        print(f"  关键帧图 : 共导出 {len(res.keyframe_paths)} 张")
        for p in res.keyframe_paths:
            print(f"    - {p}")
    return 0


def _cmd_versions(args: argparse.Namespace) -> int:
    vers = list_synced_versions(args.data_dir)
    if not vers:
        print("当前尚未同步任何俄服客户端版本，请运行 `korabli-minimap sync`。")
        return 0
    print("已同步的俄服客户端版本：")
    for v in vers:
        print(f"  - {v}")
    return 0


def _cmd_gui(args: argparse.Namespace) -> int:
    from .gui import launch_gui

    return launch_gui(data_dir=args.data_dir)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="korabli-minimap",
        description="俄服《战舰世界》（Mir Korabley / Lesta）扁平化小地图回放渲染工具",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"版本化数据缓存目录（默认: {DEFAULT_DATA_DIR}）",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # sync
    p_sync = sub.add_parser("sync", help="从俄服客户端目录同步版本定义、地图与汉化 mod")
    p_sync.add_argument(
        "--game-dir",
        type=Path,
        default=DEFAULT_GAME_DIR,
        help=f"俄服游戏根目录（默认: {DEFAULT_GAME_DIR}）",
    )
    p_sync.add_argument("--build", type=str, default=None, help="指定 bin/<build> 目录号（默认自动选取最新版本）")
    p_sync.add_argument("--i18n-only", action="store_true", help="仅快速同步汉化 mod 船名/地图名与字体")
    p_sync.add_argument("--maps", nargs="*", default=None, help="仅提取指定地图名（默认提取全部战斗地图）")
    p_sync.set_defaults(func=_cmd_sync)

    # inspect
    p_ins = sub.add_parser("inspect", help="解析并校验 .korablireplay 回放元数据、船名汉化与事件统计")
    p_ins.add_argument("replay", type=Path, help=".korablireplay 文件路径")
    p_ins.set_defaults(func=_cmd_inspect)

    # render
    p_ren = sub.add_parser("render", help="将 .korablireplay 渲染为扁平化 H.264 MP4 小地图回放视频")
    p_ren.add_argument("replay", type=Path, help=".korablireplay 文件路径")
    p_ren.add_argument("-o", "--output", type=Path, default=None, help="输出 MP4 文件路径")
    p_ren.add_argument("--fps", type=int, default=30, help="输出帧率 / 播放倍速（默认 30，即 30 倍速）")
    p_ren.add_argument("--resolution", type=str, default="1920x1080", help="输出分辨率（宽x高，默认 1920x1080）")
    p_ren.add_argument("--lang", type=str, default="auto", choices=["auto", "zh_CN", "zh_TW", "ru", "en"], help="渲染画面语言（auto 跟随系统，或指定 zh_CN / zh_TW / ru / en）")
    p_ren.add_argument("--keyframes-dir", type=Path, default=None, help="同时导出代表性关键帧 PNG 到此目录")
    p_ren.add_argument("--max-frames", type=int, default=None, help="仅渲染前 N 帧（用于快速调试）")
    p_ren.add_argument("--no-smoke", action="store_true", help="关闭地图烟雾渲染")
    p_ren.add_argument("--no-unspotted-names", action="store_true", help="关闭灭点船只名称显示")
    p_ren.add_argument("--no-spotted-names", action="store_true", help="关闭在点船只名称显示")
    p_ren.add_argument("--no-shells", action="store_true", help="关闭炮弹轨迹显示")
    p_ren.add_argument("--no-torpedoes", action="store_true", help="关闭鱼雷轨迹显示")
    p_ren.add_argument("--no-planes", action="store_true", help="关闭航空中队显示")
    p_ren.add_argument("--no-chat", action="store_true", help="关闭底部局内聊天区域显示")
    p_ren.set_defaults(func=_cmd_render)

    # versions
    p_ver = sub.add_parser("versions", help="列出已同步的俄服版本缓存")
    p_ver.set_defaults(func=_cmd_versions)

    # gui
    p_gui = sub.add_parser("gui", help="启动桌面图形界面（自动识别正式服/PT、拖拽批量渲染）")
    p_gui.set_defaults(func=_cmd_gui)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
