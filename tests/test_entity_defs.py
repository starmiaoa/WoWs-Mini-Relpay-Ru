"""BigWorld 动态 .def 排序与 NestedProperty 位流解码测试。"""

from __future__ import annotations

import struct

from korabli_minimap.config import DEFAULT_DATA_DIR
from korabli_minimap.extractor.store import list_synced_versions, load_version_store
from korabli_minimap.parser.bw_types import (
    ArrayType,
    DictField,
    FixedDictType,
    PrimitiveType,
    StreamReader,
)
from korabli_minimap.parser.entity_defs import EntityDef, EntityDefinitions, PropertyDef
from korabli_minimap.parser.nested_property import apply_nested_property


def test_stream_reader_packed_length() -> None:
    sr1 = StreamReader(bytes([12]) + b"hello_world!")
    assert sr1.read_packed_length() == 12
    assert sr1.read_bytes(12) == b"hello_world!"

    sr2 = StreamReader(bytes([0xFF, 0x04, 0x01, 0x00]))
    assert sr2.read_packed_length() == 260


def test_nested_property_fixed_dict_and_smoke_slice() -> None:
    # 构造精简 SmokeScreen 实体：仅 1 个 ARRAY<VECTOR2> 属性 points (idx=0)
    vec2 = PrimitiveType("VECTOR2", "<2f", unpack_single=False)
    arr_t = ArrayType(vec2, name="points")
    ed = EntityDef(
        type_id=4,
        name="SmokeScreen",
        all_client_properties=[PropertyDef("points", arr_t, "ALL_CLIENTS")],
        public_properties=[PropertyDef("points", arr_t, "ALL_CLIENTS")],
    )
    state = {"points": [(10.0, 20.0)]}

    # 1(top) + 0 bits (only 1 prop) + 0(final slice) + idx1=1(1b) + idx2=1(1b) + pad(4b) = 0b10110000 = 0xB0
    # 追加一个新烟团 (30.0, 40.0)
    payload = bytes([0xB0]) + struct.pack("<2f", 30.0, 40.0)
    res = apply_nested_property(ed, state, is_slice=True, payload=payload)
    assert res is not None
    assert res[0] == "points"
    assert len(state["points"]) == 2
    assert state["points"][1] == (30.0, 40.0)


def test_real_entity_defs_sorting_if_synced() -> None:
    versions = list_synced_versions(DEFAULT_DATA_DIR)
    if not versions:
        return
    store = load_version_store(versions[0], data_dir=DEFAULT_DATA_DIR)
    defs = EntityDefinitions(store.entity_defs_dir)
    smoke = defs.get_by_name("SmokeScreen")
    assert smoke is not None
    assert smoke.type_id == 4
    assert "points" in smoke.prop_by_name
    assert "radius" in smoke.prop_by_name

    avatar = defs.get_by_name("Avatar")
    assert avatar is not None
    assert "updateMinimapVisionInfo" in avatar.method_by_name
    assert "receiveVehicleDeath" in avatar.method_by_name
