import os
from datetime import datetime
from tkinter import filedialog
from typing import Dict, Optional

import customtkinter as ctk
from tkinterdnd2 import DND_FILES, TkinterDnD

from core.detector import PortDetector, get_ports
from core.flasher import FlashTask, Flasher
from core import nvs as _nvs
from core import sparkminer as _spark
from core import spiffs as _spiffs
from core import nmminer as _nmminer
from core.serial_monitor import SerialMonitor
from core import history as _hist
from core.patcher import fields_present, image_chip, patch_binary
from core.probe import ChipInfo, chip_from_vidpid, probe_chip

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Board definitions ──────────────────────────────────────────────────────────

BOARDS = {
    'CYD 2.8"  (ESP32-2432S028R)': {
        "chip": "esp32", "flash_mode": "dio", "flash_freq": "40m",
        "flash_size": "4MB", "baud": 921600, "single_offset": 0x0,
        "part_offsets": {"Bootloader": 0x1000, "Partition Table": 0x8000, "App": 0x10000},
    },
    'CYD 2USB  (ESP32-2432S028R v2)': {
        "chip": "esp32", "flash_mode": "dio", "flash_freq": "40m",
        "flash_size": "4MB", "baud": 921600, "single_offset": 0x0,
        "part_offsets": {"Bootloader": 0x1000, "Partition Table": 0x8000, "App": 0x10000},
    },
    'CYD 4.3"  (ESP32-S3 8048S043)': {
        "chip": "esp32s3", "flash_mode": "qio", "flash_freq": "80m",
        "flash_size": "16MB", "baud": 921600, "single_offset": 0x0,
        "part_offsets": {"Bootloader": 0x0, "Partition Table": 0x8000, "App": 0x10000},
    },
    "ESP32 Standard DevKit": {
        "chip": "esp32", "flash_mode": "dio", "flash_freq": "40m",
        "flash_size": "detect", "baud": 921600, "single_offset": 0x0,
        "part_offsets": {"Bootloader": 0x1000, "Partition Table": 0x8000, "App": 0x10000},
    },
}

_CHIP_BOARD_MAP = {
    ("esp32s3", "16MB"): 'CYD 4.3"  (ESP32-S3 8048S043)',
    ("esp32s3", ""):     'CYD 4.3"  (ESP32-S3 8048S043)',
    ("esp32",   "4MB"):  'CYD 2.8"  (ESP32-2432S028R)',
    ("esp32",   ""):     "ESP32 Standard DevKit",
}

# General pools — compatible with BitsyMiner, SparkMiner, and standard Stratum v1 clients.
# ocean.xyz, solo.ckpool.org, braiins set difficulty too high for ESP32-class miners.
# btcplebpool.com DNS does not resolve (defunct as of mid-2025).
XEC_POOLS: set[str] = {
    "xec.hmpool.io",
}

KNOWN_POOLS: dict[str, int] = {
    "hmpool.io":                  3337,
    "btc.hmpool.io":              3337,
    "xec.hmpool.io":              3337,
    "public-pool.io":             21496,
    "pool.sethforprivacy.com":    3333,
    "pool.nerdminer.io":          3333,
    "pool.solomining.de":         3333,
}

# NerdMiner-specific pools — pool.nerdminers.org checks the Stratum user-agent
# string and rejects any client that isn't NerdMiner firmware.
NERDMINER_POOLS: dict[str, int] = {
    "pool.nerdminers.org": 3333,
    "pool.nerdminer.io":   3333,
}

# Combined lookup used by _on_pool_selected
_ALL_POOLS: dict[str, int] = {**KNOWN_POOLS, **NERDMINER_POOLS}

# ── Display & Clock dropdown options ─────────────────────────────────────────

_BRIGHTNESS_OPTIONS = ["Full (100%)", "High (75%)", "Medium (50%)", "Low (25%)", "Minimum (10%)"]
_BRIGHTNESS_VALUES  = {"Full (100%)": 255, "High (75%)": 192, "Medium (50%)": 128,
                        "Low (25%)": 64, "Minimum (10%)": 25}
# SparkMiner uses 0-100 % brightness scale (firmware clamps anything > 100 to 100)
_SPARK_BRIGHTNESS_VALUES = {"Full (100%)": 100, "High (75%)": 75, "Medium (50%)": 50,
                             "Low (25%)": 25, "Minimum (10%)": 10}

_DIM_TIMER_OPTIONS = ["Never", "30 seconds", "1 minute", "5 minutes", "30 minutes"]
_DIM_TIMER_MS      = {"Never": None, "30 seconds": 30_000, "1 minute": 60_000,
                       "5 minutes": 300_000, "30 minutes": 1_800_000}

_DIM_TO_OPTIONS = ["Screen off", "Very dim (10%)", "Dim (25%)", "Medium (50%)"]
_DIM_TO_VALUES  = {"Screen off": 0, "Very dim (10%)": 25, "Dim (25%)": 64, "Medium (50%)": 128}

_TIMEZONE_OPTIONS: list[str] = [
    "UTC-12:00 (IDLW)",
    "UTC-11:00 (SST)",
    "UTC-10:00 (HST — Hawaii)",
    "UTC-9:00  (AKST — Alaska)",
    "UTC-8:00  (PST — Pacific)",
    "UTC-7:00  (MST / PDT — Mountain)",
    "UTC-6:00  (CST / MDT — Central)",
    "UTC-5:00  (EST / CDT — Eastern)",
    "UTC-4:00  (EDT / AST — Atlantic)",
    "UTC-3:30  (NST — Newfoundland)",
    "UTC-3:00  (BRT — Brazil / ART — Argentina)",
    "UTC-2:00  (GST — South Georgia)",
    "UTC-1:00  (CVT — Cape Verde)",
    "UTC+0:00  (GMT / UTC)",
    "UTC+1:00  (CET — Central Europe / WAT — W. Africa)",
    "UTC+2:00  (EET — Eastern Europe / CAT — C. Africa)",
    "UTC+3:00  (MSK — Moscow / EAT — E. Africa)",
    "UTC+3:30  (IRST — Iran)",
    "UTC+4:00  (GST — Gulf / GET — Georgia)",
    "UTC+4:30  (AFT — Afghanistan)",
    "UTC+5:00  (PKT — Pakistan / UZT — Uzbekistan)",
    "UTC+5:30  (IST — India / SLT — Sri Lanka)",
    "UTC+5:45  (NPT — Nepal)",
    "UTC+6:00  (BST — Bangladesh / OMST — Omsk)",
    "UTC+6:30  (MMT — Myanmar)",
    "UTC+7:00  (ICT — Indochina / WIB — W. Indonesia)",
    "UTC+8:00  (CST — China / AWST — W. Australia / SGT — Singapore)",
    "UTC+9:00  (JST — Japan / KST — Korea)",
    "UTC+9:30  (ACST — Australia Central)",
    "UTC+10:00 (AEST — Australia Eastern / PGT — PNG)",
    "UTC+10:30 (LHST — Lord Howe Island)",
    "UTC+11:00 (SBT — Solomon Islands / MAGT — Magadan)",
    "UTC+12:00 (NZST — New Zealand / FJT — Fiji)",
    "UTC+13:00 (TOT — Tonga / NZDT — NZ Daylight)",
    "UTC+14:00 (LINT — Line Islands)",
]
_TIMEZONE_SECONDS: dict[str, int] = {
    "UTC-12:00 (IDLW)":                                    -43200,
    "UTC-11:00 (SST)":                                     -39600,
    "UTC-10:00 (HST — Hawaii)":                            -36000,
    "UTC-9:00  (AKST — Alaska)":                           -32400,
    "UTC-8:00  (PST — Pacific)":                           -28800,
    "UTC-7:00  (MST / PDT — Mountain)":                    -25200,
    "UTC-6:00  (CST / MDT — Central)":                     -21600,
    "UTC-5:00  (EST / CDT — Eastern)":                     -18000,
    "UTC-4:00  (EDT / AST — Atlantic)":                    -14400,
    "UTC-3:30  (NST — Newfoundland)":                      -12600,
    "UTC-3:00  (BRT — Brazil / ART — Argentina)":          -10800,
    "UTC-2:00  (GST — South Georgia)":                      -7200,
    "UTC-1:00  (CVT — Cape Verde)":                         -3600,
    "UTC+0:00  (GMT / UTC)":                                    0,
    "UTC+1:00  (CET — Central Europe / WAT — W. Africa)":   3600,
    "UTC+2:00  (EET — Eastern Europe / CAT — C. Africa)":   7200,
    "UTC+3:00  (MSK — Moscow / EAT — E. Africa)":          10800,
    "UTC+3:30  (IRST — Iran)":                              12600,
    "UTC+4:00  (GST — Gulf / GET — Georgia)":               14400,
    "UTC+4:30  (AFT — Afghanistan)":                        16200,
    "UTC+5:00  (PKT — Pakistan / UZT — Uzbekistan)":        18000,
    "UTC+5:30  (IST — India / SLT — Sri Lanka)":            19800,
    "UTC+5:45  (NPT — Nepal)":                              20700,
    "UTC+6:00  (BST — Bangladesh / OMST — Omsk)":           21600,
    "UTC+6:30  (MMT — Myanmar)":                            23400,
    "UTC+7:00  (ICT — Indochina / WIB — W. Indonesia)":     25200,
    "UTC+8:00  (CST — China / AWST — W. Australia / SGT — Singapore)": 28800,
    "UTC+9:00  (JST — Japan / KST — Korea)":                32400,
    "UTC+9:30  (ACST — Australia Central)":                 34200,
    "UTC+10:00 (AEST — Australia Eastern / PGT — PNG)":     36000,
    "UTC+10:30 (LHST — Lord Howe Island)":                  37800,
    "UTC+11:00 (SBT — Solomon Islands / MAGT — Magadan)":   39600,
    "UTC+12:00 (NZST — New Zealand / FJT — Fiji)":          43200,
    "UTC+13:00 (TOT — Tonga / NZDT — NZ Daylight)":         46800,
    "UTC+14:00 (LINT — Line Islands)":                      50400,
}

# Pools that don't work with ESP32 miners — warn if manually entered
EXCLUDED_POOLS: dict[str, str] = {
    "btcplebpool.com":        "DNS does not resolve — appears defunct",
    "stratum.braiins.com":    "Braiins Pool is ASIC-targeted — difficulty too high for ESP32",
    "stratum.slushpool.com":  "Braiins/Slush Pool is ASIC-targeted — difficulty too high for ESP32",
    "lotterypool.io":         "No DNS record — confirmed non-functional",
    "pool.stompi.de":         "Stratum connection refused — confirmed non-functional",
    "pool.pyblock.xyz":       "Stratum connection refused — confirmed non-functional",
}

_MUTED  = ("gray50", "gray55")

# ── Section help text ─────────────────────────────────────────────────────────

_HELP_FIRMWARE = (
    "Single merged file (default)\n\n"
    "One .bin file containing the complete firmware, flashed at offset 0x0. "
    "Most miners use this format — BitsyMiner, SparkMiner, and NerdMiner v2 "
    "all distribute a single merged binary.\n\n"
    "Separate partition files\n\n"
    "Some firmware ships as multiple files, each flashed at a specific address. "
    "NMMiner uses this format and requires 4 files:\n"
    "  •  Bootloader         →  0x1000\n"
    "  •  Partition table    →  0x8000\n"
    "  •  Firmware           →  0x10000\n"
    "  •  LittleFS image     →  0x39C000\n\n"
    "Check the firmware's release notes for the correct offsets for your version."
)

_HELP_WIFI = (
    "Entering your credentials here pre-loads them directly onto the chip at flash time.\n\n"
    "When 'Pre-flash config' is checked in the Pool Settings section below, the board will "
    "connect to your WiFi network and start mining on first boot — no captive portal or "
    "mobile app setup required.\n\n"
    "Use the Clear Data button to remove saved credentials from the local history file."
)

_HELP_POOL = (
    "Firmware Family\n\n"
    "Select the firmware type that matches the miner binary you are flashing. "
    "This controls which config format is written to the chip. Choosing the wrong "
    "family will cause the board to fall back to its setup portal instead of mining.\n\n"
    "Pools\n\n"
    "The pools in the dropdown are compatible with ESP32-class miners (~25–40 kH/s). "
    "Pools designed for ASIC miners set difficulty too high for the ESP32 to ever "
    "submit a share — those are flagged with a warning if entered manually.\n\n"
    "Pre-flash config\n\n"
    "Check this box to write your WiFi, wallet, and pool settings to the chip at flash time. "
    "The board boots straight into mining with no portal needed. Leave unchecked if you "
    "prefer to configure the miner through its own web portal after flashing."
)
_GREEN  = ("green3",     "#4CAF50")
_ORANGE = ("darkorange", "#FFA726")
_RED    = ("red3",       "#EF5350")
_BLUE   = ("dodgerblue3","#29B6F6")


# ── App ────────────────────────────────────────────────────────────────────────

class ESPFlasherApp(ctk.CTk, TkinterDnD.DnDWrapper):

    def __init__(self):
        super().__init__()
        self.TkdndVersion = TkinterDnD._require(self)

        self.title("ESP32 CYD Flasher")
        self.geometry("720x580")
        self.minsize(600, 480)
        self.after(0, lambda: self.state("zoomed"))

        try:
            import sys, os
            base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
            self.iconbitmap(os.path.join(base, "icon.ico"))
        except Exception:
            pass

        # Persistent state shared across steps
        self._flasher  = Flasher()
        self._detector = PortDetector(on_change=lambda: self.after(0, self._refresh_ports))
        self._flashing = False
        self._probing  = False
        self._firmware_path: Optional[str] = None
        self._ports_info: Dict[str, dict] = {}
        self._detected_chip: Optional[ChipInfo] = None
        self._current_step = 1
        self._nvs_tmp: Optional[str] = None    # temp file for generated NVS/SPIFFS binary
        self._spiffs_tmp: Optional[str] = None
        self._nmminer_license_pending = False
        self._serial_win = None               # open serial monitor CTkToplevel, if any
        self._serial_win_mon: Optional[SerialMonitor] = None  # its monitor instance
        self._serial_log_buffer: list[str] = []   # rolling buffer for report sending

        # Shared variables (created before build so all steps can reference them)
        self._board_var  = ctk.StringVar(value=list(BOARDS)[0])
        self._port_var   = ctk.StringVar()
        self._fw_mode    = ctk.StringVar(value="single")
        self._erase_var  = ctk.BooleanVar(value=False)
        self._nvs_inject  = ctk.BooleanVar(value=False)
        self._fw_family   = ctk.StringVar(value="BitsyMiner")
        self._cfg_vars: Dict[str, ctk.StringVar] = {
            "wifi_ssid":  ctk.StringVar(),
            "wifi_pass":  ctk.StringVar(),
            "btc_wallet": ctk.StringVar(),
        }
        self._disp_vars: Dict[str, ctk.Variable] = {
            "screen_brt":  ctk.StringVar(value="Medium (50%)"),
            "inactiv_tmr": ctk.StringVar(value="Never"),
            "inactiv_brt": ctk.StringVar(value="Screen off"),
            "clock24":     ctk.BooleanVar(value=False),
            "utc_tz":      ctk.StringVar(value="UTC+0:00  (GMT / UTC)"),
        }
        self._pool_vars: Dict[str, ctk.StringVar] = {
            "pool_url":  ctk.StringVar(value=""),
            "pool_port": ctk.StringVar(value=""),
            "pool_pass": ctk.StringVar(value="x"),
        }
        self._nmminer_licence = ctk.StringVar()

        # Load saved history and pre-fill fields
        self._history: dict = _hist.load()
        self._prefill_from_history()

        self._build_ui()
        self._detector.start()
        self._refresh_ports()

    # ── Top-level layout ───────────────────────────────────────────────

    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=0)   # step bar
        self.grid_rowconfigure(1, weight=1)   # active step content
        self.grid_rowconfigure(2, weight=0)   # nav bar

        self._build_step_bar()
        self._build_step1()
        self._build_step2()
        self._build_step3()
        self._build_nav_bar()
        self._show_step(1)

    # ── Step indicator bar ─────────────────────────────────────────────

    def _build_step_bar(self):
        bar = ctk.CTkFrame(self, corner_radius=0, fg_color=("gray85", "gray15"), height=48)
        bar.grid(row=0, column=0, sticky="ew")
        bar.grid_propagate(False)

        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.place(relx=0.5, rely=0.5, anchor="center")

        self._step_labels = []
        for i, name in enumerate(["Step 1  Connect", "Step 2  Configure", "Step 3  Flash"]):
            if i > 0:
                ctk.CTkLabel(inner, text="  ──  ", text_color=_MUTED,
                             font=ctk.CTkFont(size=11)).pack(side="left")
            lbl = ctk.CTkLabel(inner, text=name,
                               font=ctk.CTkFont(size=12, weight="bold"),
                               text_color=_MUTED)
            lbl.pack(side="left")
            self._step_labels.append(lbl)

    def _update_step_bar(self, current: int):
        colors = [_MUTED] * 3
        for i in range(current - 1):
            colors[i] = _GREEN
        colors[current - 1] = _BLUE
        for lbl, color in zip(self._step_labels, colors):
            lbl.configure(text_color=color)

    # ── Nav bar ────────────────────────────────────────────────────────

    def _build_nav_bar(self):
        nav = ctk.CTkFrame(self, corner_radius=0, fg_color=("gray85", "gray15"), height=58)
        nav.grid(row=2, column=0, sticky="ew")
        nav.grid_columnconfigure(1, weight=1)
        nav.grid_propagate(False)

        self._back_btn = ctk.CTkButton(
            nav, text="← Back", width=120, height=36,
            fg_color="transparent", border_width=1,
            command=self._go_back,
        )
        self._back_btn.grid(row=0, column=0, padx=(16, 0), pady=11)

        self._step_counter = ctk.CTkLabel(
            nav, text="Step 1 of 3", text_color=_MUTED, font=ctk.CTkFont(size=12))
        self._step_counter.grid(row=0, column=1)

        self._continue_btn = ctk.CTkButton(
            nav, text="Continue →", width=130, height=36,
            command=self._go_next,
        )
        self._continue_btn.grid(row=0, column=2, padx=(0, 16), pady=11)

    # ── Step 1 — Connect ───────────────────────────────────────────────

    def _build_step1(self):
        self._step1_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._step1_frame.grid(row=1, column=0, sticky="nsew")
        self._step1_frame.grid_columnconfigure(0, weight=1)

        card = ctk.CTkFrame(self._step1_frame, corner_radius=12)
        card.grid(row=0, column=0, padx=50, pady=30, sticky="ew")
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(card, text="Connect Your ESP32",
                     font=ctk.CTkFont(size=17, weight="bold")).grid(
            row=0, column=0, columnspan=3, padx=22, pady=(20, 16), sticky="w")

        # Board
        ctk.CTkLabel(card, text="Board:", font=ctk.CTkFont(weight="bold"), anchor="w").grid(
            row=1, column=0, padx=(22, 10), pady=9, sticky="w")
        self._board_menu = ctk.CTkOptionMenu(
            card, variable=self._board_var, values=list(BOARDS), width=290,
            command=self._on_board_change,
        )
        self._board_menu.grid(row=1, column=1, columnspan=2, padx=(0, 22), pady=9, sticky="w")

        # Port
        ctk.CTkLabel(card, text="Port:", font=ctk.CTkFont(weight="bold"), anchor="w").grid(
            row=2, column=0, padx=(22, 10), pady=9, sticky="w")
        self._port_menu = ctk.CTkOptionMenu(
            card, variable=self._port_var, values=["Scanning…"], width=240,
        )
        self._port_menu.grid(row=2, column=1, padx=(0, 6), pady=9, sticky="ew")
        ctk.CTkButton(card, text="↺", width=36, command=self._refresh_ports).grid(
            row=2, column=2, padx=(0, 22), pady=9)

        # Detect chip button
        self._detect_btn = ctk.CTkButton(
            card, text="  Detect Chip  ", height=34, command=self._start_detect,
        )
        self._detect_btn.grid(row=3, column=1, columnspan=2, padx=(0, 22), pady=(2, 10), sticky="w")

        # Status line
        self._status_lbl = ctk.CTkLabel(
            card, text="● Scanning for devices…",
            text_color=_MUTED, font=ctk.CTkFont(size=12),
        )
        self._status_lbl.grid(row=4, column=0, columnspan=3, padx=22, pady=(4, 8), sticky="w")

        # Tip
        ctk.CTkLabel(
            card,
            text="Tip:  If Detect Chip fails, hold BOOT on the board, tap EN/RST, "
                 "release BOOT, then click Detect Chip again.",
            font=ctk.CTkFont(size=11), text_color=_MUTED,
            wraplength=500, justify="left",
        ).grid(row=5, column=0, columnspan=3, padx=22, pady=(0, 20), sticky="w")

    # ── Step 2 — Configure ─────────────────────────────────────────────

    def _build_step2(self):
        self._step2_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._step2_frame.grid(row=1, column=0, sticky="nsew")
        self._step2_frame.grid_columnconfigure(0, weight=1)
        self._step2_frame.grid_rowconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(self._step2_frame, fg_color="transparent", corner_radius=0)
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        # ── Firmware card ──
        fw = ctk.CTkFrame(scroll, corner_radius=12)
        fw.grid(row=0, column=0, padx=30, pady=(18, 8), sticky="ew")
        fw.grid_columnconfigure(0, weight=1)

        _fw_hdr = ctk.CTkFrame(fw, fg_color="transparent")
        _fw_hdr.grid(row=0, column=0, padx=20, pady=(16, 6), sticky="w")
        ctk.CTkLabel(_fw_hdr, text="Firmware File",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        self._help_btn(_fw_hdr, "Firmware File", _HELP_FIRMWARE).pack(side="left", padx=(10, 0))

        mode_row = ctk.CTkFrame(fw, fg_color="transparent")
        mode_row.grid(row=1, column=0, padx=20, pady=(0, 8), sticky="w")
        ctk.CTkRadioButton(
            mode_row, text="Single merged .bin",
            variable=self._fw_mode, value="single",
            command=self._on_fw_mode_change,
        ).pack(side="left", padx=(0, 24))
        ctk.CTkRadioButton(
            mode_row, text="Separate partition files",
            variable=self._fw_mode, value="parts",
            command=self._on_fw_mode_change,
        ).pack(side="left")

        # Single-file container (drop zone + info)
        self._single_container = ctk.CTkFrame(fw, fg_color="transparent")
        self._single_container.grid(row=2, column=0, padx=20, pady=(0, 8), sticky="ew")
        self._single_container.grid_columnconfigure(0, weight=1)
        self._build_drop_zone(self._single_container)

        # Separate partitions container (hidden initially)
        self._parts_outer = ctk.CTkFrame(fw, fg_color="transparent")
        self._parts_outer.grid(row=2, column=0, padx=20, pady=(0, 8), sticky="ew")
        self._parts_outer.grid_columnconfigure(1, weight=1)
        self._parts_outer.grid_remove()
        self._build_parts_rows(self._parts_outer)


        # ── Config card ──
        cfg = ctk.CTkFrame(scroll, corner_radius=12)
        cfg.grid(row=1, column=0, padx=30, pady=(0, 8), sticky="ew")
        cfg.grid_columnconfigure(1, weight=1)

        _cfg_hdr = ctk.CTkFrame(cfg, fg_color="transparent")
        _cfg_hdr.grid(row=0, column=0, columnspan=3, padx=20, pady=(16, 8), sticky="w")
        ctk.CTkLabel(_cfg_hdr, text="WiFi & Wallet",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        self._help_btn(_cfg_hdr, "WiFi & Wallet", _HELP_WIFI).pack(side="left", padx=(10, 0))

        # WiFi SSID — combobox with history
        ctk.CTkLabel(cfg, text="WiFi SSID:", width=130, anchor="w").grid(
            row=1, column=0, padx=(20, 8), pady=6, sticky="w")
        ctk.CTkComboBox(
            cfg, variable=self._cfg_vars["wifi_ssid"],
            values=_hist.get_list(self._history, "wifi_ssid") or [""],
            width=300,
        ).grid(row=1, column=1, columnspan=2, padx=(0, 20), pady=6, sticky="ew")

        # WiFi Password — masked entry (auto-filled from history; no dropdown for security)
        ctk.CTkLabel(cfg, text="WiFi Password:", width=130, anchor="w").grid(
            row=2, column=0, padx=(20, 8), pady=6, sticky="w")
        self._pass_entry = ctk.CTkEntry(
            cfg, textvariable=self._cfg_vars["wifi_pass"],
            show="•", placeholder_text="Enter WiFi Password…",
        )
        self._pass_entry.grid(row=2, column=1, padx=(0, 6), pady=6, sticky="ew")
        _sv = ctk.BooleanVar(value=False)
        def _toggle_pass(e=self._pass_entry, v=_sv):
            v.set(not v.get())
            e.configure(show="" if v.get() else "•")
        ctk.CTkButton(cfg, text="👁", width=36, command=_toggle_pass).grid(
            row=2, column=2, padx=(0, 20), pady=6)

        # Wallet — label updates to "eCash Wallet:" when an XEC pool is selected
        self._wallet_label = ctk.CTkLabel(cfg, text="Bitcoin Wallet:", width=130, anchor="w")
        self._wallet_label.grid(row=3, column=0, padx=(20, 8), pady=6, sticky="w")
        ctk.CTkComboBox(
            cfg, variable=self._cfg_vars["btc_wallet"],
            values=_hist.get_list(self._history, "btc_wallet") or [""],
            width=300,
        ).grid(row=3, column=1, columnspan=2, padx=(0, 20), pady=6, sticky="ew")

        ctk.CTkButton(
            cfg, text="Clear Saved Data", width=140, height=28,
            font=ctk.CTkFont(size=12),
            fg_color=("gray70", "gray25"), hover_color=("gray60", "gray35"),
            text_color=("gray10", "gray90"),
            command=self._clear_wifi_wallet,
        ).grid(row=4, column=0, columnspan=3, padx=20, pady=(4, 16), sticky="w")

        # ── Pool / Pre-config card ──
        pool = ctk.CTkFrame(scroll, corner_radius=12)
        pool.grid(row=2, column=0, padx=30, pady=(0, 8), sticky="ew")
        pool.grid_columnconfigure(1, weight=1)

        _pool_hdr = ctk.CTkFrame(pool, fg_color="transparent")
        _pool_hdr.grid(row=0, column=0, columnspan=3, padx=20, pady=(16, 4), sticky="w")
        ctk.CTkLabel(_pool_hdr, text="Pool Settings  &  Pre-configuration",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        self._help_btn(_pool_hdr, "Pool Settings & Pre-configuration", _HELP_POOL).pack(side="left", padx=(10, 0))

        # Firmware family row
        fam_row = ctk.CTkFrame(pool, fg_color="transparent")
        fam_row.grid(row=1, column=0, columnspan=3, padx=20, pady=(2, 4), sticky="w")
        ctk.CTkLabel(fam_row, text="Firmware:", width=90, anchor="w").pack(side="left")
        _FAMILIES = ["BitsyMiner", "SparkMiner", "NerdMiner v2", "NMMiner", "Other"]
        ctk.CTkOptionMenu(
            fam_row, variable=self._fw_family, values=_FAMILIES, width=200,
            command=self._on_fw_family_change,
        ).pack(side="left", padx=(6, 0))

        # Pre-config checkbox
        ctk.CTkCheckBox(
            pool,
            text="Pre-flash config  —  skip captive portal on first boot",
            variable=self._nvs_inject,
            font=ctk.CTkFont(size=12),
            command=self._on_fw_family_change,
        ).grid(row=2, column=0, columnspan=3, padx=20, pady=(4, 8), sticky="w")

        # Pool URL — combobox: known pools first, then history
        pool_url_values = list(KNOWN_POOLS.keys()) + [
            h for h in _hist.get_list(self._history, "pool_url")
            if h not in KNOWN_POOLS and h not in EXCLUDED_POOLS
        ]
        ctk.CTkLabel(pool, text="Pool URL:", width=100, anchor="w").grid(
            row=3, column=0, padx=(20, 8), pady=4, sticky="w")
        self._pool_combo = ctk.CTkComboBox(
            pool, variable=self._pool_vars["pool_url"],
            values=pool_url_values or [""],
            command=self._on_pool_selected,
        )
        self._pool_combo.grid(row=3, column=1, columnspan=2, padx=(0, 20), pady=4, sticky="ew")

        # Pool warning — hidden unless an excluded/incompatible pool is entered
        self._pool_warn = ctk.CTkLabel(
            pool, text="", font=ctk.CTkFont(size=11),
            text_color=("#cc2200", "#ff5555"), justify="left", wraplength=500, anchor="w",
        )
        self._pool_warn.grid(row=4, column=0, columnspan=3, padx=(20, 20), pady=(0, 2), sticky="w")
        self._pool_warn.grid_remove()
        self._pool_vars["pool_url"].trace_add(
            "write", lambda *_: self._update_pool_warning()
        )

        # Pool Port — plain entry (auto-filled when a known pool is chosen)
        ctk.CTkLabel(pool, text="Pool Port:", width=100, anchor="w").grid(
            row=5, column=0, padx=(20, 8), pady=4, sticky="w")
        ctk.CTkEntry(
            pool, textvariable=self._pool_vars["pool_port"],
            placeholder_text="e.g. 3337",
        ).grid(row=5, column=1, columnspan=2, padx=(0, 20), pady=4, sticky="ew")

        # Pool Pass
        ctk.CTkLabel(pool, text="Pool Pass:", width=100, anchor="w").grid(
            row=6, column=0, padx=(20, 8), pady=4, sticky="w")
        ctk.CTkEntry(
            pool, textvariable=self._pool_vars["pool_pass"],
            placeholder_text="usually  x",
        ).grid(row=6, column=1, columnspan=2, padx=(0, 20), pady=4, sticky="ew")

        # NMMiner License row (hidden unless NMMiner family is selected)
        self._licence_lbl = ctk.CTkLabel(pool, text="NMMiner License:", width=100, anchor="w")
        self._licence_entry = ctk.CTkEntry(
            pool, textvariable=self._nmminer_licence,
            placeholder_text="128-character hex key from nmminer.com",
        )
        self._licence_lbl.grid(row=7, column=0, padx=(20, 8), pady=4, sticky="w")
        self._licence_entry.grid(row=7, column=1, columnspan=2, padx=(0, 20), pady=4, sticky="ew")
        self._licence_lbl.grid_remove()
        self._licence_entry.grid_remove()

        # Dynamic note label (updated by _on_fw_family_change)
        self._preconfig_note = ctk.CTkLabel(
            pool, text="", font=ctk.CTkFont(size=11),
            text_color=_MUTED, justify="left", wraplength=560,
        )
        self._preconfig_note.grid(row=8, column=0, columnspan=3,
                                  padx=20, pady=(4, 14), sticky="w")
        self._on_fw_family_change()   # set initial note text

        # ── Display & Clock card (BitsyMiner only) ──
        disp = ctk.CTkFrame(scroll, corner_radius=12)
        disp.grid(row=3, column=0, padx=30, pady=(0, 8), sticky="ew")
        disp.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(disp, text="Display & Clock",
                     font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, columnspan=2, padx=20, pady=(16, 2), sticky="w")
        ctk.CTkLabel(disp, text="BitsyMiner only — settings baked into NVS at flash time",
                     font=ctk.CTkFont(size=11), text_color=_MUTED).grid(
            row=1, column=0, columnspan=2, padx=20, pady=(0, 10), sticky="w")

        # Screen brightness
        ctk.CTkLabel(disp, text="Brightness:", width=130, anchor="w").grid(
            row=2, column=0, padx=(20, 8), pady=6, sticky="w")
        ctk.CTkOptionMenu(
            disp, variable=self._disp_vars["screen_brt"],
            values=_BRIGHTNESS_OPTIONS,
        ).grid(row=2, column=1, padx=(0, 20), pady=6, sticky="ew")

        # Inactivity timer
        ctk.CTkLabel(disp, text="Dim after:", width=130, anchor="w").grid(
            row=3, column=0, padx=(20, 8), pady=6, sticky="w")
        ctk.CTkOptionMenu(
            disp, variable=self._disp_vars["inactiv_tmr"],
            values=_DIM_TIMER_OPTIONS,
        ).grid(row=3, column=1, padx=(0, 20), pady=6, sticky="ew")

        # Inactivity brightness
        ctk.CTkLabel(disp, text="Dim to:", width=130, anchor="w").grid(
            row=4, column=0, padx=(20, 8), pady=6, sticky="w")
        ctk.CTkOptionMenu(
            disp, variable=self._disp_vars["inactiv_brt"],
            values=_DIM_TO_OPTIONS,
        ).grid(row=4, column=1, padx=(0, 20), pady=6, sticky="ew")

        # 24-hour clock
        ctk.CTkCheckBox(
            disp, text="24-hour clock",
            variable=self._disp_vars["clock24"],
            font=ctk.CTkFont(size=12),
        ).grid(row=5, column=0, columnspan=2, padx=20, pady=8, sticky="w")

        # Timezone / UTC offset
        ctk.CTkLabel(disp, text="Timezone:", width=130, anchor="w").grid(
            row=6, column=0, padx=(20, 8), pady=6, sticky="w")
        ctk.CTkOptionMenu(
            disp, variable=self._disp_vars["utc_tz"],
            values=_TIMEZONE_OPTIONS,
            dynamic_resizing=False,
        ).grid(row=6, column=1, padx=(0, 20), pady=(6, 16), sticky="ew")

        # ── Options card ──
        opt = ctk.CTkFrame(scroll, corner_radius=12)
        opt.grid(row=4, column=0, padx=30, pady=(0, 20), sticky="ew")

        ctk.CTkLabel(opt, text="Options",
                     font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, padx=20, pady=(16, 8), sticky="w")
        ctk.CTkCheckBox(
            opt,
            text="Erase all flash before writing  (use for a completely clean install)",
            variable=self._erase_var, font=ctk.CTkFont(size=12),
        ).grid(row=1, column=0, padx=20, pady=(0, 16), sticky="w")

    def _build_drop_zone(self, parent):
        self._drop_zone = ctk.CTkFrame(
            parent, corner_radius=8, border_width=2,
            border_color=("gray65", "gray40"), fg_color=("gray93", "gray17"), height=74,
        )
        self._drop_zone.grid(row=0, column=0, sticky="ew")
        self._drop_zone.grid_propagate(False)

        self._drop_label = ctk.CTkLabel(
            self._drop_zone, text="Drop .bin firmware file here",
            font=ctk.CTkFont(size=13), text_color=_MUTED,
        )
        self._drop_label.place(relx=0.5, rely=0.34, anchor="center")

        self._browse_btn = ctk.CTkButton(
            self._drop_zone, text="Browse…", width=92, height=26,
            command=self._browse_firmware,
        )
        self._browse_btn.place(relx=0.5, rely=0.73, anchor="center")

        self._drop_zone.drop_target_register(DND_FILES)
        self._drop_zone.dnd_bind("<<Drop>>", self._on_drop)
        self._drop_zone.dnd_bind("<<DragEnter>>",
            lambda e: self._drop_zone.configure(border_color=("dodgerblue2", "#1F6AA5")))
        self._drop_zone.dnd_bind("<<DragLeave>>",
            lambda e: self._drop_zone.configure(border_color=("gray65", "gray40")))

        info_row = ctk.CTkFrame(parent, fg_color="transparent")
        info_row.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        info_row.grid_columnconfigure(0, weight=1)

        self._file_lbl = ctk.CTkLabel(
            info_row, text="No file selected",
            text_color=_MUTED, font=ctk.CTkFont(size=12), anchor="w",
        )
        self._file_lbl.grid(row=0, column=0, sticky="w")

        self._clear_btn = ctk.CTkButton(
            info_row, text="✕ Clear", width=70, height=22,
            fg_color="transparent", border_width=1, text_color=_MUTED,
            command=self._clear_firmware,
        )
        self._clear_btn.grid(row=0, column=1, sticky="e")
        self._clear_btn.grid_remove()

    def _build_parts_rows(self, parent):
        self._part_vars: Dict[str, tuple] = {}
        board    = BOARDS[self._board_var.get()]
        defaults = board["part_offsets"]
        rows = {
            "Bootloader":                    defaults.get("Bootloader", 0x1000),
            "Partition Table":               defaults.get("Partition Table", 0x8000),
            "App":                           defaults.get("App", 0x10000),
            "SPIFFS / LittleFS (optional)":  0x39C000,
        }
        for i, (lbl, off) in enumerate(rows.items()):
            ctk.CTkLabel(parent, text=f"{lbl}:", width=170, anchor="w").grid(
                row=i, column=0, pady=4, sticky="w")
            pv = ctk.StringVar()
            ctk.CTkEntry(parent, textvariable=pv, placeholder_text="No file").grid(
                row=i, column=1, padx=6, pady=4, sticky="ew")
            ov = ctk.StringVar(value=hex(off))
            ctk.CTkEntry(parent, textvariable=ov, width=76).grid(
                row=i, column=2, padx=(0, 6), pady=4)
            ctk.CTkButton(parent, text="…", width=30,
                          command=lambda v=pv: self._browse_partition(v)).grid(
                row=i, column=3, pady=4)
            self._part_vars[lbl] = (pv, ov)
        ctk.CTkLabel(parent, text="Offset", font=ctk.CTkFont(size=10),
                     text_color=_MUTED).grid(
            row=len(rows), column=2, sticky="n", pady=(2, 0))

    # ── Step 3 — Flash ─────────────────────────────────────────────────

    def _build_step3(self):
        self._step3_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._step3_frame.grid(row=1, column=0, sticky="nsew")
        self._step3_frame.grid_columnconfigure(0, weight=1)
        self._step3_frame.grid_rowconfigure(1, weight=1)   # log expands

        # ── Summary + flash card ──
        top = ctk.CTkFrame(self._step3_frame, corner_radius=12)
        top.grid(row=0, column=0, padx=30, pady=(14, 6), sticky="ew")
        # 5 columns: lbl_L | val_L | gap | lbl_R | val_R
        top.grid_columnconfigure(1, weight=1)
        top.grid_columnconfigure(2, minsize=18)
        top.grid_columnconfigure(4, weight=1)

        ctk.CTkLabel(top, text="Review & Flash",
                     font=ctk.CTkFont(size=16, weight="bold")).grid(
            row=0, column=0, columnspan=5, padx=20, pady=(14, 8), sticky="w")

        self._summ: Dict[str, ctk.CTkLabel] = {}

        # Two columns of 4 items each
        left_rows  = [("board", "Board:"), ("port", "Port:"),
                      ("fw",    "Firmware:"), ("wifi", "WiFi SSID:")]
        right_rows = [("wallet", "Wallet:"), ("pool", "Pool:"),
                      ("nvs",   "NVS inject:"), ("erase", "Erase first:")]

        for i, (key, label) in enumerate(left_rows):
            ctk.CTkLabel(top, text=label, width=88, anchor="w",
                         font=ctk.CTkFont(weight="bold")).grid(
                row=i + 1, column=0, padx=(20, 4), pady=2, sticky="w")
            val = ctk.CTkLabel(top, text="—", anchor="w", wraplength=260)
            val.grid(row=i + 1, column=1, padx=(0, 4), pady=2, sticky="ew")
            self._summ[key] = val

        for i, (key, label) in enumerate(right_rows):
            ctk.CTkLabel(top, text=label, width=88, anchor="w",
                         font=ctk.CTkFont(weight="bold")).grid(
                row=i + 1, column=3, padx=(4, 4), pady=2, sticky="w")
            val = ctk.CTkLabel(top, text="—", anchor="w", wraplength=260)
            val.grid(row=i + 1, column=4, padx=(0, 20), pady=2, sticky="ew")
            self._summ[key] = val

        N = max(len(left_rows), len(right_rows))  # = 4

        # Divider
        ctk.CTkFrame(top, height=1, fg_color=("gray70", "gray35")).grid(
            row=N + 1, column=0, columnspan=5, padx=20, pady=(8, 2), sticky="ew")

        # Flash button
        self._flash_btn = ctk.CTkButton(
            top, text="⚡   FLASH FIRMWARE",
            font=ctk.CTkFont(size=15, weight="bold"),
            height=44, corner_radius=8, command=self._on_flash_click,
        )
        self._flash_btn.grid(row=N + 2, column=0, columnspan=5,
                             padx=20, pady=(8, 4), sticky="ew")

        self._progress = ctk.CTkProgressBar(top, height=6)
        self._progress.grid(row=N + 3, column=0, columnspan=5,
                            padx=20, pady=(0, 12), sticky="ew")
        self._progress.set(0)
        self._progress.grid_remove()

        # ── Log ──
        log_card = ctk.CTkFrame(self._step3_frame, corner_radius=12)
        log_card.grid(row=1, column=0, padx=30, pady=(0, 14), sticky="nsew")
        log_card.grid_columnconfigure(0, weight=1)
        log_card.grid_rowconfigure(1, weight=1)

        log_hdr = ctk.CTkFrame(log_card, fg_color="transparent")
        log_hdr.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 2))
        log_hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(log_hdr, text="Output Log",
                     font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=0, sticky="w")
        ctk.CTkButton(log_hdr, text="Serial Monitor", width=110, height=22,
                      fg_color="transparent", border_width=1,
                      command=self._open_serial_window).grid(row=0, column=1, sticky="e", padx=(0, 6))
        ctk.CTkButton(log_hdr, text="Send Report", width=90, height=22,
                      fg_color="transparent", border_width=1,
                      command=self._send_report_dialog).grid(row=0, column=2, sticky="e", padx=(0, 6))
        ctk.CTkButton(log_hdr, text="Clear", width=50, height=22,
                      fg_color="transparent", border_width=1,
                      command=self._clear_log).grid(row=0, column=3, sticky="e")

        self._log_box = ctk.CTkTextbox(log_card, font=ctk.CTkFont(family="Consolas", size=11))
        self._log_box.grid(row=1, column=0, sticky="nsew", padx=12, pady=(2, 10))
        self._log_box.configure(state="disabled")

    # ── Serial Monitor window ──────────────────────────────────────────

    def _open_serial_window(self, auto_connect: bool = False):
        """Open (or focus) the serial monitor window."""
        if self._serial_win and self._serial_win.winfo_exists():
            self._serial_win.focus()
            return

        win = ctk.CTkToplevel(self)
        win.title("Serial Monitor")
        win.geometry("760x520")
        win.minsize(560, 380)
        win.grid_columnconfigure(0, weight=1)
        win.grid_rowconfigure(2, weight=1)
        self._serial_win = win

        mon = SerialMonitor()
        self._serial_win_mon = mon

        # ── Toolbar ──────────────────────────────────────────────────
        bar = ctk.CTkFrame(win, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 0))
        bar.grid_columnconfigure(3, weight=1)

        port_var = ctk.StringVar(value=self._selected_port() or "")
        baud_var = ctk.StringVar(value="115200")
        status_var = ctk.StringVar(value="Disconnected")
        conn_btn_text = ctk.StringVar(value="Connect")

        ctk.CTkLabel(bar, text="Port:", width=36, anchor="w").grid(row=0, column=0, padx=(0, 4))
        port_vals = [info["device"] for info in self._ports_info.values()] or [""]
        port_cb = ctk.CTkComboBox(bar, variable=port_var, values=port_vals, width=110)
        port_cb.grid(row=0, column=1, padx=(0, 8))

        ctk.CTkLabel(bar, text="Baud:", width=38, anchor="w").grid(row=0, column=2, padx=(0, 4))
        ctk.CTkComboBox(bar, variable=baud_var, width=90,
                        values=["9600", "74880", "115200", "230400", "921600"]).grid(
            row=0, column=3, padx=(0, 8), sticky="w")

        status_lbl = ctk.CTkLabel(bar, textvariable=status_var, text_color=_MUTED,
                                  font=ctk.CTkFont(size=11))
        status_lbl.grid(row=0, column=4, padx=8, sticky="w")

        def _do_connect():
            p = port_var.get().strip()
            b = int(baud_var.get())
            mon.connect(p, b)

        def _do_disconnect():
            mon.disconnect()

        def _refresh_ports_cb():
            self._refresh_ports()
            vals = [info["device"] for info in self._ports_info.values()] or [""]
            port_cb.configure(values=vals)
            if not port_var.get() and vals:
                port_var.set(vals[0])

        btn_frame = ctk.CTkFrame(bar, fg_color="transparent")
        btn_frame.grid(row=0, column=5, padx=(4, 0))
        ctk.CTkButton(btn_frame, text="↺", width=30, height=26,
                      command=_refresh_ports_cb).pack(side="left", padx=(0, 4))
        conn_btn = ctk.CTkButton(btn_frame, textvariable=conn_btn_text, width=90, height=26,
                                 command=_do_connect)
        conn_btn.pack(side="left", padx=(0, 4))
        ctk.CTkButton(btn_frame, text="Disconnect", width=80, height=26,
                      fg_color="transparent", border_width=1,
                      command=_do_disconnect).pack(side="left")

        # ── Output text area ──────────────────────────────────────────
        out = ctk.CTkTextbox(win, font=ctk.CTkFont(family="Consolas", size=11),
                             fg_color=("gray95", "gray10"))
        out.grid(row=2, column=0, sticky="nsew", padx=12, pady=4)
        out.configure(state="disabled")

        def _append(text: str):
            if not win.winfo_exists():
                return
            out.configure(state="normal")
            out.insert("end", text)
            out.see("end")
            out.configure(state="disabled")

        # ── Device code + licence panels ─────────────────────────────
        code_frame = ctk.CTkFrame(win, corner_radius=8,
                                  fg_color=("gray88", "gray18"), border_width=1,
                                  border_color=(_ORANGE[0], _ORANGE[1]))
        # grid at row 3 later when shown

        code_var = ctk.StringVar(value="")
        lic_var  = ctk.StringVar(value=self._nmminer_licence.get())

        def _build_code_frame():
            cf = code_frame
            cf.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(cf, text="NMMiner Device Code Detected",
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color=_ORANGE).grid(
                row=0, column=0, columnspan=3, padx=14, pady=(10, 4), sticky="w")
            ctk.CTkLabel(cf, text="Device Code:", width=100, anchor="w").grid(
                row=1, column=0, padx=(14, 6), pady=4, sticky="w")
            ctk.CTkEntry(cf, textvariable=code_var, state="readonly").grid(
                row=1, column=1, padx=(0, 6), pady=4, sticky="ew")
            ctk.CTkButton(cf, text="Copy", width=58, height=26,
                          command=lambda: (win.clipboard_clear(),
                                          win.clipboard_append(code_var.get()))).grid(
                row=1, column=2, padx=(0, 14), pady=4)

            ctk.CTkLabel(cf,
                         text="Go to nmminer.com, enter the device code above, and get your 128-char license key.",
                         font=ctk.CTkFont(size=11), text_color=_MUTED, wraplength=600).grid(
                row=2, column=0, columnspan=3, padx=14, pady=(0, 6), sticky="w")

            ctk.CTkLabel(cf, text="License Key:", width=100, anchor="w").grid(
                row=3, column=0, padx=(14, 6), pady=4, sticky="w")
            ctk.CTkEntry(cf, textvariable=lic_var,
                         placeholder_text="Paste 128-char hex license here").grid(
                row=3, column=1, padx=(0, 6), pady=4, sticky="ew")

            def _send_licence():
                lic = lic_var.get().strip()
                if len(lic) != 128 or not all(c in '0123456789abcdefABCDEF' for c in lic):
                    status_var.set("⚠  License must be 128 hex characters")
                    return
                # 1. Send via serial so the device accepts + saves it immediately
                mon.send(lic)
                self._nmminer_licence.set(lic)
                self._save_history()
                _append("\n[Flasher] License sent via serial.\n")

                # 2. Disconnect serial so esptool can open the port
                mon.disconnect()

                # 3. Flash the full NVS (WiFi + pool + wallet + licence) at 0x9000
                #    so all settings survive future power cycles and reflashes.
                port = self._selected_port()
                if not port:
                    status_var.set("License sent. No port selected — skipping NVS flash.")
                    return

                ssid      = self._cfg_vars["wifi_ssid"].get().strip()
                wifi_pass = self._cfg_vars["wifi_pass"].get().strip()
                pool_url  = self._pool_vars["pool_url"].get().strip()
                pool_port = self._pool_port_int()
                wallet    = self._cfg_vars["btc_wallet"].get().strip()
                pool_pass = self._pool_vars["pool_pass"].get().strip() or "x"

                if not any([ssid, pool_url, wallet]):
                    status_var.set("License sent via serial. Fill WiFi/pool fields and reflash to complete config.")
                    _append("[Flasher] WiFi/pool not configured — fill Step 2 fields and reflash to inject NVS.\n")
                    return

                try:
                    nvs_path = _nmminer.write_nvs_temp_file(
                        wifi_ssid=ssid, wifi_pass=wifi_pass,
                        pool_url=pool_url, pool_port=pool_port,
                        wallet=wallet, pool_pass=pool_pass,
                        licence=lic,
                    )
                except Exception as e:
                    status_var.set(f"License sent. NVS build failed: {e}")
                    return

                board = BOARDS[self._board_var.get()]
                from core.flasher import FlashTask
                task = FlashTask(
                    chip=board["chip"], port=port, baud=board["baud"],
                    flash_mode=board["flash_mode"], flash_freq=board["flash_freq"],
                    flash_size=board["flash_size"],
                    files={_nvs.NVS_PART_OFFSET: nvs_path},
                    extra_files={}, erase_first=False,
                )
                status_var.set("Flashing NVS config (WiFi + pool + wallet + licence)…")
                _append("[Flasher] Flashing NVS partition with full config…\n")

                def _nvs_log(msg):
                    if win.winfo_exists():
                        win.after(0, _append, msg + "\n")

                def _nvs_done(ok, _msg):
                    import os
                    try:
                        os.remove(nvs_path)
                    except OSError:
                        pass
                    if win.winfo_exists():
                        if ok:
                            win.after(0, status_var.set, "✓  NVS flashed — board has full config. Reconnect serial to watch boot.")
                            win.after(0, _append, "[Flasher] NVS flash complete. Reconnect serial to watch boot.\n")
                        else:
                            win.after(0, status_var.set, "NVS flash failed — license was sent via serial at least.")
                        win.after(0, lambda: mon.connect(port, 115200))

                self._flasher.flash(task, log_cb=_nvs_log, progress_cb=lambda p: None,
                                    done_cb=_nvs_done)

            ctk.CTkButton(cf, text="⚡  Send License & Flash Config", height=32,
                          command=_send_licence).grid(
                row=4, column=0, columnspan=3, padx=14, pady=(4, 12), sticky="ew")

        _build_code_frame()

        def _on_device_code(code: str):
            if not win.winfo_exists():
                return
            code_var.set(code)
            code_frame.grid(row=3, column=0, padx=12, pady=(0, 4), sticky="ew")
            _append(f"\n[Flasher] Device code captured: {code}\n")

        # ── Input bar ─────────────────────────────────────────────────
        inp_bar = ctk.CTkFrame(win, fg_color="transparent")
        inp_bar.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 10))
        inp_bar.grid_columnconfigure(0, weight=1)

        inp_var = ctk.StringVar()
        inp_entry = ctk.CTkEntry(inp_bar, textvariable=inp_var,
                                 placeholder_text="Type and press Send or Enter")
        inp_entry.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        def _send_input(event=None):
            text = inp_var.get()
            if text:
                mon.send(text + "\r\n")
                _append(f"> {text}\n")
                inp_var.set("")

        inp_entry.bind("<Return>", _send_input)
        ctk.CTkButton(inp_bar, text="Send", width=60, height=28,
                      command=_send_input).grid(row=0, column=1)

        # ── Callbacks wired into monitor ──────────────────────────────
        def _on_line(text: str):
            self._serial_log_buffer.append(text)
            if win.winfo_exists():
                win.after(0, _append, text)

        def _on_code(code: str):
            if win.winfo_exists():
                win.after(0, _on_device_code, code)

        def _on_connect(port: str, baud: int):
            if win.winfo_exists():
                win.after(0, status_var.set, f"Connected — {port}  {baud} baud")
                win.after(0, status_lbl.configure, {"text_color": _GREEN})

        def _on_disconnect_cb():
            if win.winfo_exists():
                win.after(0, status_var.set, "Disconnected")
                win.after(0, status_lbl.configure, {"text_color": _MUTED})

        mon._on_line        = _on_line
        mon._on_device_code = _on_code
        mon._on_connect     = _on_connect
        mon._on_disconnect  = _on_disconnect_cb

        def _on_close():
            mon.disconnect()
            win.destroy()
            self._serial_win = None
            self._serial_win_mon = None

        win.protocol("WM_DELETE_WINDOW", _on_close)

        if auto_connect:
            port = self._selected_port()
            if port:
                win.after(500, lambda: mon.connect(port, 115200))

    # ── Step navigation ────────────────────────────────────────────────

    def _show_step(self, n: int):
        for frame in (self._step1_frame, self._step2_frame, self._step3_frame):
            frame.grid_remove()

        {1: self._step1_frame, 2: self._step2_frame, 3: self._step3_frame}[n].grid(
            row=1, column=0, sticky="nsew")

        self._current_step = n
        self._update_step_bar(n)
        self._step_counter.configure(text=f"Step {n} of 3")
        self._back_btn.configure(state="normal" if n > 1 else "disabled")

        if n == 3:
            self._continue_btn.grid_remove()
            self._update_summary()
            self._log("Summary ready. Press ⚡ FLASH FIRMWARE when you're set.")
        else:
            self._continue_btn.grid()

    def _go_next(self):
        if self._current_step == 1:
            if not self._selected_port():
                self._status_lbl.configure(
                    text="● Please connect a device and select its port before continuing.",
                    text_color=_RED)
                return
            self._show_step(2)
        elif self._current_step == 2:
            self._save_history()
            self._show_step(3)

    def _go_back(self):
        if self._current_step > 1:
            self._show_step(self._current_step - 1)

    def _update_summary(self):
        board  = self._board_var.get()
        port   = self._selected_port() or "—"
        chip   = f"  ({self._detected_chip.summary()})" if self._detected_chip else ""
        fw     = os.path.basename(self._firmware_path) if self._firmware_path else "⚠ No file selected"
        ssid   = self._cfg_vars["wifi_ssid"].get().strip() or "Not set"
        wallet = self._cfg_vars["btc_wallet"].get().strip() or "Not set"

        pool_url  = self._pool_vars["pool_url"].get().strip()
        pool_port = self._pool_vars["pool_port"].get().strip()
        pool_str  = f"{pool_url}:{pool_port}" if pool_url else "Not set"

        self._summ["board"].configure(text=board)
        self._summ["port"].configure(text=f"{port}{chip}")
        self._summ["fw"].configure(text=fw)
        self._summ["wifi"].configure(text=ssid)
        self._summ["wallet"].configure(text=wallet)
        self._summ["pool"].configure(text=pool_str)
        fam = self._fw_family.get()
        if self._nvs_inject.get():
            if fam == "NerdMiner v2":
                nvs_text = f"Yes — pool pre-baked into SPIFFS /config.json ({fam})"
            else:
                nvs_text = f"Yes — config pre-baked into NVS ({fam})"
        else:
            nvs_text = f"No — portal on first boot  [{fam}]"
        self._summ["nvs"].configure(text=nvs_text)
        self._summ["erase"].configure(
            text="Yes — full erase before writing" if self._erase_var.get() else "No")

    # ── Port handling ──────────────────────────────────────────────────

    def _refresh_ports(self):
        ports = get_ports()
        self._ports_info = {
            label: {"device": device, "vid": vid, "pid": pid}
            for device, label, _, vid, pid in ports
        }

        if not ports:
            self._port_menu.configure(values=["No ports found"])
            self._port_var.set("No ports found")
            self._status_lbl.configure(text="● No device detected", text_color=_MUTED)
            return

        labels = [lbl for _, lbl, *_ in ports]
        self._port_menu.configure(values=labels)

        esp = [(lbl, vid, pid) for _, lbl, is_esp, vid, pid in ports if is_esp]
        if esp:
            best, vid, pid = esp[0]
            self._port_var.set(best)
            device = self._ports_info[best]["device"]
            native = chip_from_vidpid(vid, pid)
            if native:
                board = self._pick_board(native, "")
                if board:
                    self._board_var.set(board)
                    self._on_board_change()
                self._status_lbl.configure(
                    text=f"● {device}  ({native.upper()} native USB)  — ready",
                    text_color=_GREEN)
            else:
                self._status_lbl.configure(
                    text=f"● {device}  detected  — click Detect Chip to identify",
                    text_color=_GREEN)
        else:
            self._port_var.set(labels[0])
            self._status_lbl.configure(
                text="● Device found but not identified as ESP32",
                text_color=_ORANGE)

    def _selected_port(self) -> Optional[str]:
        info = self._ports_info.get(self._port_var.get())
        return info["device"] if info else None

    # ── Chip detection ─────────────────────────────────────────────────

    def _start_detect(self):
        if self._probing:
            return
        port = self._selected_port()
        if not port:
            self._status_lbl.configure(text="● No port selected.", text_color=_RED)
            return

        self._probing = True
        self._detect_btn.configure(text="Detecting…", state="disabled")
        self._status_lbl.configure(
            text=f"● Probing {port}…  (board will briefly reset)", text_color=_BLUE)

        probe_chip(
            port=port,
            result_cb=lambda info: self.after(0, self._on_chip_detected, info, port),
            log_cb=lambda _: None,
        )

    def _on_chip_detected(self, info: Optional[ChipInfo], port: str):
        self._probing = False
        self._detect_btn.configure(text="  Detect Chip  ", state="normal")
        self._detected_chip = info

        if info is None:
            self._status_lbl.configure(
                text="● Could not read chip — hold BOOT, tap EN/RST, then try again",
                text_color=_RED)
            return

        board = self._pick_board(info.chip, info.flash_size)
        if board:
            self._board_var.set(board)
            self._on_board_change()

        self._status_lbl.configure(
            text=f"● {port}  —  {info.summary()}  ✓",
            text_color=_GREEN)

    def _pick_board(self, chip: str, flash_size: str) -> Optional[str]:
        return _CHIP_BOARD_MAP.get((chip, flash_size)) or _CHIP_BOARD_MAP.get((chip, ""))

    # ── Firmware file handling ─────────────────────────────────────────

    def _on_drop(self, event):
        self._drop_zone.configure(border_color=("gray65", "gray40"))
        path = event.data.strip().strip("{}")
        if path.lower().endswith(".bin"):
            self._set_firmware(path)

    def _browse_firmware(self):
        path = filedialog.askopenfilename(
            title="Select firmware binary",
            filetypes=[("Binary files", "*.bin"), ("All files", "*.*")],
        )
        if path:
            self._set_firmware(path)

    def _browse_partition(self, path_var):
        path = filedialog.askopenfilename(
            title="Select partition binary",
            filetypes=[("Binary files", "*.bin"), ("All files", "*.*")],
        )
        if path:
            path_var.set(path)

    def _set_firmware(self, path: str):
        self._firmware_path = path
        name    = os.path.basename(path)
        size_kb = os.path.getsize(path) / 1024
        self._drop_label.configure(text=f"✓  {name}", text_color=_GREEN)
        self._browse_btn.configure(text="Change…")
        self._clear_btn.grid()

        # Chip compatibility check
        fw_chip, compat_msg = image_chip(path)
        board_chip = BOARDS[self._board_var.get()]["chip"]
        if fw_chip and fw_chip != board_chip:
            compat_color = _RED
            compat_text  = f"⚠  Chip mismatch: image targets {fw_chip.upper()} but board is {board_chip.upper()}"
        elif fw_chip == board_chip:
            compat_color = _GREEN
            compat_text  = f"✓  {compat_msg}"
        else:
            compat_color = _MUTED
            compat_text  = compat_msg

        self._file_lbl.configure(
            text=f"{name}   ({size_kb:.1f} KB)  —  {compat_text}",
            text_color=compat_color)


    def _clear_firmware(self):
        self._firmware_path = None
        self._drop_label.configure(text="Drop .bin firmware file here", text_color=_MUTED)
        self._browse_btn.configure(text="Browse…")
        self._file_lbl.configure(text="No file selected", text_color=_MUTED)
        self._clear_btn.grid_remove()

    # ── History helpers ────────────────────────────────────────────────

    def _prefill_from_history(self):
        """Load the most-recent saved value into each field (called before build)."""
        def _first(key):
            items = _hist.get_list(self._history, key)
            return items[0] if items else ""

        self._cfg_vars["wifi_ssid"].set(_first("wifi_ssid"))
        self._cfg_vars["wifi_pass"].set(_first("wifi_pass"))
        self._cfg_vars["btc_wallet"].set(_first("btc_wallet"))
        self._pool_vars["pool_url"].set(_first("pool_url"))
        self._pool_vars["pool_port"].set(_first("pool_port"))
        self._pool_vars["pool_pass"].set(_first("pool_pass") or "x")
        self._nmminer_licence.set(_first("nmminer_licence"))

    def _save_history(self):
        """Persist current field values into history (called on Continue → Step 3)."""
        h = self._history
        for key, var in [
            ("wifi_ssid",       self._cfg_vars["wifi_ssid"]),
            ("wifi_pass",       self._cfg_vars["wifi_pass"]),
            ("btc_wallet",      self._cfg_vars["btc_wallet"]),
            ("pool_url",        self._pool_vars["pool_url"]),
            ("pool_port",       self._pool_vars["pool_port"]),
            ("pool_pass",       self._pool_vars["pool_pass"]),
            ("nmminer_licence", self._nmminer_licence),
        ]:
            h = _hist.push(h, key, var.get())
        self._history = h
        _hist.save(h)

    # ── Help bubbles ──────────────────────────────────────────────────────────

    def _help_btn(self, parent, title: str, text: str) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent, text="?", width=22, height=22,
            font=ctk.CTkFont(size=11, weight="bold"),
            corner_radius=11,
            fg_color=("gray72", "gray28"),
            hover_color=("gray62", "gray38"),
            text_color=("gray10", "gray90"),
            command=lambda t=title, b=text: self._show_help(t, b),
        )

    def _show_help(self, title: str, text: str):
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.resizable(False, False)
        win.grab_set()
        win.attributes("-topmost", True)
        ctk.CTkLabel(
            win, text=text, wraplength=420, justify="left",
            font=ctk.CTkFont(size=12),
        ).pack(padx=28, pady=(22, 10), anchor="w")
        ctk.CTkButton(win, text="OK", width=90, command=win.destroy).pack(pady=(4, 18))
        win.update_idletasks()
        w = min(win.winfo_reqwidth() + 56, 520)
        h = win.winfo_reqheight() + 20
        win.geometry(f"{w}x{h}")

    # ── Bug report ────────────────────────────────────────────────────────────

    def _redact(self, text: str) -> str:
        import re
        ssid    = self._cfg_vars["wifi_ssid"].get().strip()
        password= self._cfg_vars["wifi_pass"].get().strip()
        wallet  = self._cfg_vars["btc_wallet"].get().strip()
        if ssid:
            text = text.replace(ssid, "[WIFI SSID REDACTED]")
        if password:
            text = text.replace(password, "[WIFI PASSWORD REDACTED]")
        if wallet:
            text = text.replace(wallet, "[WALLET REDACTED]")
        # Catch any Bitcoin addresses not already redacted
        text = re.sub(r'\b(bc1[a-z0-9]{25,90}|[13][a-km-zA-HJ-NP-Z1-9]{25,62})\b',
                      '[WALLET REDACTED]', text)
        return text

    def _compile_report(self, flash: bool, serial: bool, device: bool, redact: bool) -> str:
        import datetime
        lines = ["ESP32 Miner Flasher — Debug Report",
                 f"Generated : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                 "=" * 60,
                 "",
                 "To submit this report:",
                 "  Option 1 — GitHub Issues (preferred):",
                 "    https://github.com/WardedDruid/ESP32-Miner-Flasher/issues",
                 "  Option 2 — Email this file to:",
                 "    druidicspace@gmail.com",
                 "",
                 "=" * 60, ""]

        if device:
            lines += ["[DEVICE INFORMATION]"]
            lines += [f"Board          : {self._board_var.get()}"]
            lines += [f"Port           : {self._selected_port() or 'Unknown'}"]
            if self._detected_chip:
                c = self._detected_chip
                lines += [f"Chip           : {c.name}"]
                lines += [f"Flash Size     : {c.flash_size}"]
                lines += [f"MAC            : {c.mac}"]
            lines += [f"Firmware Family: {self._fw_family.get()}"]
            lines += [f"Pool           : {self._pool_vars['pool_url'].get().strip()}:{self._pool_vars['pool_port'].get().strip()}"]
            lines += [f"Firmware File  : {self._firmware_path or 'None'}"]
            lines += [""]

        if flash:
            lines += ["[FLASH LOG]"]
            raw = self._log_box.get("1.0", "end").strip()
            lines += [self._redact(raw) if redact else raw]
            lines += [""]

        if serial:
            lines += ["[SERIAL MONITOR LOG]"]
            raw = "".join(self._serial_log_buffer).strip()
            lines += [self._redact(raw) if redact else raw]
            lines += [""]

        return "\n".join(lines)

    def _send_report_dialog(self):
        import datetime, os, subprocess

        win = ctk.CTkToplevel(self)
        win.title("Send Bug Report")
        win.resizable(False, False)
        win.grab_set()
        win.attributes("-topmost", True)

        ctk.CTkLabel(win, text="Send Bug Report",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(padx=24, pady=(20, 4), anchor="w")
        ctk.CTkLabel(win,
                     text="Select what to include. The report will be saved to your Desktop\n"
                          "so you can review it before sharing.",
                     font=ctk.CTkFont(size=11), text_color=_MUTED, justify="left",
                     ).pack(padx=24, pady=(0, 12), anchor="w")

        v_flash  = ctk.BooleanVar(value=True)
        v_serial = ctk.BooleanVar(value=bool(self._serial_log_buffer))
        v_device = ctk.BooleanVar(value=True)
        v_redact = ctk.BooleanVar(value=True)

        for text, var in [
            ("Flash log",                    v_flash),
            ("Serial monitor log",           v_serial),
            ("ESP32 device information",     v_device),
        ]:
            ctk.CTkCheckBox(win, text=text, variable=var).pack(
                padx=32, pady=3, anchor="w")

        ctk.CTkFrame(win, height=1, fg_color=("gray80", "gray30")).pack(
            fill="x", padx=24, pady=(10, 6))

        ctk.CTkCheckBox(win,
                        text="Redact WiFi credentials and wallet address",
                        variable=v_redact,
                        font=ctk.CTkFont(size=11),
                        ).pack(padx=32, pady=(0, 12), anchor="w")

        status_lbl = ctk.CTkLabel(win, text="", font=ctk.CTkFont(size=11),
                                  text_color=_GREEN, wraplength=360, justify="left")
        status_lbl.pack(padx=24, pady=(0, 4), anchor="w")

        def _save():
            if not any([v_flash.get(), v_serial.get(), v_device.get()]):
                status_lbl.configure(text="Select at least one item.", text_color=_ORANGE)
                return
            report = self._compile_report(v_flash.get(), v_serial.get(),
                                          v_device.get(), v_redact.get())
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            path = os.path.join(desktop, f"ESP32_Flasher_Report_{ts}.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(report)
            subprocess.Popen(["notepad.exe", path])
            status_lbl.configure(
                text=f"Saved to Desktop: ESP32_Flasher_Report_{ts}.txt\n"
                     "Review it, then attach to a GitHub issue:\n"
                     "github.com/WardedDruid/ESP32-Miner-Flasher/issues",
                text_color=_GREEN)

        btn_row = ctk.CTkFrame(win, fg_color="transparent")
        btn_row.pack(padx=24, pady=(4, 20), anchor="e")
        ctk.CTkButton(btn_row, text="Cancel", width=80,
                      fg_color="transparent", border_width=1,
                      command=win.destroy).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_row, text="Save Report", width=100,
                      command=_save).pack(side="left")

        win.update_idletasks()
        win.geometry(f"420x{win.winfo_reqheight() + 10}")

    # ── WiFi / Wallet data management ─────────────────────────────────────────

    def _clear_wifi_wallet(self):
        for key in ("wifi_ssid", "wifi_pass", "btc_wallet"):
            self._history.pop(key, None)
            if key in self._cfg_vars:
                self._cfg_vars[key].set("")
        _hist.save(self._history)

    def _on_pool_selected(self, url: str):
        """Auto-fill port when a known pool is chosen from the dropdown."""
        port = _ALL_POOLS.get(url)
        if port is not None:
            self._pool_vars["pool_port"].set(str(port))
        # Update wallet label for XEC vs BTC pools
        if hasattr(self, "_wallet_label"):
            is_xec = url.strip().lower() in XEC_POOLS
            self._wallet_label.configure(text="eCash Wallet:" if is_xec else "Bitcoin Wallet:")
        self._update_pool_warning(url)

    def _update_pool_warning(self, url: str = None):
        """Show a red warning if the entered pool is known to not work with ESP32 miners."""
        if not hasattr(self, "_pool_warn"):
            return
        if url is None:
            url = self._pool_vars["pool_url"].get().strip()
        url_lower = url.lower()
        fam = self._fw_family.get()

        reason = EXCLUDED_POOLS.get(url_lower)
        if not reason and url_lower == "pool.nerdminers.org" and fam != "NerdMiner v2":
            reason = "pool.nerdminers.org rejects non-NerdMiner Stratum clients"

        if reason:
            self._pool_warn.configure(text=f"⚠  {reason}")
            self._pool_warn.grid()
        else:
            self._pool_warn.grid_remove()

    def _on_fw_family_change(self, _=None):
        fam    = self._fw_family.get()
        active = self._nvs_inject.get()

        # Update pool dropdown to show family-appropriate pools
        if hasattr(self, "_pool_combo"):
            base = NERDMINER_POOLS if fam == "NerdMiner v2" else KNOWN_POOLS
            hist = [h for h in _hist.get_list(self._history, "pool_url") if h not in base and h not in EXCLUDED_POOLS]
            self._pool_combo.configure(values=list(base.keys()) + hist)
        self._update_pool_warning()

        # Show/hide NMMiner license row
        if hasattr(self, "_licence_lbl"):
            if fam == "NMMiner":
                self._licence_lbl.grid()
                self._licence_entry.grid()
            else:
                self._licence_lbl.grid_remove()
                self._licence_entry.grid_remove()

        notes  = {
            "BitsyMiner":  (
                "Pre-flashes WiFi + pool into NVS (namespace 'storage'). "
                "Board boots straight into mining — no web portal needed."
            ),
            "SparkMiner":  (
                "Pre-flashes WiFi + pool as NVS blob (namespace 'sparkminer'). "
                "Board boots straight into mining — no captive portal needed."
            ),
            "NerdMiner v2": (
                "Pre-flashes WiFi into NVS (nvs.net80211) + pool/wallet into SPIFFS /config.json. "
                "Board boots straight into mining — no captive portal needed."
            ),
            "NMMiner": (
                "Pre-flashes WiFi, pool, wallet + license into NVS (namespace 'miner_settings'). "
                "Enter your 128-char hex license from nmminer.com above. "
                "Board boots straight into mining — no portal needed."
            ),
            "Other": "No pre-configuration available for generic firmware.",
        }
        note = notes.get(fam, "")
        if fam == "Other" and active:
            note = "⚠  Pre-config is not supported for this firmware family. Uncheck or choose a different family."
        if hasattr(self, "_preconfig_note"):
            self._preconfig_note.configure(text=note if active else "")

    def _on_fw_mode_change(self):
        if self._fw_mode.get() == "single":
            self._single_container.grid()
            self._parts_outer.grid_remove()
        else:
            self._single_container.grid_remove()
            self._parts_outer.grid()

    def _on_board_change(self, _=None):
        if not hasattr(self, "_part_vars"):
            return
        defaults = BOARDS[self._board_var.get()]["part_offsets"]
        for lbl, (_, ov) in self._part_vars.items():
            key = lbl.replace(" (optional)", "")
            if key in defaults:
                ov.set(hex(defaults[key]))

    # ── Flash ──────────────────────────────────────────────────────────

    def _on_flash_click(self):
        if self._flashing:
            self._flasher.cancel()
        else:
            self._start_flash()

    def _start_flash(self):
        # Release the serial port so esptool can open it
        if self._serial_win_mon and self._serial_win_mon.connected:
            self._serial_win_mon.disconnect()
            self._log("Note: Serial monitor disconnected to free port for flashing.")
        self._nmminer_license_pending = False

        port  = self._selected_port()
        board = BOARDS[self._board_var.get()]

        if not port:
            self._log("ERROR: No port. Go back to Step 1.")
            return

        if self._fw_mode.get() == "single":
            if not self._firmware_path:
                self._log("ERROR: No firmware file. Go back to Step 2.")
                return
            working = self._apply_config(self._firmware_path)
            files   = {board["single_offset"]: working}
        else:
            files = {}
            for lbl, (pv, ov) in self._part_vars.items():
                p = pv.get().strip()
                if not p:
                    continue
                try:
                    offset = int(ov.get(), 16)
                except ValueError:
                    self._log(f"ERROR: Bad offset for '{lbl}'")
                    return
                files[offset] = p
            if not files:
                self._log("ERROR: No partition files selected.")
                return

        extra_files = {}
        if self._nvs_inject.get():
            fam = self._fw_family.get()
            if fam == "NerdMiner v2":
                sp_path = self._build_spiffs_binary()
                if sp_path:
                    self._spiffs_tmp = sp_path
                    extra_files[_spiffs.NERDMINER_SPIFFS_OFFSET] = sp_path
                    self._log(f"SPIFFS image generated → will flash at 0x{_spiffs.NERDMINER_SPIFFS_OFFSET:X}")
                ssid = self._cfg_vars["wifi_ssid"].get().strip()
                wifi_pass = self._cfg_vars["wifi_pass"].get().strip()
                if ssid:
                    try:
                        wifi_nvs = _nvs.write_wifi_nvs_temp_file(ssid, wifi_pass)
                        self._nvs_tmp = wifi_nvs
                        extra_files[_nvs.NVS_PART_OFFSET] = wifi_nvs
                        self._log(f"WiFi NVS generated → will flash at 0x{_nvs.NVS_PART_OFFSET:X}")
                    except Exception as e:
                        self._log(f"Warning: could not build WiFi NVS: {e}")
            elif fam == "NMMiner":
                if self._nmminer_licence.get().strip():
                    nvs_path = self._build_nvs_binary()
                    if nvs_path:
                        self._nvs_tmp = nvs_path
                        extra_files[_nvs.NVS_PART_OFFSET] = nvs_path
                        self._log(f"NVS partition generated → will flash at 0x{_nvs.NVS_PART_OFFSET:X}")
                else:
                    self._nmminer_license_pending = True
                    self._log("NMMiner: No license entered — serial monitor will capture device code after flash.")
            elif fam in ("BitsyMiner", "SparkMiner"):
                nvs_path = self._build_nvs_binary()
                if nvs_path:
                    self._nvs_tmp = nvs_path
                    extra_files[_nvs.NVS_PART_OFFSET] = nvs_path
                    self._log(f"NVS partition generated → will flash at 0x{_nvs.NVS_PART_OFFSET:X}")
            else:
                self._log(f"Note: Pre-config not supported for {fam} — skipping.")

        task = FlashTask(
            chip        = board["chip"],
            port        = port,
            baud        = board["baud"],
            flash_mode  = board["flash_mode"],
            flash_freq  = board["flash_freq"],
            flash_size  = board["flash_size"],
            files       = files,
            extra_files = extra_files,
            erase_first = self._erase_var.get(),
        )

        self._set_busy(True)
        self._log(f"--- Flashing {self._board_var.get()} on {port} ---")

        self._flasher.flash(
            task,
            log_cb      = lambda m: self.after(0, self._log, m),
            progress_cb = lambda p: self.after(0, self._set_progress, p),
            done_cb     = lambda ok, msg: self.after(0, self._flash_done, ok, msg),
        )

    def _apply_config(self, src: str) -> str:
        config = {k: v.get().strip() for k, v in self._cfg_vars.items()}
        if not any(config.values()):
            return src
        patched = patch_binary(src, config)
        if patched:
            self._log(f"Config patched → {os.path.basename(patched)}")
            return patched
        self._log("Note: No placeholders found in firmware — config fields not injected.")
        self._log("      If this firmware uses a web portal for setup, that's expected.")
        return src

    def _pool_port_int(self) -> int:
        s = self._pool_vars["pool_port"].get().strip()
        return int(s) if s.isdigit() else 0

    def _build_nvs_binary(self) -> Optional[str]:
        """Generate NVS partition binary. Returns temp-file path or None."""
        fam = self._fw_family.get()
        try:
            ssid      = self._cfg_vars["wifi_ssid"].get().strip()
            wifi_pass = self._cfg_vars["wifi_pass"].get().strip()
            pool_url  = self._pool_vars["pool_url"].get().strip()
            pool_port = self._pool_port_int()
            wallet    = self._cfg_vars["btc_wallet"].get().strip()
            pool_pass = self._pool_vars["pool_pass"].get().strip() or "x"

            if fam == "SparkMiner":
                brightness      = _SPARK_BRIGHTNESS_VALUES.get(self._disp_vars["screen_brt"].get(), 100)
                tmr_ms          = _DIM_TIMER_MS.get(self._disp_vars["inactiv_tmr"].get())
                screen_timeout_s = (tmr_ms // 1000) if tmr_ms else 0
                tz_s            = _TIMEZONE_SECONDS.get(self._disp_vars["utc_tz"].get(), 0)
                timezone_hours  = tz_s // 3600
                return _spark.write_nvs_temp_file(
                    ssid=ssid, wifi_pass=wifi_pass,
                    pool_url=pool_url, pool_port=pool_port,
                    wallet=wallet, pool_pass=pool_pass,
                    brightness=brightness,
                    screen_timeout_s=screen_timeout_s,
                    timezone_hours=timezone_hours,
                )
            elif fam == "NMMiner":
                return _nmminer.write_nvs_temp_file(
                    wifi_ssid=ssid, wifi_pass=wifi_pass,
                    pool_url=pool_url, pool_port=pool_port,
                    wallet=wallet, pool_pass=pool_pass,
                    licence=self._nmminer_licence.get().strip(),
                )
            else:  # BitsyMiner (default)
                screen_brt     = _BRIGHTNESS_VALUES.get(self._disp_vars["screen_brt"].get())
                inactiv_tmr_ms = _DIM_TIMER_MS.get(self._disp_vars["inactiv_tmr"].get())
                inactiv_brt    = _DIM_TO_VALUES.get(self._disp_vars["inactiv_brt"].get())
                clock24        = self._disp_vars["clock24"].get()
                utc_offset_s   = _TIMEZONE_SECONDS.get(self._disp_vars["utc_tz"].get())

                return _nvs.write_nvs_temp_file(
                    wifi_ssid=ssid, wifi_pass=wifi_pass,
                    pool_url=pool_url, pool_port=pool_port,
                    wallet=wallet, pool_pass=pool_pass,
                    screen_brt=screen_brt,
                    inactiv_tmr_ms=inactiv_tmr_ms,
                    inactiv_brt=inactiv_brt,
                    clock24=clock24,
                    utc_offset_s=utc_offset_s,
                )
        except Exception as exc:
            self._log(f"ERROR: Failed to generate NVS binary: {exc}")
            return None

    def _build_spiffs_binary(self) -> Optional[str]:
        """Generate NerdMiner SPIFFS image. Returns temp-file path or None."""
        try:
            tz_s       = _TIMEZONE_SECONDS.get(self._disp_vars["utc_tz"].get(), 0)
            brightness = _BRIGHTNESS_VALUES.get(self._disp_vars["screen_brt"].get(), 250)
            path = _spiffs.write_spiffs_temp_file(
                pool_url   = self._pool_vars["pool_url"].get().strip(),
                pool_port  = self._pool_port_int(),
                wallet     = self._cfg_vars["btc_wallet"].get().strip(),
                pool_pass  = self._pool_vars["pool_pass"].get().strip() or "x",
                gmt_zone   = tz_s // 3600,
                brightness = brightness,
            )
            return path
        except Exception as exc:
            self._log(f"ERROR: Failed to generate SPIFFS image: {exc}")
            return None

    def _set_busy(self, busy: bool):
        self._flashing = busy
        if busy:
            self._flash_btn.configure(text="✕  CANCEL", fg_color=_RED)
            self._progress.configure(mode="indeterminate")
            self._progress.grid()
            self._progress.start()
        else:
            self._flash_btn.configure(
                text="⚡   FLASH FIRMWARE",
                fg_color=ctk.ThemeManager.theme["CTkButton"]["fg_color"],
            )
            self._progress.stop()
            self._progress.configure(mode="determinate")

    def _set_progress(self, pct: int):
        if self._progress.cget("mode") == "indeterminate":
            self._progress.stop()
            self._progress.configure(mode="determinate")
        self._progress.set(pct / 100)

    def _flash_done(self, success: bool, _msg: str):
        self._set_busy(False)
        if success:
            self._progress.set(1.0)
            self._log("--- SUCCESS: Flash complete! ---")
            if self._nmminer_license_pending:
                self._nmminer_license_pending = False
                self._log("NMMiner: Opening serial monitor — waiting for device code...")
                self.after(1500, lambda: self._open_serial_window(auto_connect=True))
            elif self._nvs_inject.get():
                self._log("NVS config pre-loaded. Board should connect to WiFi + pool on first boot.")
        else:
            self._progress.grid_remove()
            self._log(f"--- FAILED — see log above ---")
        for attr in ("_nvs_tmp", "_spiffs_tmp"):
            path = getattr(self, attr, None)
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass
                setattr(self, attr, None)

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self._log_box.configure(state="normal")
        self._log_box.insert("end", f"[{ts}] {msg}\n")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _clear_log(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    def destroy(self):
        self._detector.stop()
        self._flasher.cancel()
        super().destroy()
