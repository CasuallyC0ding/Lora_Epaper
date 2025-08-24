import threading
import serial, json, requests, os, time
from datetime import datetime, timedelta

SERIAL_PORT = "COM7"
BAUD = 115200
TB_HOST = "http://demo.thingsboard.io"
TB_USER = "youareaverygoodpersontrustme@gmail.com"
TB_PASS = "Thingsboard"
UUID = "8aa29b50-658a-11f0-83dd-65e1b21422bc"  # Tenant device UUID (for delete calls)
JWT = None

MAP_FILE = "device_mac_token_map.json"
SCHEDULE_FILE = "scheduled_events.json"

ser = None  # Global serial object
get_msg_event = threading.Event()

# ---------------- Auth / device mapping ----------------

def login():
    global JWT
    resp = requests.post(f"{TB_HOST}/api/auth/login",
                         json={"username": TB_USER, "password": TB_PASS})
    resp.raise_for_status()
    JWT = resp.json()["token"]

def load_map():
    if os.path.exists(MAP_FILE):
        with open(MAP_FILE, "r") as f:
            return json.load(f)
    return {}

def save_map(mac_token_map):
    with open(MAP_FILE, "w") as f:
        json.dump(mac_token_map, f, indent=2)

# --------------- schedule persistence ------------------

def load_schedule():
    if os.path.exists(SCHEDULE_FILE):
        with open(SCHEDULE_FILE, "r") as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}

def save_schedule(scheduled_events):
    with open(SCHEDULE_FILE, "w") as f:
        json.dump(scheduled_events, f, indent=2)

# In-memory schedule: { mac: { label: {"start": int, "end": int, "sent": bool} } }
scheduled_events = load_schedule()

# ---------------- ThingsBoard helpers ------------------

def get_or_create_device(mac, mac_token_map):
    if mac in mac_token_map:
        return mac_token_map[mac]

    hdr = {"X-Authorization": f"Bearer {JWT}"}

    # Generate display name like lora_1, lora_2...
    counter_file = "device_counter.txt"
    if os.path.exists(counter_file):
        with open(counter_file, "r") as f:
            counter = int(f.read().strip())
    else:
        counter = 1

    device_name = f"lora_{counter}"

    # Create the device
    resp = requests.post(f"{TB_HOST}/api/device", headers=hdr, json={"name": device_name})
    resp.raise_for_status()
    dev_id = resp.json()["id"]["id"]

    # Save counter
    with open(counter_file, "w") as f:
        f.write(str(counter + 1))

    # Store MAC → token mapping
    token_resp = requests.get(f"{TB_HOST}/api/device/{dev_id}/credentials", headers=hdr)
    token_resp.raise_for_status()
    token = token_resp.json()["credentialsId"]
    mac_token_map[mac] = token
    save_map(mac_token_map)

    # Optional attribute
    requests.post(f"{TB_HOST}/api/v1/{token}/attributes", json={"mac_address": mac})

    # Optional dashboard creation
    create_dashboard(dev_id, device_name)

    return token

def create_dashboard(device_id, device_name):
    hdr = {"X-Authorization": f"Bearer {JWT}", "Content-Type": "application/json"}
    db = {
        "title": f"Dashboard {device_name}",
        "configuration": {
            "widgets": {},
            "layout": {}
        },
        "assignToCustomer": False
    }
    try:
        resp = requests.post(f"{TB_HOST}/api/dashboard", headers=hdr, json=db)
        resp.raise_for_status()
        did = resp.json()["id"]["id"]
        requests.post(f"{TB_HOST}/api/dashboard/{did}/assignToEntity?entityId={device_id}", headers=hdr)
    except Exception:
        # Non-fatal if dashboard creation fails
        pass

def send_telemetry(token, data_value, battery_value):
    payload = {"data": data_value, "battery": float(battery_value)}
    requests.post(f"{TB_HOST}/api/v1/{token}/telemetry", json=payload)

# --------------- one-time TB message filter ------------

def is_calendar_value(value: str) -> bool:
    # Expecting "Start:YYYY-MM-DD HH:MM\nEnd:YYYY-MM-DD HH:MM"
    return isinstance(value, str) and value.startswith("Start:") and "\nEnd:" in value

def parse_calendar_value(value: str):
    # Returns (start_ts, end_ts)
    start = value.split("\n")[0].split("Start:")[1].strip()
    end = value.split("\n")[1].split("End:")[1].strip()
    start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M")
    end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M")
    return int(start_dt.timestamp()), int(end_dt.timestamp())

# ----------------- GET_MSG worker thread ---------------

def check_for_extra_fields(mac_token_map):
    global ser, scheduled_events
    while True:
        # Wait for ESP32 request
        get_msg_event.wait()
        get_msg_event.clear()

        now_dt = datetime.now()
        now_ts = int(now_dt.timestamp())
        horizon_ts = now_ts + 2 * 3600

        for mac, token in mac_token_map.items():
            try:
                # Ensure schedule bucket for this mac
                scheduled_events.setdefault(mac, {})

                # 1) Fetch shared attributes from TB
                url = f"{TB_HOST}/api/v1/{token}/attributes?clientKeys=&sharedKeys="
                response = requests.get(url)
                response.raise_for_status()
                data = response.json()
                shared = data.get("shared", {})

                # 2) Update local schedule with only events in next 2 hours
                for key, value in list(shared.items()):
                    lower = key.lower()
                    if lower in {"battery", "id", "mac_address", "data"}:
                        continue

                    if is_calendar_value(value):
                        try:
                            start_ts, end_ts = parse_calendar_value(value)

                            # Delete expired events from TB
                            if end_ts < now_ts:
                                del_url = f"{TB_HOST}/api/plugins/telemetry/DEVICE/{UUID}/SHARED_SCOPE?keys={key}"
                                headers = {"X-Authorization": f"Bearer {JWT}"}
                                requests.delete(del_url, headers=headers)
                                print(f"🗑 Deleted expired TB event '{key}'")
                                # Also drop from local schedule if present
                                scheduled_events[mac].pop(key, None)
                                continue

                            # Keep only within next 2 hours in local schedule
                            if now_ts <= start_ts <= horizon_ts:
                                rec = scheduled_events[mac].get(key, {"start": start_ts, "end": end_ts, "sent": False})
                                # Update times in case edited in TB
                                rec["start"] = start_ts
                                rec["end"] = end_ts
                                # Don't reset 'sent' if already sent
                                scheduled_events[mac][key] = rec
                            else:
                                # Not within the next two hours → drop from local schedule if exists
                                if key in scheduled_events[mac]:
                                    scheduled_events[mac].pop(key, None)

                        except Exception as e:
                            print(f"⚠ Calendar parse error for key '{key}': {e}")

                    elif isinstance(value, str):
                        # One-time plain message: send now and delete key in TB
                        dev_number = list(mac_token_map.keys()).index(mac) + 1
                        dev_name = f"lora_{dev_number}"
                        line = f"{dev_name}:{value}\n"
                        ser.write(line.encode())
                        print(f"📡 Sent one-time msg: {line.strip()}")

                        del_url = f"{TB_HOST}/api/plugins/telemetry/DEVICE/{UUID}/SHARED_SCOPE?keys={key}"
                        headers = {"X-Authorization": f"Bearer {JWT}"}
                        requests.delete(del_url, headers=headers)
                        print(f"🗑 Deleted TB key '{key}' after sending")

                # 3) Now send ONLY due events (start ≤ now ≤ end) that haven't been sent yet
                due_labels = []
                for label, rec in list(scheduled_events[mac].items()):
                    start_ts = rec["start"]
                    end_ts = rec["end"]
                    sent = rec.get("sent", False)

                    # Clean up expired locals
                    if end_ts < now_ts:
                        scheduled_events[mac].pop(label, None)
                        continue

                    # Send when it's time (edge inclusive at start)
                    if (start_ts <= now_ts <= end_ts) and not sent:
                        msg = f"calendar:{label}:Start={start_ts}:End={end_ts}\n"
                        ser.write(msg.encode())
                        print(f"📅 Sent DUE event: {msg.strip()}")
                        rec["sent"] = True
                        due_labels.append(label)

                # (Optional) If you want to re-notify on every GET_MSG while active window, comment out the 'sent' flag logic above.

                # 4) Persist schedule to disk
                save_schedule(scheduled_events)

                # 5) Sync time at the end
                ser.write(f"time:{now_ts}\n".encode())
                print(f"⏰ Synced time {now_ts} with ESP32")

            except Exception as e:
                print(f"❌ Polling error for MAC {mac}: {e}")

# --------------------------- main ----------------------

if __name__ == "__main__":
    login()
    mac_token_map = load_map()

    with serial.Serial(SERIAL_PORT, BAUD, timeout=1) as s:
        ser = s  # Assign to global

        # Start background worker
        threading.Thread(target=check_for_extra_fields, args=(mac_token_map,), daemon=True).start()

        buffer = {"mac": None, "id": None, "battery": None}

        while True:
            try:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode(errors='ignore').strip()

                # Trigger worker on GET_MSG
                if line == "GET_MSG":
                    print("🔄 Received GET_MSG → update schedule & send due events")
                    get_msg_event.set()
                    continue

                # Parse MAC/ID/Battery triplet from ESP32 (then upload to TB)
                if line.startswith("MAC:"):
                    buffer["mac"] = line.split("MAC:", 1)[1].strip()
                elif line.startswith("ID:"):
                    buffer["id"] = line.split("ID:", 1)[1].strip()
                elif line.startswith("Battery:"):
                    buffer["battery"] = line.split("Battery:", 1)[1].strip().replace("%", "")
                else:
                    continue  # Ignore unrelated lines

                if all(buffer.values()):
                    mac = buffer["mac"]
                    device_id = buffer["id"]
                    battery = buffer["battery"]

                    token = get_or_create_device(mac, mac_token_map)
                    send_telemetry(token, float(device_id), float(battery))
                    print(f"✅ Uploaded data={device_id}, battery={battery}% for MAC {mac}")
                    buffer = {"mac": None, "id": None, "battery": None}

            except Exception as e:
                print("❌ Error:", e)
