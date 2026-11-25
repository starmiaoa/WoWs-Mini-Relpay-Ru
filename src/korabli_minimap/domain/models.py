"""与游戏版本完全解耦的标准化战局领域模型与时间轴快照。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# relation 语义约定
RELATION_SELF = -1
RELATION_ALLY = 0
RELATION_ENEMY = 1
RELATION_DIVISION = 2


@dataclass(frozen=True)
class ShipMeta:
    """单艘参战舰船的静态元数据（含汉化船名与舰种信息）。"""

    vehicle_id: int
    avatar_id: int
    account_db_id: int
    player_name: str
    clan_tag: str
    team_id: int
    relation: int           # -1=自己, 0=友军, 1=敌军, 2=小队队友
    ship_params_id: int
    internal_name: str
    index: str
    species: str            # Destroyer / Cruiser / Battleship / AirCarrier / Submarine / Auxiliary
    level: int
    nation: str
    zh_name: str
    ru_name: str
    max_hp: int
    main_battery_range_m: float = 20000.0
    division_id: int = 0
    chat_sender_id: int = 0


@dataclass(frozen=True)
class ChatMessage:
    """局内聊天消息记录（从 Avatar.onChatMessageRegular 解码）。"""

    clock: float
    time_left: int
    sender_vehicle_id: int | None
    sender_name: str
    sender_clan: str
    sender_ship_zh: str
    sender_relation: int    # -1=自己, 0=友军, 1=敌军, 2=小队, 9=系统/未知
    channel: str            # "battle_common" | "battle_team" | "battle_prebattle" | ...
    channel_zh: str         # "全部" | "队友" | "小队" | "系统"
    text: str


@dataclass(frozen=True)
class ActiveConsumable:
    """舰船当前激活的探测/功能消耗品（雷达/水听等）。"""

    consumable_type: str    # "rls" | "sonar" | "smokeGenerator" | ...
    consumable_id: int
    radius_bw: float
    expires_clock: float


@dataclass(frozen=True)
class ShipState:
    """某一帧时刻的单船状态快照。"""

    vehicle_id: int
    x: float
    z: float
    yaw_rad: float
    hp: int
    max_hp: int
    healable_hp: int
    is_alive: bool
    is_spotted: bool            # True=当前在点可见; False=灭点或尚未点亮
    has_been_spotted: bool      # True=整局至少被点亮过一次（用于绘制灭点位置与灭点船名）
    in_aoi: bool                # 是否在录制者 3D 渲染视野圈内（决定血量实时性）
    is_detected_by_enemy: bool  # 友方/己方当前是否被敌方点亮
    kills_count: int = 0        # 本船累计击沉数（用于 Tactical-Tech 面板击杀角标）
    active_consumables: tuple[ActiveConsumable, ...] = ()
    last_spotted_clock: float = 0.0
    death_clock: float | None = None


@dataclass(frozen=True)
class SmokeState:
    """地图上单个活跃烟雾实体快照（包含所有未消散烟团圆心与半径）。"""

    entity_id: int
    radius_bw: float
    points: tuple[tuple[float, float], ...]  # BigWorld (x, z)
    created_clock: float


@dataclass(frozen=True)
class CaptureZoneState:
    """占领点 / 基地状态快照。"""

    entity_id: int
    index: int
    label: str              # "A", "B", "C", "D" 或基地图标标记
    x: float
    z: float
    radius_bw: float
    owner_team: int         # -1=中立, 0/1=阵营 ID
    invader_team: int       # -1=无占领中, 0/1=正在占领方
    progress: float         # 0.0 ~ 1.0
    is_contested: bool
    control_point_type: int = 0


@dataclass(frozen=True)
class TorpedoState:
    """活跃鱼雷快照。"""

    key: str
    owner_id: int
    team_id: int
    relation: int
    x: float
    z: float
    yaw_rad: float


@dataclass(frozen=True)
class ShellTracer:
    """主炮/副炮在当前帧可见的弹道线段。"""

    owner_id: int
    relation: int
    ammo_type: str          # "HE" | "AP" | "CS"
    is_secondary: bool
    x0: float
    z0: float
    x1: float
    z1: float


@dataclass(frozen=True)
class PlaneState:
    """小地图航空中队快照。"""

    plane_id: int
    team_id: int
    relation: int
    x: float
    z: float
    yaw_rad: float
    purpose: int            # 0=作战中, 1=返航中


@dataclass(frozen=True)
class KillEvent:
    """击沉事件记录。"""

    clock: float
    victim_vehicle_id: int
    killer_vehicle_id: int
    death_type_id: int
    death_type_zh: str


@dataclass(frozen=True)
class SelfBattleStats:
    """主角累计战绩统计。"""

    enemy_damage: int = 0
    potential_damage: int = 0
    spotting_damage: int = 0
    kills: int = 0


@dataclass(frozen=True)
class BattleFrame:
    """时间轴单帧完整不可变状态快照（渲染器唯一输入）。"""

    frame_index: int
    clock: float
    time_left: int
    ally_score: int
    enemy_score: int
    win_score: int
    ally_ttw_sec: int | None
    enemy_ttw_sec: int | None
    ships: dict[int, ShipState]
    smokes: tuple[SmokeState, ...]
    capture_zones: tuple[CaptureZoneState, ...]
    torpedoes: tuple[TorpedoState, ...]
    shells: tuple[ShellTracer, ...]
    planes: tuple[PlaneState, ...]
    kills: tuple[KillEvent, ...]
    chat_messages: tuple[ChatMessage, ...] = ()
    self_stats: SelfBattleStats = field(default_factory=SelfBattleStats)


@dataclass
class BattleTimeline:
    """完整战局解析输出，供渲染器与质量检查使用。"""

    replay_path: str
    client_version: str
    short_version: str
    map_space: str
    map_zh_name: str
    battle_type: str
    battle_type_zh: str
    scenario: str
    scenario_zh: str
    date_time: str
    battle_duration: int
    player_name: str
    player_vehicle_id: int
    ally_team_id: int
    enemy_team_id: int
    win_team: int | None
    result_zh: str          # "胜利" | "失败" | "平局" | "战斗结束"
    ships_meta: dict[int, ShipMeta]
    frames: list[BattleFrame]
    diagnostics: dict[str, Any] = field(default_factory=dict)
