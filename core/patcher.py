"""
Binary config patcher — searches a .bin file for placeholder strings and
replaces them with user-supplied values before flashing.

To use this in your own ESP32 firmware, declare fixed-size buffers with the
exact placeholder strings below.  Example (C / Arduino):

    char wifi_ssid[64]   = "<<WIFI_SSID>>";
    char wifi_pass[64]   = "<<WIFI_PASS>>";
    char btc_wallet[64]  = "<<BTC_WALLET>>";

The patcher finds these strings in the compiled binary and overwrites the
64-byte slot with your real value (null-padded to fit).
"""

import os
import struct
from typing import Dict, Optional, Tuple

FIELD_SIZE = 64  # bytes reserved per config value in the firmware

PLACEHOLDERS: Dict[str, bytes] = {
    "wifi_ssid":  b"<<WIFI_SSID>>",
    "wifi_pass":  b"<<WIFI_PASS>>",
    "btc_wallet": b"<<BTC_WALLET>>",
}


def patch_binary(src_path: str, config: Dict[str, str]) -> Optional[str]:
    """
    Patch config values into src_path and write a new *_configured.bin beside it.
    Returns the patched file path, or None if no placeholders were found.
    config keys must match PLACEHOLDERS keys above.
    """
    with open(src_path, "rb") as f:
        data = bytearray(f.read())

    patched_count = 0
    for key, placeholder in PLACEHOLDERS.items():
        value = config.get(key, "").strip()
        if not value:
            continue
        idx = data.find(placeholder)
        if idx == -1:
            continue
        encoded = value.encode("utf-8")
        if len(encoded) >= FIELD_SIZE:
            encoded = encoded[: FIELD_SIZE - 1] + b"\x00"
        else:
            encoded = encoded + b"\x00" * (FIELD_SIZE - len(encoded))
        data[idx : idx + FIELD_SIZE] = encoded
        patched_count += 1

    if patched_count == 0:
        return None

    base, ext = os.path.splitext(src_path)
    out_path = base + "_configured" + ext
    with open(out_path, "wb") as f:
        f.write(data)
    return out_path


# ── Firmware chip-compatibility check ────────────────────────────────────────

_ESP_MAGIC = 0xE9

# chip_id (uint16 LE at header offset 12) → esptool chip name
_CHIP_ID: Dict[int, str] = {
    0x0000: "esp32",    # original ESP32 or "any" (older toolchains)
    0x0002: "esp32s2",
    0x0005: "esp32c3",
    0x0006: "esp32h2",
    0x0009: "esp32s3",
    0x000A: "esp32h2",
    0x000C: "esp32c2",
    0x000D: "esp32c6",
    0x000E: "esp32c5",
    0x0010: "esp32p4",
}


def image_chip(bin_path: str) -> Tuple[Optional[str], str]:
    """
    Read the chip target embedded in an ESP32 image header.

    Returns (chip, message) where:
      chip    — esptool chip string ("esp32", "esp32s3", …) or None if undetectable
      message — human-readable result for display in the UI
    """
    try:
        with open(bin_path, "rb") as f:
            hdr = f.read(16)
    except Exception as exc:
        return None, f"Cannot read file: {exc}"

    if len(hdr) < 16:
        return None, "File too small to be a valid ESP32 image"

    if hdr[0] != _ESP_MAGIC:
        return None, f"Not an ESP32 image (magic=0x{hdr[0]:02X}, expected 0xE9)"

    chip_id = struct.unpack_from("<H", hdr, 12)[0]
    chip    = _CHIP_ID.get(chip_id)

    if chip_id == 0x0000:
        # 0x0000 = original ESP32 or older firmware that omits chip ID
        return "esp32", "Target chip: ESP32 (or unspecified — older build)"
    if chip:
        return chip, f"Target chip: {chip.upper()}"
    return None, f"Unknown chip ID 0x{chip_id:04X} in image header"


def fields_present(src_path: str) -> list[str]:
    """Return list of placeholder keys found in the binary."""
    with open(src_path, "rb") as f:
        data = f.read()
    return [key for key, ph in PLACEHOLDERS.items() if ph in data]
