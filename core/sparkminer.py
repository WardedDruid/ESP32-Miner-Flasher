"""
SparkMiner NVS pre-configuration generator.

SparkMiner stores all settings as a single binary blob (the miner_config_t C struct)
in NVS namespace "sparkminer" under key "config".  The struct ends with a polynomial
checksum seeded with CONFIG_MAGIC = 0x5350524B ("SPRK").

Field sizes from include/board_config.h (SneezeGUI/SparkMiner):
  MAX_SSID_LENGTH  = 63  → char[64]
  MAX_PASSWORD_LEN = 64  → char[65]
  MAX_POOL_URL_LEN = 80  → char[81]
  MAX_WALLET_LEN   = 120 → char[121]

Struct layout (GCC Xtensa, double is 8-byte aligned):
  [0]   char ssid[64]
  [64]  char wifiPassword[65]
  [129] char poolUrl[81]
  [210] uint16_t poolPort
  [212] char wallet[121]
  [333] char poolPassword[65]
  [398] char backupPoolUrl[81]
  [479] 1-byte pad
  [480] uint16_t backupPoolPort
  [482] char backupWallet[121]
  [603] char backupPoolPassword[65]
  [668] uint8_t brightness
  [669] uint8_t screenTimeout  ← v2.9.5: changed from uint16_t to uint8_t (0-255 seconds)
  [670] uint8_t rotation
  [671] bool displayEnabled
  [672] bool invertColors
  [673] int8_t timezoneOffset
  [674] char workerName[32]
  [706] 6-byte pad (double requires 8-byte alignment; 706%8=2 → pad to 712)
  [712] double targetDifficulty
  [720] bool statsEnabled
  [721] char statsApiUrl[128]
  [849] char statsProxyUrl[128]
  [977] bool enableHttpsStats
  [978] 2-byte pad (uint32 alignment)
  [980] uint32_t checksum
  Total: 984 bytes
"""

import os
import struct
import tempfile

from core.nvs import NVS_PART_OFFSET, NVS_PART_SIZE, _NVSPage

# ── Constants ─────────────────────────────────────────────────────────────────

NAMESPACE    = "sparkminer"
KEY          = "config"
CONFIG_MAGIC = 0x5350524B   # "SPRK"
STRUCT_SIZE  = 984

PART_OFFSET = NVS_PART_OFFSET   # 0x9000
PART_SIZE   = NVS_PART_SIZE     # 0x5000


# ── Struct builder ─────────────────────────────────────────────────────────────

def _put_str(buf: bytearray, offset: int, s: str, field_size: int):
    b = (s or "").encode("utf-8")[:field_size - 1]
    buf[offset:offset + len(b)] = b
    # null-terminate; rest already zero from bytearray()

def _checksum(buf: bytearray) -> int:
    s = CONFIG_MAGIC
    for b in buf[: STRUCT_SIZE - 4]:   # all bytes except last uint32
        s = (s * 31 + b) & 0xFFFFFFFF
    return s

def build_config_struct(
    ssid:            str,
    wifi_pass:       str,
    pool_url:        str,
    pool_port:       int,
    wallet:          str,
    pool_pass:       str  = "x",
    backup_pool_url: str  = "",
    backup_pool_port:int  = 0,
    backup_wallet:   str  = "",
    backup_pool_pass:str  = "x",
    brightness:      int  = 128,
    screen_timeout:  int  = 0,
    rotation:        int  = 0,
    display_enabled: bool = True,
    invert_colors:   bool = False,  # False → invertDisplay(true) = INVON = dark mode on CYD
    timezone_offset: int  = 0,
    worker_name:     str  = "",
    target_diff:     float= 0.0,
) -> bytes:
    """Return the 980-byte miner_config_t struct with valid checksum."""
    buf = bytearray(STRUCT_SIZE)   # zero-initialised

    _put_str(buf, 0,   ssid,            64)
    _put_str(buf, 64,  wifi_pass,       65)
    _put_str(buf, 129, pool_url,        81)
    struct.pack_into("<H", buf, 210, pool_port & 0xFFFF)
    _put_str(buf, 212, wallet,          121)
    _put_str(buf, 333, pool_pass,       65)
    _put_str(buf, 398, backup_pool_url, 81)
    # 1-byte pad at 479 — already zero
    struct.pack_into("<H", buf, 480, backup_pool_port & 0xFFFF)
    _put_str(buf, 482, backup_wallet,   121)
    _put_str(buf, 603, backup_pool_pass,65)
    buf[668] = brightness & 0xFF
    buf[669] = min(max(screen_timeout, 0), 0xFF)  # uint8_t in v2.9.5, max 255 seconds
    buf[670] = rotation & 0xFF
    buf[671] = 1 if display_enabled else 0
    buf[672] = 1 if invert_colors   else 0
    struct.pack_into("<b", buf, 673, max(-128, min(127, timezone_offset)))
    _put_str(buf, 674, worker_name, 32)
    # 6-byte pad at 706-711 (already zero) — aligns double to 8-byte boundary at 712
    struct.pack_into("<d", buf, 712, target_diff)
    # buf[720] statsEnabled = 0 (false)
    # buf[721..] statsApiUrl, statsProxyUrl = "" (already zero)
    # buf[977] enableHttpsStats = 0 (false)
    # 2-byte pad at 978-979 — already zero

    struct.pack_into("<I", buf, 980, _checksum(buf))
    return bytes(buf)


# ── NVS partition generator ───────────────────────────────────────────────────

def generate_nvs_partition(
    ssid:             str,
    wifi_pass:        str,
    pool_url:         str,
    pool_port:        int,
    wallet:           str,
    pool_pass:        str = "x",
    brightness:       int = 128,
    screen_timeout_s: int = 0,
    timezone_hours:   int = 0,
) -> bytes:
    """Return PART_SIZE bytes ready to flash at PART_OFFSET (0x9000).

    Display params:
      brightness:       active display brightness 0-255 (default 128)
      screen_timeout_s: seconds before screen off, 0 = never (default 0)
      timezone_hours:   UTC offset in whole hours, e.g. -5 for EST (default 0)
    """
    blob = build_config_struct(
        ssid=ssid, wifi_pass=wifi_pass,
        pool_url=pool_url, pool_port=pool_port,
        wallet=wallet, pool_pass=pool_pass,
        brightness=brightness,
        screen_timeout=screen_timeout_s,
        timezone_offset=timezone_hours,
    )
    page = _NVSPage(page_num=0)
    page.write_namespace(NAMESPACE, 1)
    page.write_blob(1, KEY, blob)
    return page.to_bytes() + b"\xff" * (PART_SIZE - 4096)


def write_nvs_temp_file(
    ssid:             str,
    wifi_pass:        str,
    pool_url:         str,
    pool_port:        int,
    wallet:           str,
    pool_pass:        str = "x",
    brightness:       int = 128,
    screen_timeout_s: int = 0,
    timezone_hours:   int = 0,
) -> str:
    """Write NVS partition binary to a temp file. Returns path."""
    data = generate_nvs_partition(
        ssid, wifi_pass, pool_url, pool_port, wallet, pool_pass,
        brightness=brightness,
        screen_timeout_s=screen_timeout_s,
        timezone_hours=timezone_hours,
    )
    fd, path = tempfile.mkstemp(suffix="_nvs.bin", prefix="spark_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    return path
