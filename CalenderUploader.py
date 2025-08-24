import tkinter as tk
from tkinterdnd2 import DND_FILES, TkinterDnD
import requests
import json
import datetime
import pickle
import os.path
import threading

from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ----------- CONFIGURATION -----------
THINGSBOARD_URL = "http://demo.thingsboard.io"
TB_USERNAME = "youareaverygoodpersontrustme@gmail.com"
TB_PASSWORD = "Thingsboard"
TB_DEVICE_ID = "8aa29b50-658a-11f0-83dd-65e1b21422bc"
GOOGLE_CREDENTIALS_FILE = "credentials.json"
TOKEN_PICKLE = "token.pickle"
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']
SYNC_INTERVAL_MS = 5 * 60 * 1000  # 5 minutes
# -------------------------------------

def get_jwt_token(username, password):
    try:
        response = requests.post(
            f"{THINGSBOARD_URL}/api/auth/login",
            json={"username": username, "password": password},
            timeout=10
        )
        if response.status_code == 200:
            return response.json()["token"]
        else:
            print(f"Failed to get JWT token: {response.text}")
            return None
    except Exception as e:
        print(f"Error getting token: {e}")
        return None

def upload_events_to_thingsboard(payload):
    jwt = get_jwt_token(TB_USERNAME, TB_PASSWORD)
    if not jwt:
        return

    headers = {
        "Content-Type": "application/json",
        "X-Authorization": f"Bearer {jwt}"
    }
    url = f"{THINGSBOARD_URL}/api/plugins/telemetry/DEVICE/{TB_DEVICE_ID}/SHARED_SCOPE"

    try:
        response = requests.post(url, data=json.dumps(payload), headers=headers, timeout=10)
        if response.status_code == 200:
            print(f"Uploaded {len(payload)} events successfully to ThingsBoard.")
        else:
            print(f"Upload failed: {response.status_code}\n{response.text}")
    except Exception as e:
        print(f"Upload exception: {e}")

def format_datetime(dt_str):
    try:
        dt = datetime.datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return dt_str  # fallback

def fetch_google_calendar_events():
    creds = None
    if os.path.exists(TOKEN_PICKLE):
        with open(TOKEN_PICKLE, 'rb') as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PICKLE, 'wb') as token:
            pickle.dump(creds, token)

    service = build('calendar', 'v3', credentials=creds)
    now = datetime.datetime.utcnow().isoformat() + 'Z'
    events_result = service.events().list(
        calendarId='primary',
        timeMin=now,
        maxResults=20,
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    events = events_result.get('items', [])

    payload = {}
    for event in events:
        if 'start' in event and 'end' in event and 'summary' in event:
            raw_start = event['start'].get('dateTime', event['start'].get('date'))
            raw_end = event['end'].get('dateTime', event['end'].get('date'))
            start = format_datetime(raw_start)
            end = format_datetime(raw_end)
            title = event['summary'].replace(" ", "_").replace(".", "-").replace("$", "-")
            value = f"Start: {start}\nEnd: {end}"
            payload[title] = value

    if payload:
        upload_events_to_thingsboard(payload)

def parse_ics_datetime(dt_str):
    try:
        if 'T' in dt_str:
            dt = datetime.datetime.strptime(dt_str[:15], "%Y%m%dT%H%M%S")
        else:
            dt = datetime.datetime.strptime(dt_str, "%Y%m%d")
        return dt.strftime("%Y-%m-%d %H:%M")
    except:
        return dt_str  # fallback

def schedule_calendar_sync(app_instance):
    threading.Thread(target=fetch_google_calendar_events, daemon=True).start()
    app_instance.after(SYNC_INTERVAL_MS, lambda: schedule_calendar_sync(app_instance))

class App(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        self.title("Calendar Uploader")
        self.geometry("500x200")

        self.label = tk.Label(self, text="Drag and drop an .ics file here", font=("Arial", 12))
        self.label.pack(pady=30)

        self.drop_area = tk.Text(self, height=4, width=40)
        self.drop_area.pack()
        self.drop_area.insert("1.0", "Drop .ics file here")
        self.drop_area.drop_target_register(DND_FILES)
        self.drop_area.dnd_bind("<<Drop>>", self.handle_drop)

        # Start calendar sync loop
        self.after(1000, lambda: schedule_calendar_sync(self))

    def handle_drop(self, event):
        filepath = event.data.strip('{}')
        if filepath.endswith('.ics'):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    lines = f.readlines()

                payload = {}
                current_event = {}
                for line in lines:
                    line = line.strip()
                    if line.startswith("SUMMARY:"):
                        current_event['summary'] = line.split("SUMMARY:")[1]
                    elif line.startswith("DTSTART"):
                        current_event['start'] = line.split(":")[1]
                    elif line.startswith("DTEND"):
                        current_event['end'] = line.split(":")[1]
                    elif line.startswith("END:VEVENT"):
                        if 'summary' in current_event and 'start' in current_event and 'end' in current_event:
                            key = current_event['summary'].replace(" ", "_")
                            start = parse_ics_datetime(current_event['start'])
                            end = parse_ics_datetime(current_event['end'])
                            value = f"Start: {start}\nEnd: {end}"
                            payload[key] = value
                        current_event = {}

                if payload:
                    upload_events_to_thingsboard(payload)
                    self.label.config(text="Upload complete.")
                else:
                    self.label.config(text="No events found.")
            except Exception as e:
                self.label.config(text=f"Error: {e}")
        else:
            self.label.config(text="Only .ics files are supported.")

if __name__ == "__main__":
    app = App()
    app.mainloop()
