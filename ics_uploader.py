import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinterdnd2 import TkinterDnD, DND_FILES
from ics import Calendar
import requests
import json

# --- Configuration ---
# The URL of your ThingsBoard instance.
# Make sure there is no trailing slash.
THINGSBOARD_URL = "http://demo.thingsboard.io"

# --- User Credentials & Device ID ---
# !! IMPORTANT !!
# Replace these placeholder values with your actual credentials and device ID.
TB_USERNAME = "youareaverygoodpersontrustme@gmail.com" # Use your actual ThingsBoard username
TB_PASSWORD = "Thingsboard" # Use your actual ThingsBoard password
TB_DEVICE_ID = "8aa29b50-658a-11f0-83dd-65e1b21422bc" # Use the UUID of your ThingsBoard device


# --- Core Functions ---

def get_jwt_token(username, password):
    """
    Logs into ThingsBoard using a username and password to retrieve a JWT token
    for authorizing subsequent API calls.
    """
    login_url = f"{THINGSBOARD_URL}/api/auth/login"
    credentials = {"username": username, "password": password}
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    
    try:
        # Send the login request
        response = requests.post(login_url, json=credentials, headers=headers, timeout=10)
        # Raise an exception if the login failed (e.g., 401 Unauthorized)
        response.raise_for_status()  
        
        response_data = response.json()
        token = response_data.get("token")
        
        if not token:
            messagebox.showerror("Login Error", "Failed to get JWT token from the response. Please check your credentials.")
            return None
            
        return token
        
    except requests.exceptions.HTTPError as http_err:
        error_message = f"HTTP Error: {http_err}\nResponse: {response.text}"
        messagebox.showerror("Login Failed", error_message)
        return None
    except requests.exceptions.RequestException as req_err:
        messagebox.showerror("Connection Error", f"Could not connect to ThingsBoard: {req_err}")
        return None
    except json.JSONDecodeError:
        error_message = f"Failed to parse login response. The server response was not valid JSON:\n{response.text}"
        messagebox.showerror("Login Error", error_message)
        return None


def upload_ics_to_thingsboard(file_path):
    """
    Parses an .ics file and uploads the events as SHARED attributes
    to a specific device on ThingsBoard using user authentication.
    """
    # Ensure all required constant fields are filled
    if any(val.startswith("YOUR_") for val in [TB_USERNAME, TB_PASSWORD, TB_DEVICE_ID]):
        messagebox.showerror("Configuration Error", "Please update the placeholder values for username, password, and device ID in the script.")
        return

    # 1. Authenticate as a user to get the JWT token
    jwt_token = get_jwt_token(TB_USERNAME, TB_PASSWORD)
    if not jwt_token:
        # The error message is already shown by the get_jwt_token function
        return 

    # 2. Read the .ics file and parse its events
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            cal = Calendar(f.read())

        if not cal.events:
            messagebox.showinfo("No Events", "The selected .ics file does not contain any events.")
            return

        # Prepare the payload for ThingsBoard. Each event becomes an attribute.
        payload = {}
        for event in cal.events:
            # Sanitize the event name to be a valid JSON key for the attribute.
            # Replace characters that might be problematic.
            title = event.name.replace(" ", "_").replace(".", "-").replace("$", "-")
            
            # Ensure start and end times are formatted as strings
            start_time_str = event.begin.to("local").format("YYYY-MM-DD HH:mm")
            end_time_str = event.end.to("local").format("YYYY-MM-DD HH:mm")
            
            # Construct the value as a multi-line string compatible with the other script
            # Ensure description and location are not None, use empty string if they are
            # description = event.description if event.description else ""
            # location = event.location if event.location else ""

            value = (
                f"Start: {start_time_str}\n"
                f"End: {end_time_str}\n"
                # f"Description: {description}\n"
                # f"Location: {location}"
            )
            payload[title] = value

    except Exception as e:
        messagebox.showerror("File Error", f"Failed to read or parse the .ics file:\n{e}")
        return

    # 3. Construct the request and upload attributes to the SHARED_SCOPE
    # This is the correct endpoint for setting server-side/shared attributes
    attributes_url = f"{THINGSBOARD_URL}/api/plugins/telemetry/DEVICE/{TB_DEVICE_ID}/SHARED_SCOPE"
    headers = {
        "Content-Type": "application/json",
        "X-Authorization": f"Bearer {jwt_token}" # Use the JWT for authorization
    }

    try:
        # Note: We are no longer json.dumps(payload) because the values are already strings.
        # However, the overall payload still needs to be JSON encoded.
        response = requests.post(attributes_url, data=json.dumps(payload), headers=headers, timeout=10)
        
        if response.status_code == 200:
            messagebox.showinfo("Success", f"Uploaded {len(payload)} events as shared attributes!\nYou may now close this window.")
        else:
            messagebox.showerror(
                "Upload Error", 
                f"Failed to upload attributes.\nStatus Code: {response.status_code}\nResponse: {response.text}"
            )
            
    except requests.exceptions.RequestException as e:
        messagebox.showerror("Upload Error", f"An error occurred during the upload request:\n{e}")


# --- GUI Application Class ---

class App(TkinterDnD.Tk):
    """Main application window."""
    def __init__(self):
        super().__init__()
        self.title("Upload Calendar to ThingsBoard (Shared Attributes)")
        self.geometry("550x250")
        self.resizable(False, False)
        
        # --- Style ---
        style = ttk.Style(self)
        style.configure("TLabel", font=("Arial", 11))
        style.configure("TButton", font=("Arial", 12))
        
        main_frame = ttk.Frame(self, padding="20")
        main_frame.pack(fill="both", expand=True)
        
        # --- File Upload Frame ---
        upload_frame = ttk.LabelFrame(main_frame, text="Upload .ics File", padding="10")
        upload_frame.pack(fill="both", expand=True)

        self.drop_label = tk.Label(
            upload_frame, 
            text="Drag and drop your .ics file here",
            relief="groove", 
            borderwidth=2, 
            font=("Arial", 12),
            pady=20
        )
        self.drop_label.pack(fill="x", expand=True, pady=10)
        
        ttk.Label(upload_frame, text="or", justify="center").pack(fill="x")

        self.browse_button = ttk.Button(
            upload_frame, 
            text="Select File Manually", 
            command=self.browse_file
        )
        self.browse_button.pack(pady=10)

        # Register the label as a drop target
        self.drop_label.drop_target_register(DND_FILES)
        self.drop_label.dnd_bind('<<Drop>>', self.on_drop)

    def browse_file(self):
        """Opens a file dialog to select an .ics file."""
        file_path = filedialog.askopenfilename(filetypes=[("ICS files", "*.ics")])
        if file_path:
            upload_ics_to_thingsboard(file_path)

    def on_drop(self, event):
        """Handles the file drop event."""
        # tkinterdnd2 can wrap paths in curly braces, so we use splitlist
        file_path = self.tk.splitlist(event.data)[0]
        if file_path.lower().endswith(".ics"):
            upload_ics_to_thingsboard(file_path)
        else:
            messagebox.showerror("Invalid File", "Please drop a .ics calendar file.")

# --- Main Execution ---
if __name__ == "__main__":
    app = App()
    app.mainloop()