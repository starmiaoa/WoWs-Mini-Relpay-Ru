"""BigWorld 引擎 XML 数据类型系统与二进制解码器（动态解析 alias.xml 与 *.def）。"""

from __future__ import annotations

import io
import pickle
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any


class StreamReader:
    """轻量级字节流读取器，支持 BigWorld 变长前缀与基础类型解包。"""

    __slots__ = ("buf", "pos", "length")

    def __init__(self, data: bytes) -> None:
        self.buf = data
        self.pos = 0
        self.length = len(data)

    @property
    def remaining(self) -> int:
        return self.length - self.pos

    def read_bytes(self, n: int) -> bytes:
        if n < 0 or self.pos + n > self.length:
            raise EOFError(f"流截断: 需要 {n}B，仅剩 {self.remaining}B")
        out = self.buf[self.pos : self.pos + n]
        self.pos += n
        return out

    def unpack(self, fmt: str) -> tuple[Any, ...]:
        sz = struct.calcsize(fmt)
        if self.pos + sz > self.length:
            raise EOFError(f"流截断({fmt}): 需要 {sz}B，仅剩 {self.remaining}B")
        vals = struct.unpack_from(fmt, self.buf, self.pos)
        self.pos += sz
        return vals

    def read_packed_length(self) -> int:
        """BigWorld 紧凑长度前缀：1 字节 <255，或 0xFF + 3 字节小端无符号整数。"""
        (b0,) = self.unpack("<B")
        if b0 == 0xFF:
            raw = self.read_bytes(3)
            return raw[0] | (raw[1] << 8) | (raw[2] << 16)
        return b0


class BWType:
    """BigWorld 类型基类。fixed_size > 0 表示定长字节数，-1 表示变长。"""

    fixed_size: int = -1
    name: str = "UNKNOWN"

    def decode(self, reader: StreamReader) -> Any:
        raise NotImplementedError

    def decode_bytes(self, data: bytes) -> Any:
        return self.decode(StreamReader(data))


class PrimitiveType(BWType):
    """定长标量/向量类型（INT8..UINT64, FLOAT32/64, VECTOR2/3/4）。"""

    def __init__(self, name: str, fmt: str, unpack_single: bool = True) -> None:
        self.name = name
        self.fmt = fmt
        self.fixed_size = struct.calcsize(fmt)
        self.unpack_single = unpack_single

    def decode(self, reader: StreamReader) -> Any:
        vals = reader.unpack(self.fmt)
        return vals[0] if self.unpack_single else vals


class BlobType(BWType):
    """变长二进制 BLOB / STRING / UNICODE_STRING / PYTHON。"""

    def __init__(self, name: str, decode_utf8: bool = False, unpickle: bool = False) -> None:
        self.name = name
        self.fixed_size = -1
        self.decode_utf8 = decode_utf8
        self.unpickle = unpickle

    def decode(self, reader: StreamReader) -> Any:
        length = reader.read_packed_length()
        raw = reader.read_bytes(length)
        if self.decode_utf8:
            return raw.decode("utf-8", errors="replace")
        if self.unpickle and raw:
            try:
                return pickle.loads(raw, encoding="latin1")
            except Exception:
                return raw
        return raw


class ArrayType(BWType):
    """BigWorld ARRAY / TUPLE（支持定长 <size>N</size> 与变长两种编码）。"""

    def __init__(self, element_type: BWType, fixed_count: int | None = None, name: str = "ARRAY") -> None:
        self.name = name
        self.element_type = element_type
        self.fixed_count = fixed_count
        if fixed_count is not None and element_type.fixed_size > 0:
            self.fixed_size = fixed_count * element_type.fixed_size
        else:
            self.fixed_size = -1

    def decode(self, reader: StreamReader) -> list[Any]:
        count = self.fixed_count if self.fixed_count is not None else reader.read_packed_length()
        return [self.element_type.decode(reader) for _ in range(count)]


@dataclass(frozen=True)
class DictField:
    name: str
    bw_type: BWType


class FixedDictType(BWType):
    """BigWorld FIXED_DICT（支持 AllowNone 与嵌套属性路径寻址）。"""

    def __init__(self, fields: list[DictField], allow_none: bool = False, name: str = "FIXED_DICT") -> None:
        self.name = name
        self.fields = fields
        self.allow_none = allow_none
        if not allow_none and fields and all(f.bw_type.fixed_size > 0 for f in fields):
            self.fixed_size = sum(f.bw_type.fixed_size for f in fields)
        elif not allow_none and not fields:
            self.fixed_size = 0
        else:
            self.fixed_size = -1

    def decode(self, reader: StreamReader) -> dict[str, Any] | None:
        if self.allow_none:
            (present,) = reader.unpack("<B")
            if present == 0:
                return None
        out: dict[str, Any] = {}
        for f in self.fields:
            out[f.name] = f.bw_type.decode(reader)
        return out


class UserType(BWType):
    """BigWorld USER_TYPE：若显式声明 <Type> 则按内层类型解码，否则按 BLOB 处理。"""

    def __init__(self, inner_type: BWType | None = None, name: str = "USER_TYPE") -> None:
        self.name = name
        self.inner_type = inner_type
        # BigWorld 中 USER_TYPE 统一走变长打包（除非明确映射为固定类型如 FLAT_VECTOR -> VECTOR2）
        if inner_type is not None and inner_type.fixed_size > 0:
            self.fixed_size = inner_type.fixed_size
        else:
            self.fixed_size = -1

    def decode(self, reader: StreamReader) -> Any:
        if self.inner_type is not None:
            return self.inner_type.decode(reader)
        length = reader.read_packed_length()
        return reader.read_bytes(length)


BUILTIN_TYPES: dict[str, BWType] = {
    "INT8": PrimitiveType("INT8", "<b"),
    "UINT8": PrimitiveType("UINT8", "<B"),
    "INT16": PrimitiveType("INT16", "<h"),
    "UINT16": PrimitiveType("UINT16", "<H"),
    "INT32": PrimitiveType("INT32", "<i"),
    "UINT32": PrimitiveType("UINT32", "<I"),
    "INT64": PrimitiveType("INT64", "<q"),
    "UINT64": PrimitiveType("UINT64", "<Q"),
    "FLOAT": PrimitiveType("FLOAT", "<f"),
    "FLOAT32": PrimitiveType("FLOAT32", "<f"),
    "FLOAT64": PrimitiveType("FLOAT64", "<d"),
    "VECTOR2": PrimitiveType("VECTOR2", "<2f", unpack_single=False),
    "VECTOR3": PrimitiveType("VECTOR3", "<3f", unpack_single=False),
    "VECTOR4": PrimitiveType("VECTOR4", "<4f", unpack_single=False),
    "STRING": BlobType("STRING", decode_utf8=True),
    "UNICODE_STRING": BlobType("UNICODE_STRING", decode_utf8=True),
    "BLOB": BlobType("BLOB", decode_utf8=False),
    "PYTHON": BlobType("PYTHON", unpickle=True),
    "MAILBOX": PrimitiveType("MAILBOX", "<IIHH", unpack_single=False),
}


class TypeRegistry:
    """解析并缓存 alias.xml 中的所有类型别名与复合类型定义。"""

    def __init__(self) -> None:
        self.types: dict[str, BWType] = dict(BUILTIN_TYPES)

    def load_alias_xml(self, alias_xml_bytes: bytes) -> None:
        root = ET.fromstring(alias_xml_bytes)
        # 按声明顺序解析；若存在前向引用则多轮收敛
        pending = list(root)
        for _ in range(8):
            next_pending: list[ET.Element] = []
            for child in pending:
                alias_name = child.tag.strip()
                try:
                    bw_type = self.parse_type_element(child, name_hint=alias_name)
                    self.types[alias_name] = bw_type
                except KeyError:
                    next_pending.append(child)
            pending = next_pending
            if not pending:
                break

        # 兜底：任何未解析成功的前向引用按 BLOB 处理
        for child in pending:
            self.types[child.tag.strip()] = BlobType(child.tag.strip())

    def parse_type_element(self, elem: ET.Element, name_hint: str = "") -> BWType:
        type_text = (elem.text or "").strip()

        if type_text in {"ARRAY", "TUPLE"}:
            of_el = elem.find("of")
            if of_el is None:
                return BlobType(name_hint or type_text)
            elem_type = self.parse_type_element(of_el)
            size_el = elem.find("size")
            fixed_count = int(size_el.text.strip()) if (size_el is not None and size_el.text) else None
            return ArrayType(elem_type, fixed_count=fixed_count, name=name_hint or type_text)

        if type_text == "FIXED_DICT":
            fields: list[DictField] = []
            props_el = elem.find("Properties")
            if props_el is not None:
                for p in props_el:
                    p_type_el = p.find("Type")
                    if p_type_el is not None:
                        fields.append(DictField(name=p.tag.strip(), bw_type=self.parse_type_element(p_type_el)))
            allow_none_el = elem.find("AllowNone")
            allow_none = (
                allow_none_el is not None
                and (allow_none_el.text or "").strip().lower() in {"true", "1", "yes"}
            )
            return FixedDictType(fields=fields, allow_none=allow_none, name=name_hint or "FIXED_DICT")

        if type_text == "USER_TYPE":
            inner_el = elem.find("Type")
            if inner_el is not None:
                inner_type = self.parse_type_element(inner_el)
                return UserType(inner_type=inner_type, name=name_hint or "USER_TYPE")
            return UserType(inner_type=None, name=name_hint or "USER_TYPE")

        if type_text in self.types:
            return self.types[type_text]

        raise KeyError(f"未知 BigWorld 类型: {type_text!r} (tag={elem.tag})")
