# LoRa E-Paper Display System

A wireless IoT system that uses **LoRa radio** to transmit sensor badge data (device ID + battery level) from battery-powered ESP32 sender nodes to a central receiver, which forwards everything to **ThingsBoard** for monitoring and pushes calendar events and messages back down to the display.

## System Overview

```
┌─────────────────────┐        LoRa 868 MHz        ┌──────────────────────┐
│   Sender (ESP32)    │  ──── SYN / ACK / DATA ───▶ │  Receiver (ESP32)    │
│  - Badge / sensor   │                             │  - Always-on hub     │
│  - Deep sleep node  │                             │  - OLED status disp. │
│  - Battery monitor  │                             │  - EEPROM persistence │
└─────────────────────┘                             └──────────┬───────────┘
                                                               │ Serial (USB)
                                                               ▼
                                                  ┌────────────────────────┐
                                                  │   Python Server (PC)   │
                                                  │  tempServer.py  or     │
                                                  │  thingsboard_api_server│
                                                  └──────────┬─────────────┘
                                                             │ REST API
                                                             ▼
                                                  ┌────────────────────────┐
                                                  │     ThingsBoard IoT    │
                                                  │  - Telemetry storage   │
                                                  │  - Shared attributes   │
                                                  │  - Per-device dashbrd  │
                                                  └────────────────────────┘
                                                             ▲
                                                  ┌────────────────────────┐
                                                  │  Calendar Uploader GUI │
                                                  │  CalenderUploader.py   │
                                                  │  ics_uploader.py       │
                                                  │  - Google Calendar sync│
                                                  │  - .ics drag-and-drop  │
                                                  └────────────────────────┘
```

## Hardware

Both boards use the same ESP32 + LoRa wiring:

| Signal | GPIO |
|---|---|
| LoRa SCK | 5 |
| LoRa MISO | 19 |
| LoRa MOSI | 27 |
| LoRa SS | 18 |
| LoRa RST | 14 |
| LoRa DIO0 | 26 |
| OLED SDA/SCL | I²C default |
| Battery ADC (sender only) | 35 |

- **Frequency:** 868 MHz (EU band)
- **OLED:** 128×64 SSD1306

## Repository Structure

```
Lora_Epaper/
├── Sender.ino                  # ESP32 firmware — battery-powered sender node
├── reciever.ino                # ESP32 firmware — always-on receiver/hub
├── tempServer.py               # Python bridge — simpler version (v1)
├── thingsboard_api_server.py   # Python bridge — full version with scheduled events (v2)
├── CalenderUploader.py         # GUI — Google Calendar → ThingsBoard sync
├── ics_uploader.py             # GUI — .ics file drag-and-drop uploader
├── device_mac_token_map.json   # MAC address → ThingsBoard token mapping (auto-generated)
├── device_counter.txt          # Auto-incrementing device name counter
└── secret.gitignore            # Lists credential files to exclude from git
```

## Components

### `Sender.ino` — Battery-Powered Sender Node

The sender is designed to wake up, transmit, and immediately go back into deep sleep to maximise battery life.

**Boot sequence:**
1. Starts a Wi-Fi access point (`ESP32_Config` / `12345678`) for up to 20–25 seconds, allowing the device ID to be set via a browser at the AP IP address.
2. After the Wi-Fi window closes, initialises LoRa and performs a **SYN → ACK → DATA** handshake with the receiver (up to 5 retries with exponential backoff).
3. Transmits a payload of `<MAC>:<DeviceID>:<BatteryPercent>`.
4. Enters deep sleep indefinitely until manually reset.

**OTA updates** are checked every 6 hours during the Wi-Fi window from a configurable GitHub raw URL.

**Battery reading** maps a 0–4.2V LiPo voltage (read on GPIO 35 via a ÷2 resistor divider) to a 0–100% percentage.

---

### `reciever.ino` — Always-On Receiver / Hub

The receiver stays permanently awake, listening for incoming LoRa packets and bridging them to the Python server over USB serial.

**Serial protocol (receiver → PC):**

| Message | Meaning |
|---|---|
| `GET_MSG` | Sent every 2 s — requests pending messages/events from the server |
| `MAC:<address>` | MAC of the sender that just transmitted |
| `ID:<value>` | Device ID from the sender payload |
| `Battery:<pct>` | Battery percentage from the sender payload |

**Serial protocol (PC → receiver):**

| Message | Meaning |
|---|---|
| `time:<unix_ts>` | Unix timestamp for the receiver's software clock |
| `<name>:<text>` | Plain text message to display on the OLED (15 s lifetime) |
| `calendar:<label>:Start=<ts>:End=<ts>` | Calendar event — shown on the OLED while active |

Received LoRa data (MAC, ID, battery) is forwarded over serial; the Python server consumes these and uploads them to ThingsBoard. The receiver also saves the most recent message to EEPROM for persistence across power cycles.

---

### `thingsboard_api_server.py` — Python Bridge (Recommended)

The main bridge between the receiver's serial output and ThingsBoard. Run this on any PC connected to the receiver via USB.

**What it does:**

- **Receives** the `GET_MSG` signal every 2 seconds and queries ThingsBoard shared attributes for each known device.
- **Auto-registers** new devices on first sight by MAC address: creates a ThingsBoard device named `lora_1`, `lora_2`, etc., fetches its access token, and persists the MAC → token mapping to `device_mac_token_map.json`.
- **Uploads telemetry** (device ID value + battery %) to ThingsBoard for each transmission.
- **Delivers scheduled calendar events** to the receiver over serial when `start ≤ now ≤ end`, using a local `scheduled_events.json` cache to survive restarts and avoid re-sending.
- **Delivers one-time plain text messages** set as shared attributes in ThingsBoard, then deletes them from TB after sending.
- **Deletes expired calendar events** from ThingsBoard automatically.
- **Syncs the receiver's clock** by sending the current Unix timestamp after every `GET_MSG`.

`tempServer.py` is an earlier, simpler version of the same bridge without the scheduled-event caching — kept for reference.

---

### `CalenderUploader.py` — Google Calendar GUI

A Tkinter desktop app that periodically syncs your **Google Calendar** to ThingsBoard shared attributes and also accepts **.ics file drag-and-drop**.

- Authenticates with Google OAuth 2.0 (requires `credentials.json` from Google Cloud Console).
- Fetches the next 20 upcoming events every 5 minutes and uploads them as shared attributes in the format `Start: YYYY-MM-DD HH:MM\nEnd: YYYY-MM-DD HH:MM`.
- Also handles dropped `.ics` files for one-off imports.

### `ics_uploader.py` — ICS File Uploader GUI

A simpler Tkinter tool focused solely on `.ics` file upload — supports drag-and-drop and a file browser dialog. Uses the `ics` Python library for robust parsing.

---

## Setup

### 1. Arduino (both boards)

Install these libraries via the Arduino Library Manager:

- `LoRa` by Sandeep Mistry
- `Adafruit SSD1306`
- `Adafruit GFX Library`

**Sender** — before flashing, update:
```cpp
const char* ota_url = "https://raw.githubusercontent.com/<your_username>/<your_repo>/main/sender.ino.bin";
```

**Receiver** — before flashing, update:
```cpp
#define WIFI_SSID "YourWiFiSSID"
#define WIFI_PASS "YourWiFiPassword"
#define OTA_URL   "https://raw.githubusercontent.com/.../receiver.ino.bin"
```

### 2. Python server

```bash
pip install pyserial requests
python thingsboard_api_server.py
```

Edit the top of the file:
```python
SERIAL_PORT = "COM7"        # Change to your receiver's serial port (e.g. /dev/ttyUSB0)
TB_HOST     = "http://demo.thingsboard.io"
TB_USER     = "your@email.com"
TB_PASS     = "yourpassword"
UUID        = "<your-tenant-device-uuid>"   # Used for deleting shared attributes
```

### 3. Calendar uploaders

```bash
pip install requests tkinterdnd2 google-auth google-auth-oauthlib google-api-python-client
python CalenderUploader.py
```

For `ics_uploader.py`:
```bash
pip install requests tkinterdnd2 ics
python ics_uploader.py
```

Place your Google Cloud OAuth `credentials.json` in the same directory before first run. The token will be cached in `token.pickle`.

---

## Configuration Files

| File | Purpose | Auto-generated? |
|---|---|---|
| `device_mac_token_map.json` | Maps each sender MAC to its ThingsBoard access token | ✅ Yes |
| `device_counter.txt` | Tracks the next `lora_N` device name index | ✅ Yes |
| `scheduled_events.json` | Local cache of pending/sent calendar events | ✅ Yes |
| `credentials.json` | Google OAuth client secrets | ❌ Manual (Google Cloud) |
| `token.pickle` | Cached Google OAuth token | ✅ After first login |

> `credentials.json`, `token.json`, and `token.pickle` are listed in `secret.gitignore` — do not commit them.

---

## Setting a Sender's Device ID

1. Power on the sender board.
2. On your phone or laptop, connect to Wi-Fi network **`ESP32_Config`** (password: `12345678`).
3. Open a browser and navigate to the IP shown on the sender's OLED (typically `192.168.4.1`).
4. Enter the desired device ID and click **Save**.
5. The ID is stored in flash (NVS) and will persist across deep sleep cycles.

## Sending a Message to a Display

In ThingsBoard, set a **shared attribute** on the target device with any key name and a string value. The server will forward it to the receiver over serial and then delete the attribute. The message appears on the OLED for 15 seconds.

To send a calendar event, set the attribute value to:
```
Start: 2025-09-01 09:00
End: 2025-09-01 10:30
```
The event will be displayed on the OLED for its entire duration and automatically cleaned up from ThingsBoard once it expires.
