"""ISFP v2 .idx/.pkg 虚拟文件系统与客户端定位单元测试。"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

import pytest

from korabli_minimap.extractor.client_locator import (
    _parse_game_info,
    normalize_short_version,
)
from korabli_minimap.extractor.idx_vfs import ISFP_MAGIC, KorabliVFS, ROOT_PARENT_ID


def _build_synthetic_idx_and_pkg(tmp_path: Path) -> tuple[Path, Path]:
    idx_dir = tmp_path / "idx"
    pkg_dir = tmp_path / "res_packages"
    idx_dir.mkdir()
    pkg_dir.mkdir()

    file_content = b"<root><ClientServerEntities><Avatar/></ClientServerEntities></root>"
    pkg_name = "test_0001.pkg"
    (pkg_dir / pkg_name).write_bytes(file_content)

    # 2 nodes: dir "scripts" (id=10, parent=ROOT), file "entities.xml" (id=11, parent=10)
    # 1 file entry, 1 volume entry
    num_nodes = 2
    num_files = 1
    num_volumes = 1

    # Layout relative to 0x10:
    # 0x00..0x27: metadata (40B)
    # 0x28: nodes (2 * 32B = 64B)
    # 0x68: files (1 * 48B = 48B)
    # 0x98: volumes (1 * 24B = 24B)
    # 0xB0: string pool
    nodes_rel = 0x28
    files_rel = nodes_rel + num_nodes * 32
    volumes_rel = files_rel + num_files * 48
    str_base_abs = 0x10 + volumes_rel + num_volumes * 24

    s_dir = b"scripts\x00"
    s_file = b"entities.xml\x00"
    s_vol = pkg_name.encode("utf-8") + b"\x00"

    off_s_dir = str_base_abs
    off_s_file = off_s_dir + len(s_dir)
    off_s_vol = off_s_file + len(s_file)

    buf = bytearray()
    # 16B header
    buf += ISFP_MAGIC
    buf += struct.pack("<3I", 0x02000000, 0x12345678, 0x40)
    # 40B metadata (starts at 0x10)
    buf += struct.pack("<4I", num_nodes, num_files, num_volumes, 0)
    buf += struct.pack("<3Q", nodes_rel, files_rel, volumes_rel)

    # Node 0 at 0x38
    node0_abs = 0x10 + nodes_rel
    buf += struct.pack("<QqQQ", len(s_dir), off_s_dir - node0_abs, 10, ROOT_PARENT_ID)
    # Node 1 at 0x58
    node1_abs = node0_abs + 32
    buf += struct.pack("<QqQQ", len(s_file), off_s_file - node1_abs, 11, 10)

    # File 0
    crc = zlib.crc32(file_content) & 0xFFFFFFFF
    buf += struct.pack("<QQQQIIQ", 11, 99, 0, 6, len(file_content), crc, len(file_content))

    # Volume 0
    vol0_abs = 0x10 + volumes_rel
    buf += struct.pack("<QqQ", len(s_vol), off_s_vol - vol0_abs, 99)

    # String pool
    buf += s_dir + s_file + s_vol
    (idx_dir / "test.idx").write_bytes(bytes(buf))
    return idx_dir, pkg_dir


def test_synthetic_isfp_v2_vfs(tmp_path: Path) -> None:
    idx_dir, pkg_dir = _build_synthetic_idx_and_pkg(tmp_path)
    vfs = KorabliVFS(idx_dir, pkg_dir)
    assert len(vfs) == 1
    assert vfs.exists("scripts/entities.xml")
    assert vfs.exists("SCRIPTS/ENTITIES.XML")
    raw = vfs.read_bytes("scripts/entities.xml", verify_crc=True)
    assert b"<Avatar/>" in raw
    assert len(vfs.glob("scripts/*.xml")) == 1


def test_normalize_short_version() -> None:
    assert normalize_short_version("26.11.1.0.8871067") == "26.11.1"
    assert normalize_short_version("26,11,1,8871067") == "26.11.1"
    assert normalize_short_version("25.8.0") == "25.8.0"


def test_parse_lesta_game_info_xml(tmp_path: Path) -> None:
    xml_path = tmp_path / "game_info.xml"
    xml_path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<protocol wgc_publisher_id="lesta" name="game_info" version="2.17">
  <game>
    <id>MK.RU.PRODUCTION</id>
    <part_versions>
      <version name="client" installed="26.11.1.0.8871067" available="26.11.1.0.8871067"/>
    </part_versions>
  </game>
</protocol>
""",
        encoding="utf-8",
    )
    full_ver, build, pub, gid = _parse_game_info(xml_path)
    assert full_ver == "26.11.1.0.8871067"
    assert build == "8871067"
    assert pub == "lesta"
    assert gid == "MK.RU.PRODUCTION"
