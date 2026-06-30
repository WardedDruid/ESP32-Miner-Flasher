"""
NVS partition generator for NMMiner firmware.

Namespace: "miner_settings"
Keys discovered by string-scanning firmware.bin:
  WifiSSID       STR  — WiFi SSID
  WiFiPSWD       STR  — WiFi password
  btcWallet      STR  — primary BTC wallet address
  PoolUrl1       STR  — primary pool (full URL: stratum+tcp://host:port)
  poolPassword1  STR  — primary pool password
  licence        STR  — 128-char hex license key (British spelling, required)

Flash at the same 0x9000 NVS offset used by BitsyMiner/SparkMiner.
NMMiner's partition table also places nvs at 0x9000 (20 KB, same layout).
"""

import os
import tempfile

from core.nvs import (
    _NVSPage,
    NVS_PART_OFFSET,
    NVS_PART_SIZE,
    NVS_PAGE_SIZE,
)

NMMINER_NAMESPACE = "miner_settings"


def generate_nvs_partition(
    wifi_ssid: str,
    wifi_pass: str,
    wallet: str,
    pool_url: str,
    pool_port: int,
    pool_pass: str = "x",
    licence: str = "",
    partition_size: int = NVS_PART_SIZE,
) -> bytes:
    """
    Build an NMMiner-compatible NVS partition binary.

    pool_url should be just the hostname (e.g. 'hmpool.io'); the port is
    appended here to form the full stratum+tcp://host:port URL that NMMiner
    expects in PoolUrl1.

    Returns partition_size bytes ready to flash at 0x9000.
    """
    page = _NVSPage(page_num=0)
    page.write_namespace(NMMINER_NAMESPACE, 1)

    if wifi_ssid:
        page.write_string(1, "WifiSSID", wifi_ssid)
    if wifi_pass:
        page.write_string(1, "WiFiPSWD", wifi_pass)
    if wallet:
        page.write_string(1, "btcWallet", wallet)
    if pool_url:
        full_url = f"stratum+tcp://{pool_url}:{pool_port}" if pool_port else f"stratum+tcp://{pool_url}"
        page.write_string(1, "PoolUrl1", full_url)
    if pool_pass:
        page.write_string(1, "poolPassword1", pool_pass)
    if licence:
        page.write_string(1, "licence", licence)

    page_data = page.to_bytes()
    padding   = b'\xff' * (partition_size - NVS_PAGE_SIZE)
    return page_data + padding


def write_nvs_temp_file(
    wifi_ssid: str,
    wifi_pass: str,
    wallet: str,
    pool_url: str,
    pool_port: int,
    pool_pass: str = "x",
    licence: str = "",
) -> str:
    """Generate NMMiner NVS binary, write to a temp file, return its path."""
    data = generate_nvs_partition(
        wifi_ssid=wifi_ssid,
        wifi_pass=wifi_pass,
        wallet=wallet,
        pool_url=pool_url,
        pool_port=pool_port,
        pool_pass=pool_pass,
        licence=licence,
    )
    fd, path = tempfile.mkstemp(suffix="_nvs.bin", prefix="nmminer_")
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
