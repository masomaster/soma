"""Build a minimal FIT file with record power samples (for hermetic tests).

Implements enough of the FIT protocol to be read by ``fitdecode``: file header,
one record definition (timestamp + power), data records, and CRC.
"""

from __future__ import annotations

import struct
from datetime import datetime, timezone

# FIT epoch: 1989-12-31 00:00:00 UTC
_FIT_EPOCH = datetime(1989, 12, 31, tzinfo=timezone.utc)

# CRC-16/ARC table used by FIT
_CRC_TABLE = [
    0x0000,
    0xCC01,
    0xD801,
    0x1400,
    0xF001,
    0x3C00,
    0x2800,
    0xE401,
    0xA001,
    0x6C00,
    0x7800,
    0xB401,
    0x5000,
    0x9C01,
    0x8801,
    0x4400,
]


def _crc16(data: bytes, crc: int = 0) -> int:
    for byte in data:
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[byte & 0xF]
        tmp = _CRC_TABLE[crc & 0xF]
        crc = (crc >> 4) & 0x0FFF
        crc = crc ^ tmp ^ _CRC_TABLE[(byte >> 4) & 0xF]
    return crc & 0xFFFF


def _fit_time(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int((dt.astimezone(timezone.utc) - _FIT_EPOCH).total_seconds())


def build_minimal_fit_with_power(
    *,
    start: datetime,
    watts: list[int],
    sample_dt_sec: int = 1,
) -> bytes:
    """Return FIT bytes: ``len(watts)`` record messages with timestamp + power."""
    local_msg = 0
    # Definition message for record (mesg_num=20): timestamp (uint32), power (uint16)
    # Header: 0x40 | local_msg  => definition
    def_header = bytes([0x40 | local_msg])
    # reserved, architecture (0=little), global msg num (20), field count (2)
    def_body = struct.pack("<BBHB", 0, 0, 20, 2)
    # field defs: field_def_num, size, base_type
    # timestamp: field 253, 4 bytes, uint32 (0x86)
    # power: field 7, 2 bytes, uint16 (0x84)
    def_body += bytes([253, 4, 0x86, 7, 2, 0x84])
    definition = def_header + def_body

    data_records = bytearray()
    t0 = _fit_time(start)
    for i, w in enumerate(watts):
        # Data message header: local_msg only (normal header)
        data_records.append(local_msg)
        data_records += struct.pack("<IH", t0 + i * sample_dt_sec, int(w) & 0xFFFF)

    # Optional sport message so activity_type resolves — skip for minimal fixture.
    data_content = definition + bytes(data_records)
    data_size = len(data_content)

    # 14-byte header with CRC of header bytes 0-11
    header_wo_crc = struct.pack("<BBHI4s", 14, 0x10, 0x0829, data_size, b".FIT")
    header_crc = _crc16(header_wo_crc)
    header = header_wo_crc + struct.pack("<H", header_crc)

    file_wo_crc = header + data_content
    file_crc = _crc16(file_wo_crc)
    return file_wo_crc + struct.pack("<H", file_crc)
