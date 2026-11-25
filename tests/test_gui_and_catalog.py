"""俄服正式服/PT 探测、回放目录扫描与文件名解析、时间戳批次目录、多分辨率缩放与 GUI 基础回归测试。"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PIL import Image

from korabli_minimap.config import DEFAULT_DATA_DIR, RenderConfig
from korabli_minimap.extractor.client_locator import _classify_channel, discover_korabli_clients
from korabli_minimap.extractor.store import VersionDataStore, list_synced_versions, load_version_store
from korabli_minimap.renderer.video import _resize_frame_to_output
from korabli_minimap.replay_catalog import (
    collect_korabli_replays_from_paths,
    create_batch_output_dir,
    parse_replay_item,
    scan_replays_in_game_dir,
)


def _dummy_store(tmp_path: Path) -> VersionDataStore:
    return VersionDataStore(
        version="26.11.1",
        root_dir=tmp_path,
        manifest={"version": "26.11.1"},
        ships={
            "4073633616": {
                "id": 4073633616,
                "internal_name": "PFSC211_La_Rochelle",
                "index": "PFSC211",
                "species": "Cruiser",
                "level": 11,
                "nation": "France",
                "default_hp": 83450,
                "zh_name": "拉罗谢尔",
                "ru_name": "La Rochelle",
            },
            "4179539920": {
                "id": 4179539920,
                "internal_name": "PRSD110_Pr_24_Khabarovsk",
                "index": "PRSD110",
                "species": "Destroyer",
                "level": 10,
                "nation": "USSR",
                "default_hp": 22500,
                "zh_name": "哈巴罗夫斯克",
                "ru_name": "Хабаровск",
            },
        },
        projectiles={},
        consumables={},
        localization={
            "maps_zh": {
                "47_SLEEPING_GIANT": "沉睡的渤海湾",
                "35_NE_NORTH_WINTER": "北方水域",
            }
        },
    )


def test_classify_live_and_pt_channels() -> None:
    assert _classify_channel(Path(r"D:\Games\Korabli"), "MK.RU.PRODUCTION") == ("live", "正式服")
    assert _classify_channel(Path(r"D:\Games\Korabli_PT"), "MK.PT.PRODUCTION") == ("pt", "PT测试服")
    assert _classify_channel(Path(r"D:\Games\Mir_Korabley_PT"), "MK.RU.PRODUCTION", hint="mk.pt") == ("pt", "PT测试服")


def test_parse_replay_filename_and_strict_extension_filter(tmp_path: Path) -> None:
    store = _dummy_store(tmp_path)
    replays_dir = tmp_path / "replays"
    replays_dir.mkdir()

    r1 = replays_dir / "20261121_164854_PFSC211-La-Rochelle_47_Sleeping_Giant.korablireplay"
    r2 = replays_dir / "20261120_192217_PRSD110-Pr-24-Khabarovsk_35_NE_north_winter.korablireplay"
    r_temp = replays_dir / "temp.korablireplay"
    non_replay1 = replays_dir / "screenshot.png"
    non_replay2 = replays_dir / "other.wowsreplay"
    for f in (r1, r2, r_temp, non_replay1, non_replay2):
        f.write_bytes(b"x" * 2048)

    items = scan_replays_in_game_dir(tmp_path, store=store)
    assert len(items) == 2
    # 最新时间排前面
    assert items[0].filename == r1.name
    assert items[0].battle_time_str == "2026-11-21 16:48:54"
    assert items[0].ship_display == "★ 拉罗谢尔 (巡洋)"
    assert items[0].map_display == "沉睡的渤海湾"

    assert items[1].filename == r2.name
    assert items[1].battle_time_str == "2026-11-20 19:22:17"
    assert items[1].ship_display == "X 哈巴罗夫斯克 (驱逐)"
    assert items[1].map_display == "北方水域"

    # 拖拽目录收集也只收 .korablireplay 且自动过滤非回放与 temp
    collected = collect_korabli_replays_from_paths([replays_dir, non_replay1, r1])
    assert {p.name for p in collected} == {r1.name, r2.name}


def _sample_replay_path() -> Path | None:
    raw = os.environ.get("KORABLI_SAMPLE_REPLAY", "").strip()
    if not raw:
        return None
    p = Path(raw)
    return p if p.is_file() else None


def test_renamed_replay_header_fallback_if_sample_present() -> None:
    sample_replay = _sample_replay_path()
    if sample_replay is None or not list_synced_versions(DEFAULT_DATA_DIR):
        return
    store = load_version_store(data_dir=DEFAULT_DATA_DIR)
    item = parse_replay_item(sample_replay, store=store, source="手动拖入")
    assert item.ship_index == "PFSC211"
    assert "拉罗谢尔" in item.ship_display
    assert item.map_display == "沉睡的渤海湾"
    assert item.battle_time_str == "2026-11-21 16:48:54"


def test_batch_output_dir_and_multi_resolution_resize(tmp_path: Path) -> None:
    dt = datetime(2026, 11, 25, 12, 50, 8)
    d1 = create_batch_output_dir(tmp_path, now=dt)
    d2 = create_batch_output_dir(tmp_path, now=dt)
    assert d1.is_dir() and d1.name == "2026-11-25_12-50-08"
    assert d2.is_dir() and d2.name == "2026-11-25_12-50-08_02"

    cfg = RenderConfig(output_width=1281, output_height=721)
    assert cfg.resolved_output_size() == (1280, 720)

    master = Image.new("RGB", (1920, 1080), (20, 40, 60))
    assert _resize_frame_to_output(master, 1280, 720).size == (1280, 720)
    assert _resize_frame_to_output(master, 2560, 1440).size == (2560, 1440)
    # 非 16:9 分辨率保留 16:9 比例并居中补深色底边
    non169 = _resize_frame_to_output(master, 1920, 1200)
    assert non169.size == (1920, 1200)
    assert non169.getpixel((10, 5)) == (10, 12, 16)
    assert non169.getpixel((960, 600)) == (20, 40, 60)


def test_discover_local_korabli_clients_if_installed() -> None:
    if not Path(r"D:\Games\Korabli").is_dir():
        return
    clients = discover_korabli_clients()
    assert any(str(c.game_dir).lower() == r"d:\games\korabli" and c.channel == "live" for c in clients)


def test_instant_loc_bundle_fallback_without_synced_store(tmp_path: Path) -> None:
    from korabli_minimap.extractor.localization import LocalizationBundle

    loc = LocalizationBundle(
        zh_catalog={
            "IDS_PGSB310": "普鲁士",
            "IDS_SPACES/19_OC_PREY": "陷阱",
        },
        ru_catalog={
            "IDS_PGSB310": "Preussen",
            "IDS_SPACES/19_OC_PREY": "Ловушка",
        },
    )
    f = tmp_path / "20261121_171458_PGSB310-Preussen_19_OC_prey.korablireplay"
    f.write_bytes(b"x" * 1024)
    item_cn = parse_replay_item(f, store=None, loc_bundle=loc, lang="zh_CN")
    item_tw = parse_replay_item(f, store=None, loc_bundle=loc, lang="zh_TW")
    item_ru = parse_replay_item(f, store=None, loc_bundle=loc, lang="ru")
    item_en = parse_replay_item(f, store=None, loc_bundle=loc, lang="en")
    assert item_cn.ship_display == "普鲁士" and item_cn.map_display == "陷阱"
    assert item_tw.ship_display == "普魯士" and item_tw.map_display == "陷阱"
    assert item_ru.ship_display == "Preussen" and item_ru.map_display == "Ловушка"
    assert item_en.ship_display == "Preussen" and item_en.map_display == "Trap"


def test_four_language_i18n_store_and_renderer() -> None:
    from korabli_minimap.i18n import (
        SUPPORTED_LANGUAGES,
        map_locale_to_lang,
        map_windows_lcid_to_lang,
        tr,
    )

    # 校验系统语言映射规则：目标语言精准命中，非目标语言统一回退英语
    assert map_locale_to_lang("zh_CN") == "zh_CN"
    assert map_locale_to_lang("zh-SG") == "zh_CN"
    assert map_locale_to_lang("zh_TW") == "zh_TW"
    assert map_locale_to_lang("zh-HK") == "zh_TW"
    assert map_locale_to_lang("ru_RU") == "ru"
    assert map_locale_to_lang("en_US") == "en"
    assert map_locale_to_lang("ja_JP") == "en"
    assert map_locale_to_lang("de_DE") == "en"
    assert map_locale_to_lang("ko_KR") == "en"
    assert map_windows_lcid_to_lang(0x0804) == "zh_CN"
    assert map_windows_lcid_to_lang(0x0404) == "zh_TW"
    assert map_windows_lcid_to_lang(0x0C04) == "zh_TW"
    assert map_windows_lcid_to_lang(0x0419) == "ru"
    assert map_windows_lcid_to_lang(0x0409) == "en"
    assert map_windows_lcid_to_lang(0x0411) == "en"  # ja-JP -> English fallback

    sample_replay = _sample_replay_path()
    if sample_replay is None or not list_synced_versions(DEFAULT_DATA_DIR):
        return

    from korabli_minimap.parser import parse_replay
    from korabli_minimap.renderer.layers import MinimapFrameRenderer

    tl, store = parse_replay(sample_replay, data_dir=DEFAULT_DATA_DIR)
    assert store.get_map_name("47_Sleeping_Giant", lang="zh_CN") == "沉睡的渤海湾"
    assert store.get_map_name("47_Sleeping_Giant", lang="zh_TW") == "沉睡的渤海灣"
    assert store.get_map_name("47_Sleeping_Giant", lang="ru") == "Сонный Бохайвань"
    assert store.get_map_name("47_Sleeping_Giant", lang="en") == "Sleeping Giant"

    for code, _name in SUPPORTED_LANGUAGES:
        assert tr(code, "tab_settings")
        ren = MinimapFrameRenderer(tl, store, RenderConfig(language=code))
        img = ren.render_frame(tl.frames[-1], end_banner_alpha=1.0)
        assert img.size == (1920, 1080)


def test_custom_resolution_and_progress_callback_video_export(tmp_path: Path) -> None:
    sample_replay = _sample_replay_path()
    if sample_replay is None or not list_synced_versions(DEFAULT_DATA_DIR):
        return

    import imageio_ffmpeg
    from korabli_minimap.parser import parse_replay
    from korabli_minimap.renderer.video import render_timeline_to_video

    tl, store = parse_replay(sample_replay, data_dir=DEFAULT_DATA_DIR)
    batch_dir = create_batch_output_dir(tmp_path)
    out_mp4 = batch_dir / "test_720p_60fps.mp4"
    progress_calls: list[tuple[int, int]] = []
    cfg = RenderConfig(fps=60, output_width=1280, output_height=720, end_banner_seconds=0.0)
    res = render_timeline_to_video(
        timeline=tl,
        store=store,
        output_mp4=out_mp4,
        config=cfg,
        max_frames=10,
        progress_callback=lambda done, total: progress_calls.append((done, total)),
    )
    assert res.width == 1280 and res.height == 720 and res.fps == 60
    assert res.total_frames == 10
    assert progress_calls and progress_calls[-1] == (10, 10)
    assert out_mp4.is_file() and out_mp4.stat().st_size > 1024

    gen = imageio_ffmpeg.read_frames(str(out_mp4))
    meta = next(gen)
    gen.close()
    assert meta["size"] == (1280, 720)
    assert abs(float(meta["fps"]) - 60.0) < 0.5
