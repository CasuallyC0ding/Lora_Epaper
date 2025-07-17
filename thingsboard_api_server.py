import serial, json, requests, os, time

SERIAL_PORT = "COM7"
BAUD = 115200
TB_HOST = "http://demo.thingsboard.io"
TB_USER = "youareaverygoodpersontrustme@gmail.com"
TB_PASS = "Thingsboard"
JWT = None

DEVICE_MAP = {}  # MAC → device_id
COUNTER_FILE = "device_counter.txt"

def login():
    global JWT
    resp = requests.post(f"{TB_HOST}/api/auth/login", json={"username": TB_USER, "password": TB_PASS})
    JWT = resp.json()["token"]

def load_device_counter():
    if os.path.exists(COUNTER_FILE):
        with open(COUNTER_FILE, "r") as f:
            return int(f.read().strip())
    return 1

def save_device_counter(counter):
    with open(COUNTER_FILE, "w") as f:
        f.write(str(counter))

def get_or_create_device(mac):
    if mac in DEVICE_MAP:
        return DEVICE_MAP[mac]

    hdr = {"X-Authorization": f"Bearer {JWT}"}
    # Try to find existing device by MAC
    resp = requests.get(f"{TB_HOST}/api/tenant/devices?deviceName={mac}", headers=hdr)
    items = resp.json().get("data", [])
    if items:
        device_id = items[0]["id"]["id"]
        DEVICE_MAP[mac] = device_id
        return device_id

    # New device
    counter = load_device_counter()
    device_name = f"lora_{counter}"
    resp = requests.post(f"{TB_HOST}/api/device", headers=hdr, json={"name": device_name})
    device_id = resp.json()["id"]["id"]

    create_dashboard(device_id, device_name)
    DEVICE_MAP[mac] = device_id

    save_device_counter(counter + 1)
    return device_id

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
                "id_display": {
                    "type": "latest",
                    "title": "Device ID",
                    "datasources": [{
                        "type": "device",
                        "name": "${entityName}",
                        "dataKeys": [{"name": "ID", "type": "attribute"}]
                    }],
                    "settings": {},
                    "sizeX": 4, "sizeY": 2, "row": 4, "col": 0
                }
            },
            "layout": {}
        },
        "assignToCustomer": False
    }
    resp = requests.post(f"{TB_HOST}/api/dashboard", headers=hdr, json=db)
    did = resp.json()["id"]["id"]
    requests.post(f"{TB_HOST}/api/dashboard/{did}/assignToEntity?entityId={device_id}", headers=hdr)

def get_token(dev_id):
    hdr = {"X-Authorization": f"Bearer {JWT}"}
    return requests.get(f"{TB_HOST}/api/device/{dev_id}/credentials", headers=hdr).json()["credentialsId"]

def send_telemetry(token, value):
    requests.post(f"{TB_HOST}/api/v1/{token}/telemetry", json={"data": float(value)})

if __name__ == "__main__":
    login()
    with serial.Serial(SERIAL_PORT, BAUD, timeout=1) as ser:
        while True:
            line = ser.readline().decode().strip()
            if not line:
                continue

            if "MAC=" in line and "DATA=" in line:
                try:
                    mac = line.split("MAC=")[1].split(";")[0].strip()
                    payload = line.split("DATA=")[1].strip()
                    dev_id = get_or_create_device(mac)
                    token = get_token(dev_id)
                    send_telemetry(token, payload)
                    print(f"Uploaded data={payload} for MAC {mac}")
                except Exception as e:
                    print("Error handling line:", line)
                    print("Exception:", e)
            else:
                print("Ignored line:", line)
