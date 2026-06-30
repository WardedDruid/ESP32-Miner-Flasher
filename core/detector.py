import threading
import serial.tools.list_ports

# USB VID:PID for chips commonly used on ESP32 boards
_CHIP_NAMES = {
    (0x1A86, 0x7523): "CH340",
    (0x1A86, 0x7522): "CH341",
    (0x1A86, 0x55D4): "CH9102",   # newer CYD boards
    (0x10C4, 0xEA60): "CP210x",
    (0x0403, 0x6001): "FTDI FT232R",
    (0x0403, 0x6010): "FTDI FT2232",
    (0x0403, 0x6014): "FTDI FT232H",
    (0x303A, 0x1001): "ESP32-S3 USB",
    (0x303A, 0x0002): "ESP32-S2 USB",
}


def _chip_name(port_info):
    if port_info.vid and port_info.pid:
        return _CHIP_NAMES.get((port_info.vid, port_info.pid))
    return None


def get_ports():
    """Return list of (device, display_label, is_esp32, vid, pid) sorted ESP32-first."""
    results = []
    for p in serial.tools.list_ports.comports():
        chip = _chip_name(p)
        is_esp32 = chip is not None
        desc = chip if chip else (p.description or "Unknown")
        label = f"{p.device}  —  {desc}"
        results.append((p.device, label, is_esp32, p.vid, p.pid))
    return sorted(results, key=lambda x: (not x[2], x[0]))


class PortDetector:
    """Background thread that calls on_change() whenever the port list changes."""

    def __init__(self, on_change, poll_interval=1.5):
        self._on_change = on_change
        self._poll_interval = poll_interval
        self._stop = threading.Event()
        self._last_snapshot = None

    def start(self):
        t = threading.Thread(target=self._run, daemon=True)
        t.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(self._poll_interval):
            snapshot = frozenset(p.device for p in serial.tools.list_ports.comports())
            if snapshot != self._last_snapshot:
                self._last_snapshot = snapshot
                self._on_change()
