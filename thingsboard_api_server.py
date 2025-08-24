import threading
import serial, json, requests, os, time
from datetime import datetime

SERIAL_PORT = "COM7"
BAUD = 115200
TB_HOST = "http://demo.thingsboard.io"
TB_USER = "youareaverygoodpersontrustme@gmail.com"
TB_PASS = "Thingsboard"
UUID = "8aa29b50-658a-11f0-83dd-65e1b21422bc" # this is the UUID of the device (device ID)
JWT = None

MAP_FILE = "device_mac_token_map.json"
ser = None  # Global serial object
get_msg_event = threading.Event()

def login():
    global JWT
    resp = requests.post(f"{TB_HOST}/api/auth/login", json={"username": TB_USER, "password": TB_PASS})
    JWT = resp.json()["token"]

def load_map():
    if os.path.exists(MAP_FILE):
        with open(MAP_FILE, "r") as f:
            return json.load(f)
    return {}

def save_map(mac_token_map):
    with open(MAP_FILE, "w") as f:
        json.dump(mac_token_map, f, indent=2)

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
    dev_id = resp.json()["id"]["id"]

    # Save counter
    with open(counter_file, "w") as f:
        f.write(str(counter + 1))

    # Store MAC → token mapping
    token_resp = requests.get(f"{TB_HOST}/api/device/{dev_id}/credentials", headers=hdr)
    token = token_resp.json()["credentialsId"]
    mac_token_map[mac] = token
    save_map(mac_token_map)

    # Optionally: Add MAC as attribute for tracking
    requests.post(
        f"{TB_HOST}/api/v1/{token}/attributes",
        json={"mac_address": mac}
    )

    # Create dashboard
    create_dashboard(dev_id, device_name)

    return token

def create_dashboard(device_id, device_name):
    hdr = {"X-Authorization": f"Bearer {JWT}", "Content-Type": "application/json"}
    db = {
        "title": f"Dashboard {device_name}",
        "configuration": {
            "widgets": {
                "data_chart": {
                    "type": "timeseries",
                    "title": "Data Chart",
                    "datasources": [{
                        "type": "device",
                        "name": "${entityName}",
                        "dataKeys": [{"name": "data", "type": "timeseries"}]
                    }],
                    "settings": {"showLegend": True},
                    "sizeX": 8, "sizeY": 4, "row": 0, "col": 0
                },
                "battery_chart": {
                    "type": "timeseries",
                    "title": "Battery Level",
                    "datasources": [{
                        "type": "device",
                        "name": "${entityName}",
                        "dataKeys": [{"name": "battery", "type": "timeseries"}]
                    }],
                    "settings": {"showLegend": True},
                    "sizeX": 8, "sizeY": 4, "row": 4, "col": 0
                },
                "id_display": {
                    "type": "latest",
                    "title": "Device ID",
                    "datasources": [{
                        "type": "device",
                        "name": "${entityName}",
                        "dataKeys": [{"name": "ID", "type": "attribute"}]
                    }],
                    "settings": {},
                    "sizeX": 4, "sizeY": 2, "row": 8, "col": 0
                }
            },
            "layout": {}
        },
        "assignToCustomer": False
    }
    resp = requests.post(f"{TB_HOST}/api/dashboard", headers=hdr, json=db)
    did = resp.json()["id"]["id"]
    requests.post(f"{TB_HOST}/api/dashboard/{did}/assignToEntity?entityId={device_id}", headers=hdr)

def send_telemetry(token, data_value, battery_value):
    payload = {
        "data": data_value,
        "battery": float(battery_value)
    }
    requests.post(f"{TB_HOST}/api/v1/{token}/telemetry", json=payload)

# # Time sync every X seconds
# def sync_time_loop():
#     while True:
#         try:
#             now = int(time.time())
#             if ser and ser.writable():
#                 ser.write(f"time:{now}\n".encode())
#                 print(f"⏰ Sent time sync: {now}")
#         except Exception as e:
#             print(f"⚠ Failed to sync time: {e}")
#         time.sleep(30)  # sync every 30 seconds

# Dictionary to store last seen messages per MAC and key
last_shared_values = {}

def check_for_extra_fields(mac_token_map):
    global ser
    while True:
        get_msg_event.wait()  # Wait until GET_MSG is received
        get_msg_event.clear()  # Reset the event so it waits again next time

        for mac, token in mac_token_map.items():
            try:
                url = f"https://demo.thingsboard.io/api/v1/{token}/attributes?clientKeys=&sharedKeys="
                response = requests.get(url)
                data = response.json()
                shared = data.get("shared", {})

                if mac not in last_shared_values:
                    last_shared_values[mac] = {}

                for key, value in shared.items():
                    if key.lower() in {"battery", "id", "mac_address", "data"}:
                        continue

                    # Calendar
                    if isinstance(value, str) and value.startswith("Start:"):
                        try:
                            start = value.split("\n")[0].split("Start:")[1].strip()
                            end = value.split("\n")[1].split("End:")[1].strip()

                            start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M")
                            end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M")
                            now = datetime.now()

                            if end_dt < now:
                                # Delete expired event
                                del_url = f"https://demo.thingsboard.io/api/plugins/telemetry/DEVICE/{UUID}/SHARED_SCOPE?keys={key}"
                                headers = {"X-Authorization": f"Bearer {JWT}"}
                                requests.delete(del_url, headers=headers)
                                print(f"🗑 Deleted expired event {key}")
                                continue

                            elif start_dt <= now <= end_dt and last_shared_values[mac].get(key) != value:
                                msg = f"calendar:{key}:Start={int(start_dt.timestamp())}:End={int(end_dt.timestamp())}\n"
                                ser.write(msg.encode())
                                print(f"📅 Sent calendar: {msg.strip()}")
                                last_shared_values[mac][key] = value

                        except Exception as e:
                            print(f"⚠ Calendar parse error: {e}")

                    elif isinstance(value, str):
                        if last_shared_values[mac].get(key) != value:
                            dev_number = list(mac_token_map.keys()).index(mac) + 1
                            dev_name = f"lora_{dev_number}"
                            line = f"{dev_name}:{value}\n"
                            ser.write(line.encode())
                            print(f"📡 Sent msg: {line.strip()}")
                            last_shared_values[mac][key] = value

                            del_url = f"{TB_HOST}/api/plugins/telemetry/DEVICE/{UUID}/SHARED_SCOPE?keys={key}"
                            headers = {"X-Authorization": f"Bearer {JWT}"}
                            requests.delete(del_url, headers=headers)
                            print(f"🗑 Deleted one-time message key '{key}' after use")

            except Exception as e:
                print(f"❌ Polling error: {e}")

if __name__ == "__main__":
    login()
    mac_token_map = load_map()

    with serial.Serial(SERIAL_PORT, BAUD, timeout=1) as s:
        ser = s  # Assign to global

        # Start background threads
        threading.Thread(target=check_for_extra_fields, args=(mac_token_map,), daemon=True).start()
        # threading.Thread(target=sync_time_loop, daemon=True).start()

        buffer = {"mac": None, "id": None, "battery": None}

        while True:
            try:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode(errors='ignore').strip()

                # 👇 New behavior: Trigger event when "GET_MSG" is received
                if line.strip() == "GET_MSG":
                    print("🔄 Received GET_MSG, triggering attribute check...")
                    get_msg_event.set()
                     # After sending all messages/events
                    unix_time = int(datetime.now().timestamp())
                    ser.write(f"time:{unix_time}\n".encode())
                    continue

                # Existing data parsing
                if line.startswith("MAC:"):
                    buffer["mac"] = line.split("MAC:", 1)[1].strip()
                elif line.startswith("ID:"):
                    buffer["id"] = line.split("ID:", 1)[1].strip()
                elif line.startswith("Battery:"):
                    buffer["battery"] = line.split("Battery:", 1)[1].strip().replace("%", "")
                else:
                    continue  # Ignore unrelated lines

                # Once complete, send to ThingsBoard
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
