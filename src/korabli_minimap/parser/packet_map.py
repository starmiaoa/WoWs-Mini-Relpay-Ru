"""俄服（Lesta / Mir Korabley）版本化网络包 ID 映射表。

当未来客户端版本变更包 ID 时，只需在此注册新版本覆盖项即可，无需改动状态机或渲染层。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..extractor.client_locator import normalize_short_version


@dataclass(frozen=True)
class PacketIdMap:
    """单个俄服协议世代的网络包类型 ID 映射。"""

    base_player_create: int = 0x00
    cell_player_create: int = 0x01
    entity_control: int = 0x02
    entity_enter: int = 0x03
    entity_leave: int = 0x04
    entity_create: int = 0x05
    entity_property: int = 0x07
    entity_method: int = 0x08
    position: int = 0x0A
    version: int = 0x16
    arena_state_roster: int = 0x1D
    attached_vehicle: int = 0x22
    nested_property: int = 0x24
    map_geometry: int = 0x2A
    volatile_position: int = 0x2C
    player_position: int = 0x2E


# 默认适配 Lesta 25.x / 26.x+（已针对 26.11.1 全量包流实测验证）
LESTA_26_PACKETS = PacketIdMap()

# 若需兼容更早的 Lesta 13.x 历史回放，可按主版本回退
LESTA_13_PACKETS = PacketIdMap(
    nested_property=0x23,
    map_geometry=0x28,
    volatile_position=0x2A,
    player_position=0x2C,
)


def get_packet_map(client_version: str) -> PacketIdMap:
    """根据回放客户端版本返回对应的俄服包 ID 映射。"""
    short = normalize_short_version(client_version)
    segs = [int(s) for s in short.split(".") if s.isdigit()]
    major = segs[0] if segs else 26
    if major < 25:
        return LESTA_13_PACKETS
    return LESTA_26_PACKETS
