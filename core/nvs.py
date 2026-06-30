"""
Pure-Python ESP32 NVS partition binary generator.

Generates a pre-configured NVS partition that can be flashed alongside
BitsyMiner firmware so the board connects to WiFi and the mining pool
automatically — no captive portal needed.

NVS binary format follows ESP-IDF nvs_partition_gen.py specification.
Namespace "storage" with keys matching BitsyMiner's nvs_handler.cpp.
"""

import os
import struct
import tempfile
import zlib

# ── Page layout constants ─────────────────────────────────────────────────────

NVS_PAGE_SIZE           = 4096
NVS_ENTRY_SIZE          = 32
NVS_MAX_ENTRIES         = 126
NVS_BITMAP_OFFSET       = 32    # immediately after 32-byte page header
NVS_FIRST_ENTRY_OFFSET  = 64    # header (32) + bitmap (32)

PAGE_ACTIVE = 0xFFFFFFFE
PAGE_FULL   = 0xFFFFFFFC
VERSION2    = 0xFE

# Entry type codes
TYPE_U8        = 0x01
TYPE_U16       = 0x02
TYPE_STR       = 0x21
TYPE_BLOB_DATA = 0x42   # chunked blob data entry  (0x41 = old single-entry blob, NOT this)
TYPE_BLOB_IDX  = 0x48   # blob index (finalizer)

CHUNK_ANY = 0xFF

# ── BitsyMiner-specific constants ─────────────────────────────────────────────

# All BitsyMiner settings live under namespace "storage" (nvs_handler.cpp)
BITSY_NAMESPACE = "storage"

# Standard Arduino ESP32 NVS partition (default.csv for 4 MB flash boards)
NVS_PART_OFFSET = 0x9000   # flash offset
NVS_PART_SIZE   = 0x5000   # 5 pages × 4096 bytes = 20 KB


# ── CRC helper ────────────────────────────────────────────────────────────────

def _crc32(data: bytes) -> int:
    """ESP32 NVS CRC-32: init=0xFFFFFFFF, poly=0xEDB88320, no final XOR."""
    return zlib.crc32(data, 0xFFFFFFFF) & 0xFFFFFFFF


# ── NVS page builder ──────────────────────────────────────────────────────────

class _NVSPage:
    def __init__(self, page_num: int = 0):
        self._buf      = bytearray(b'\xff' * NVS_PAGE_SIZE)
        self._bitmap   = bytearray(b'\xff' * 32)
        self._entry_num = 0

        # Page header (32 bytes)
        struct.pack_into('<I', self._buf, 0, PAGE_ACTIVE)   # status
        struct.pack_into('<I', self._buf, 4, page_num)      # sequence number
        self._buf[8] = VERSION2                              # format version
        # bytes 9-27: reserved (0xFF already)
        crc = _crc32(bytes(self._buf[4:28]))
        struct.pack_into('<I', self._buf, 28, crc)          # header CRC

        # Bitmap starts all-EMPTY (0xFF = all bits 1 = all entries EMPTY)
        self._sync_bitmap()

    def _sync_bitmap(self):
        self._buf[NVS_BITMAP_OFFSET:NVS_BITMAP_OFFSET + 32] = self._bitmap

    def _mark_written(self):
        """Mark current entry as WRITTEN (0b11 → 0b10: clear lower bit of 2-bit pair)."""
        bitnum     = self._entry_num * 2
        byte_idx   = bitnum // 8
        bit_offset = bitnum & 7
        self._bitmap[byte_idx] &= ~(1 << bit_offset) & 0xFF
        self._buf[NVS_BITMAP_OFFSET + byte_idx] = self._bitmap[byte_idx]
        self._entry_num += 1

    def _place(self, data: bytes):
        """Write exactly 32 bytes at the current entry slot, then advance."""
        off = NVS_FIRST_ENTRY_OFFSET + self._entry_num * NVS_ENTRY_SIZE
        self._buf[off:off + NVS_ENTRY_SIZE] = data
        self._mark_written()

    def _entry_header_crc(self, e: bytearray) -> int:
        """Entry header CRC covers bytes 0-3 + bytes 8-31 (28 bytes)."""
        return _crc32(bytes(e[0:4]) + bytes(e[8:32]))

    # ── Entry writers ────────────────────────────────────────────────────

    def write_namespace(self, name: str, ns_index: int):
        """Write a namespace definition entry (type U8, value = ns_index)."""
        e = bytearray(b'\xff' * NVS_ENTRY_SIZE)
        e[0] = 0          # namespace entries belong to namespace 0
        e[1] = TYPE_U8    # namespace index stored as U8
        e[2] = 1          # span
        e[3] = CHUNK_ANY
        kb = name.encode('utf-8')[:15]
        e[8:8 + len(kb)] = kb
        e[8 + len(kb):24] = b'\x00' * (16 - len(kb))
        e[24] = ns_index  # the value
        # bytes 25-31: 0xFF
        struct.pack_into('<I', e, 4, self._entry_header_crc(e))
        self._place(bytes(e))

    def write_string(self, ns_index: int, key: str, value: str):
        """Write a null-terminated string entry (header + data entries)."""
        raw      = value.encode('utf-8') + b'\x00'
        datalen  = len(raw)
        rounded  = (datalen + NVS_ENTRY_SIZE - 1) & ~(NVS_ENTRY_SIZE - 1)
        n_data   = rounded // NVS_ENTRY_SIZE

        # Header entry
        e = bytearray(b'\xff' * NVS_ENTRY_SIZE)
        e[0] = ns_index
        e[1] = TYPE_STR
        e[2] = 1 + n_data   # total span
        e[3] = CHUNK_ANY
        kb = key.encode('utf-8')[:15]
        e[8:8 + len(kb)] = kb
        e[8 + len(kb):24] = b'\x00' * (16 - len(kb))
        struct.pack_into('<H', e, 24, datalen)          # data length (incl. \0)
        # bytes 26-27: reserved (0xFF)
        struct.pack_into('<I', e, 28, _crc32(bytes(raw)))  # data CRC
        struct.pack_into('<I', e, 4, self._entry_header_crc(e))
        self._place(bytes(e))

        # Data entries: raw bytes padded with 0xFF to NVS_ENTRY_SIZE boundary
        padded = raw + b'\xff' * (rounded - datalen)
        for i in range(n_data):
            self._place(padded[i * NVS_ENTRY_SIZE:(i + 1) * NVS_ENTRY_SIZE])

    def write_u8(self, ns_index: int, key: str, value: int):
        """Write a U8 (uint8) entry."""
        e = bytearray(b'\xff' * NVS_ENTRY_SIZE)
        e[0] = ns_index
        e[1] = TYPE_U8
        e[2] = 1
        e[3] = CHUNK_ANY
        kb = key.encode('utf-8')[:15]
        e[8:8 + len(kb)] = kb
        e[8 + len(kb):24] = b'\x00' * (16 - len(kb))
        e[24] = value & 0xFF
        struct.pack_into('<I', e, 4, self._entry_header_crc(e))
        self._place(bytes(e))

    def write_u16(self, ns_index: int, key: str, value: int):
        """Write a U16 (uint16) entry."""
        e = bytearray(b'\xff' * NVS_ENTRY_SIZE)
        e[0] = ns_index
        e[1] = TYPE_U16
        e[2] = 1
        e[3] = CHUNK_ANY
        kb = key.encode('utf-8')[:15]
        e[8:8 + len(kb)] = kb
        e[8 + len(kb):24] = b'\x00' * (16 - len(kb))
        struct.pack_into('<H', e, 24, value & 0xFFFF)
        # bytes 26-31: 0xFF
        struct.pack_into('<I', e, 4, self._entry_header_crc(e))
        self._place(bytes(e))

    def write_blob(self, ns_index: int, key: str, data: bytes):
        """Write arbitrary bytes as a BLOB_DATA + BLOB_IDX pair."""
        datalen  = len(data)
        rounded  = (datalen + NVS_ENTRY_SIZE - 1) & ~(NVS_ENTRY_SIZE - 1)
        n_data   = rounded // NVS_ENTRY_SIZE
        kb = key.encode('utf-8')[:15]

        # ── BLOB_DATA header ─────────────────────────────────────────────
        e = bytearray(b'\xff' * NVS_ENTRY_SIZE)
        e[0] = ns_index
        e[1] = TYPE_BLOB_DATA
        e[2] = 1 + n_data    # span: header + data entries
        e[3] = 0             # chunk_index = 0 (first/only chunk)
        e[8:8 + len(kb)] = kb
        e[8 + len(kb):24] = b'\x00' * (16 - len(kb))
        struct.pack_into('<H', e, 24, datalen)
        # e[26:28] reserved — leave 0xFF
        struct.pack_into('<I', e, 28, _crc32(bytes(data)))
        struct.pack_into('<I', e, 4, self._entry_header_crc(e))
        self._place(bytes(e))

        # ── Raw data entries ──────────────────────────────────────────────
        padded = data + b'\xff' * (rounded - datalen)
        for i in range(n_data):
            self._place(padded[i * NVS_ENTRY_SIZE:(i + 1) * NVS_ENTRY_SIZE])

        # ── BLOB_IDX finalizer ────────────────────────────────────────────
        e = bytearray(b'\xff' * NVS_ENTRY_SIZE)
        e[0] = ns_index
        e[1] = TYPE_BLOB_IDX
        e[2] = 1
        e[3] = CHUNK_ANY      # 0xFF
        e[8:8 + len(kb)] = kb
        e[8 + len(kb):24] = b'\x00' * (16 - len(kb))
        struct.pack_into('<I', e, 24, datalen)   # total blob size
        e[28] = 1   # chunk_count
        e[29] = 0   # chunk_start
        struct.pack_into('<I', e, 4, self._entry_header_crc(e))
        self._place(bytes(e))

    def to_bytes(self) -> bytes:
        return bytes(self._buf)


# ── ESP32 WiFi stack NVS (shared by NerdMiner, SparkMiner, etc.) ─────────────

WIFI_NAMESPACE = "nvs.net80211"   # ESP32 WiFi stack NVS namespace


def generate_wifi_nvs_partition(
    wifi_ssid: str,
    wifi_pass: str,
    partition_size: int = NVS_PART_SIZE,
) -> bytes:
    """
    Build an NVS partition that pre-seeds ESP32 WiFi credentials.

    Writes sta.ssid (32-byte blob) and sta.pswd (64-byte blob) into the
    'nvs.net80211' namespace — the same location the ESP32 WiFi stack reads
    on boot, so the device connects without a captive portal.
    """
    page = _NVSPage(page_num=0)
    page.write_namespace(WIFI_NAMESPACE, 1)

    # opmode: 1 = WIFI_MODE_STA (required so the driver loads the STA config on boot)
    page.write_u8 (1, "opmode",   1)
    # bssid.set: 0 = connect to any AP with matching SSID, don't filter by BSSID
    page.write_u8 (1, "bssid.set", 0)
    # sta.chan: 0 = any channel
    page.write_u8 (1, "sta.chan", 0)

    if wifi_ssid:
        # 36 bytes: 4-byte length (uint32 LE) + 32-byte SSID field — ESP-IDF WiFi NVS format
        ssid_bytes = wifi_ssid.encode('utf-8')[:32].ljust(32, b'\x00')
        ssid_len   = struct.pack('<I', len(wifi_ssid))
        page.write_blob(1, "sta.ssid", ssid_len + ssid_bytes)
    if wifi_pass:
        # 65 bytes: 64-byte password field + 1 null terminator — matches ESP-IDF WiFi NVS format
        pswd_blob = wifi_pass.encode('utf-8')[:64].ljust(64, b'\x00') + b'\x00'
        page.write_blob(1, "sta.pswd", pswd_blob)

    page_data = page.to_bytes()
    padding   = b'\xff' * (partition_size - NVS_PAGE_SIZE)
    return page_data + padding


def write_wifi_nvs_temp_file(wifi_ssid: str, wifi_pass: str) -> str:
    """Write WiFi NVS partition to a temp file and return its path."""
    data = generate_wifi_nvs_partition(wifi_ssid, wifi_pass)
    import tempfile
    fd, path = tempfile.mkstemp(suffix="_nvs.bin", prefix="wifi_")
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    return path


# ── Public API ────────────────────────────────────────────────────────────────

def generate_nvs_partition(
    wifi_ssid: str,
    wifi_pass: str,
    pool_url: str,
    pool_port: int,
    wallet: str,
    pool_pass: str = "x",
    partition_size: int = NVS_PART_SIZE,
) -> bytes:
    """
    Build a BitsyMiner-compatible NVS partition binary.

    Returns `partition_size` bytes ready to flash at NVS_PART_OFFSET (0x9000).
    Only writes fields that are non-empty / non-zero.
    The remaining pages are 0xFF (erased flash).
    """
    page = _NVSPage(page_num=0)
    page.write_namespace(BITSY_NAMESPACE, 1)

    # MODE_INSTALL_COMPLETE (0x0707) — must be set or BitsyMiner ignores all
    # stored credentials and falls back to captive-portal AP mode on every boot.
    page.write_u16(1, "currentMode", 0x0707)

    if wifi_ssid:
        page.write_string(1, "ssid",         wifi_ssid)
    if wifi_pass:
        page.write_string(1, "ssidPassword", wifi_pass)
    if pool_url:
        page.write_string(1, "poolUrl",      pool_url)
    if pool_port:
        page.write_u16  (1, "poolPort",      pool_port)
    if wallet:
        page.write_string(1, "wallet",       wallet)
    if pool_pass:
        page.write_string(1, "poolPass",     pool_pass)

    page_data = page.to_bytes()
    padding   = b'\xff' * (partition_size - NVS_PAGE_SIZE)
    return page_data + padding


def write_nvs_temp_file(
    wifi_ssid: str,
    wifi_pass: str,
    pool_url: str,
    pool_port: int,
    wallet: str,
    pool_pass: str = "x",
) -> str:
    """
    Generate a BitsyMiner NVS partition binary and write it to a temp file.
    Returns the temp file path (caller is responsible for deleting it).
    """
    data = generate_nvs_partition(
        wifi_ssid  = wifi_ssid,
        wifi_pass  = wifi_pass,
        pool_url   = pool_url,
        pool_port  = pool_port,
        wallet     = wallet,
        pool_pass  = pool_pass,
    )
    fd, path = tempfile.mkstemp(suffix="_nvs.bin", prefix="bitsy_")
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    return path
