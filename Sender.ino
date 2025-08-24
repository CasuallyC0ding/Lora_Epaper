#include <WiFi.h>
#include <WebServer.h>
#include <SPI.h>
#include <LoRa.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Preferences.h>
#include <HTTPClient.h>
#include <Update.h>

// WiFi AP for setting device ID
const char* ssid = "ESP32_Config";
const char* password = "12345678";

// OTA CONFIG
const char* ota_url = "https://raw.githubusercontent.com/your_username/your_repo/main/sender.ino.bin";  // UPDATE THIS
const unsigned long otaCheckInterval = 6UL * 60UL * 60UL * 1000UL;
unsigned long lastOTACheck = 0;

// LoRa settings
#define LORA_FREQ 868E6
#define SCK 5
#define MISO 19
#define MOSI 27
#define SS   18
#define RST  14
#define DIO0 26

// OLED display
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// Battery
#define BATTERY_PIN 35

// Web server
WebServer server(80);
Preferences preferences;

String deviceID = "";
bool wifiActive = true;
bool messageSent = false;
unsigned long startTime;
unsigned long wifiDuration;
const unsigned long wifiFirstBootDuration = 20000;
const unsigned long wifiResetBootDuration = 25000;

float voltageToPercent(float v) {
  if (v >= 4.2) return 100;
  if (v >= 4.1) return 90 + (v - 4.1) * 100;
  if (v >= 4.0) return 80 + (v - 4.0) * 100;
  if (v >= 3.9) return 70 + (v - 3.9) * 100;
  if (v >= 3.8) return 60 + (v - 3.8) * 100;
  if (v >= 3.75) return 50 + (v - 3.75) * 200;
  if (v >= 3.7) return 40 + (v - 3.7) * 200;
  if (v >= 3.65) return 30 + (v - 3.65) * 200;
  if (v >= 3.6) return 20 + (v - 3.6) * 200;
  if (v >= 3.5) return 10 + (v - 3.5) * 100;
  if (v >= 3.4) return (v - 3.4) * 100;
  return 0;
}

void handleRoot() {
  String html = "<h1>Enter Device ID</h1><form action=\"/set\" method=\"get\">";
  html += "ID: <input name=\"id\" type=\"text\"><input type=\"submit\" value=\"Save\"></form>";
  server.send(200, "text/html", html);
}

void handleSet() {
  if (server.hasArg("id")) {
    deviceID = server.arg("id");
    preferences.putString("deviceID", deviceID);
    server.send(200, "text/html", "<h2>ID Saved: " + deviceID + "</h2><p>You can close this.</p>");
  } else {
    server.send(200, "text/html", "<h2>No ID received</h2>");
  }
}

void enterDeepSleep() {
  display.clearDisplay();
  display.setCursor(0, 0);
  display.println("Sleeping...");
  display.display();
  delay(3000);
  display.ssd1306_command(SSD1306_DISPLAYOFF);
  esp_deep_sleep_start();
}

void checkOTAUpdate() {
  WiFiClient client;
  HTTPClient http;
  http.begin(client, ota_url);
  int httpCode = http.GET();

  if (httpCode == 200) {
    int len = http.getSize();
    bool canBegin = Update.begin(len);
    if (canBegin) {
      WiFiClient& stream = http.getStream();
      size_t written = Update.writeStream(stream);
      if (written == len && Update.end(true)) {
        ESP.restart();
      }
    }
  }
  http.end();
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);

  bool isFirstBoot = esp_reset_reason() == ESP_RST_POWERON;
  wifiDuration = isFirstBoot ? wifiFirstBootDuration : wifiResetBootDuration;

  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) while (1);
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);

  preferences.begin("storage", false);
  deviceID = preferences.getString("deviceID", "");

  WiFi.softAP(ssid, password);
  server.on("/", handleRoot);
  server.on("/set", handleSet);
  server.begin();

  display.setCursor(0, 0);
  display.println("AP Mode:");
  display.println(ssid);
  display.println(WiFi.softAPIP());
  display.display();

  String mac = WiFi.softAPmacAddress();
  preferences.putString("mac", mac);
  startTime = millis();
}

void loop() {
  unsigned long now = millis();
  float battV = ((float)analogRead(BATTERY_PIN) / 4095.0) * 3.3 * 2.0;
  uint8_t battPercent = (uint8_t)voltageToPercent(battV);

  if (wifiActive) {
    server.handleClient();
    if (millis() - lastOTACheck > otaCheckInterval) {
      checkOTAUpdate();
      lastOTACheck = millis();
    }
    if (now - startTime > wifiDuration) {
      server.stop();
      WiFi.softAPdisconnect(true);
      wifiActive = false;

      SPI.begin(SCK, MISO, MOSI, SS);
      LoRa.setPins(SS, RST, DIO0);
      if (!LoRa.begin(LORA_FREQ)) {
        display.clearDisplay();
        display.setCursor(0, 0);
        display.println("LoRa init failed");
        display.display();
        while (1);
      }

      display.clearDisplay();
      display.setCursor(0, 0);
      display.println("LoRa Ready");
      display.display();
    }
    return;
  }

  display.clearDisplay();
  display.setCursor(0, 0);
  display.printf("Battery: %d%%\n", battPercent);
  display.display();

  if (deviceID == "") {
    display.println("No ID set!");
    display.display();
    delay(2000);
    enterDeepSleep();
  }

  if (!messageSent) {
    String mac = preferences.getString("mac", "00:00:00:00:00:00");

    int retries = 0;
    bool gotACK = false;
    while (retries < 5 && !gotACK) {
      String syn = "SYN:" + mac;
      LoRa.beginPacket();
      LoRa.print(syn);
      LoRa.endPacket();
      LoRa.receive();
      Serial.println("Sent SYN: " + syn);
      display.println("Sent SYN");
      display.display();

      unsigned long t0 = millis();
      while (millis() - t0 < 3000) {
        if (LoRa.parsePacket()) {
          String in = "";
          while (LoRa.available()) in += (char)LoRa.read();
          in.trim();
          if (in == "ACK") {
            gotACK = true;
            break;
          }
        }
        delay(100);
      }

      if (!gotACK) {
        retries++;
        display.println("No ACK, retrying...");
        display.display();
        delay(500 * pow(2, retries));  // Exponential backoff
      }
    }

    if (!gotACK) {
      display.println("Giving up");
      display.display();
      delay(2000);
      enterDeepSleep();
    }
    // Give receiver time to prepare
    delay(500);  // <-- NEW DELAY
    // Send plain text: <MAC>:<ID>:<battery_percentage>
    String data = mac + ":" + deviceID + ":" + String(battPercent);
    LoRa.beginPacket();
    LoRa.print(data);
    LoRa.endPacket(true);
    Serial.println("Sent DATA: " + data);
    display.println("Sent DATA");
    display.display();

    messageSent = true;
    delay(1000);
    enterDeepSleep();
  }
}
