#include <WiFi.h>
#include <WebServer.h>
#include <SPI.h>
#include <LoRa.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Preferences.h>

// WiFi AP for payload entry
const char* ssid = "ESP32_Config";
const char* password = "12345678";

// LoRa pins & freq
#define LORA_FREQ 868E6
#define SCK 5
#define MISO 19
#define MOSI 27
#define SS   18
#define RST  14
#define DIO0 26

// OLED
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET    -1
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// Battery pin
#define BATTERY_PIN 35

// Web server
WebServer server(80);

// State
Preferences preferences;
String deviceID = "";
unsigned long startTime;
bool messageSent = false;
bool wifiActive = true;
unsigned long wifiDuration;
const unsigned long wifiFirstBootDuration = 20000; // 20s
const unsigned long wifiResetBootDuration = 25000; // 25s

float voltageToPercent(float v) {
  if (v >= 4.20) return 100.0;
  else if (v >= 4.10) return 90.0 + (v - 4.10)*100.0;
  else if (v >= 4.00) return 80.0 + (v - 4.00)*100.0;
  else if (v >= 3.90) return 70.0 + (v - 3.90)*100.0;
  else if (v >= 3.80) return 60.0 + (v - 3.80)*100.0;
  else if (v >= 3.75) return 50.0 + (v - 3.75)*200.0;
  else if (v >= 3.70) return 40.0 + (v - 3.70)*200.0;
  else if (v >= 3.65) return 30.0 + (v - 3.65)*200.0;
  else if (v >= 3.60) return 20.0 + (v - 3.60)*200.0;
  else if (v >= 3.50) return 10.0 + (v - 3.50)*100.0;
  else if (v >= 3.40) return (v - 3.40)*100.0;
  else return 0.0;
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
  display.setCursor(0,0);
  display.println("Sleeping in 10s...");
  display.display();
  delay(10000);
  display.ssd1306_command(SSD1306_DISPLAYOFF);
  esp_deep_sleep_start();
}

void setup() {
  Serial.begin(115200);
  analogReadResolution(12);

  // Determine boot cause
  bool isFirstBoot = esp_reset_reason() == ESP_RST_POWERON;
  wifiDuration = isFirstBoot ? wifiFirstBootDuration : wifiResetBootDuration;

  // OLED init
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    while (1);
  }
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);

  // Preferences: load saved ID
  preferences.begin("storage", false);
  deviceID = preferences.getString("deviceID", "");

  // Start AP for ID entry
  WiFi.softAP(ssid, password);
  server.on("/", handleRoot);
  server.on("/set", handleSet);
  server.begin();

  display.clearDisplay();
  display.setCursor(0,0);
  display.println("AP Mode:");
  display.println(ssid);
  display.println("IP:");
  display.println(WiFi.softAPIP());
  display.display();

  // Grab SoftAP MAC *while AP is active* and save it
  String mac = WiFi.softAPmacAddress();
  preferences.putString("mac", mac);

  startTime = millis();
}

void loop() {
  unsigned long now = millis();
  float batt = voltageToPercent(((float)analogRead(BATTERY_PIN)/4095.0)*3.3*2);

  if (wifiActive) {
    server.handleClient();
    if (now - startTime > wifiDuration) {
      server.stop();
      WiFi.softAPdisconnect(true);
      wifiActive = false;

      // Init LoRa
      SPI.begin(SCK, MISO, MOSI, SS);
      LoRa.setPins(SS, RST, DIO0);
      if (!LoRa.begin(LORA_FREQ)) {
        display.clearDisplay();
        display.setCursor(0,0);
        display.println("LoRa init failed");
        display.display();
        while (1);
      }

      display.clearDisplay();
      display.setCursor(0,0);
      display.println("LoRa Mode");
      display.display();
    }
    return;
  }

  // LoRa Mode
  display.clearDisplay();
  display.setCursor(0,0);
  display.println("Battery: " + String(batt,1) + "%");
  display.display();

  if (!messageSent && deviceID != "") {
    // 1) Send SYN:<MAC>
    String mac = preferences.getString("mac", "00:00:00:0000:00");
    String syn = "SYN:" + mac;
    LoRa.beginPacket();
    LoRa.print(syn);
    LoRa.endPacket();
    Serial.println("SYN sent: " + syn);
    display.println("SYN sent");
    display.display();

    // 2) Wait for ACK (3s)
    bool gotACK = false;
    unsigned long t0 = millis();
    while (millis() - t0 < 3000) {
      int sz = LoRa.parsePacket();
      if (sz) {
        String in;
        while (LoRa.available()) in += (char)LoRa.read();
        in.trim();
        Serial.println("LoRa in: " + in);
        if (in == "ACK") {
          gotACK = true;
          break;
        }
      }
      delay(100);
    }

    if (!gotACK) {
      Serial.println("No ACK, abort");
      display.println("No ACK");
      display.display();
      delay(2000);
      enterDeepSleep();
      return;
    }

    // 3) Send DATA:<MAC>:<ID>
    String data = "DATA:" + mac + ":" + deviceID;
    LoRa.beginPacket();
    LoRa.print(data);
    LoRa.endPacket(true);  // blocking
    delay(100);
    Serial.println("DATA sent: " + data);
    display.println("DATA sent");
    display.display();

    messageSent = true;
    delay(500);
    enterDeepSleep();
  }
  else if (deviceID == "") {
    display.clearDisplay();
    display.setCursor(0,0);
    display.println("No ID set");
    display.display();
    delay(2000);
    enterDeepSleep();
  }
}
