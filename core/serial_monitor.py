"""
Background threaded serial reader with ANSI stripping and NMMiner device-code detection.

Callbacks fire from the reader thread — callers must marshal to the UI thread
(e.g. ``widget.after(0, callback, arg)``).
"""

import re
import threading

import serial

# Strips ANSI colour / cursor escape sequences
ANSI_RE = re.compile(rb'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

# Matches NMMiner's  "Device code [64-hex-chars]"
DEVICE_CODE_RE = re.compile(r'Device code \[([0-9a-f]{64})\]')


class SerialMonitor:
    """Threaded serial reader / writer."""

    def __init__(self, on_line=None, on_device_code=None,
                 on_connect=None, on_disconnect=None):
        self._on_line        = on_line
        self._on_device_code = on_device_code
        self._on_connect     = on_connect
        self._on_disconnect  = on_disconnect

        self._ser    = None
        self._thread = None
        self._stop   = threading.Event()
        self._lock   = threading.Lock()
        self._seen_codes: set = set()

    # ── Public API ────────────────────────────────────────────────────────

    def connect(self, port: str, baud: int = 115200) -> bool:
        self.disconnect()
        self._stop.clear()
        self._seen_codes.clear()
        try:
            self._ser = serial.Serial(port, baud, timeout=0.1)
        except Exception as e:
            if self._on_line:
                self._on_line(f"[Serial] Cannot open {port}: {e}\n")
            return False
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        if self._on_connect:
            self._on_connect(port, baud)
        return True

    def disconnect(self):
        self._stop.set()
        with self._lock:
            if self._ser:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None
        if self._on_disconnect:
            self._on_disconnect()

    def send(self, text: str):
        with self._lock:
            if self._ser and self._ser.is_open:
                self._ser.write(text.encode('utf-8', errors='replace'))

    @property
    def connected(self) -> bool:
        with self._lock:
            return bool(self._ser and self._ser.is_open)

    # ── Reader thread ─────────────────────────────────────────────────────

    def _read_loop(self):
        buf = b''
        while not self._stop.is_set():
            try:
                with self._lock:
                    ser = self._ser
                if ser and ser.is_open:
                    chunk = ser.read(256)
                    if chunk:
                        buf += chunk
                        while b'\n' in buf:
                            line, buf = buf.split(b'\n', 1)
                            self._dispatch(line + b'\n')
            except Exception as e:
                if not self._stop.is_set():
                    if self._on_line:
                        self._on_line(f"[Serial] Read error: {e}\n")
                    with self._lock:
                        self._ser = None
                    if self._on_disconnect:
                        self._on_disconnect()
                break
        if buf:
            self._dispatch(buf)

    def _dispatch(self, raw: bytes):
        clean = ANSI_RE.sub(b'', raw).decode('utf-8', errors='replace')
        if self._on_line:
            self._on_line(clean)
        if self._on_device_code:
            m = DEVICE_CODE_RE.search(clean)
            if m:
                code = m.group(1)
                if code not in self._seen_codes:
                    self._seen_codes.add(code)
                    self._on_device_code(code)
