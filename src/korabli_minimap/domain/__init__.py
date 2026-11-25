"""领域模型与版本无关的战局状态机层。"""

from .battle_state import BattleStateMachine
from .models import (
    ActiveConsumable,
    BattleFrame,
    BattleTimeline,
    CaptureZoneState,
    ChatMessage,
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

__all__ = [
    "ActiveConsumable",
    "BattleFrame",
    "BattleStateMachine",
    "BattleTimeline",
    "CaptureZoneState",
    "ChatMessage",
    "KillEvent",
    "PlaneState",
    "RELATION_ALLY",
    "RELATION_DIVISION",
    "RELATION_ENEMY",
    "RELATION_SELF",
    "SelfBattleStats",
    "ShellTracer",
    "ShipMeta",
    "ShipState",
    "SmokeState",
    "TorpedoState",
]
