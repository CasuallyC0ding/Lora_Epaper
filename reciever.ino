#include <SPI.h>
#include <LoRa.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <vector>
#include <EEPROM.h>

#define WIFI_SSID "YourWiFiSSID"
#define WIFI_PASS "YourWiFiPassword"
#define OTA_CHECK_INTERVAL_HOURS 6
#define OTA_URL "https://raw.githubusercontent.com/CasuallyC0ding/Lora_Epaper/main/receiver.ino.bin"

#define LORA_FREQ 868E6
#define SCK 5
#define MISO 19
#define MOSI 27
#define SS 18
#define RST 14
#define DIO0 26
#define EEPROM_SIZE 256
#define MSG_ADDR 0

#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);
bool loraWindowActive = false;
unsigned long windowStartTime = 0;
int broadcastCount = 0;
unsigned long lastBroadcastTime = 0;


enum ReceiverState {
  WAITING_FOR_SYN,
  ACK_SENT,
  WAITING_FOR_DATA,
  DATA_RECEIVED,
  PARSE_ERROR
};

ReceiverState state = WAITING_FOR_SYN;
unsigned long lastUpdateCheck = 0;

struct Message {
  String sender;
  String text;
  unsigned long timestamp;
};

struct CalendarEvent {
  String label;
  unsigned long start;
  unsigned long end;
};

std::vector<Message> messages;
std::vector<CalendarEvent> calendarEvents;

const unsigned long MESSAGE_LIFETIME = 15000;  // 15 sec
unsigned long currentUnixTime = 0; // From server
// remember the MAC from the last SYN so we can forward it on DATA
static String lastMac = "";


void updateDisplayWithState(const String &msg) {
  display.clearDisplay();
  display.setCursor(0, 0);
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.println("Receiver Active");
  display.println("State:");
  display.println(msg);
  display.display();
}
void updateDisplayWithMessages() {
  display.clearDisplay();
  display.setCursor(0, 0);
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);

  int lines = 0;

  for (auto &e : calendarEvents) {
    if (currentUnixTime >= e.start && currentUnixTime <= e.end) {
      time_t localEndTime = e.end;
      struct tm *timeinfo = localtime(&localEndTime);
      char timeStr[6];
      snprintf(timeStr, sizeof(timeStr), "%02d:%02d", timeinfo->tm_hour, timeinfo->tm_min);
      display.println(e.label + " ends: " + String(timeStr));
      lines++;
      if (lines >= 6) break;
    }
  }

  for (int i = 0; i < messages.size(); i++) {
    if (lines >= 6) break;
    display.println(messages[i].sender + ": " + messages[i].text);
    lines++;
  }

  display.display();

  // 💾 Save the topmost message to EEPROM with a 15s lifetime
  if (!messages.empty()) {
    String msg = messages[0].sender + ": " + messages[0].text;
    char msgBuffer[101];
    msg.substring(0, 100).toCharArray(msgBuffer, 101);
    uint32_t start = currentUnixTime;
    uint32_t end = start + 15;

    EEPROM.put(0, start);
    EEPROM.put(4, end);
    EEPROM.put(8, msgBuffer);
    EEPROM.commit();
  }
}


void pruneOldMessages() {
  unsigned long now = millis();
  messages.erase(
    std::remove_if(messages.begin(), messages.end(),
                   [now](const Message &msg) {
                     return now - msg.timestamp > MESSAGE_LIFETIME;
                   }),
    messages.end());
}

void pruneExpiredCalendarEvents() {
  calendarEvents.erase(
    std::remove_if(calendarEvents.begin(), calendarEvents.end(),
                   [](const CalendarEvent &e) {
                     return e.end < time(nullptr);
                   }),
    calendarEvents.end());
}


void handleSerialInput() {
  if (Serial.available()) {
    String incoming = Serial.readStringUntil('\n');
    incoming.trim();

    // Time sync
    if (incoming.startsWith("time:")) {
      currentUnixTime = incoming.substring(5).toInt();
      return;
    }

    // Calendar format: calendar:Lecture:Start=1721722800:End=1721726400
    if (incoming.startsWith("calendar:")) {
      int labelStart = 9;
      int labelEnd = incoming.indexOf(':', labelStart);
      String label = incoming.substring(labelStart, labelEnd);
      int sIdx = incoming.indexOf("Start=") + 6;
      int eIdx = incoming.indexOf("End=") + 4;

      unsigned long startT = incoming.substring(sIdx, incoming.indexOf(':', sIdx)).toInt();
      unsigned long endT = incoming.substring(eIdx).toInt();

      calendarEvents.push_back({ label, startT, endT });
      return;
    }

    int colon = incoming.indexOf(':');
    if (colon > 0) {
      String sender = incoming.substring(0, colon);
      String msg = incoming.substring(colon + 1);
      messages.push_back({sender, msg, millis()});
    }

    pruneOldMessages();
    pruneExpiredCalendarEvents();
    updateDisplayWithMessages();
  }
}

void handleLoRa() {
  int packetSize = LoRa.parsePacket();
  if (packetSize) {
    String incoming = "";
    while (LoRa.available()) incoming += (char)LoRa.read();
    incoming.trim();

    if (incoming.startsWith("SYN:")) {
      String mac = incoming.substring(4);
      lastMac = mac; // ⬅ Store MAC for later DATA
      updateDisplayWithState("SYN Received");
      state = ACK_SENT;

      delay(100);
      LoRa.beginPacket();
      LoRa.print("ACK");
      LoRa.endPacket();
      updateDisplayWithState("ACK Sent");

      delay(200);
      LoRa.receive();
      state = WAITING_FOR_DATA;
      return;
    }

    if (incoming.startsWith("DATA:")) {
      incoming = incoming.substring(5);
    }

    int colons = 0;
    for (char c : incoming) if (c == ':') colons++;

    if (colons == 7) {
      int indices[8], count = 0;
      for (int i = 0; i < incoming.length(); i++)
        if (incoming.charAt(i) == ':') indices[count++] = i;

      String deviceID = incoming.substring(indices[5] + 1, indices[6]);
      String battStr = incoming.substring(indices[6] + 1);

      // 🟢 Forward readings to Python over Serial
      Serial.println("MAC:" + lastMac);
      Serial.println("ID:" + deviceID);
      Serial.println("Battery:" + battStr);

      messages.push_back({ "ID " + deviceID, "Battery " + battStr + "%", millis() });

      state = DATA_RECEIVED;
      delay(2000);
      updateDisplayWithState("Waiting for SYN...");
      state = WAITING_FOR_SYN;
    } else {
      updateDisplayWithState("Parse Error");
      state = PARSE_ERROR;
      delay(1500);
      updateDisplayWithState("Waiting for SYN...");
      state = WAITING_FOR_SYN;
    }

    LoRa.receive();
  }
}

void sendGetMsgAndStartWindow() {
  Serial.println("GET_MSG");
  delay(100); // Let it send

  // 🟢 Start 30s LoRa active window
  loraWindowActive = true;
  windowStartTime = millis();
  broadcastCount = 0;
  lastBroadcastTime = 0;

  LoRa.receive();
  updateDisplayWithState("Listening (30s)");
}

void setup() {
  Serial.begin(115200);

  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) while (1);
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  updateDisplayWithState("Waking Up...");

  SPI.begin(SCK, MISO, MOSI, SS);
  LoRa.setPins(SS, RST, DIO0);
  if (!LoRa.begin(LORA_FREQ)) {
    updateDisplayWithState("LoRa Init Failed");
    while (1);
  }

  sendGetMsgAndStartWindow();
}

void loop() {
  handleSerialInput();
  handleLoRa();

  // 🔄 Request new messages from server every 2 seconds
  static unsigned long lastGet = 0;
  if (millis() - lastGet >= 2000) { // 2000 ms = 2 seconds
    Serial.println("GET_MSG");
    lastGet = millis();
  }

  // ⏱ Time and message cleanup every 1 second
  static unsigned long lastSync = 0;
  if (millis() - lastSync >= 1000) {
    lastSync = millis();
    currentUnixTime++;
    pruneOldMessages();
    pruneExpiredCalendarEvents();
    updateDisplayWithMessages();
  }

  // 📡 Keep LoRa receiver always active
  if (!loraWindowActive) {
    loraWindowActive = true;
    LoRa.receive();
  }

  // 💤 Removed deep sleep logic so device stays awake
  // esp_sleep_enable_timer_wakeup(20 * 1000000ULL);
  // esp_deep_sleep_start();
}