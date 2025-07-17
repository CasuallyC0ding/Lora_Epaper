#include <SPI.h>
#include <LoRa.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

// LoRa
#define LORA_FREQ 868E6
#define SCK 5
#define MISO 19
#define MOSI 27
#define SS 18
#define RST 14
#define DIO0 26

// OLED
#define SCREEN_WIDTH 128
#define SCREEN_HEIGHT 64
#define OLED_RESET -1
Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET);

// States
enum ReceiverState {
  WAITING_FOR_SYN,
  ACK_SENT,
  WAITING_FOR_DATA,
  DATA_RECEIVED,
  INVALID_MESSAGE
};

ReceiverState state = WAITING_FOR_SYN;

void updateDisplay(const String& msg) {
  display.clearDisplay();
  display.setCursor(0, 0);
  display.println("Receiver Active");
  display.println("State:");
  display.println(msg);
  display.display();
}

void setup() {
  Serial.begin(115200);

  // OLED init
  if (!display.begin(SSD1306_SWITCHCAPVCC, 0x3C)) {
    while (1);  // Freeze if OLED fails
  }
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  updateDisplay("Starting...");

  // LoRa init
  SPI.begin(SCK, MISO, MOSI, SS);
  LoRa.setPins(SS, RST, DIO0);
  if (!LoRa.begin(LORA_FREQ)) {
    updateDisplay("LoRa Init Failed");
    while (1);  // Freeze if LoRa fails
  }

  updateDisplay("Waiting for SYN...");
}

void loop() {
  int packetSize = LoRa.parsePacket();
  if (packetSize) {
    String incoming = "";
    while (LoRa.available()) {
      incoming += (char)LoRa.read();
    }
    incoming.trim();
    Serial.println("Received: " + incoming);

    if (incoming.startsWith("SYN:")) {
      String mac = incoming.substring(4);
      Serial.println("SYN from MAC: " + mac);
      updateDisplay("SYN Received");

      // Send ACK
      LoRa.beginPacket();
      LoRa.print("ACK");
      LoRa.endPacket();
      Serial.println("ACK sent");
      updateDisplay("ACK Sent");

      // Wait up to 3 seconds for DATA
      unsigned long startWait = millis();
      while (millis() - startWait < 3000) {
        int size = LoRa.parsePacket();
        if (size) {
          String dataMsg = "";
          while (LoRa.available()) {
            dataMsg += (char)LoRa.read();
          }
          dataMsg.trim();

          Serial.println("Received (after ACK): " + dataMsg);

          if (dataMsg.startsWith("DATA:")) {
            int firstColon = dataMsg.indexOf(":");
            int secondColon = dataMsg.indexOf(":", firstColon + 1);
            int lastColon = dataMsg.lastIndexOf(":");

            if (firstColon != -1 && secondColon != -1 && lastColon > secondColon) {
              String macPart = dataMsg.substring(firstColon + 1, lastColon);
              String payload = dataMsg.substring(lastColon + 1);

              String formatted = "MAC=" + macPart + ";DATA=" + payload;
              Serial.println("Final Data: " + formatted);
              updateDisplay("Data Received");
            } else {
              Serial.println("Malformed DATA message");
              updateDisplay("Parse Error");
            }

            delay(1500);
            updateDisplay("Waiting for SYN...");
            return;
          }
        }
        delay(100);
      }

      Serial.println("No DATA received after ACK");
      updateDisplay("No DATA received");
      delay(1500);
      updateDisplay("Waiting for SYN...");
      return;
    }

    else if (incoming.startsWith("DATA:")) {
      updateDisplay("Data Received");

      int firstColon = incoming.indexOf(":");
      int secondColon = incoming.indexOf(":", firstColon + 1);
      int lastColon = incoming.lastIndexOf(":");

      if (firstColon != -1 && secondColon != -1 && lastColon > secondColon) {
        String mac = incoming.substring(firstColon + 1, lastColon);
        String payload = incoming.substring(lastColon + 1);

        String formatted = "MAC=" + mac + ";DATA=" + payload;
        Serial.println("Final Data: " + formatted);
        updateDisplay("Sent to Serial");
      } else {
        updateDisplay("Parse Error");
        Serial.println("Malformed DATA message");
      }

      delay(1500);
      updateDisplay("Waiting for SYN...");
      state = WAITING_FOR_SYN;
    }

    else {
      updateDisplay("Invalid Message");
      Serial.println("Invalid message received");
      delay(1500);
      updateDisplay("Waiting for SYN...");
      state = WAITING_FOR_SYN;
    }
  }
}
