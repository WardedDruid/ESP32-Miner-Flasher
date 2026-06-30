"""
Minimal pure-Python SPIFFS image writer — enough to write a single JSON file.

Used to pre-configure NerdMiner v2 pool settings in /config.json so users
skip the captive portal for pool config.  WiFi credentials are stored by
WiFiManager separately and still require the portal on first boot.

SPIFFS parameters (Arduino ESP32 defaults):
  page_size  = 256 bytes
  block_size = 4096 bytes  (= 1 flash erase sector)
  obj_id_len = 2 bytes
  pages_per_block = 16

NerdMiner huge_app.csv partition layout:
  SPIFFS at 0x310000, size 0xE0000 (224 blocks of 4096 bytes each)

References:
  ESP-IDF spiffsgen.py, SPIFFS source (spiffs_nucleus.c / spiffs.h)
"""

import json
import os
import struct
import tempfile

# ── Geometry ──────────────────────────────────────────────────────────────────

PAGE_SIZE       = 256
BLOCK_SIZE      = 4096
OBJ_ID_LEN      = 2
OBJ_NAME_LEN    = 32
META_LEN        = 4
PAGES_PER_BLOCK = BLOCK_SIZE // PAGE_SIZE        # 16
OBJ_IX_FLAG     = 0x8000                         # MSB set → index page

# Page flags (active-low bits — set by clearing from 0xFF)
# ESP-IDF SPIFFS uses only bits 0 (USED) and 1 (FINAL); both cleared = written & finalized.
# No separate index-page flag bit exists — index pages use the same 0xFC as data pages.
FLAG_DATA_FINAL  = 0xFC   # 0b11111100 — bits 0,1 cleared
FLAG_INDEX_FINAL = 0xFC   # same as data pages (matches spiffsgen.py)

SPIFFS_TYPE_FILE = 1

# Partition constants for NerdMiner v2 (huge_app partition scheme)
NERDMINER_SPIFFS_OFFSET = 0x310000
NERDMINER_SPIFFS_SIZE   = 0x0E0000    # 917 504 bytes = 224 blocks


# ── SPIFFS block magic ────────────────────────────────────────────────────────

def _block_magic(total_blocks: int, bix: int) -> int:
    """
    Compute the 16-bit block-check magic used by Arduino ESP32 SPIFFS.
    SPIFFS_USE_MAGIC=y, SPIFFS_USE_MAGIC_LENGTH=y (default for esp32).
    """
    magic = (0x20140529 ^ PAGE_SIZE) ^ (total_blocks - bix)
    return magic & 0xFFFF


# ── Page builders ─────────────────────────────────────────────────────────────

def _lu_page(entries: list[int], magic: int) -> bytes:
    """
    Build one Lookup (LU) page (256 bytes).
    entries — obj_id per page in the block (len == PAGES_PER_BLOCK).
    magic   — uint16 block-check magic value.
    """
    page = bytearray(b"\xff" * PAGE_SIZE)
    for i, oid in enumerate(entries):
        struct.pack_into("<H", page, i * OBJ_ID_LEN, oid)
    # Magic is placed immediately after the lookup table
    magic_off = PAGES_PER_BLOCK * OBJ_ID_LEN   # = 32
    struct.pack_into("<H", page, magic_off, magic)
    return bytes(page)


def _index_page(obj_id: int, file_size: int, filename: str, data_page_ix: int) -> bytes:
    """
    Build an object-index page (span_ix=0) for a file.
    data_page_ix — physical page number holding span-0 data.
    """
    page = bytearray(b"\xff" * PAGE_SIZE)

    # 5-byte page header
    struct.pack_into("<H", page, 0, obj_id | OBJ_IX_FLAG)  # MSB set = index
    struct.pack_into("<H", page, 2, 0)                     # span_ix = 0
    page[4] = FLAG_INDEX_FINAL

    # Object-index header (only for span_ix == 0)
    struct.pack_into("<I", page, 5, file_size)   # file size
    page[9] = SPIFFS_TYPE_FILE
    fn = filename.encode("utf-8")[: OBJ_NAME_LEN - 1]
    page[10 : 10 + len(fn)] = fn
    page[10 + len(fn) : 10 + OBJ_NAME_LEN] = b"\x00" * (OBJ_NAME_LEN - len(fn))
    # Metadata (META_LEN bytes, already 0xFF)

    # Page-reference array — starts at offset 5 + 4 + 1 + OBJ_NAME_LEN + META_LEN = 46
    ref_off = 5 + 4 + 1 + OBJ_NAME_LEN + META_LEN  # = 46
    struct.pack_into("<H", page, ref_off, data_page_ix)
    # remaining refs stay 0xFFFF

    return bytes(page)


def _data_page(obj_id: int, span_ix: int, content: bytes) -> bytes:
    """Build one data page (256 bytes) holding up to 251 bytes of file content."""
    page = bytearray(b"\xff" * PAGE_SIZE)
    struct.pack_into("<H", page, 0, obj_id)   # no MSB flag for data pages
    struct.pack_into("<H", page, 2, span_ix)
    page[4] = FLAG_DATA_FINAL
    chunk = content[:PAGE_SIZE - 5]
    page[5 : 5 + len(chunk)] = chunk
    return bytes(page)


# ── filesystem tool helpers ───────────────────────────────────────────────────

import glob as _glob
import subprocess as _subprocess

def _find_mkspiffs() -> str:
    """Return path to mkspiffs.exe from Arduino ESP32 install, or ''."""
    pattern = (
        r"C:/Users/*/AppData/Local/Arduino15/packages/esp32"
        r"/tools/mkspiffs/**/*.exe"
    )
    candidates = _glob.glob(pattern, recursive=True)
    return sorted(candidates)[-1] if candidates else ""


def _find_mklittlefs() -> str:
    """Return path to mklittlefs.exe from Arduino ESP32 3.x install, or ''."""
    pattern = (
        r"C:/Users/*/AppData/Local/Arduino15/packages/esp32"
        r"/tools/mklittlefs/**/*.exe"
    )
    candidates = _glob.glob(pattern, recursive=True)
    return sorted(candidates)[-1] if candidates else ""


# ── Public API ────────────────────────────────────────────────────────────────

def write_spiffs_temp_file(
    pool_url:  str,
    pool_port: int,
    wallet:    str,
    pool_pass: str = "x",
) -> str:
    """
    Generate the NerdMiner /config.json filesystem image and return the path.

    Arduino ESP32 3.x uses LittleFS internally even when the code says SPIFFS,
    so we use mklittlefs.exe when available.  Falls back to the pure-Python
    SPIFFS generator for Arduino ESP32 2.x installs.
    """
    config = {
        "poolString":   pool_url,
        "portNumber":   pool_port,
        "poolPassword": pool_pass,
        "btcString":    wallet,
        "gmtZone":      0,
    }
    json_str = json.dumps(config, separators=(",", ":"))

    mkspiffs = _find_mkspiffs()
    if mkspiffs:
        # ── SPIFFS path via mkspiffs (Arduino ESP32 2.x — what NerdMiner uses)
        src_dir = tempfile.mkdtemp(prefix="nerd_src_")
        try:
            with open(os.path.join(src_dir, "config.json"), "w") as f:
                f.write(json_str)

            fd, out_path = tempfile.mkstemp(suffix="_spiffs.bin", prefix="nerd_")
            os.close(fd)

            result = _subprocess.run(
                [
                    mkspiffs,
                    "-c", src_dir,
                    "-b", str(BLOCK_SIZE),
                    "-p", str(PAGE_SIZE),
                    "-s", str(NERDMINER_SPIFFS_SIZE),
                    out_path,
                ],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"mkspiffs failed: {result.stderr.strip() or result.stdout.strip()}"
                )
            return out_path
        finally:
            import shutil
            shutil.rmtree(src_dir, ignore_errors=True)

    else:
        # ── Pure-Python SPIFFS fallback ───────────────────────────────────
        json_bytes = json_str.encode("utf-8")
        num_blocks = NERDMINER_SPIFFS_SIZE // BLOCK_SIZE
        magic      = _block_magic(num_blocks, 0)

        lu_entries = [0xFFFF] * PAGES_PER_BLOCK
        lu_entries[1] = 1 | OBJ_IX_FLAG
        lu_entries[2] = 1

        block0 = (
            _lu_page(lu_entries, magic)
            + _index_page(1, len(json_bytes), "/config.json", 2)
            + _data_page(1, 0, json_bytes)
            + b"\xff" * ((PAGES_PER_BLOCK - 3) * PAGE_SIZE)
        )
        data = block0 + b"\xff" * (NERDMINER_SPIFFS_SIZE - BLOCK_SIZE)

        fd, path = tempfile.mkstemp(suffix="_spiffs.bin", prefix="nerd_")
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
    return path
