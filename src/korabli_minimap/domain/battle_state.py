"""与游戏版本完全解耦的战局状态机：将底层实体/方法事件聚合为逐秒时间轴快照。"""

from __future__ import annotations

from bisect import bisect_right
import math
from dataclasses import dataclass, field, replace
from typing import Any

from ..extractor.store import VersionDataStore
from .models import (
    ActiveConsumable,
    BattleFrame,
    BattleTimeline,
    CaptureZoneState,
    ChatMessage,
    FighterWardState,
    KillEvent,
    PlaneState,
    RELATION_ALLY,
    RELATION_DIVISION,
    RELATION_ENEMY,
    RELATION_SELF,
    SelfBattleStats,
    ShellTracer,
    ShipMeta,
    ShipState,
    SmokeState,
    TorpedoState,
)

CHAT_CHANNEL_ZH: dict[str, str] = {
    "battle_common": "全部",
    "battle_team": "队友",
    "battle_prebattle": "小队",
    "battle_division": "小队",
}


# 俄服/战舰世界击沉原因中文映射（涵盖常见主炮、鱼雷、航空、起火、进水、撞击、深弹等）
DEATH_REASON_ZH: dict[int, str] = {
    0: "击沉",
    1: "击沉",
    2: "副炮",
    3: "鱼雷",
    4: "航弹",
    5: "航雷",
    6: "起火",
    7: "撞击",
    8: "进水",
    9: "主炮",
    10: "副炮",
    11: "火箭",
    12: "火箭",
    13: "深弹",
    14: "进水",
    15: "火箭",
    16: "主炮",
    17: "主炮",
    18: "主炮",
    19: "主炮",
    20: "航空",
    21: "副炮",
    22: "跳弹",
    23: "潜艇鱼雷",
    24: "导弹",
    25: "空袭",
    28: "空袭深弹",
}


@dataclass
class _MutableShip:
    meta: ShipMeta
    x: float = 0.0
    z: float = 0.0
    yaw_rad: float = 0.0
    hp: int = 0
    max_hp: int = 0
    healable_hp: int = 0
    is_alive: bool = True
    is_spotted: bool = False
    has_been_spotted: bool = False
    in_aoi: bool = False
    visibility_flags: int = 0
    last_spotted_clock: float = 0.0
    last_aoi_pos_clock: float = -999.0
    death_clock: float | None = None
    kills_count: int = 0
    active_consumables: dict[int, ActiveConsumable] = field(default_factory=dict)


@dataclass
class _MutableSmoke:
    entity_id: int
    radius_bw: float
    points: list[tuple[float, float]]
    created_clock: float


@dataclass
class _MutableZone:
    entity_id: int
    index: int
    x: float
    z: float
    radius_bw: float
    owner_team: int = -1
    invader_team: int = -1
    progress: float = 0.0
    both_inside: bool = False
    has_invaders: bool = False
    control_point_type: int = 0
    is_control_point: bool = False


@dataclass
class _MutableTorpedo:
    key: str
    owner_id: int
    team_id: int
    relation: int
    x: float
    z: float
    vx: float
    vz: float
    yaw_rad: float
    speed_bw: float
    last_clock: float
    spawn_clock: float
    max_life_sec: float = 120.0


@dataclass
class _MutableShell:
    owner_id: int
    relation: int
    ammo_type: str
    is_secondary: bool
    x0: float
    z0: float
    x1: float
    z1: float
    start_clock: float
    flight_time: float


@dataclass
class _MutablePlane:
    plane_id: int
    team_id: int
    relation: int
    x: float
    z: float
    prev_x: float
    prev_z: float
    yaw_rad: float = 0.0
    purpose: int = 0
    params_id: int = 0
    icon_name: str = "bomber_he"
    is_consumable: bool = False
    last_clock: float = 0.0


@dataclass
class _MutableFighterWard:
    squadron_id: int
    team_id: int
    relation: int
    x: float
    z: float
    radius_bw: float
    expires_clock: float


def _clean_internal_ship_name(internal: str) -> str:
    """当船名在词库中缺失时的最后兜底清洗，例如 PFSC211_La_Rochelle -> La Rochelle。"""
    if not internal:
        return "未知战舰"
    # 去掉前缀代号如 PFSC211_ / PFSC211-
    parts = internal.replace("-", "_").split("_")
    if len(parts) > 1 and parts[0].isalnum() and len(parts[0]) >= 5:
        return " ".join(parts[1:])
    return internal


class BattleStateMachine:
    """维护整场战斗的实时实体状态，并在每个战斗时钟秒点输出标准化快照。"""

    def __init__(
        self,
        replay_path: str,
        meta: dict[str, Any],
        store: VersionDataStore,
        seconds_per_frame: float = 1.0,
    ) -> None:
        self.replay_path = replay_path
        self.meta = meta
        self.store = store
        self.seconds_per_frame = max(0.25, float(seconds_per_frame))

        self.client_version = str(
            meta.get("clientVersionFromExe") or meta.get("clientVersionFromXml") or store.version
        )
        self.map_space = str(meta.get("mapName") or meta.get("mapDisplayName") or "").replace("spaces/", "").strip("/")
        self.battle_type = str(meta.get("gameType") or "RandomBattle")
        self.scenario = str(meta.get("scenario") or "")
        self.date_time = str(meta.get("dateTime") or "")
        self.battle_duration = int(meta.get("duration") or meta.get("battleDuration") or 1200)
        self.player_name = str(meta.get("playerName") or "")

        self.player_vehicle_id: int = 0
        self.player_avatar_id: int = 0
        self.ally_team_id: int = 0
        self.enemy_team_id: int = 1

        # 核心动态实体状态
        self.ships: dict[int, _MutableShip] = {}
        self.avatar_to_vehicle: dict[int, int] = {}
        self.chat_sender_to_vehicle: dict[int, int] = {}
        self.db_to_vehicle: dict[int, int] = {}
        self.smokes: dict[int, _MutableSmoke] = {}
        self.zones: dict[int, _MutableZone] = {}
        self.torpedoes: dict[str, _MutableTorpedo] = {}
        self.shells: list[_MutableShell] = []
        self.planes: dict[int, _MutablePlane] = {}
        self._plane_tracks: dict[int, list[tuple[float, float, float]]] = {}
        self.fighter_wards: dict[int, _MutableFighterWard] = {}
        self.kills: list[KillEvent] = []
        self.chat_messages: list[ChatMessage] = []

        # 战局宏观状态
        self.battle_stage: int = 2
        self.time_left: int = self.battle_duration
        self.win_score: int = 1000
        self.team_scores: dict[int, int] = {0: 0, 1: 0}
        self.hold_rules: list[dict[str, Any]] = []
        self.win_team: int | None = None
        self.finish_reason: int | None = None

        # 主角战绩（按 (weapon_type, stat_type) 汇总）
        self.damage_stat_map: dict[tuple[int, int], tuple[int, float]] = {}
        self.self_kills: int = 0

        # 时间轴采样状态
        self.frames: list[BattleFrame] = []
        self._last_snapshot_time_left: int | None = None
        self._last_snapshot_clock: float = -999.0
        self._battle_started: bool = False
        self._battle_ended: bool = False

        # 诊断统计（用于回归校验与排障）
        self.total_smokes_seen: int = 0
        self.total_unspot_events: int = 0
        self.total_spot_events: int = 0
        self.max_concurrent_smokes: int = 0
        self.max_concurrent_unspotted: int = 0
        self.unknown_packet_counts: dict[int, int] = {}

    def set_player_avatar(self, avatar_id: int) -> None:
        self.player_avatar_id = avatar_id

    def set_player_vehicle(self, vehicle_id: int) -> None:
        if vehicle_id > 0:
            self.player_vehicle_id = vehicle_id
            self._refresh_relations()

    def register_roster(self, raw_players: list[dict[str, Any]]) -> None:
        """从 0x1d 或 Block 1 注册全员参战舰船，自动完成俄服汉化映射。"""
        if not raw_players:
            return

        # 优先锁定录制者所在阵营与小队 ID
        self_division_id = 0
        for p in raw_players:
            vid = int(p.get("vehicle_id", 0) or 0)
            aid = int(p.get("avatar_id", 0) or 0)
            pname = str(p.get("player_name", "") or "")
            if (
                (self.player_vehicle_id and vid == self.player_vehicle_id)
                or (self.player_avatar_id and aid == self.player_avatar_id)
                or (self.player_name and pname == self.player_name)
            ):
                if vid > 0:
                    self.player_vehicle_id = vid
                if aid > 0:
                    self.player_avatar_id = aid
                self.ally_team_id = int(p.get("team_id", 0))
                self.enemy_team_id = 1 - self.ally_team_id
                self_division_id = int(p.get("division_id", 0) or 0)
                break

        for p in raw_players:
            vid = int(p.get("vehicle_id", 0) or 0)
            if vid <= 0:
                continue
            aid = int(p.get("avatar_id", 0) or 0)
            db_id = int(p.get("account_db_id", 0) or 0)
            team_id = int(p.get("team_id", 0))
            params_id = int(p.get("ship_params_id", 0) or 0)
            max_hp = int(p.get("max_hp", 0) or 0)
            pname = str(p.get("player_name", "") or f"Player_{vid}")
            clan = str(p.get("clan_tag", "") or "")
            div_id = int(p.get("division_id", 0) or 0)
            chat_sid = int(p.get("chat_sender_id", 0) or 0)

            ship_data = self.store.get_ship(params_id)
            if ship_data:
                internal_name = ship_data.get("internal_name", "")
                index = ship_data.get("index", "")
                species = ship_data.get("species", "Cruiser")
                level = int(ship_data.get("level", 10) or 10)
                nation = ship_data.get("nation", "")
                zh_name = ship_data.get("zh_name") or ship_data.get("ru_name") or _clean_internal_ship_name(internal_name)
                ru_name = ship_data.get("ru_name") or _clean_internal_ship_name(internal_name)
                if max_hp <= 0:
                    max_hp = int(ship_data.get("default_hp", 0) or 50000)
                main_range = float(ship_data.get("main_battery_range_m", 20000.0) or 20000.0)
            else:
                # 兜底：若录制者本身未在当前版本 GameParams 中命中，使用 meta.playerVehicle
                fallback_raw = str(self.meta.get("playerVehicle", "")) if vid == self.player_vehicle_id else ""
                internal_name = fallback_raw
                index = fallback_raw.split("-", 1)[0] if "-" in fallback_raw else ""
                species = "Cruiser"
                level = 10
                nation = ""
                zh_name = _clean_internal_ship_name(fallback_raw) if fallback_raw else f"战舰#{params_id}"
                ru_name = zh_name
                if max_hp <= 0:
                    max_hp = 50000
                main_range = 20000.0

            if vid == self.player_vehicle_id:
                relation = RELATION_SELF
            elif team_id == self.ally_team_id:
                if self_division_id > 0 and div_id == self_division_id:
                    relation = RELATION_DIVISION
                else:
                    relation = RELATION_ALLY
            else:
                relation = RELATION_ENEMY

            meta_obj = ShipMeta(
                vehicle_id=vid,
                avatar_id=aid,
                account_db_id=db_id,
                player_name=pname,
                clan_tag=clan,
                team_id=team_id,
                relation=relation,
                ship_params_id=params_id,
                internal_name=internal_name,
                index=index,
                species=species,
                level=level,
                nation=nation,
                zh_name=zh_name,
                ru_name=ru_name,
                max_hp=max_hp,
                main_battery_range_m=main_range,
                division_id=div_id,
                chat_sender_id=chat_sid,
            )

            existing = self.ships.get(vid)
            if existing is None:
                ship = _MutableShip(
                    meta=meta_obj,
                    hp=max_hp,
                    max_hp=max_hp,
                    is_alive=True,
                    # 己方与友军开局即在己方小地图视野内
                    is_spotted=(team_id == self.ally_team_id),
                    has_been_spotted=(team_id == self.ally_team_id),
                )
                self.ships[vid] = ship
            else:
                existing.meta = meta_obj
                if max_hp > existing.max_hp:
                    existing.max_hp = max_hp
                    if existing.hp == 0 and existing.is_alive:
                        existing.hp = max_hp

            if aid > 0:
                self.avatar_to_vehicle[aid] = vid
            if chat_sid > 0:
                self.chat_sender_to_vehicle[chat_sid] = vid
            if db_id > 0:
                self.db_to_vehicle[db_id] = vid

    def _refresh_relations(self) -> None:
        if not self.ships or not self.player_vehicle_id:
            return
        own = self.ships.get(self.player_vehicle_id)
        if own is None:
            return
        self.ally_team_id = own.meta.team_id
        self.enemy_team_id = 1 - self.ally_team_id
        own_div = own.meta.division_id
        for vid, s in self.ships.items():
            if vid == self.player_vehicle_id:
                rel = RELATION_SELF
            elif s.meta.team_id == self.ally_team_id:
                rel = RELATION_DIVISION if (own_div > 0 and s.meta.division_id == own_div) else RELATION_ALLY
            else:
                rel = RELATION_ENEMY
            if rel != s.meta.relation:
                m = s.meta
                s.meta = ShipMeta(
                    vehicle_id=m.vehicle_id,
                    avatar_id=m.avatar_id,
                    account_db_id=m.account_db_id,
                    player_name=m.player_name,
                    clan_tag=m.clan_tag,
                    team_id=m.team_id,
                    relation=rel,
                    ship_params_id=m.ship_params_id,
                    internal_name=m.internal_name,
                    index=m.index,
                    species=m.species,
                    level=m.level,
                    nation=m.nation,
                    zh_name=m.zh_name,
                    ru_name=m.ru_name,
                    max_hp=m.max_hp,
                    main_battery_range_m=m.main_battery_range_m,
                    division_id=m.division_id,
                )

    # ---------------- 实体与位置事件 ----------------

    def on_vehicle_create(
        self, vehicle_id: int, x: float, z: float, yaw: float, state: dict[str, Any], clock: float
    ) -> None:
        ship = self.ships.get(vehicle_id)
        if ship is None:
            return
        ship.in_aoi = True
        ship.x = x
        ship.z = z
        ship.yaw_rad = yaw
        ship.last_aoi_pos_clock = clock

        if "maxHealth" in state and float(state["maxHealth"]) > 0:
            ship.max_hp = int(round(float(state["maxHealth"])))
        if "health" in state:
            hp_val = int(round(float(state["health"])))
            if hp_val >= 0:
                ship.hp = hp_val
        if "isAlive" in state:
            ship.is_alive = bool(state["isAlive"])
        if "visibilityFlags" in state:
            ship.visibility_flags = int(state["visibilityFlags"])
        if "regenCrewHpLimit" in state:
            ship.healable_hp = int(round(float(state["regenCrewHpLimit"])))

        # 敌舰进入 3D AOI 意味着它当前处于被点亮状态（存活时）
        if ship.is_alive:
            if not ship.is_spotted:
                self.total_spot_events += 1
            ship.is_spotted = True
            ship.has_been_spotted = True
            ship.last_spotted_clock = clock

    def on_entity_leave(self, entity_id: int, clock: float) -> None:
        # 1. 烟雾消散
        if entity_id in self.smokes:
            del self.smokes[entity_id]
            return

        # 2. 船只离开 3D AOI（注意：友军离开 AOI 仍可在小地图上可见；敌军若不再可见将由 updateMinimapVisionInfo 触发灭点，若未触发也标记 in_aoi=False）
        ship = self.ships.get(entity_id)
        if ship is not None:
            ship.in_aoi = False

    def on_vehicle_position(self, vehicle_id: int, x: float, z: float, yaw: float, clock: float) -> None:
        ship = self.ships.get(vehicle_id)
        if ship is None or not ship.is_alive:
            return
        # 过滤全零无效坐标
        if x == 0.0 and z == 0.0 and ship.has_been_spotted and (abs(ship.x) > 50 or abs(ship.z) > 50):
            return
        ship.x = x
        ship.z = z
        ship.yaw_rad = yaw
        ship.last_aoi_pos_clock = clock
        ship.in_aoi = True
        if ship.meta.team_id == self.ally_team_id:
            ship.is_spotted = True
            ship.has_been_spotted = True
            ship.last_spotted_clock = clock

    def on_vehicle_property(self, vehicle_id: int, prop_name: str, value: Any, clock: float) -> None:
        ship = self.ships.get(vehicle_id)
        if ship is None:
            return
        if prop_name == "health":
            ship.hp = max(0, int(round(float(value))))
        elif prop_name == "maxHealth":
            mh = int(round(float(value)))
            if mh > 0:
                ship.max_hp = mh
        elif prop_name == "regenCrewHpLimit":
            ship.healable_hp = max(0, int(round(float(value))))
        elif prop_name == "isAlive":
            alive = bool(value)
            ship.is_alive = alive
            if not alive:
                ship.hp = 0
                ship.is_spotted = False
                if ship.death_clock is None:
                    ship.death_clock = clock
        elif prop_name == "visibilityFlags":
            ship.visibility_flags = int(value)

    def on_minimap_vision_info(self, ships_diff: list[dict[str, Any]], clock: float) -> None:
        """核心点亮 / 灭点（Unspotted）状态机。"""
        for item in ships_diff:
            vid = int(item.get("vehicleID", 0))
            packed = int(item.get("packedData", 0))
            ship = self.ships.get(vid)
            if ship is None or not ship.is_alive:
                continue

            x_raw = packed & 0x7FF
            y_raw = (packed >> 11) & 0x7FF
            yaw_raw = (packed >> 22) & 0xFF
            is_disappearing = (packed >> 31) & 1

            is_unspot_sentinel = (x_raw == 0 and y_raw == 0) or (is_disappearing == 1)

            if is_unspot_sentinel:
                # 灭点！保留最后已知坐标与航向，供渲染器绘制灭点图标 + 中文船名
                if ship.is_spotted and ship.meta.relation == RELATION_ENEMY:
                    self.total_unspot_events += 1
                if ship.meta.team_id != self.ally_team_id:
                    ship.is_spotted = False
                continue

            wx = x_raw / 2047.0 * 5000.0 - 2500.0
            wz = y_raw / 2047.0 * 5000.0 - 2500.0
            yaw_rad = yaw_raw / 256.0 * (2.0 * math.pi) - math.pi

            if not ship.is_spotted and ship.meta.relation == RELATION_ENEMY:
                self.total_spot_events += 1

            ship.is_spotted = True
            ship.has_been_spotted = True
            ship.last_spotted_clock = clock

            # 若船只当前不在 3D AOI（或超过 1.2 秒未收到高精度 Position 包），使用小地图包坐标
            if not ship.in_aoi or (clock - ship.last_aoi_pos_clock) > 1.2:
                ship.x = wx
                ship.z = wz
                ship.yaw_rad = yaw_rad

    # ---------------- 烟雾实体事件 ----------------

    def on_smoke_create(self, entity_id: int, x: float, z: float, state: dict[str, Any], clock: float) -> None:
        radius = float(state.get("radius") or state.get("bcRadius") or 17.0)
        raw_pts = state.get("points") or []
        pts: list[tuple[float, float]] = []
        for pt in raw_pts:
            if isinstance(pt, (tuple, list)) and len(pt) >= 2:
                # 兼容 VECTOR2 (x, z) 与 VECTOR3 (x, y, z)
                px = float(pt[0])
                pz = float(pt[2] if len(pt) >= 3 else pt[1])
                pts.append((px, pz))
        if not pts:
            pts.append((x, z))
        self.smokes[entity_id] = _MutableSmoke(
            entity_id=entity_id,
            radius_bw=radius,
            points=pts,
            created_clock=clock,
        )
        self.total_smokes_seen += 1

    def on_smoke_points_update(self, entity_id: int, raw_points: list[Any]) -> None:
        smoke = self.smokes.get(entity_id)
        if smoke is None:
            return
        pts: list[tuple[float, float]] = []
        for pt in raw_points:
            if isinstance(pt, (tuple, list)) and len(pt) >= 2:
                px = float(pt[0])
                pz = float(pt[2] if len(pt) >= 3 else pt[1])
                pts.append((px, pz))
        smoke.points = pts

    def on_smoke_property(self, entity_id: int, prop_name: str, value: Any) -> None:
        smoke = self.smokes.get(entity_id)
        if smoke is None:
            return
        if prop_name == "radius" and float(value) > 0:
            smoke.radius_bw = float(value)

    # ---------------- 占领点 / BattleLogic 事件 ----------------

    def on_zone_create(self, entity_id: int, x: float, z: float, state: dict[str, Any]) -> None:
        comp = state.get("componentsState") or {}
        cp = comp.get("controlPoint")
        cap = comp.get("captureLogic") or {}
        is_cp = isinstance(cp, dict) and bool(cp)
        idx = int(cp.get("index", len(self.zones))) if is_cp else len(self.zones)
        cp_type = int(cp.get("type", 0)) if is_cp else 0
        zone = _MutableZone(
            entity_id=entity_id,
            index=idx,
            x=x,
            z=z,
            radius_bw=float(state.get("radius") or 100.0),
            owner_team=int(state.get("teamId", -1)),
            invader_team=int(cap.get("invaderTeam", -1)),
            progress=float(cap.get("progress", 0.0)),
            both_inside=bool(cap.get("bothInside", False)),
            has_invaders=bool(cap.get("hasInvaders", False)),
            control_point_type=cp_type,
            is_control_point=is_cp,
        )
        self.zones[entity_id] = zone

    def on_zone_state_sync(self, entity_id: int, full_state: dict[str, Any]) -> None:
        zone = self.zones.get(entity_id)
        if zone is None:
            return
        if "teamId" in full_state:
            zone.owner_team = int(full_state["teamId"])
        if "radius" in full_state and float(full_state["radius"]) > 0:
            zone.radius_bw = float(full_state["radius"])
        comp = full_state.get("componentsState")
        if isinstance(comp, dict):
            cap = comp.get("captureLogic")
            if isinstance(cap, dict):
                if "progress" in cap:
                    zone.progress = max(0.0, min(1.0, float(cap["progress"])))
                if "invaderTeam" in cap:
                    zone.invader_team = int(cap["invaderTeam"])
                if "bothInside" in cap:
                    zone.both_inside = bool(cap["bothInside"])
                if "hasInvaders" in cap:
                    zone.has_invaders = bool(cap["hasInvaders"])

    def on_battle_logic_sync(self, full_state: dict[str, Any], clock: float, time_left_changed: bool = False) -> None:
        if "battleStage" in full_state:
            self.battle_stage = int(full_state["battleStage"])
            if self.battle_stage == 0:
                self._battle_started = True
        if "duration" in full_state and int(full_state["duration"]) > 0:
            self.battle_duration = int(full_state["duration"])
        if "timeLeft" in full_state:
            self.time_left = int(full_state["timeLeft"])
        if "battleResult" in full_state and isinstance(full_state["battleResult"], dict):
            br = full_state["battleResult"]
            wt = br.get("winnerTeamId")
            if wt is not None and int(wt) >= 0:
                self.win_team = int(wt)
            if "finishReason" in br:
                self.finish_reason = int(br["finishReason"])

        st = full_state.get("state")
        if isinstance(st, dict):
            missions = st.get("missions")
            if isinstance(missions, dict):
                ws = missions.get("teamWinScore")
                if ws is not None and int(ws) > 0:
                    self.win_score = int(ws)
                ts = missions.get("teamsScore")
                if isinstance(ts, list):
                    for item in ts:
                        if isinstance(item, dict) and "teamId" in item and "score" in item:
                            self.team_scores[int(item["teamId"])] = int(item["score"])
                hold = missions.get("hold")
                if isinstance(hold, list) and hold:
                    self.hold_rules = hold

        # 战斗开始后（battleStage == 0），每当 timeLeft 递减即采样 1 帧
        if time_left_changed and self.battle_stage == 0 and not self._battle_ended:
            if self._last_snapshot_time_left is None or self.time_left < self._last_snapshot_time_left:
                self._last_snapshot_time_left = self.time_left
                self._emit_frame(clock)

    def on_battle_end(self, clock: float) -> None:
        if not self._battle_ended:
            self._emit_frame(clock)
            self._battle_ended = True

    # ---------------- 战斗动作事件（击沉 / 炮弹 / 鱼雷 / 飞机 / 伤害 / 消耗品） ----------------

    def on_vehicle_death(self, victim_id: int, killer_id: int, death_reason_id: int, clock: float) -> None:
        victim = self.ships.get(victim_id)
        if victim is not None:
            victim.is_alive = False
            victim.hp = 0
            victim.is_spotted = False
            victim.death_clock = clock
        killer = self.ships.get(killer_id)
        if killer is not None and killer_id != victim_id:
            # 同阵营误伤不计击杀数，敌方击沉累加
            if victim is None or killer.meta.team_id != victim.meta.team_id:
                killer.kills_count += 1
        if killer_id == self.player_vehicle_id and victim_id != self.player_vehicle_id:
            self.self_kills += 1

        reason_zh = DEATH_REASON_ZH.get(death_reason_id, "击沉")
        self.kills.append(
            KillEvent(
                clock=clock,
                victim_vehicle_id=victim_id,
                killer_vehicle_id=killer_id,
                death_type_id=death_reason_id,
                death_type_zh=reason_zh,
            )
        )

    def on_chat_message(self, sender_id: int, channel: str, text: str, clock: float) -> None:
        clean_text = text.replace("\r", " ").replace("\n", " ").strip()
        if not clean_text:
            return
        vid = (
            self.chat_sender_to_vehicle.get(sender_id)
            or self.avatar_to_vehicle.get(sender_id)
            or self.db_to_vehicle.get(sender_id)
            or (sender_id if sender_id in self.ships else None)
        )
        ship = self.ships.get(vid) if vid is not None else None
        if ship is not None:
            m = ship.meta
            sender_name = m.player_name
            sender_clan = m.clan_tag
            sender_ship_zh = m.zh_name
            sender_rel = m.relation
        else:
            sender_name = "系统" if sender_id == 0 else f"玩家#{sender_id}"
            sender_clan = ""
            sender_ship_zh = ""
            sender_rel = 9

        chan_zh = CHAT_CHANNEL_ZH.get(channel, "队伍" if "team" in channel else "全部")
        self.chat_messages.append(
            ChatMessage(
                clock=clock,
                time_left=self.time_left,
                sender_vehicle_id=vid,
                sender_name=sender_name,
                sender_clan=sender_clan,
                sender_ship_zh=sender_ship_zh,
                sender_relation=sender_rel,
                channel=channel,
                channel_zh=chan_zh,
                text=clean_text,
            )
        )

    def on_damage_stat(self, stat_dict: dict[Any, Any]) -> None:
        for k, v in stat_dict.items():
            if isinstance(k, (tuple, list)) and len(k) == 2 and isinstance(v, (tuple, list)) and len(v) >= 2:
                self.damage_stat_map[(int(k[0]), int(k[1]))] = (int(v[0]), float(v[1]))

    def on_artillery_shots(self, packs: list[dict[str, Any]], clock: float) -> None:
        for pack in packs:
            owner_id = int(pack.get("ownerID", 0))
            params_id = int(pack.get("paramsID", 0))
            owner = self.ships.get(owner_id)
            rel = owner.meta.relation if owner else RELATION_ENEMY
            proj = self.store.get_projectile(params_id)
            ammo_type = (proj.get("ammo_type") if proj else "") or "HE"
            if ammo_type not in {"HE", "AP", "CS"}:
                ammo_type = "HE"
            is_sec = bool(proj.get("is_secondary")) if proj else False

            shots = pack.get("shots") or []
            # 为防止同齐射 12 根炮管重叠过密，最多采样 4 发代表性弹道
            step = max(1, len(shots) // 4)
            for s in shots[::step]:
                pos = s.get("pos")
                tpos = s.get("tarPos")
                if not pos or not tpos:
                    continue
                server_time_left = float(s.get("serverTimeLeft", 10.0) or 10.0)
                # BigWorld serverTimeLeft / 2.75 ≈ 实际飞行秒数
                flight_time = max(1.0, min(22.0, server_time_left / 2.75))
                self.shells.append(
                    _MutableShell(
                        owner_id=owner_id,
                        relation=rel,
                        ammo_type=ammo_type,
                        is_secondary=is_sec,
                        x0=float(pos[0]),
                        z0=float(pos[2]),
                        x1=float(tpos[0]),
                        z1=float(tpos[2]),
                        start_clock=clock,
                        flight_time=flight_time,
                    )
                )

    def on_torpedoes(self, packs: list[dict[str, Any]], clock: float) -> None:
        for pack in packs:
            owner_id = int(pack.get("ownerID", 0))
            params_id = int(pack.get("paramsID", 0))
            owner = self.ships.get(owner_id)
            team_id = owner.meta.team_id if owner else self.enemy_team_id
            rel = owner.meta.relation if owner else RELATION_ENEMY
            proj = self.store.get_projectile(params_id)
            max_dist_bw = float(proj.get("max_dist", 0.0) or 0.0) / 30.0 if proj else 0.0

            for torp in pack.get("torpedoes") or []:
                pos = torp.get("pos")
                tdir = torp.get("dir")
                shot_id = int(torp.get("shotID", 0))
                if not pos or not tdir:
                    continue
                vx = float(tdir[0])
                vz = float(tdir[2])
                speed_bw = math.hypot(vx, vz)
                if speed_bw < 0.05:
                    speed_bw = 3.5
                yaw = math.atan2(vx, vz)
                key = f"{owner_id}:{shot_id}"
                max_life = (max_dist_bw / speed_bw) if (max_dist_bw > 10 and speed_bw > 0.1) else 110.0
                self.torpedoes[key] = _MutableTorpedo(
                    key=key,
                    owner_id=owner_id,
                    team_id=team_id,
                    relation=rel,
                    x=float(pos[0]),
                    z=float(pos[2]),
                    vx=vx,
                    vz=vz,
                    yaw_rad=yaw,
                    speed_bw=speed_bw,
                    last_clock=clock,
                    spawn_clock=clock,
                    max_life_sec=min(160.0, max(25.0, max_life)),
                )

    def on_torpedo_direction(
        self, owner_id: int, shot_id: int, pos: tuple[float, float, float], yaw: float, clock: float
    ) -> None:
        key = f"{owner_id}:{shot_id}"
        t = self.torpedoes.get(key)
        if t is None:
            return
        t.x = float(pos[0])
        t.z = float(pos[2])
        if abs(math.degrees(yaw) - 360.0) > 1.0:
            t.yaw_rad = yaw
            t.vx = math.sin(yaw) * t.speed_bw
            t.vz = math.cos(yaw) * t.speed_bw
        t.last_clock = clock

    def on_shot_kills(self, packs: list[dict[str, Any]]) -> None:
        for pack in packs:
            owner_id = int(pack.get("ownerID", 0))
            for k in pack.get("kills") or []:
                shot_id = int(k.get("shotID", 0))
                self.torpedoes.pop(f"{owner_id}:{shot_id}", None)

    def on_plane_add(
        self,
        plane_id: int,
        team_id: int,
        params_id: int,
        pos: tuple[float, float],
        purpose: int,
        clock: float = 0.0,
    ) -> None:
        rel = RELATION_ALLY if team_id == self.ally_team_id else RELATION_ENEMY
        ac = self.store.get_aircraft(params_id)
        icon_name = str(ac["icon_name"]) if (ac and ac.get("icon_name")) else "bomber_he"
        is_cons = bool(ac.get("is_consumable")) if ac else False
        px, pz = float(pos[0]), float(pos[1])
        self.planes[plane_id] = _MutablePlane(
            plane_id=plane_id,
            team_id=team_id,
            relation=rel,
            x=px,
            z=pz,
            prev_x=px,
            prev_z=pz,
            purpose=purpose,
            params_id=params_id,
            icon_name=icon_name,
            is_consumable=is_cons,
            last_clock=clock,
        )
        self._plane_tracks[plane_id] = [(clock, px, pz)]

    def on_plane_update(self, plane_id: int, pos: tuple[float, float], clock: float = 0.0) -> None:
        p = self.planes.get(plane_id)
        if p is None:
            return
        nx, nz = float(pos[0]), float(pos[1])
        dx = nx - p.x
        dz = nz - p.z
        if abs(dx) > 0.5 or abs(dz) > 0.5:
            p.yaw_rad = math.atan2(dx, dz)
        p.prev_x, p.prev_z = p.x, p.z
        p.x, p.z = nx, nz
        p.last_clock = clock
        track = self._plane_tracks.setdefault(plane_id, [])
        if track and abs(clock - track[-1][0]) < 0.05:
            track[-1] = (clock, nx, nz)
        else:
            track.append((clock, nx, nz))

    def on_plane_deactivate(self, plane_id: int) -> None:
        p = self.planes.get(plane_id)
        if p is not None and not p.is_consumable:
            p.purpose = 1

    def on_plane_remove(self, plane_id: int, clock: float = 0.0) -> None:
        _ = clock
        self.planes.pop(plane_id, None)

    def on_ward_add(self, squadron_id: int, ward_info: Any, clock: float) -> None:
        """记录巡逻战斗机巡逻圈（wardTypes=2 且 wardShape=0 的圆形拦截区）。"""
        if not isinstance(ward_info, dict):
            return
        ward_shape = int(ward_info.get("wardShape", -1))
        ward_types = int(ward_info.get("wardTypes", 0))
        # wardTypes=2 & wardShape=0 为航母/舰船巡逻战斗机圆形防空圈（排除烟雾矩形/间谍侦察圈）
        if ward_shape != 0 or (ward_types & 2) == 0:
            return
        pos = ward_info.get("position")
        wsize = ward_info.get("wardSize")
        if not pos or not wsize or len(pos) < 3 or len(wsize) < 2:
            return
        radius_bw = float(wsize[1] or wsize[0] or 0.0)
        if radius_bw <= 5.0:
            return
        team_id = int(ward_info.get("teamId", self.enemy_team_id))
        rel = RELATION_ALLY if team_id == self.ally_team_id else RELATION_ENEMY
        rem = float(ward_info.get("timeRemaining", 60.0) or 60.0)
        self.fighter_wards[squadron_id] = _MutableFighterWard(
            squadron_id=squadron_id,
            team_id=team_id,
            relation=rel,
            x=float(pos[0]),
            z=float(pos[2]),
            radius_bw=radius_bw,
            expires_clock=clock + max(5.0, rem) + 2.0,
        )

    def on_ward_remove(self, squadron_id: int) -> None:
        self.fighter_wards.pop(squadron_id, None)

    def on_consumable_used(self, vehicle_id: int, consumable_id: int, work_time: float, clock: float) -> None:
        ship = self.ships.get(vehicle_id)
        if ship is None or work_time <= 0:
            return
        # 俄服常见雷达/水听 ID（若未来扩展可在 consumables.json 中动态查表）
        ctype = ""
        radius_bw = 0.0
        if consumable_id == 10:      # 俄服水听 sonar
            ctype = "sonar"
            radius_bw = 166.7        # 默认 5.0 km / 30m
        elif consumable_id == 12:    # 俄服雷达 rls
            ctype = "rls"
            radius_bw = 333.3        # 默认 10.0 km / 30m
        else:
            ctype = f"c_{consumable_id}"
        ship.active_consumables[consumable_id] = ActiveConsumable(
            consumable_type=ctype,
            consumable_id=consumable_id,
            radius_bw=radius_bw,
            expires_clock=clock + work_time,
        )

    # ---------------- 时间轴采样与终局整理 ----------------

    def _compute_ttw(self, team_id: int) -> int | None:
        """基于当前已占领点数与规则计算预计点数获胜剩余秒数。"""
        score = self.team_scores.get(team_id, 0)
        if score >= self.win_score:
            return 0
        rate_per_sec = 0.0
        owned_indices = {
            z.index for z in self.zones.values() if z.is_control_point and z.owner_team == team_id
        }
        if self.hold_rules:
            for rule in self.hold_rules:
                reward = float(rule.get("reward", 0) or 0)
                period = float(rule.get("period", 0) or 0)
                cp_indices = set(rule.get("cpIndices") or [])
                if reward > 0 and period > 0:
                    matched = len(owned_indices & cp_indices) if cp_indices else len(owned_indices)
                    rate_per_sec += matched * (reward / period)
        if rate_per_sec <= 0.001:
            return None
        return int(math.ceil((self.win_score - score) / rate_per_sec))

    def _emit_frame(self, clock: float) -> None:
        # 1. 清理过期弹道、推进鱼雷位置、清理过期消耗品
        active_shells: list[ShellTracer] = []
        still_alive_shells: list[_MutableShell] = []
        for sh in self.shells:
            elapsed = clock - sh.start_clock
            if elapsed < 0 or elapsed > sh.flight_time + 0.5:
                continue
            still_alive_shells.append(sh)
            t0 = max(0.0, (elapsed - 0.9) / sh.flight_time)
            t1 = min(1.0, elapsed / sh.flight_time)
            if t1 > t0:
                active_shells.append(
                    ShellTracer(
                        owner_id=sh.owner_id,
                        relation=sh.relation,
                        ammo_type=sh.ammo_type,
                        is_secondary=sh.is_secondary,
                        x0=sh.x0 + (sh.x1 - sh.x0) * t0,
                        z0=sh.z0 + (sh.z1 - sh.z0) * t0,
                        x1=sh.x0 + (sh.x1 - sh.x0) * t1,
                        z1=sh.z0 + (sh.z1 - sh.z0) * t1,
                    )
                )
        self.shells = still_alive_shells

        active_torps: list[TorpedoState] = []
        expired_torp_keys: list[str] = []
        for key, t in self.torpedoes.items():
            age = clock - t.spawn_clock
            if age > t.max_life_sec or abs(t.x) > 1500 or abs(t.z) > 1500:
                expired_torp_keys.append(key)
                continue
            dt = max(0.0, clock - t.last_clock)
            t.x += t.vx * dt
            t.z += t.vz * dt
            t.last_clock = clock
            active_torps.append(
                TorpedoState(
                    key=t.key,
                    owner_id=t.owner_id,
                    team_id=t.team_id,
                    relation=t.relation,
                    x=t.x,
                    z=t.z,
                    yaw_rad=t.yaw_rad,
                )
            )
        for k in expired_torp_keys:
            self.torpedoes.pop(k, None)

        # 2. 舰船快照与统计
        ship_snaps: dict[int, ShipState] = {}
        unspotted_now = 0
        for vid, s in self.ships.items():
            # 过期消耗品清理
            active_cons = tuple(
                c for c in s.active_consumables.values() if c.expires_clock > clock
            )
            s.active_consumables = {c.consumable_id: c for c in active_cons}

            if s.is_alive and s.has_been_spotted and not s.is_spotted and s.meta.relation == RELATION_ENEMY:
                unspotted_now += 1

            ship_snaps[vid] = ShipState(
                vehicle_id=vid,
                x=s.x,
                z=s.z,
                yaw_rad=s.yaw_rad,
                hp=s.hp,
                max_hp=s.max_hp,
                healable_hp=s.healable_hp,
                is_alive=s.is_alive,
                is_spotted=s.is_spotted,
                has_been_spotted=s.has_been_spotted,
                in_aoi=s.in_aoi,
                is_detected_by_enemy=(s.visibility_flags > 0),
                kills_count=s.kills_count,
                active_consumables=active_cons,
                last_spotted_clock=s.last_spotted_clock,
                death_clock=s.death_clock,
            )

        self.max_concurrent_unspotted = max(self.max_concurrent_unspotted, unspotted_now)

        # 3. 烟雾快照
        smoke_snaps = tuple(
            SmokeState(
                entity_id=sm.entity_id,
                radius_bw=sm.radius_bw,
                points=tuple(sm.points),
                created_clock=sm.created_clock,
            )
            for sm in self.smokes.values()
            if sm.points
        )
        self.max_concurrent_smokes = max(self.max_concurrent_smokes, len(smoke_snaps))

        # 4. 占领点快照
        cp_zones = sorted(
            [z for z in self.zones.values() if z.is_control_point],
            key=lambda z: z.index,
        )
        zone_snaps = tuple(
            CaptureZoneState(
                entity_id=z.entity_id,
                index=z.index,
                label=chr(ord("A") + z.index) if 0 <= z.index < 26 else str(z.index + 1),
                x=z.x,
                z=z.z,
                radius_bw=z.radius_bw,
                owner_team=z.owner_team,
                invader_team=z.invader_team,
                progress=z.progress,
                is_contested=z.both_inside,
                control_point_type=z.control_point_type,
            )
            for z in cp_zones
        )

        # 5. 飞机快照与巡逻战斗机防空圈快照
        plane_snaps = tuple(
            PlaneState(
                plane_id=p.plane_id,
                team_id=p.team_id,
                relation=p.relation,
                x=p.x,
                z=p.z,
                yaw_rad=p.yaw_rad,
                purpose=p.purpose,
                params_id=p.params_id,
                icon_name=p.icon_name,
                is_consumable=p.is_consumable,
            )
            for p in self.planes.values()
        )
        expired_wards = [sid for sid, w in self.fighter_wards.items() if w.expires_clock < clock]
        for sid in expired_wards:
            self.fighter_wards.pop(sid, None)
        ward_snaps = tuple(
            FighterWardState(
                squadron_id=w.squadron_id,
                team_id=w.team_id,
                relation=w.relation,
                x=w.x,
                z=w.z,
                radius_bw=w.radius_bw,
            )
            for w in self.fighter_wards.values()
        )

        # 6. 主角伤害汇总（俄服实测：stat_type 0=对敌伤害, 2=潜在伤害, 3=侦察伤害）
        enemy_dmg = 0.0
        spot_dmg = 0.0
        pot_dmg = 0.0
        for (_wpn, stat_type), (_hits, dmg) in self.damage_stat_map.items():
            if stat_type == 0:
                enemy_dmg += dmg
            elif stat_type == 2:
                pot_dmg += dmg
            elif stat_type == 3:
                spot_dmg += dmg

        frame = BattleFrame(
            frame_index=len(self.frames),
            clock=clock,
            time_left=self.time_left,
            ally_score=self.team_scores.get(self.ally_team_id, 0),
            enemy_score=self.team_scores.get(self.enemy_team_id, 0),
            win_score=self.win_score,
            ally_ttw_sec=self._compute_ttw(self.ally_team_id),
            enemy_ttw_sec=self._compute_ttw(self.enemy_team_id),
            ships=ship_snaps,
            smokes=smoke_snaps,
            capture_zones=zone_snaps,
            torpedoes=tuple(active_torps),
            shells=tuple(active_shells),
            planes=plane_snaps,
            kills=tuple(self.kills),
            chat_messages=tuple(self.chat_messages),
            self_stats=SelfBattleStats(
                enemy_damage=int(round(enemy_dmg)),
                potential_damage=int(round(pot_dmg)),
                spotting_damage=int(round(spot_dmg)),
                kills=self.self_kills,
            ),
            fighter_wards=ward_snaps,
        )
        self.frames.append(frame)
        self._last_snapshot_clock = clock

    @staticmethod
    def _sample_plane_hermite(
        ts: list[float],
        pts: list[tuple[float, float, float]],
        t: float,
        is_consumable: bool,
        fallback_x: float,
        fallback_z: float,
        fallback_yaw: float,
    ) -> tuple[float, float, float]:
        """对服务器 2.0s 离散小地图飞机采样点做时间加权三次 Hermite（Catmull-Rom）平滑插值。

        消除“每 2 秒跳一次、中间 1 帧卡住不动”的 15fps 顿挫感，使 CV 中队直线突击与战斗机盘旋丝滑连贯。
        """
        n = len(pts)
        if n < 2:
            return fallback_x, fallback_z, fallback_yaw

        hi = bisect_right(ts, t)
        if hi <= 0:
            return pts[0][1], pts[0][2], fallback_yaw

        if hi >= n:
            # 位于最后一次 2s 更新与 removeMinimapSquadron 之间的尾帧：温和按末段速度外推
            t_prev, x_prev, z_prev = pts[-2]
            t_last, x_last, z_last = pts[-1]
            dt_prev = t_last - t_prev
            dt_tail = t - t_last
            if dt_prev > 0.1 and dt_prev <= 3.5 and dt_tail > 0.0:
                vx = (x_last - x_prev) / dt_prev
                vz = (z_last - z_prev) / dt_prev
                spd = math.hypot(vx, vz)
                if 0.1 < spd < 35.0:
                    cap = 0.7 if is_consumable else 1.4
                    ext = min(dt_tail, cap)
                    return x_last + vx * ext, z_last + vz * ext, math.atan2(vx, vz)
            return x_last, z_last, fallback_yaw

        t1, x1, z1 = pts[hi - 1]
        t2, x2, z2 = pts[hi]
        dt = t2 - t1
        if dt <= 1e-4:
            return x2, z2, fallback_yaw

        # 异常大跳跃/长间隔（如重新出现）不做跨图插值
        seg_dist = math.hypot(x2 - x1, z2 - z1)
        if dt > 5.0 or seg_dist > 180.0:
            return x1, z1, fallback_yaw

        u = max(0.0, min(1.0, (t - t1) / dt))
        vx_seg = (x2 - x1) / dt
        vz_seg = (z2 - z1) / dt

        # 时间加权速度切线（在等速直线上精确退化为线性运动，在弯道/盘旋上平滑 C¹ 连续）
        if hi - 2 >= 0 and (t1 - pts[hi - 2][0]) > 0.1 and (t1 - pts[hi - 2][0]) <= 3.5:
            dt0 = t1 - pts[hi - 2][0]
            v0x = (x1 - pts[hi - 2][1]) / dt0
            v0z = (z1 - pts[hi - 2][2]) / dt0
            m1x = 0.5 * (v0x + vx_seg) * dt
            m1z = 0.5 * (v0z + vz_seg) * dt
        else:
            m1x = x2 - x1
            m1z = z2 - z1

        if hi + 1 < n and (pts[hi + 1][0] - t2) > 0.1 and (pts[hi + 1][0] - t2) <= 3.5:
            dt3 = pts[hi + 1][0] - t2
            v3x = (pts[hi + 1][1] - x2) / dt3
            v3z = (pts[hi + 1][2] - z2) / dt3
            m2x = 0.5 * (vx_seg + v3x) * dt
            m2z = 0.5 * (vz_seg + v3z) * dt
        else:
            m2x = x2 - x1
            m2z = z2 - z1

        u2 = u * u
        u3 = u2 * u
        h00 = 2.0 * u3 - 3.0 * u2 + 1.0
        h10 = u3 - 2.0 * u2 + u
        h01 = -2.0 * u3 + 3.0 * u2
        h11 = u3 - u2

        ix = h00 * x1 + h10 * m1x + h01 * x2 + h11 * m2x
        iz = h00 * z1 + h10 * m1z + h01 * z2 + h11 * m2z

        # 由 Hermite 一阶导数求平滑航向
        dh00 = 6.0 * u2 - 6.0 * u
        dh10 = 3.0 * u2 - 4.0 * u + 1.0
        dh01 = -6.0 * u2 + 6.0 * u
        dh11 = 3.0 * u2 - 2.0 * u
        dx = dh00 * x1 + dh10 * m1x + dh01 * x2 + dh11 * m2x
        dz = dh00 * z1 + dh10 * m1z + dh01 * z2 + dh11 * m2z
        iyaw = math.atan2(dx, dz) if (abs(dx) > 1e-3 or abs(dz) > 1e-3) else fallback_yaw
        return ix, iz, iyaw

    def _apply_smooth_plane_interpolation(self) -> list[BattleFrame]:
        """利用完整时间轴前后采样点对所有飞机轨迹进行三次样条平滑插值。"""
        if not self.frames or not self._plane_tracks:
            return self.frames

        track_index: dict[int, tuple[list[float], list[tuple[float, float, float]]]] = {}
        for pid, pts in self._plane_tracks.items():
            if len(pts) >= 2:
                track_index[pid] = ([pt[0] for pt in pts], pts)

        if not track_index:
            return self.frames

        smoothed: list[BattleFrame] = []
        for frame in self.frames:
            if not frame.planes:
                smoothed.append(frame)
                continue
            t = frame.clock
            new_planes: list[PlaneState] = []
            changed = False
            for p in frame.planes:
                entry = track_index.get(p.plane_id)
                if entry is None:
                    new_planes.append(p)
                    continue
                ts, pts = entry
                ix, iz, iyaw = self._sample_plane_hermite(
                    ts, pts, t, p.is_consumable, p.x, p.z, p.yaw_rad
                )
                if ix != p.x or iz != p.z or iyaw != p.yaw_rad:
                    new_planes.append(replace(p, x=ix, z=iz, yaw_rad=iyaw))
                    changed = True
                else:
                    new_planes.append(p)
            smoothed.append(replace(frame, planes=tuple(new_planes)) if changed else frame)
        return smoothed

    def build_timeline(self, extra_diagnostics: dict[str, Any] | None = None) -> BattleTimeline:
        if not self.frames and self.ships:
            self._emit_frame(0.0)
        smoothed_frames = self._apply_smooth_plane_interpolation()

        # 推断胜负文案
        if self.win_team is not None:
            if self.win_team == self.ally_team_id:
                result_zh = "胜利"
            elif self.win_team == self.enemy_team_id:
                result_zh = "失败"
            else:
                result_zh = "平局"
        else:
            # 若回放未记录到 battleResult，按终局比分兜底
            a_sc = self.team_scores.get(self.ally_team_id, 0)
            e_sc = self.team_scores.get(self.enemy_team_id, 0)
            if a_sc > e_sc:
                result_zh = "胜利"
            elif e_sc > a_sc:
                result_zh = "失败"
            else:
                result_zh = "战斗结束"

        diag: dict[str, Any] = {
            "total_frames": len(self.frames),
            "ship_count": len(self.ships),
            "ship_zh_resolved": sum(
                1 for s in self.ships.values() if not s.meta.zh_name.startswith("未知战舰")
            ),
            "total_smokes_seen": self.total_smokes_seen,
            "max_concurrent_smokes": self.max_concurrent_smokes,
            "total_spot_events": self.total_spot_events,
            "total_unspot_events": self.total_unspot_events,
            "max_concurrent_unspotted": self.max_concurrent_unspotted,
            "total_kills": len(self.kills),
            "total_chat_messages": len(self.chat_messages),
            "capture_zone_count": sum(1 for z in self.zones.values() if z.is_control_point),
            "unknown_packet_counts": dict(self.unknown_packet_counts),
        }
        if extra_diagnostics:
            diag.update(extra_diagnostics)

        return BattleTimeline(
            replay_path=self.replay_path,
            client_version=self.client_version,
            short_version=self.store.version,
            map_space=self.map_space,
            map_zh_name=self.store.get_map_name_zh(self.map_space),
            battle_type=self.battle_type,
            battle_type_zh=self.store.get_battle_type_zh(self.battle_type),
            scenario=self.scenario,
            scenario_zh=self.store.get_scenario_zh(self.scenario),
            date_time=self.date_time,
            battle_duration=self.battle_duration,
            player_name=self.player_name,
            player_vehicle_id=self.player_vehicle_id,
            ally_team_id=self.ally_team_id,
            enemy_team_id=self.enemy_team_id,
            win_team=self.win_team,
            result_zh=result_zh,
            ships_meta={vid: s.meta for vid, s in self.ships.items()},
            frames=smoothed_frames,
            diagnostics=diag,
        )
