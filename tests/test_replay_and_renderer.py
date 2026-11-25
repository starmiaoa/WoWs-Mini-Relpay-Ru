"""战局状态机（烟雾/灭点状态保持）与扁平渲染器回归测试。"""

from __future__ import annotations

import os
from pathlib import Path

from korabli_minimap.config import DEFAULT_DATA_DIR, RenderConfig
from korabli_minimap.domain.battle_state import BattleStateMachine
from korabli_minimap.extractor.store import VersionDataStore, list_synced_versions, load_version_store
from korabli_minimap.parser import parse_replay
from korabli_minimap.renderer.icons import GameIconFactory
from korabli_minimap.renderer.layers import CANVAS_H, CANVAS_W, MinimapFrameRenderer


def _make_dummy_store(tmp_path: Path) -> VersionDataStore:
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
                "internal_name": "PBSC110_Minotaur",
                "index": "PBSC110",
                "species": "Cruiser",
                "level": 10,
                "nation": "UK",
                "default_hp": 44400,
                "zh_name": "米诺陶",
                "ru_name": "Minotaur",
            },
        },
        projectiles={},
        consumables={},
        localization={
            "maps_zh": {"47_SLEEPING_GIANT": "沉睡的渤海湾"},
            "battle_types_zh": {"RandomBattle": "随机战"},
            "scenarios_zh": {"DOMINATION_4POINT": "制海权"},
            "modes_zh": {"DOMINATION": "制海权"},
        },
    )


def test_smoke_and_unspotted_state_machine_and_render(tmp_path: Path) -> None:
    store = _make_dummy_store(tmp_path)
    meta = {
        "clientVersionFromExe": "26,11,1,8871067",
        "mapName": "spaces/47_Sleeping_Giant",
        "gameType": "RandomBattle",
        "scenario": "domination_4point",
        "duration": 1200,
        "playerName": "tester",
    }
    sm = BattleStateMachine("dummy.korablireplay", meta, store)
    sm.set_player_vehicle(1001)
    sm.register_roster(
        [
            {
                "vehicle_id": 1001,
                "avatar_id": 1000,
                "account_db_id": 1,
                "team_id": 0,
                "ship_params_id": 4073633616,
                "max_hp": 83450,
                "player_name": "tester",
            },
            {
                "vehicle_id": 2001,
                "avatar_id": 2000,
                "account_db_id": 2,
                "team_id": 1,
                "ship_params_id": 4179539920,
                "max_hp": 44400,
                "player_name": "enemy_cruiser",
            },
        ]
    )

    # 1. 敌舰点亮并更新坐标
    sm.on_vehicle_create(1001, -100.0, 200.0, 3.14, {"health": 83450, "maxHealth": 83450, "isAlive": 1}, 10.0)
    sm.on_vehicle_create(2001, 120.0, -150.0, 0.5, {"health": 32000, "maxHealth": 44400, "isAlive": 1}, 10.0)

    # 2. 创建烟雾并追加新烟团
    sm.on_smoke_create(5001, 120.0, -150.0, {"radius": 18.0, "points": [(120.0, -150.0)]}, 12.0)
    sm.on_smoke_points_update(5001, [(120.0, -150.0), (145.0, -140.0)])

    # 3. 敌舰进入烟雾后触发灭点哨兵 (x_raw=0, y_raw=0, bit31=1)
    unspot_packed = 1 << 31
    sm.on_minimap_vision_info([{"vehicleID": 2001, "packedData": unspot_packed}], 15.0)
    sm.on_battle_logic_sync({"battleStage": 0, "timeLeft": 1185}, 15.0, time_left_changed=True)

    # 4. 校验灭点后位置保留 & 烟雾存在
    tl = sm.build_timeline()
    assert len(tl.frames) == 1
    f0 = tl.frames[0]
    enemy_st = f0.ships[2001]
    assert enemy_st.is_alive is True
    assert enemy_st.has_been_spotted is True
    assert enemy_st.is_spotted is False
    assert enemy_st.x == 120.0 and enemy_st.z == -150.0
    assert len(f0.smokes) == 1 and len(f0.smokes[0].points) == 2

    # 5. 渲染该帧确保无异常且画布尺寸准确
    renderer = MinimapFrameRenderer(tl, store, RenderConfig())
    img = renderer.render_frame(f0)
    assert img.size == (CANVAS_W, CANVAS_H)
    assert img.mode == "RGB"

    # 6. 烟雾消散
    sm.on_entity_leave(5001, 100.0)
    assert len(sm.smokes) == 0


def test_real_replay_regression_if_present() -> None:
    raw = os.environ.get("KORABLI_SAMPLE_REPLAY", "").strip()
    sample_replay = Path(raw) if raw else None
    if sample_replay is None or not sample_replay.is_file() or not list_synced_versions(DEFAULT_DATA_DIR):
        return

    tl, _store = parse_replay(sample_replay, data_dir=DEFAULT_DATA_DIR)
    assert tl.map_zh_name == "沉睡的渤海湾"
    assert tl.result_zh == "胜利"
    assert tl.diagnostics["ship_count"] == 24
    assert tl.diagnostics["ship_zh_resolved"] == 24
    assert tl.diagnostics["prop_errors"] == 0
    assert tl.diagnostics["nested_errors"] == 0
    assert tl.diagnostics["total_smokes_seen"] >= 10
    assert tl.diagnostics["total_unspot_events"] >= 50
    assert tl.diagnostics["total_kills"] == 20
    assert tl.diagnostics["total_chat_messages"] == 9

    # 校验局内聊天正确映射发送者与中文船名
    last_chats = tl.frames[-1].chat_messages
    assert len(last_chats) == 9
    assert any(c.sender_ship_zh == "米诺陶" and c.channel_zh == "全部" for c in last_chats)
    assert any(c.sender_ship_zh == "石墙" and c.channel_zh == "队友" for c in last_chats)

    # 回归断言：开局第 0 帧所有敌舰必须处于未发现满血状态，严禁错误堆叠在 (0, 0)
    f0 = tl.frames[0]
    for vid, meta in tl.ships_meta.items():
        st = f0.ships[vid]
        if meta.team_id == tl.enemy_team_id:
            assert st.hp == meta.max_hp
            assert st.has_been_spotted is False
            assert st.is_spotted is False


def test_hp_bar_three_tier_color_thresholds(tmp_path: Path) -> None:
    store = _make_dummy_store(tmp_path)
    meta = {
        "clientVersionFromExe": "26,11,1,8871067",
        "mapName": "spaces/47_Sleeping_Giant",
        "gameType": "RandomBattle",
        "scenario": "domination_4point",
        "duration": 1200,
        "playerName": "tester",
    }
    cfg = RenderConfig()
    assert cfg.fps == 30
    assert (CANVAS_W, CANVAS_H) == (1920, 1080)
    sm = BattleStateMachine("dummy.korablireplay", meta, store)
    ren = MinimapFrameRenderer(sm.build_timeline(), store, cfg)
    assert ren.hp_threshold_color(1.0) == ren.theme.hp_green
    assert ren.hp_threshold_color(0.70) == ren.theme.hp_green
    assert ren.hp_threshold_color(0.69) == ren.theme.hp_yellow
    assert ren.hp_threshold_color(0.30) == ren.theme.hp_yellow
    assert ren.hp_threshold_color(0.29) == ren.theme.hp_red
    assert ren.hp_threshold_color(0.0) == ren.theme.hp_red


def test_dead_ships_sorted_to_bottom_for_both_teams(tmp_path: Path) -> None:
    store = _make_dummy_store(tmp_path)
    meta = {
        "clientVersionFromExe": "26,11,1,8871067",
        "mapName": "spaces/47_Sleeping_Giant",
        "gameType": "RandomBattle",
        "scenario": "domination_4point",
        "duration": 1200,
        "playerName": "tester",
    }
    sm = BattleStateMachine("dummy.korablireplay", meta, store)
    sm.set_player_vehicle(1001)
    sm.register_roster(
        [
            {"vehicle_id": 1001, "avatar_id": 1000, "account_db_id": 1, "team_id": 0, "ship_params_id": 4073633616, "max_hp": 83450, "player_name": "p1"},
            {"vehicle_id": 1002, "avatar_id": 1003, "account_db_id": 2, "team_id": 0, "ship_params_id": 4179539920, "max_hp": 44400, "player_name": "p2"},
            {"vehicle_id": 2001, "avatar_id": 2000, "account_db_id": 3, "team_id": 1, "ship_params_id": 4073633616, "max_hp": 83450, "player_name": "e1"},
            {"vehicle_id": 2002, "avatar_id": 2003, "account_db_id": 4, "team_id": 1, "ship_params_id": 4179539920, "max_hp": 44400, "player_name": "e2"},
        ]
    )
    # 高级舰 1001 和 2001 阵亡，低级舰 1002 和 2002 存活，阵亡者应下沉到列表末尾
    sm.on_vehicle_death(1001, 2002, 17, 100.0)
    sm.on_vehicle_death(2001, 1002, 17, 110.0)
    sm.on_battle_logic_sync({"battleStage": 0, "timeLeft": 1090}, 110.0, time_left_changed=True)

    tl = sm.build_timeline()
    ren = MinimapFrameRenderer(tl, store, RenderConfig())
    f0 = tl.frames[0]
    ally_ids = [m.vehicle_id for m in ren.sort_roster_for_frame(ren.ally_roster, f0)]
    enemy_ids = [m.vehicle_id for m in ren.sort_roster_for_frame(ren.enemy_roster, f0)]
    assert ally_ids == [1002, 1001]
    assert enemy_ids == [2002, 2001]


def test_official_korabli_icons_rasterization() -> None:
    versions = list_synced_versions(DEFAULT_DATA_DIR)
    if not versions:
        return
    store = load_version_store(versions[0], data_dir=DEFAULT_DATA_DIR)
    if not store.icons_dir.is_dir():
        return

    factory = GameIconFactory(icons_dir=store.icons_dir)
    for sp in ("Destroyer", "Cruiser", "Battleship", "AirCarrier", "Submarine"):
        for state in ("spotted", "unspotted", "dead"):
            im = factory.get_minimap_ship_icon(sp, (76, 232, 170, 255), yaw_rad=0.5, state=state, height=21)
            assert im.mode == "RGBA"
            assert im.getbbox() is not None
        hud_im = factory.get_hud_class_icon(sp, (254, 77, 42, 255), size=22, flip_left=True)
        assert hud_im.size == (22, 22)
        assert hud_im.getbbox() is not None

    assert factory.get_frag_icon(17, height=16) is not None
    assert factory.get_damage_counter_icon("caused", size=16) is not None
