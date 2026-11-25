"""BigWorld NestedProperty (0x24) 位流路径解码器与实体状态原地更新器。

支持：
- SmokeScreen.points (ARRAY<VECTOR2> / ARRAY<VECTOR3>) 切片增删（烟团生成与消散）
- InteractiveZone.componentsState.captureLogic（占点进度与归属更新）
- BattleLogic.state.missions.teamsScore（实时比分更新）
- Vehicle / Avatar 嵌套属性更新
"""

from __future__ import annotations

from typing import Any

from .bw_types import ArrayType, BWType, FixedDictType, StreamReader
from .entity_defs import EntityDef


class BitReader:
    """BigWorld MSB-first 位流读取器（与 replays_unpack BitReader 协议一致）。"""

    __slots__ = ("data", "byte_pos", "bit_pos", "total_bytes")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.byte_pos = 0
        self.bit_pos = 0  # 0..7，表示当前字节已消费的高位数
        self.total_bytes = len(data)

    def read_bits(self, count: int) -> int:
        if count <= 0:
            return 0
        val = 0
        remaining = count
        while remaining > 0:
            if self.byte_pos >= self.total_bytes:
                raise EOFError("NestedProperty 位流越界")
            avail = 8 - self.bit_pos
            take = min(remaining, avail)
            shift = avail - take
            mask = (1 << take) - 1
            chunk = (self.data[self.byte_pos] >> shift) & mask
            val = (val << take) | chunk
            self.bit_pos += take
            if self.bit_pos == 8:
                self.bit_pos = 0
                self.byte_pos += 1
            remaining -= take
        return val

    def remaining_aligned_bytes(self) -> bytes:
        """对齐到下一字节边界并返回剩余所有原始字节（供标量/切片值解码）。"""
        pos = self.byte_pos + (1 if self.bit_pos > 0 else 0)
        return self.data[pos:]


def _bits_needed(max_exclusive: int) -> int:
    """编码 [0, max_exclusive - 1] 所需位数（与 BigWorld bitsRequired 一致）。"""
    if max_exclusive <= 1:
        return 0
    return (max_exclusive - 1).bit_length()


def apply_nested_property(
    ent_def: EntityDef,
    entity_state: dict[str, Any],
    is_slice: bool,
    payload: bytes,
) -> tuple[str, Any] | None:
    """解码并应用单个 NestedProperty 更新到 entity_state，返回 (路径字符串, 最新值)。"""
    if not payload:
        return None

    br = BitReader(payload)

    # BigWorld 路径以 1 bit 标志位开头（1 = 有顶层属性索引）
    cont = br.read_bits(1)
    if cont == 0:
        return None

    # 顶层索引：在 ent_def.all_client_properties 中定位根属性
    top_count = len(ent_def.all_client_properties)
    top_idx = br.read_bits(_bits_needed(top_count))
    if top_idx >= top_count:
        return None

    prop_def = ent_def.all_client_properties[top_idx]
    path_parts: list[str] = [prop_def.name]

    cur_parent: Any = entity_state
    cur_key: str | int = prop_def.name
    cur_type: BWType = prop_def.bw_type
    cur_val: Any = entity_state.get(prop_def.name)

    # BigWorld 路径编码规则：
    # - 1 = 下钻到子容器，读取下一级索引后继续循环
    # - 0 = 到达叶节点：
    #     * 若 is_slice=True 且当前节点是 ARRAY，则读取切片 (idx1, idx2)
    #     * 否则若当前节点是 FIXED_DICT 或 ARRAY，则读取最终叶字段/元素索引一次后立即结束
    while True:
        cont = br.read_bits(1)
        is_final_step = cont == 0

        if isinstance(cur_type, FixedDictType):
            n_fields = len(cur_type.fields)
            fidx = br.read_bits(_bits_needed(n_fields))
            if fidx >= n_fields:
                return None
            field_def = cur_type.fields[fidx]
            if not isinstance(cur_val, dict):
                cur_val = {}
                cur_parent[cur_key] = cur_val
            cur_parent = cur_val
            cur_key = field_def.name
            cur_type = field_def.bw_type
            cur_val = cur_val.get(field_def.name)
            path_parts.append(field_def.name)
            if is_final_step:
                break

        elif isinstance(cur_type, ArrayType):
            if not isinstance(cur_val, list):
                cur_val = []
                cur_parent[cur_key] = cur_val
            arr_len = len(cur_val)
            if is_final_step and is_slice:
                # 切片索引范围为 [0, arr_len]（共 arr_len + 1 个边界位置）
                sbits = _bits_needed(arr_len + 1)
                idx1 = br.read_bits(sbits)
                idx2 = br.read_bits(sbits)
                val_bytes = br.remaining_aligned_bytes()
                new_items: list[Any] = []
                if val_bytes:
                    sr = StreamReader(val_bytes)
                    while sr.remaining > 0:
                        try:
                            new_items.append(cur_type.element_type.decode(sr))
                        except EOFError:
                            break
                cur_val[idx1:idx2] = new_items
                return ".".join(path_parts), cur_val
            else:
                eidx = br.read_bits(_bits_needed(max(arr_len, 1)))
                while len(cur_val) <= eidx:
                    cur_val.append(None)
                cur_parent = cur_val
                cur_key = eidx
                cur_type = cur_type.element_type
                cur_val = cur_val[eidx]
                path_parts.append(f"[{eidx}]")
                if is_final_step:
                    break
        else:
            break

    # 普通叶节点赋值
    val_bytes = br.remaining_aligned_bytes()
    if not val_bytes:
        return None
    new_val = cur_type.decode_bytes(val_bytes)
    cur_parent[cur_key] = new_val
    return ".".join(path_parts), new_val
