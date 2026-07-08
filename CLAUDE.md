# ESP32 CYD Flasher

A Python desktop app for flashing ESP32 CYD (Cheap Yellow Display) boards with auto-detection, drag-and-drop firmware loading, and pre-flash configuration injection.

## Tech Stack

- **Python 3.14** (Windows)
- **customtkinter 5.2.2** — dark-themed GUI
- **tkinterdnd2** — drag-and-drop file support
- **pyserial** — USB port detection
- **esptool** — firmware flashing (called via subprocess)

## Running the App

```bat
# First run — installs deps and launches:
run.bat

# After first run:
python main.py

# Build a standalone .exe:
build_exe.bat
```

## File Structure

```
ESPflasher/
├── main.py                  # Entry point
├── requirements.txt
├── run.bat                  # Install deps + launch
├── build_exe.bat            # PyInstaller .exe builder
├── core/
│   ├── detector.py          # USB port scanning (VID:PID recognition, background polling)
│   ├── flasher.py           # esptool wrapper — FlashTask dataclass + Flasher class
│   ├── history.py           # JSON field-history store (~/.espflasher_history.json)
│   ├── nvs.py               # BitsyMiner NVS partition binary generator
│   ├── patcher.py           # Binary config injection (placeholder patching + chip detection)
│   ├── probe.py             # Chip detection via esptool flash_id (ChipInfo dataclass)
│   ├── sparkminer.py        # SparkMiner NVS blob generator (miner_config_t struct)
│   └── spiffs.py            # Pure-Python SPIFFS image writer (NerdMiner /config.json)
└── gui/
    └── app.py               # Main UI — 3-step wizard (ESPFlasherApp)
```

## UI: 3-Step Wizard

### Step 1 — Connect
- Board type dropdown (CYD 2.8", CYD 2USB, CYD 4.3", ESP32 Standard DevKit)
- Port auto-detected via USB VID:PID polling every 1.5 seconds
- **Instant detection** (no probe needed): ESP32-S3/S2 native USB identified from VID:PID alone
- **Detect Chip button**: runs `esptool flash_id`, parses chip name + flash size, auto-selects board preset
- Board auto-selected on detection (e.g. ESP32 v3.1 + 4MB → CYD 2.8")
- Continue button disabled until a valid port is selected

### Step 2 — Configure

**Firmware section:**
- Single merged `.bin` mode (default) — drag/drop or browse, one file flashed at offset 0x0
- Separate partition files mode — individual bootloader/partition table/app files with hex offset fields
- Chip compatibility check: reads ESP32 image header (byte 12 = chip_id) and compares against selected board — green/red/gray indicator shown immediately after file is loaded

**WiFi & Bitcoin section:**
- WiFi SSID — CTkComboBox with history (up to 8 remembered entries)
- WiFi Password — masked CTkEntry, auto-filled from last saved value on startup
- Bitcoin Wallet — CTkComboBox with history

**Pool section:**
- Pool URL — CTkComboBox with built-in known pools + custom history; selecting a known pool auto-fills the port
- Pool Port — CTkEntry (auto-filled when pool URL is selected)
- Pool Pass — CTkEntry (default "x")
- Firmware Family — dropdown: BitsyMiner / SparkMiner / NerdMiner v2 / NMMiner / Other
- Inject pre-config checkbox — enables NVS/SPIFFS generation alongside firmware flash

**Known pools (built-in):**

General (BitsyMiner / SparkMiner / Other):
| Pool URL | Port | Notes |
|---|---|---|
| hmpool.io | 3337 | Confirmed working with BitsyMiner, SparkMiner, NerdMiner v2 |
| btc.hmpool.io | 3337 | HMPool BTC subdomain — confirmed working with BitsyMiner, SparkMiner, NerdMiner v2 |
| xec.hmpool.io | 3337 | HMPool eCash (XEC) — requires eCash wallet — confirmed working with BitsyMiner, SparkMiner, NerdMiner v2 |
| public-pool.io | 21496 | Confirmed working with BitsyMiner, SparkMiner — does NOT work with NerdMiner v2 |
| pool.sethforprivacy.com | 3333 | Confirmed working with BitsyMiner, SparkMiner, NerdMiner v2 |
| pool.nerdminer.io | 3333 | Confirmed working with BitsyMiner, SparkMiner, NerdMiner v2 |
| pool.solomining.de | 3333 | Confirmed working with BitsyMiner, SparkMiner, NerdMiner v2 |

NerdMiner v2 only (pool.nerdminers.org blocks non-NerdMiner user agents):
| Pool URL | Port |
|---|---|
| pool.nerdminers.org | 3333 |
| pool.nerdminer.io | 3333 |

**Excluded pools and reasons:**
- `ocean.xyz` — ASIC-targeted; sets difficulty 65k+ which an ESP32 (25–40 kH/s) will never satisfy
- `solo.ckpool.org` — sets initial difficulty 10,000; takes weeks per share on ESP32
- `btcplebpool.com` — DNS does not resolve; appears defunct as of mid-2025
- `stratum.braiins.com` / `stratum.slushpool.com` — Braiins Pool is ASIC-targeted
- `pool.nerdminers.org` (general list) — rejects non-NerdMiner Stratum user agents
- `lotterypool.io` — no DNS record found; confirmed non-functional as of 2026-07
- `pool.stompi.de` — stratum connection refused; confirmed non-functional as of 2026-07
- `pool.pyblock.xyz` — stratum connection refused; confirmed non-functional as of 2026-07
- `pool.pyblock.xyz` — stratum connection refused; confirmed non-functional as of 2026-07

**Erase flash before writing** checkbox

**History** saved to `~/.espflasher_history.json` (WiFi SSID, WiFi Password, Bitcoin Wallet, Pool URL, Pool Port, Pool Pass) when navigating Step 2 → Step 3.

### Step 3 — Flash
- 2-column summary grid (5-column: lbl_L | val_L | gap | lbl_R | val_R) showing all settings
- ⚡ FLASH FIRMWARE button (large, prominent)
- Cancel mid-flash support
- Progress bar (indeterminate during connect/erase, determinate during write)
- Output log with timestamps (Consolas font)

## Board Presets

| Board | Chip | Flash | Flash Mode | Freq |
|---|---|---|---|---|
| CYD 2.8" (ESP32-2432S028R) | esp32 | 4MB | dio | 40m |
| CYD 2USB (ESP32-2432S028R v2) | esp32 | 4MB | dio | 40m |
| CYD 4.3" (ESP32-S3 8048S043) | esp32s3 | 16MB | qio | 80m |
| ESP32 Standard DevKit | esp32 | detect | dio | 40m |

## Config Injection — Firmware Families

### BitsyMiner (`core/nvs.py`)

NVS partition (20 KB, offset 0x9000) with namespace `"storage"`.

| NVS Key | Type | Notes |
|---|---|---|
| `currentMode` | U16 | Must be `0x0707` (MODE_INSTALL_COMPLETE) or BitsyMiner ignores all stored credentials |
| `ssid` | STR | WiFi SSID |
| `ssidPassword` | STR | WiFi password |
| `poolUrl` | STR | Pool hostname only (no protocol prefix) |
| `poolPort` | U16 | Pool port number |
| `wallet` | STR | Bitcoin wallet address |
| `poolPass` | STR | Pool password (usually "x") |

**Flash overlap fix:** The merged 4MB firmware at offset 0x0 extends past 0x9000, so the NVS binary is flashed in a **separate second `write_flash` command** via `FlashTask.extra_files`.

**BitsyMiner default web portal credentials:**
- Username: `bitsy` / Password: `miner`
- URL: `http://192.168.4.1` (connected to BitsyMiner AP) or scan the QR code on the CYD screen

### SparkMiner (`core/sparkminer.py`)

NVS partition (same offset 0x9000) with namespace `"sparkminer"`, single blob key `"config"`.

The blob is the `miner_config_t` C struct (980 bytes), ending with a `uint32_t` polynomial checksum seeded with `CONFIG_MAGIC = 0x5350524B` ("SPRK"), polynomial `sum = sum * 31 + byte`.

Key field offsets: ssid[0:64], wifiPassword[64:129], poolUrl[129:210], poolPort[210:212], wallet[212:333], poolPassword[333:398].

### NerdMiner v2 (`core/spiffs.py`)

SPIFFS image (224 KB, offset 0x310000) containing `/config.json`.

JSON keys: `poolString`, `portNumber`, `poolPassword`, `btcString`, `gmtZone`.

**WiFi IS pre-configurable** — injected via `nvs.net80211` NVS namespace (keys: `opmode` U8=1, `bssid.set` U8=0, `sta.chan` U8=0, `sta.ssid` 36-byte blob, `sta.pswd` 65-byte blob). Blob format is `[4-byte uint32 LE length][32-byte SSID padded]` for SSID and `[64-byte password padded][1 null byte]` for password. Both use TYPE_BLOB_DATA=0x42. This NVS is written alongside the SPIFFS image in a second `write_flash` pass.

### NMMiner

Uses 4 separate binary files (bootloader/partitions/firmware/littlefs). Flash using "Separate partition files" mode. Offsets: 0x1000 / 0x8000 / 0x10000 / 0x39C000. boot_app0 is not needed.

## FlashTask Two-Pass Write

```python
@dataclass
class FlashTask:
    chip: str
    port: str
    baud: int
    flash_mode: str
    flash_freq: str
    flash_size: str
    files: Dict[int, str]        # primary firmware files
    extra_files: Dict[int, str]  # NVS/SPIFFS — written in a separate second write_flash call
    erase_first: bool = False
```

The flasher runs `files` in the first `write_flash`, then (if `extra_files` is non-empty) runs a second `write_flash` for the extra files. This avoids the esptool "overlap at 0x9000" error that occurs when both are in the same command.

## Binary Placeholder Patching (`core/patcher.py`)

For custom firmware compiled with fixed-size marker strings. Searches the `.bin` for:

```c
char wifi_ssid[64]   = "<<WIFI_SSID>>";
char wifi_pass[64]   = "<<WIFI_PASS>>";
char btc_wallet[64]  = "<<BTC_WALLET>>";
```

Also exposes `image_chip(bin_path) → (chip_str, message)`: reads ESP32 image header byte 12 (`chip_id` uint16 LE) and maps to esptool chip string. `0x0000` = esp32, `0x0009` = esp32s3, `0x0002` = esp32s2.

## User's Current Setup

- Board: CYD 2.8" (ESP32-2432S028R), COM5, CH340, ESP32 v3.1, 4MB flash
- Firmware: `BitsyMinerOpenSource-2.8inch_9341.New_Install.bin`
- Confirmed working with: BitsyMiner NVS injection (hmpool.io:3337)
- NVS injection, pool dropdown, and field history all implemented and verified
