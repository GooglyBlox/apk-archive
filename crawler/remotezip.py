from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from .config import EOCD_TAIL_BYTES

EOCD_SIG = b"PK\x05\x06"
EOCD64_LOCATOR_SIG = b"PK\x06\x07"
EOCD64_SIG = b"PK\x06\x06"
CD_SIG = b"PK\x01\x02"
LFH_SIG = b"PK\x03\x04"

STORED, DEFLATED = 0, 8


class RemoteZipError(Exception):
    def __init__(self, message: str, *, permanent: bool = True):
        super().__init__(message)
        self.permanent = permanent


@dataclass(slots=True)
class ZipEntry:
    name: str
    method: int
    compressed_size: int
    uncompressed_size: int
    local_header_offset: int
    crc32: int


class RemoteZip:
    def __init__(self, fetch, size: int):
        self.fetch = fetch
        self.size = size
        self._entries: dict[str, ZipEntry] | None = None

    def _read_eocd(self) -> tuple[int, int]:
        tail_len = min(EOCD_TAIL_BYTES, self.size)
        tail = self.fetch(None, tail_len)
        pos = tail.rfind(EOCD_SIG)
        if pos < 0:
            raise RemoteZipError("no End of Central Directory (not a zip, or truncated)")
        if pos + 22 > len(tail):
            raise RemoteZipError("truncated EOCD record")
        cd_size, cd_offset = struct.unpack("<II", tail[pos + 12:pos + 20])
        if cd_offset == 0xFFFFFFFF or cd_size == 0xFFFFFFFF:
            cd_offset, cd_size = self._read_eocd64(tail, pos)
        return cd_offset, cd_size

    def _read_eocd64(self, tail: bytes, eocd_pos: int) -> tuple[int, int]:
        loc = tail.rfind(EOCD64_LOCATOR_SIG, 0, eocd_pos)
        if loc < 0:
            raise RemoteZipError("ZIP64 markers present but locator missing")
        eocd64_offset, = struct.unpack("<Q", tail[loc + 8:loc + 16])
        rec = self.fetch(eocd64_offset, eocd64_offset + 55)
        if rec[:4] != EOCD64_SIG:
            raise RemoteZipError("bad ZIP64 EOCD signature")
        cd_size, cd_offset = struct.unpack("<QQ", rec[40:56])
        return cd_offset, cd_size

    def entries(self) -> dict[str, ZipEntry]:
        if self._entries is not None:
            return self._entries
        cd_offset, cd_size = self._read_eocd()
        if cd_size <= 0 or cd_offset + cd_size > self.size:
            raise RemoteZipError("central directory bounds outside file")
        cd = self.fetch(cd_offset, cd_offset + cd_size - 1)

        entries: dict[str, ZipEntry] = {}
        off = 0
        while off + 46 <= len(cd) and cd[off:off + 4] == CD_SIG:
            method, = struct.unpack("<H", cd[off + 10:off + 12])
            crc, csize, usize = struct.unpack("<III", cd[off + 16:off + 28])
            nlen, elen, clen = struct.unpack("<HHH", cd[off + 28:off + 34])
            lho, = struct.unpack("<I", cd[off + 42:off + 46])
            name = cd[off + 46:off + 46 + nlen].decode("utf-8", "replace")
            extra = cd[off + 46 + nlen:off + 46 + nlen + elen]
            if 0xFFFFFFFF in (csize, usize, lho):
                csize, usize, lho = _zip64_extra(extra, csize, usize, lho)
            entries[name] = ZipEntry(name, method, csize, usize, lho, crc)
            off += 46 + nlen + elen + clen

        if not entries:
            raise RemoteZipError("central directory contained no entries")
        self._entries = entries
        return entries

    def read(self, name: str) -> bytes:
        entry = self.entries().get(name)
        if entry is None:
            raise RemoteZipError(f"{name!r} not present in archive")
        return self.read_entry(entry)

    def data_offset(self, entry: ZipEntry) -> int:
        lfh = self.fetch(entry.local_header_offset, entry.local_header_offset + 29)
        if lfh[:4] != LFH_SIG:
            raise RemoteZipError("bad local file header signature")
        nlen, elen = struct.unpack("<HH", lfh[26:30])
        return entry.local_header_offset + 30 + nlen + elen

    def read_entry(self, entry: ZipEntry) -> bytes:
        start = self.data_offset(entry)

        if entry.compressed_size == 0:
            return b""
        raw = self.fetch(start, start + entry.compressed_size - 1)

        if entry.method == STORED:
            data = raw
        elif entry.method == DEFLATED:
            try:
                data = zlib.decompress(raw, -15)
            except zlib.error as exc:
                raise RemoteZipError(f"inflate failed for {entry.name}: {exc}") from exc
        else:
            raise RemoteZipError(f"unsupported compression method {entry.method}")

        if entry.uncompressed_size and len(data) != entry.uncompressed_size:
            raise RemoteZipError(
                f"size mismatch for {entry.name}: got {len(data)}, "
                f"expected {entry.uncompressed_size}"
            )
        return data


def nested(outer: RemoteZip, entry: ZipEntry) -> RemoteZip:
    if entry.method != STORED:
        raise RemoteZipError("nested entry is compressed; read it fully instead")
    base = outer.data_offset(entry)
    size = entry.uncompressed_size

    def fetch(start, end):
        if start is None:
            length = min(end, size)
            return outer.fetch(base + size - length, base + size - 1)
        end = size - 1 if end is None else min(end, size - 1)
        return outer.fetch(base + start, base + end)

    return RemoteZip(fetch, size)


def from_bytes(blob: bytes) -> RemoteZip:
    def fetch(start, end):
        if start is None:
            return blob[max(0, len(blob) - end):]
        return blob[start:(len(blob) if end is None else end + 1)]

    return RemoteZip(fetch, len(blob))


def _zip64_extra(extra: bytes, csize: int, usize: int, lho: int) -> tuple[int, int, int]:
    off = 0
    while off + 4 <= len(extra):
        tag, size = struct.unpack("<HH", extra[off:off + 4])
        body = extra[off + 4:off + 4 + size]
        if tag == 0x0001:
            pos = 0
            if usize == 0xFFFFFFFF and pos + 8 <= len(body):
                usize, = struct.unpack("<Q", body[pos:pos + 8]); pos += 8
            if csize == 0xFFFFFFFF and pos + 8 <= len(body):
                csize, = struct.unpack("<Q", body[pos:pos + 8]); pos += 8
            if lho == 0xFFFFFFFF and pos + 8 <= len(body):
                lho, = struct.unpack("<Q", body[pos:pos + 8]); pos += 8
            break
        off += 4 + size
    return csize, usize, lho
