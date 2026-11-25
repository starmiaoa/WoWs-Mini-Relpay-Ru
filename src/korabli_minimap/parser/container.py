"""俄服 .korablireplay 容器读取、Blowfish-ECB-XOR 解密、zlib 解压与数据包切片。"""

from __future__ import annotations

import io
import json
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from Crypto.Cipher import Blowfish


REPLAY_MAGIC = b"\x12\x32\x34\x11"
KORABLI_BLOWFISH_KEY = b"\x29\xB7\xC9\x09\x38\x3F\x84\x88\xFA\x98\xEC\x4E\x13\x19\x79\xFB"


@dataclass(frozen=True)
class RawPacket:
    """解密后的单条 BigWorld 原始网络包。"""

    index: int
    packet_type: int
    clock: float
    payload: bytes


@dataclass
class ReplayContainer:
    """解析后的 .korablireplay 文件头、JSON 元数据块与解压后的二进制包流。"""

    path: Path
    meta: dict[str, Any]
    post_battle: dict[str, Any] | None
    extra_raw_blocks: list[bytes] = field(default_factory=list)
    decompressed_size: int = 0
    compressed_size: int = 0
    raw_stream: bytes = b""

    @property
    def client_version(self) -> str:
        return str(
            self.meta.get("clientVersionFromExe")
            or self.meta.get("clientVersionFromXml")
            or ""
        )

    def iter_packets(self) -> Iterator[RawPacket]:
        """按 <IIf (size, type, clock) + payload 切片遍历全部网络包。"""
        buf = self.raw_stream
        total = len(buf)
        pos = 0
        idx = 0
        while pos + 12 <= total:
            size, ptype, clock = struct.unpack_from("<IIf", buf, pos)
            pos += 12
            if pos + size > total:
                break
            payload = buf[pos : pos + size]
            pos += size
            yield RawPacket(index=idx, packet_type=ptype, clock=clock, payload=payload)
            idx += 1


def decrypt_korabli_stream(encrypted_body: bytes) -> bytes:
    """执行俄服 8 字节 Blowfish-ECB + 前一块明文 XOR 链接解密。"""
    cipher = Blowfish.new(KORABLI_BLOWFISH_KEY, Blowfish.MODE_ECB)
    usable_len = (len(encrypted_body) // 8) * 8
    prev_plain = 0
    out = bytearray(usable_len)

    # 先整块 ECB 解密（C 实现，速度比逐块调用快一个数量级）
    ecb_dec = cipher.decrypt(encrypted_body[:usable_len])
    for off in range(0, usable_len, 8):
        (val,) = struct.unpack_from("<Q", ecb_dec, off)
        plain = val ^ prev_plain
        prev_plain = plain
        struct.pack_into("<Q", out, off, plain)
    return bytes(out)


def read_replay_meta_only(replay_path: Path | str) -> dict[str, Any]:
    """仅快速读取 .korablireplay 头部 Block 0 JSON 元数据（无需解密压缩流，耗时 < 1ms）。"""
    path = Path(replay_path)
    with path.open("rb") as f:
        hdr = f.read(12)
        if len(hdr) < 12 or hdr[:4] != REPLAY_MAGIC:
            raise ValueError(f"无效的俄服回放文件头: {path}")
        (blocks_count,) = struct.unpack_from("<I", hdr, 4)
        if blocks_count < 1:
            raise ValueError(f"回放缺少元数据块: {path}")
        (blen,) = struct.unpack_from("<I", hdr, 8)
        if blen <= 0 or blen > 4 * 1024 * 1024:
            raise ValueError(f"回放元数据块长度异常 ({blen}): {path}")
        raw_meta = f.read(blen)
    return json.loads(raw_meta.decode("utf-8"))


def read_replay_container(replay_path: Path | str) -> ReplayContainer:
    """读取并解密解压 .korablireplay 文件，容错俄服特有的多块及非 JSON ASCII 块。"""
    path = Path(replay_path)
    data = path.read_bytes()
    if len(data) < 12 or data[:4] != REPLAY_MAGIC:
        raise ValueError(f"无效的俄服回放文件头（预期 12 32 34 11）: {path}")

    (blocks_count,) = struct.unpack_from("<I", data, 4)
    pos = 8
    raw_blocks: list[bytes] = []
    for _ in range(blocks_count):
        if pos + 4 > len(data):
            raise ValueError(f"回放块头截断: {path}")
        (blen,) = struct.unpack_from("<I", data, pos)
        pos += 4
        if pos + blen > len(data):
            raise ValueError(f"回放块内容截断 (len={blen}): {path}")
        raw_blocks.append(data[pos : pos + blen])
        pos += blen

    if not raw_blocks:
        raise ValueError(f"回放中缺少基础元数据块: {path}")

    meta = json.loads(raw_blocks[0].decode("utf-8"))
    post_battle: dict[str, Any] | None = None
    extra_raw: list[bytes] = []

    for blk in raw_blocks[1:]:
        if not blk:
            continue
        if post_battle is None and blk.lstrip().startswith(b"{"):
            try:
                parsed = json.loads(blk.decode("utf-8"))
                if isinstance(parsed, dict):
                    post_battle = parsed
                    continue
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        extra_raw.append(blk)

    if pos + 8 > len(data):
        raise ValueError(f"回放缺少加密流长度头: {path}")

    decompressed_size, compressed_size = struct.unpack_from("<II", data, pos)
    pos += 8
    encrypted_body = data[pos : pos + compressed_size] if compressed_size > 0 else data[pos:]

    decrypted = decrypt_korabli_stream(encrypted_body)
    raw_stream = zlib.decompress(decrypted)

    return ReplayContainer(
        path=path,
        meta=meta,
        post_battle=post_battle,
        extra_raw_blocks=extra_raw,
        decompressed_size=decompressed_size,
        compressed_size=compressed_size,
        raw_stream=raw_stream,
    )
