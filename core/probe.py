"""
Chip probing via esptool flash_id.
Runs in a background thread; results delivered via callbacks.
"""

import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class ChipInfo:
    chip: str        # esptool --chip arg: "esp32", "esp32s3", "esp32c3", …
    name: str        # human-readable: "ESP32-D0WD-V3", "ESP32-S3", …
    flash_size: str  # "4MB", "8MB", "16MB", or "detect"
    revision: str    # "v3.1", "v0.2", or ""

    def summary(self) -> str:
        parts = [self.name]
        if self.revision:
            parts.append(self.revision)
        parts.append(self.flash_size + " flash")
        return "  ".join(parts)


# Maps chip name fragment (upper) → esptool chip arg
_CHIP_MAP = {
    "ESP32-S3": "esp32s3",
    "ESP32-S2": "esp32s2",
    "ESP32-C3": "esp32c3",
    "ESP32-C6": "esp32c6",
    "ESP32-H2": "esp32h2",
    "ESP32":    "esp32",   # must be last (broadest match)
}

# Native USB VID:PID → chip family (no probe needed)
NATIVE_USB = {
    (0x303A, 0x1001): "esp32s3",
    (0x303A, 0x1002): "esp32s3",
    (0x303A, 0x0002): "esp32s2",
    (0x303A, 0x1003): "esp32c3",
}


def chip_from_vidpid(vid: Optional[int], pid: Optional[int]) -> Optional[str]:
    """Return esptool chip arg if VID:PID identifies a native-USB ESP32 variant."""
    if vid and pid:
        return NATIVE_USB.get((vid, pid))
    return None


def probe_chip(
    port: str,
    result_cb: Callable[[Optional[ChipInfo]], None],
    log_cb: Callable[[str], None],
):
    """Non-blocking: run esptool flash_id and deliver ChipInfo (or None) via result_cb."""
    threading.Thread(
        target=_run_probe,
        args=(port, result_cb, log_cb),
        daemon=True,
    ).start()


def _esptool_cmd(args: list) -> list:
    """Build an esptool subprocess command that works both frozen and unfrozen."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-esptool"] + args
    return [sys.executable, "-m", "esptool"] + args


def _run_probe(port, result_cb, log_cb):
    cmd = _esptool_cmd(["--port", port, "flash_id"])
    kwargs: dict = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "text": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    lines = []
    try:
        proc = subprocess.Popen(cmd, **kwargs)
        for raw in proc.stdout:
            line = raw.rstrip()
            if line:
                lines.append(line)
                log_cb(line)
        proc.wait()
        result_cb(_parse(lines) if proc.returncode == 0 else None)
    except Exception as exc:
        log_cb(f"Probe error: {exc}")
        result_cb(None)


def _parse(lines: list[str]) -> Optional[ChipInfo]:
    text = "\n".join(lines)

    # Chip name — prefer "Detecting chip type... ESP32-S3" over "Chip is …"
    name = ""
    m = re.search(r"Detecting chip type\.\.\. (.+)", text)
    if m:
        name = m.group(1).strip()
    if not name:
        m = re.search(r"Chip is (ESP32[\w-]*)", text, re.IGNORECASE)
        if m:
            name = m.group(1).strip()
    if not name:
        return None

    # Map to chip family
    name_up = name.upper()
    chip = "esp32"
    for fragment, family in _CHIP_MAP.items():
        if fragment in name_up:
            chip = family
            break

    # Flash size
    flash_size = "detect"
    m = re.search(r"Detected flash size:\s*(\S+)", text)
    if m:
        flash_size = m.group(1)

    # Revision
    revision = ""
    m = re.search(r"\(revision (v[\d.]+)\)", text)
    if m:
        revision = m.group(1)

    return ChipInfo(chip=chip, name=name, flash_size=flash_size, revision=revision)
