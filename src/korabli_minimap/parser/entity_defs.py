"""动态加载俄服客户端 entities.xml 与 *.def，生成精确的属性/方法网络索引表。

遵循 BigWorld 引擎确定性排序规范：
- 实体 typeID 为 entities.xml <ClientServerEntities> 的 1-based 序号；
- 客户端可见属性按固定字节大小升序排列（变长属性排在末尾，同大小保持定义顺序）；
- ClientMethods 按参数总大小与 VariableLengthHeaderSize 排序，生成与真实网络包一致的 method_id。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .bw_types import BWType, StreamReader, TypeRegistry


# 客户端可见（用于其他实体或自身实体）的 BigWorld Property Flags
CLIENT_VISIBLE_FLAGS = {
    "ALL_CLIENTS",
    "OTHER_CLIENTS",
    "OWN_CLIENT",
    "CELL_PUBLIC_AND_OWN",
    "BASE_AND_CLIENT",
}

# 在 EntityCreate (0x05) 初始状态块中广播的属性标志
ENTITY_CREATE_FLAGS = {
    "ALL_CLIENTS",
    "OTHER_CLIENTS",
}

# BigWorld 线缆大小排序键：按定长字节数升序，变长按 header_size 归入末尾
_VAR_SORT_BASE = 1_000_000


def _size_sort_key(fixed_size: int, header_size: int = 1) -> int:
    if fixed_size >= 0:
        return fixed_size
    return _VAR_SORT_BASE + header_size


@dataclass(frozen=True)
class PropertyDef:
    name: str
    bw_type: BWType
    flags: str
    header_size: int = 1

    @property
    def sort_key(self) -> int:
        return _size_sort_key(self.bw_type.fixed_size, self.header_size)


@dataclass(frozen=True)
class MethodArgDef:
    name: str
    bw_type: BWType


@dataclass(frozen=True)
class MethodDef:
    name: str
    args: tuple[MethodArgDef, ...]
    header_size: int = 1

    @property
    def fixed_size(self) -> int:
        if not self.args:
            return 0
        if all(a.bw_type.fixed_size >= 0 for a in self.args):
            return sum(a.bw_type.fixed_size for a in self.args)
        return -1

    @property
    def sort_key(self) -> int:
        return _size_sort_key(self.fixed_size, self.header_size)

    def decode_args(self, data: bytes) -> list[Any]:
        reader = StreamReader(data)
        return [arg.bw_type.decode(reader) for arg in self.args]


@dataclass
class EntityDef:
    """单个实体的完整客户端协议描述。"""

    type_id: int
    name: str
    all_client_properties: list[PropertyDef] = field(default_factory=list)   # 含 OWN_CLIENT（用于 0x07 / 0x24）
    public_properties: list[PropertyDef] = field(default_factory=list)       # 仅 ALL_CLIENTS / OTHER_CLIENTS（用于 0x05）
    client_methods: list[MethodDef] = field(default_factory=list)            # 排序后的 ClientMethods（用于 0x08）
    prop_by_name: dict[str, int] = field(default_factory=dict)
    method_by_name: dict[str, int] = field(default_factory=dict)

    def get_property(self, prop_id: int) -> PropertyDef | None:
        if 0 <= prop_id < len(self.all_client_properties):
            return self.all_client_properties[prop_id]
        return None

    def get_method(self, method_id: int) -> MethodDef | None:
        if 0 <= method_id < len(self.client_methods):
            return self.client_methods[method_id]
        return None

    def decode_create_state(self, state_bytes: bytes) -> dict[str, Any]:
        """解码 EntityCreate (0x05) 中的初始公共属性字典。"""
        reader = StreamReader(state_bytes)
        result: dict[str, Any] = {}
        # BigWorld EntityCreate (0x05) 状态段：1 字节属性数量 + 逐项 (prop_index_in_public, value) 或顺序全量
        if reader.remaining == 0:
            return result
        # 先按全量顺序尝试（BigWorld CreateEntity 默认顺序发送全部 public_properties，或带 1B count 前缀）
        saved = reader.pos
        # 情况 A：首字节 == len(public_properties)（无索引前缀，顺序排列）
        first_byte = state_bytes[0]
        if first_byte == len(self.public_properties):
            try:
                reader.pos = 1
                tmp: dict[str, Any] = {}
                for prop in self.public_properties:
                    tmp[prop.name] = prop.bw_type.decode(reader)
                if reader.remaining == 0:
                    return tmp
            except Exception:
                pass

        # 情况 B：无 count 前缀，直接顺序排列全部 public_properties
        try:
            reader.pos = saved
            tmp = {}
            for prop in self.public_properties:
                tmp[prop.name] = prop.bw_type.decode(reader)
            if reader.remaining == 0:
                return tmp
        except Exception:
            pass

        # 情况 C：带 u8 count + 每项 (u8 prop_idx, value)
        try:
            reader.pos = 0
            (count,) = reader.unpack("<B")
            tmp = {}
            for _ in range(count):
                (pidx,) = reader.unpack("<B")
                if pidx >= len(self.all_client_properties):
                    break
                prop = self.all_client_properties[pidx]
                tmp[prop.name] = prop.bw_type.decode(reader)
            if tmp and reader.remaining == 0:
                return tmp
        except Exception:
            pass

        # 情况 D：尽可能顺序解码（容错未来新版本末尾追加未知字段）
        reader.pos = 1 if first_byte == len(self.public_properties) else 0
        for prop in self.public_properties:
            if reader.remaining <= 0:
                break
            try:
                result[prop.name] = prop.bw_type.decode(reader)
            except Exception:
                break
        return result


class EntityDefinitions:
    """加载某一版本 entity_defs 目录下的全部实体与接口定义。"""

    def __init__(self, defs_dir: Path) -> None:
        self.defs_dir = defs_dir
        self.registry = TypeRegistry()
        self.entities_by_id: dict[int, EntityDef] = {}
        self.entities_by_name: dict[str, EntityDef] = {}
        self._load_all()

    def _load_all(self) -> None:
        alias_path = self.defs_dir / "alias.xml"
        if alias_path.is_file():
            self.registry.load_alias_xml(alias_path.read_bytes())

        entities_xml = self.defs_dir / "entities.xml"
        if not entities_xml.is_file():
            raise FileNotFoundError(f"缺少 entities.xml: {entities_xml}")

        root = ET.fromstring(entities_xml.read_bytes())
        cs_el = root.find("ClientServerEntities")
        if cs_el is None:
            raise ValueError("entities.xml 缺少 <ClientServerEntities>")

        for idx, child in enumerate(cs_el, start=1):
            ent_name = child.tag.strip()
            def_path = self.defs_dir / f"{ent_name}.def"
            if not def_path.is_file():
                continue
            ent_def = self._parse_entity_def(idx, ent_name, def_path)
            self.entities_by_id[idx] = ent_def
            self.entities_by_name[ent_name] = ent_def

    def _collect_sections(
        self,
        xml_path: Path,
        props: list[PropertyDef],
        methods: list[MethodDef],
        seen_interfaces: set[str],
    ) -> None:
        root = ET.fromstring(xml_path.read_bytes())

        # BigWorld 先合入 <Implements> 中的接口，再合入自身定义
        impl_el = root.find("Implements")
        if impl_el is not None:
            for iface in impl_el.findall("Interface"):
                iface_name = (iface.text or "").strip()
                if not iface_name or iface_name in seen_interfaces:
                    continue
                seen_interfaces.add(iface_name)
                iface_path = self.defs_dir / "interfaces" / f"{iface_name}.def"
                if iface_path.is_file():
                    self._collect_sections(iface_path, props, methods, seen_interfaces)

        # 收集属性
        props_el = root.find("Properties")
        if props_el is not None:
            for p in props_el:
                pname = p.tag.strip()
                flags_el = p.find("Flags")
                flags = (flags_el.text or "").strip() if flags_el is not None else ""
                if flags not in CLIENT_VISIBLE_FLAGS:
                    continue
                type_el = p.find("Type")
                if type_el is None:
                    continue
                bw_type = self.registry.parse_type_element(type_el, name_hint=pname)
                hdr_el = p.find("VariableLengthHeaderSize")
                hdr_txt = (hdr_el.text or "").strip() if hdr_el is not None else ""
                hdr_sz = int(hdr_txt) if hdr_txt.isdigit() else 1
                props.append(PropertyDef(name=pname, bw_type=bw_type, flags=flags, header_size=hdr_sz))

        # 收集 ClientMethods
        cm_el = root.find("ClientMethods")
        if cm_el is not None:
            for m in cm_el:
                mname = m.tag.strip()
                args: list[MethodArgDef] = []
                # 两种写法：<Arg>TYPE</Arg> 或 <Args><argName>TYPE</argName></Args>
                args_container = m.find("Args")
                if args_container is not None:
                    for a in args_container:
                        args.append(
                            MethodArgDef(
                                name=a.tag.strip(),
                                bw_type=self.registry.parse_type_element(a, name_hint=a.tag.strip()),
                            )
                        )
                else:
                    for idx, a in enumerate(m.findall("Arg")):
                        args.append(
                            MethodArgDef(
                                name=f"arg{idx}",
                                bw_type=self.registry.parse_type_element(a, name_hint=f"arg{idx}"),
                            )
                        )
                hdr_el = m.find("VariableLengthHeaderSize")
                hdr_txt = (hdr_el.text or "").strip() if hdr_el is not None else ""
                hdr_sz = int(hdr_txt) if hdr_txt.isdigit() else 1
                methods.append(MethodDef(name=mname, args=tuple(args), header_size=hdr_sz))

    def _parse_entity_def(self, type_id: int, name: str, def_path: Path) -> EntityDef:
        raw_props: list[PropertyDef] = []
        raw_methods: list[MethodDef] = []
        self._collect_sections(def_path, raw_props, raw_methods, seen_interfaces=set())

        # 去重保持首次出现顺序
        dedup_props: list[PropertyDef] = []
        seen_p: set[str] = set()
        for p in raw_props:
            if p.name not in seen_p:
                seen_p.add(p.name)
                dedup_props.append(p)

        dedup_methods: list[MethodDef] = []
        seen_m: set[str] = set()
        for m in raw_methods:
            if m.name not in seen_m:
                seen_m.add(m.name)
                dedup_methods.append(m)

        # BigWorld 引擎按线缆长度稳定排序以分配 prop_id / method_id
        sorted_all_props = sorted(dedup_props, key=lambda p: p.sort_key)
        sorted_public_props = [p for p in sorted_all_props if p.flags in ENTITY_CREATE_FLAGS]
        sorted_methods = sorted(dedup_methods, key=lambda m: m.sort_key)

        return EntityDef(
            type_id=type_id,
            name=name,
            all_client_properties=sorted_all_props,
            public_properties=sorted_public_props,
            client_methods=sorted_methods,
            prop_by_name={p.name: i for i, p in enumerate(sorted_all_props)},
            method_by_name={m.name: i for i, m in enumerate(sorted_methods)},
        )

    def get_by_id(self, type_id: int) -> EntityDef | None:
        return self.entities_by_id.get(type_id)

    def get_by_name(self, name: str) -> EntityDef | None:
        return self.entities_by_name.get(name)
