"""纯 Python 俄服 .idx/.pkg (ISFP v2) 虚拟文件系统读取器。"""

from __future__ import annotations

import fnmatch
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT_PARENT_ID = 0xDBB1A1D1B108B927
ISFP_MAGIC = b"ISFP"


@dataclass(frozen=True)
class IdxFileEntry:
    """单个虚拟文件条目元数据。"""

    path: str
    pkg_name: str
    pkg_path: Path
    offset: int
    packed_size: int
    unpacked_size: int
    comp_info: int
    crc32: int

    @property
    def comp_type(self) -> int:
        return (self.comp_info >> 32) & 0xFFFFFFFF

    @property
    def is_compressed(self) -> bool:
        return self.comp_type != 0


def _read_cstr(buf: bytes, pos: int) -> str:
    end = buf.find(b"\x00", pos)
    if end == -1:
        end = len(buf)
    return buf[pos:end].decode("utf-8", errors="replace")


def parse_idx_file(idx_path: Path, pkg_dir: Path) -> list[IdxFileEntry]:
    """解析单个 ISFP v2 (.idx) 文件，返回其包含的全部虚拟文件条目。"""
    data = idx_path.read_bytes()
    if len(data) < 56 or data[:4] != ISFP_MAGIC:
        raise ValueError(f"不支持或损坏的 .idx 文件: {idx_path}")

    version = struct.unpack_from("<I", data, 4)[0]
    if version == 0x02000000:
        return _parse_idx_v2(data, pkg_dir)
    if version == 0x01010004:
        return _parse_idx_v1(data, pkg_dir)
    raise ValueError(f"未知的 .idx 版本号 0x{version:08x}: {idx_path}")


def _parse_idx_v2(data: bytes, pkg_dir: Path) -> list[IdxFileEntry]:
    # Header: 16 bytes + 40 bytes metadata (offsets relative to 0x10)
    num_nodes, num_files, num_volumes, _pad = struct.unpack_from("<4I", data, 0x10)
    nodes_rel, files_rel, volumes_rel = struct.unpack_from("<3Q", data, 0x20)

    base = 0x10
    nodes_off = base + nodes_rel
    files_off = base + files_rel
    volumes_off = base + volumes_rel

    # 1. Node table: 32 bytes each (<QqQQ)
    nodes: dict[int, tuple[str, int]] = {}
    for i in range(num_nodes):
        rec_off = nodes_off + i * 32
        _name_len, name_rel, node_id, parent_id = struct.unpack_from("<QqQQ", data, rec_off)
        name = _read_cstr(data, rec_off + name_rel)
        nodes[node_id] = (name, parent_id)

    # 2. Volume table: 24 bytes each (<QqQ)
    volumes: dict[int, str] = {}
    for i in range(num_volumes):
        rec_off = volumes_off + i * 24
        _name_len, name_rel, volume_id = struct.unpack_from("<QqQ", data, rec_off)
        vol_name = _read_cstr(data, rec_off + name_rel)
        if vol_name.startswith("//.//"):
            vol_name = vol_name[5:]
        volumes[volume_id] = vol_name

    # Reconstruct full paths with memoization
    path_cache: dict[int, str] = {}

    def resolve_path(node_id: int) -> str:
        cached = path_cache.get(node_id)
        if cached is not None:
            return cached
        parts: list[str] = []
        cur = node_id
        visited: set[int] = set()
        while cur in nodes and cur not in visited:
            visited.add(cur)
            name, parent_id = nodes[cur]
            if name:
                parts.append(name)
            if parent_id == ROOT_PARENT_ID:
                break
            cur = parent_id
        full = "/".join(reversed(parts))
        path_cache[node_id] = full
        return full

    # 3. File table: 48 bytes each (<QQQQIIQ)
    entries: list[IdxFileEntry] = []
    for i in range(num_files):
        rec_off = files_off + i * 48
        node_id, volume_id, pkg_offset, comp_info, packed_size, crc32, unpacked_size = (
            struct.unpack_from("<QQQQIIQ", data, rec_off)
        )
        vfs_path = resolve_path(node_id)
        pkg_name = volumes.get(volume_id, "")
        entries.append(
            IdxFileEntry(
                path=vfs_path,
                pkg_name=pkg_name,
                pkg_path=pkg_dir / pkg_name,
                offset=pkg_offset,
                packed_size=packed_size,
                unpacked_size=unpacked_size,
                comp_info=comp_info,
                crc32=crc32,
            )
        )
    return entries


def _parse_idx_v1(data: bytes, pkg_dir: Path) -> list[IdxFileEntry]:
    """兼容旧版 v0x20 索引结构。"""
    base = 0x10
    num_nodes, nodes_rel = struct.unpack_from("<IQ", data, base)
    num_files, files_rel = struct.unpack_from("<IQ", data, base + 12)
    num_volumes, volumes_rel = struct.unpack_from("<IQ", data, base + 24)

    nodes_off = base + nodes_rel
    files_off = base + files_rel
    volumes_off = base + volumes_rel

    nodes: dict[int, tuple[str, int]] = {}
    for i in range(num_nodes):
        rec_off = nodes_off + i * 24
        name_rel, node_id, parent_id = struct.unpack_from("<qQQ", data, rec_off)
        name = _read_cstr(data, rec_off + name_rel)
        nodes[node_id] = (name, parent_id)

    volumes: dict[int, str] = {}
    for i in range(num_volumes):
        rec_off = volumes_off + i * 16
        name_rel, volume_id = struct.unpack_from("<qQ", data, rec_off)
        vol_name = _read_cstr(data, rec_off + name_rel).lstrip("/.")
        volumes[volume_id] = vol_name

    def resolve_path(node_id: int) -> str:
        parts: list[str] = []
        cur = node_id
        while cur in nodes:
            name, parent_id = nodes[cur]
            if name:
                parts.append(name)
            if parent_id == ROOT_PARENT_ID:
                break
            cur = parent_id
        return "/".join(reversed(parts))

    entries: list[IdxFileEntry] = []
    for i in range(num_files):
        rec_off = files_off + i * 48
        node_id, volume_id, pkg_offset, comp_info, packed_size, crc32, unpacked_size = (
            struct.unpack_from("<QQQQIIQ", data, rec_off)
        )
        pkg_name = volumes.get(volume_id, "")
        entries.append(
            IdxFileEntry(
                path=resolve_path(node_id),
                pkg_name=pkg_name,
                pkg_path=pkg_dir / pkg_name,
                offset=pkg_offset,
                packed_size=packed_size,
                unpacked_size=unpacked_size,
                comp_info=comp_info,
                crc32=crc32,
            )
        )
    return entries


class KorabliVFS:
    """面向俄服 bin/<build>/idx/*.idx 与 res_packages/*.pkg 的只读虚拟文件系统。"""

    def __init__(self, idx_dir: Path, pkg_dir: Path, idx_filter: Iterable[str] | None = None) -> None:
        self.idx_dir = idx_dir
        self.pkg_dir = pkg_dir
        self._entries: dict[str, IdxFileEntry] = {}
        self._load_indexes(idx_filter)

    def _load_indexes(self, idx_filter: Iterable[str] | None) -> None:
        if idx_filter is not None:
            idx_files = [self.idx_dir / name for name in idx_filter if (self.idx_dir / name).is_file()]
        else:
            idx_files = sorted(self.idx_dir.glob("*.idx"))

        for idx_path in idx_files:
            for entry in parse_idx_file(idx_path, self.pkg_dir):
                key = entry.path.replace("\\", "/").lstrip("/").lower()
                self._entries[key] = entry

    def __len__(self) -> int:
        return len(self._entries)

    def exists(self, vfs_path: str) -> bool:
        key = vfs_path.replace("\\", "/").lstrip("/").lower()
        return key in self._entries

    def get_entry(self, vfs_path: str) -> IdxFileEntry:
        key = vfs_path.replace("\\", "/").lstrip("/").lower()
        entry = self._entries.get(key)
        if entry is None:
            raise FileNotFoundError(f"VFS 中未找到文件: {vfs_path}")
        return entry

    def list_prefix(self, prefix: str) -> list[IdxFileEntry]:
        norm = prefix.replace("\\", "/").lstrip("/").lower()
        return [entry for key, entry in self._entries.items() if key.startswith(norm)]

    def glob(self, pattern: str) -> list[IdxFileEntry]:
        norm = pattern.replace("\\", "/").lstrip("/").lower()
        return [entry for key, entry in self._entries.items() if fnmatch.fnmatch(key, norm)]

    def read_bytes(self, vfs_path: str, verify_crc: bool = True) -> bytes:
        entry = self.get_entry(vfs_path)
        if not entry.pkg_path.is_file():
            raise FileNotFoundError(f"缺少资源包文件: {entry.pkg_path}")

        with entry.pkg_path.open("rb") as f:
            f.seek(entry.offset)
            raw = f.read(entry.packed_size)

        if len(raw) != entry.packed_size:
            raise IOError(
                f"读取 {entry.path} 字节不足: 预期 {entry.packed_size}, 实际 {len(raw)}"
            )

        if entry.comp_type == 0:
            data = raw
        elif entry.comp_type == 1:
            data = zlib.decompress(raw, -15)
        else:
            raise NotImplementedError(
                f"文件 {entry.path} 使用压缩类型 {entry.comp_type}（非未压缩/Deflate），暂不支持解码"
            )

        if verify_crc and entry.crc32:
            actual_crc = zlib.crc32(data) & 0xFFFFFFFF
            if actual_crc != entry.crc32:
                raise ValueError(
                    f"CRC32 校验失败 ({entry.path}): 预期 0x{entry.crc32:08x}, 实际 0x{actual_crc:08x}"
                )
        return data
