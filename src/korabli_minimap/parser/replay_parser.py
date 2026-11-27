"""俄服 .korablireplay 完整回放解析编排器。

根据回放版本号自动加载对应客户端的 entity_defs 与包 ID 映射，
容错解码全部 BigWorld 网络包并驱动与版本无关的 BattleStateMachine。
"""

from __future__ import annotations

import logging
import struct
import zlib
from pathlib import Path
from typing import Any

import msgpack

from ..config import DEFAULT_DATA_DIR
from ..domain.battle_state import BattleStateMachine
from ..domain.models import BattleTimeline
from ..extractor.store import VersionDataStore, load_version_store
from .container import ReplayContainer, read_replay_container
from .entity_defs import EntityDefinitions
from .nested_property import apply_nested_property
from .packet_map import get_packet_map

logger = logging.getLogger(__name__)


def _extract_roster_from_0x1d(payload: bytes) -> list[dict[str, Any]]:
    """解析俄服 0x1d (ArenaState / BattleRoster) 中的 zlib + msgpack 全员名单。"""
    idx = 0
    best_list: list[Any] | None = None
    while idx < len(payload):
        pos = payload.find(b"\x78", idx)
        if pos == -1:
            break
        try:
            dec = zlib.decompress(payload[pos:])
            if len(dec) > 500:
                unp = msgpack.unpackb(dec, raw=False, strict_map_key=False)
                if isinstance(unp, list) and len(unp) >= 2 and isinstance(unp[0], list):
                    best_list = unp
                    break
            idx = pos + 2
        except Exception:
            idx = pos + 1

    if not best_list:
        return []

    players: list[dict[str, Any]] = []
    for raw_item in best_list:
        if not isinstance(raw_item, list):
            continue
        kv = {int(pair[0]): pair[1] for pair in raw_item if isinstance(pair, (list, tuple)) and len(pair) == 2}
        vehicle_id = int(kv.get(38, 0) or 0)
        if vehicle_id <= 0:
            continue
        players.append(
            {
                "account_db_id": int(kv.get(0, 0) or 0),
                "avatar_id": int(kv.get(3, 0) or 0),
                "clan_tag": str(kv.get(8, "") or ""),
                "chat_sender_id": int(kv.get(15, 0) or 0),
                "max_hp": int(kv.get(29, 0) or 0),
                "player_name": str(kv.get(30, "") or ""),
                "division_id": int(kv.get(32, 0) or 0),
                "vehicle_id": vehicle_id,
                "ship_params_id": int(kv.get(39) or kv.get(40) or 0),
                "team_id": int(kv.get(41, 0) or 0),
            }
        )
    return players


def _extract_block1_enrichment(post_battle: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    """从 Block 1 playersPublicInfo 按 account_db_id 提取补充字段（严禁把 row[37] 击杀者 ID 当成本船 vehicle_id）。"""
    if not post_battle:
        return {}
    ppi = post_battle.get("playersPublicInfo")
    if not isinstance(ppi, dict):
        return {}

    by_db: dict[int, dict[str, Any]] = {}
    for db_id_str, row in ppi.items():
        if not isinstance(row, list) or len(row) < 16:
            continue
        db_id = int(row[0] or int(db_id_str))
        by_db[db_id] = {
            "player_name": str(row[1] or ""),
            "clan_tag": str(row[3] or ""),
            "team_id": int(row[6] or 0),
            "ship_params_id": int(row[7] or 0),
            "division_id": int(row[14] or row[8] or 0) if isinstance(row[14], int) else 0,
            "max_hp": int(row[15] or 0) if isinstance(row[15], (int, float)) else 0,
        }
    return by_db


def _decode_damage_stat_blob(blob: Any) -> dict[Any, Any] | None:
    if not isinstance(blob, (bytes, bytearray)) or not blob:
        return None
    try:
        dec = zlib.decompress(blob)
    except Exception:
        dec = bytes(blob)
    try:
        obj = msgpack.unpackb(dec, raw=False, strict_map_key=False, use_list=False)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    return None


def parse_replay(
    replay_path: Path | str,
    data_dir: Path | str = DEFAULT_DATA_DIR,
    store: VersionDataStore | None = None,
    seconds_per_frame: float | None = None,
) -> tuple[BattleTimeline, VersionDataStore]:
    """解析单个俄服 .korablireplay 文件，返回标准化 BattleTimeline 与对应版本仓库。"""
    container: ReplayContainer = read_replay_container(replay_path)
    if store is None:
        store = load_version_store(container.client_version, data_dir=data_dir)

    defs = EntityDefinitions(store.entity_defs_dir)
    pmap = get_packet_map(container.client_version)
    sm = BattleStateMachine(
        replay_path=str(container.path),
        meta=container.meta,
        store=store,
        seconds_per_frame=seconds_per_frame,
    )

    # 仅按 account_db_id 准备 Block 1 补充映射，待 0x1d 权威名单到达时合并
    b1_by_db = _extract_block1_enrichment(container.post_battle)

    entity_types: dict[int, int] = {}
    entity_states: dict[int, dict[str, Any]] = {}

    # 诊断计数器
    total_packets = 0
    prop_errors = 0
    method_errors = 0
    nested_errors = 0

    for pkt in container.iter_packets():
        total_packets += 1
        pt = pkt.packet_type
        pl = pkt.payload
        clk = pkt.clock
        sm.on_clock_tick(clk)

        if pt == pmap.base_player_create and len(pl) >= 6:
            eid, tid = struct.unpack_from("<IH", pl, 0)
            entity_types[eid] = tid
            entity_states[eid] = {}
            sm.set_player_avatar(eid)

        elif pt == pmap.attached_vehicle and len(pl) >= 4:
            (vid,) = struct.unpack_from("<I", pl, 0)
            sm.set_player_vehicle(vid)

        elif pt == 0x36 and len(pl) >= 4:
            # 俄服 OwnShipEntityID
            (vid,) = struct.unpack_from("<I", pl, 0)
            sm.set_player_vehicle(vid)

        elif pt == pmap.arena_state_roster:
            roster = _extract_roster_from_0x1d(pl)
            if roster:
                # 用 Block 1 按 account_db_id 补充缺失字段（不覆盖真实 vehicle_id）
                for item in roster:
                    extra = b1_by_db.get(item.get("account_db_id", 0))
                    if extra:
                        if not item.get("division_id") and extra.get("division_id"):
                            item["division_id"] = extra["division_id"]
                        if not item.get("clan_tag") and extra.get("clan_tag"):
                            item["clan_tag"] = extra["clan_tag"]
                        if not item.get("max_hp") and extra.get("max_hp"):
                            item["max_hp"] = extra["max_hp"]
                sm.register_roster(roster)

        elif pt == pmap.entity_create and len(pl) >= 42:
            eid, tid, _sid, _vid, x, _y, z, _rx, ry, _rz, slen = struct.unpack_from("<IHIIffffffI", pl, 0)
            entity_types[eid] = tid
            ed = defs.get_by_id(tid)
            state: dict[str, Any] = {}
            if ed is not None:
                try:
                    state = ed.decode_create_state(pl[42 : 42 + slen])
                except Exception as exc:
                    logger.debug("EntityCreate %s(%d) 状态解码告警: %s", ed.name, eid, exc)
            entity_states[eid] = state

            if ed is not None:
                if ed.name == "Vehicle":
                    sm.on_vehicle_create(eid, x, z, ry, state, clk)
                elif ed.name == "SmokeScreen":
                    sm.on_smoke_create(eid, x, z, state, clk)
                elif ed.name == "InteractiveZone":
                    sm.on_zone_create(eid, x, z, state)
                elif ed.name == "BattleLogic":
                    sm.on_battle_logic_sync(state, clk, time_left_changed=False)

        elif pt == pmap.entity_leave and len(pl) >= 4:
            (eid,) = struct.unpack_from("<I", pl, 0)
            sm.on_entity_leave(eid, clk)

        elif pt == pmap.position and len(pl) >= 44:
            eid, _vid, x, _y, z, _ex, _ey, _ez, yaw, _pitch, _roll = struct.unpack_from("<IIfffffffff", pl, 0)
            ed = defs.get_by_id(entity_types.get(eid, 0))
            if ed is None or ed.name == "Vehicle":
                sm.on_vehicle_position(eid, x, z, yaw, clk)

        elif pt == pmap.player_position and len(pl) >= 32:
            eid1, eid2, x, _y, z, yaw, _pitch, _roll = struct.unpack_from("<IIffffff", pl, 0)
            target_vid = eid2 if eid2 != 0 else eid1
            if target_vid in sm.ships and (abs(x) > 0.01 or abs(z) > 0.01):
                sm.on_vehicle_position(target_vid, x, z, yaw, clk)

        elif pt == pmap.volatile_position and len(pl) >= 32:
            eid1, _eid2, x, _y, z, yaw, _pitch, _roll = struct.unpack_from("<IIffffff", pl, 0)
            if eid1 in sm.ships and (abs(x) > 0.01 or abs(z) > 0.01):
                sm.on_vehicle_position(eid1, x, z, yaw, clk)

        elif pt == pmap.entity_property and len(pl) >= 12:
            eid, pid, dlen = struct.unpack_from("<III", pl, 0)
            ed = defs.get_by_id(entity_types.get(eid, 0))
            if ed is None:
                continue
            prop = ed.get_property(pid)
            if prop is None:
                prop_errors += 1
                continue
            try:
                val = prop.bw_type.decode_bytes(pl[12 : 12 + dlen])
            except Exception:
                prop_errors += 1
                continue

            st = entity_states.setdefault(eid, {})
            st[prop.name] = val

            if ed.name == "Vehicle":
                sm.on_vehicle_property(eid, prop.name, val, clk)
            elif ed.name == "SmokeScreen":
                sm.on_smoke_property(eid, prop.name, val)
            elif ed.name == "InteractiveZone":
                sm.on_zone_state_sync(eid, st)
            elif ed.name == "BattleLogic":
                sm.on_battle_logic_sync(st, clk, time_left_changed=(prop.name == "timeLeft"))

        elif pt == pmap.nested_property and len(pl) >= 9:
            eid, is_slice, dlen = struct.unpack_from("<IBI", pl, 0)
            ed = defs.get_by_id(entity_types.get(eid, 0))
            if ed is None:
                continue
            st = entity_states.setdefault(eid, {})
            try:
                res = apply_nested_property(ed, st, bool(is_slice), pl[9 : 9 + dlen])
            except Exception:
                nested_errors += 1
                continue
            if res is None:
                nested_errors += 1
                continue
            path_str, new_val = res

            if ed.name == "SmokeScreen" and path_str == "points" and isinstance(new_val, list):
                sm.on_smoke_points_update(eid, new_val)
            elif ed.name == "InteractiveZone":
                sm.on_zone_state_sync(eid, st)
            elif ed.name == "BattleLogic":
                sm.on_battle_logic_sync(st, clk, time_left_changed=False)

        elif pt == pmap.entity_method and len(pl) >= 12:
            eid, mid, dlen = struct.unpack_from("<III", pl, 0)
            ed = defs.get_by_id(entity_types.get(eid, 0))
            if ed is None:
                continue
            method = ed.get_method(mid)
            if method is None:
                method_errors += 1
                continue

            # 仅对渲染所需的高价值方法解码，提升长回放解析速度并保持健壮
            mname = method.name
            if ed.name == "Avatar":
                if mname not in {
                    "updateMinimapVisionInfo",
                    "receiveVehicleDeath",
                    "receiveDamageStat",
                    "receiveArtilleryShots",
                    "receiveTorpedoes",
                    "receiveTorpedoDirection",
                    "receiveShotKills",
                    "receive_addMinimapSquadron",
                    "receive_updateMinimapSquadron",
                    "receive_removeMinimapSquadron",
                    "receive_deactivateSquadron",
                    "receive_wardAdded",
                    "receive_wardRemoved",
                    "onChatMessageRegular",
                    "onBattleEnd",
                }:
                    continue
                try:
                    args = method.decode_args(pl[12 : 12 + dlen])
                except Exception:
                    method_errors += 1
                    continue

                if mname == "updateMinimapVisionInfo" and len(args) >= 1:
                    sm.on_minimap_vision_info(args[0], clk)
                elif mname == "receiveVehicleDeath" and len(args) >= 3:
                    sm.on_vehicle_death(int(args[0]), int(args[1]), int(args[2]), clk)
                elif mname == "receiveDamageStat" and len(args) >= 1:
                    dmg_dict = _decode_damage_stat_blob(args[0])
                    if dmg_dict:
                        sm.on_damage_stat(dmg_dict)
                elif mname == "receiveArtilleryShots" and len(args) >= 1:
                    sm.on_artillery_shots(args[0], clk)
                elif mname == "receiveTorpedoes" and len(args) >= 1:
                    sm.on_torpedoes(args[0], clk)
                elif mname == "receiveTorpedoDirection" and len(args) >= 4:
                    sm.on_torpedo_direction(int(args[0]), int(args[1]), args[2], float(args[3]), clk)
                elif mname == "receiveShotKills" and len(args) >= 1:
                    sm.on_shot_kills(args[0])
                elif mname == "receive_addMinimapSquadron" and len(args) >= 5:
                    sm.on_plane_add(
                        plane_id=int(args[0]),
                        team_id=int(args[1]),
                        params_id=int(args[2]),
                        pos=args[3],
                        purpose=int(args[4]),
                        clock=clk,
                    )
                elif mname == "receive_updateMinimapSquadron" and len(args) >= 2:
                    sm.on_plane_update(int(args[0]), args[1], clock=clk)
                elif mname == "receive_removeMinimapSquadron" and len(args) >= 1:
                    sm.on_plane_remove(int(args[0]), clock=clk)
                elif mname == "receive_deactivateSquadron" and len(args) >= 1:
                    sm.on_plane_deactivate(int(args[0]))
                elif mname == "receive_wardAdded" and len(args) >= 2:
                    sm.on_ward_add(int(args[0]), args[1], clock=clk)
                elif mname == "receive_wardRemoved" and len(args) >= 1:
                    sm.on_ward_remove(int(args[0]))
                elif mname == "onChatMessageRegular" and len(args) >= 1:
                    raw_blob = args[0]
                    if isinstance(raw_blob, (bytes, bytearray)) and raw_blob:
                        try:
                            unp = msgpack.unpackb(raw_blob, raw=False)
                            if isinstance(unp, list) and len(unp) >= 2 and isinstance(unp[1], list) and len(unp[1]) >= 3:
                                text_msg = str(unp[1][0] or "")
                                sender_id = int(unp[1][1] or 0)
                                chan_name = str(unp[1][2] or "battle_common")
                                sm.on_chat_message(sender_id, chan_name, text_msg, clk)
                        except Exception:
                            pass
                elif mname == "onBattleEnd":
                    sm.on_battle_end(clk)

            elif ed.name == "Vehicle" and mname == "consumableUsed":
                try:
                    args = method.decode_args(pl[12 : 12 + dlen])
                    if len(args) >= 2:
                        sm.on_consumable_used(eid, int(args[0]), float(args[1]), clk)
                except Exception:
                    method_errors += 1

        else:
            # 忽略纯客户端摄像机/FPS/心跳包，未知包静默计数告警不中断
            if pt not in {0x01, 0x02, 0x0E, 0x0F, 0x10, 0x13, 0x16, 0x18, 0x1F, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x31, 0x32, 0x34, 0x35, 0xFFFFFFFF}:
                sm.unknown_packet_counts[pt] = sm.unknown_packet_counts.get(pt, 0) + 1

    timeline = sm.build_timeline(
        extra_diagnostics={
            "total_packets": total_packets,
            "prop_errors": prop_errors,
            "method_errors": method_errors,
            "nested_errors": nested_errors,
        }
    )
    return timeline, store
