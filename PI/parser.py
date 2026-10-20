# fossibot/parser.py
# Parses the binary telemetry frames pushed by the Fossibot device.
#
# Frame structure (reverse-engineered from app-service.js):
#
#   Header: 6 bytes  (d4 00 0b 00 ce 00 or similar — varies, not fully decoded)
#   Body:   repeated 6-byte register entries until 2 bytes before end
#     [addr_lo] [addr_hi] [val0] [val1] [val2] [val3]
#   Footer: 2-byte CRC-16
#
# Each register entry is 6 bytes:
#   bytes 0-1: register address, little-endian uint16
#   bytes 2-5: value, 4 bytes, interpreted per data_type in registers.py

import struct
import logging
from registers import REGISTERS

log = logging.getLogger(__name__)


def _parse_value(key, raw4):
    """Interpret 4 raw bytes according to the register's data type."""
    reg = REGISTERS.get(key)
    if reg is None:
        return None

    dtype = reg[1]

    if dtype in ("uint8_t", "bool"):
        return raw4[0]

    elif dtype == "uint16_t":
        val = raw4[0] | (raw4[1] << 8)
        # PCS version displayed as 4-digit hex string
        if key == 18:
            return f"{val:04x}"
        return val

    elif dtype == "int16_t":
        val = raw4[0] | (raw4[1] << 8)
        if val >= 0x8000:
            val -= 0x10000
        return val

    elif dtype == "uint32_t":
        return raw4[0] | (raw4[1] << 8) | (raw4[2] << 16) | (raw4[3] << 24)

    elif dtype == "int32_t":
        if key == 2:
            # Battery max temp: single signed byte
            b = raw4[0]
            return b if b < 128 else b - 256
        else:
            # Reconstruct as signed 32-bit, but the app uses a hybrid approach
            lo = raw4[0] | (raw4[1] << 8)
            hi = raw4[2] | (raw4[3] << 8)
            if hi == 0 or hi == 0xFFFF:
                # 16-bit range
                val = lo
                if val >= 0x8000:
                    val -= 0x10000
            else:
                val = raw4[0] | (raw4[1] << 8) | (raw4[2] << 16) | (raw4[3] << 24)
                if val >= 0x80000000:
                    val -= 0x100000000
            return val

    elif dtype == "version":
        # Display as hex bytes reversed, dash-separated (matches app display)
        return f"{raw4[0]:02x}-{raw4[1]:02x}-{raw4[2]:02x}-{raw4[3]:02x}"

    return None


def parse_frame(data: bytes) -> dict:
    """
    Parse a raw telemetry frame from the device.
    Frame structure:
      - 6 byte header: e0 00 0b 00 da 00 (marker + length info)
      - N × 6 byte register entries: [addr_lo][addr_hi][val0][val1][val2][val3]
      - 2-byte trailer (appears to be a CRC-16; not yet verified)
    Frame is exactly 224 bytes: 6 header + 36 registers × 6 bytes + 2 = 224.
    The periodic push carries registers 1-5, 18-23 and 34-58 only; the BMS
    detail and fault registers (6-12, 24-30) are not included.
    Returns dict of {field_name: value}.
    """
    if len(data) < 12:
        log.debug(f"Frame too short: {len(data)} bytes")
        return {}

    results = {}
    body = data[6:]  # skip 6-byte header, no CRC to strip

    for i in range(0, len(body) - 5, 6):
        chunk = body[i:i+6]
        addr = chunk[0] | (chunk[1] << 8)
        raw4 = chunk[2:6]

        if addr not in REGISTERS:
            log.debug(f"Unknown register {addr}, skipping")
            continue

        value = _parse_value(addr, raw4)
        if value is not None:
            field = REGISTERS[addr][0]
            results[field] = value
            log.debug(f"  reg {addr:3d} ({field}): {value} {REGISTERS[addr][2]}")

    return results


def frame_to_hex_dump(data: bytes) -> str:
    """Returns a formatted hex dump for debugging."""
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        asc_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"  {i:04x}  {hex_part:<47}  {asc_part}")
    return "\n".join(lines)