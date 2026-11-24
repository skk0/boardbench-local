// Optional diagnostic sketch for ESP32 Arduino. Commands only run on explicit serial requests.
#include <Arduino.h>
#include <WiFi.h>
#include <Wire.h>
#include <esp_heap_caps.h>
#include <soc/soc_caps.h>

String input;
bool overflow = false;

void memoryTest(bool external) {
  const size_t size = 16 * 1024;
  uint32_t caps = MALLOC_CAP_8BIT | (external ? MALLOC_CAP_SPIRAM : MALLOC_CAP_INTERNAL);
  volatile uint8_t *buffer = (uint8_t *)heap_caps_malloc(size, caps);
  if (!buffer) {
    Serial.println("ERR cannot allocate 16 KiB; verify PSRAM build option and hardware");
    return;
  }
  bool ok = true;
  for (int pattern : {0x55, 0xaa}) {
    for (size_t i = 0; i < size; i++) buffer[i] = uint8_t(i ^ pattern);
    for (size_t i = 0; i < size; i++) {
      if (buffer[i] != uint8_t(i ^ pattern)) { ok = false; break; }
    }
    if (!ok) break;
  }
  heap_caps_free((void *)buffer);
  Serial.printf("%s %s bytes=%u\n", ok ? "OK" : "ERR", external ? "PSRAM" : "SRAM", unsigned(size));
}

void wifiScan() {
  WiFi.mode(WIFI_STA);
  int n = WiFi.scanNetworks(false, true);
  if (n < 0) Serial.printf("ERR WiFi scan code=%d\n", n);
  else {
    Serial.printf("OK WIFI networks=%d", n);
    if (n) Serial.printf(" first_rssi=%d", WiFi.RSSI(0));
    Serial.println();
  }
  WiFi.scanDelete();
  WiFi.mode(WIFI_OFF);
}

void scanBus(int sda, int scl) {
  if (sda == scl || sda < 0 || scl < 0 || sda >= SOC_GPIO_PIN_COUNT || scl >= SOC_GPIO_PIN_COUNT) {
    Serial.println("ERR invalid SDA/SCL");
    return;
  }
  if (!Wire.begin(sda, scl, 100000)) { Serial.println("ERR I2C init"); return; }
  Wire.setTimeOut(30);
  unsigned count = 0;
  for (int address = 8; address <= 0x77; address++) {
    Wire.beginTransmission(address);
    if (Wire.endTransmission() == 0) { Serial.printf("I2C ACK 0x%02X\n", address); count++; }
  }
  Wire.end();
  Serial.printf("%s I2C devices=%u\n", count ? "OK" : "ERR", count);
}

void dispatch(const String &line) {
  if (line == "PING") Serial.println("OK PONG");
  else if (line == "INFO") Serial.printf("OK INFO model=%s revision=%u cores=%u flash=%u psram=%u heap=%u\n",
    ESP.getChipModel(), ESP.getChipRevision(), ESP.getChipCores(), ESP.getFlashChipSize(), ESP.getPsramSize(), ESP.getFreeHeap());
  else if (line == "SRAM") memoryTest(false);
  else if (line == "PSRAM") memoryTest(true);
  else if (line == "WIFI") wifiScan();
  else if (line == "SAMPLE") {
    for (int i = 0; i < 30; i++) {
      Serial.printf("{\"heap\":%u,\"uptime_ms\":%lu}\n", ESP.getFreeHeap(), millis());
      delay(100);
    }
    Serial.println("OK SAMPLE 30");
  } else {
    int sda, scl;
    if (sscanf(line.c_str(), "I2C %d %d", &sda, &scl) == 2) scanBus(sda, scl);
    else Serial.println("ERR available: PING INFO SRAM PSRAM WIFI SAMPLE I2C <sda> <scl>");
  }
}

void setup() {
  Serial.begin(115200);
  input.reserve(96);
  Serial.println("BoardBenchProbe ready");
}
void loop() {
  while (Serial.available()) {
    char c = char(Serial.read());
    if (c == '\r' || c == '\n') {
      if (overflow) Serial.println("ERR line too long");
      else if (input.length()) dispatch(input);
      overflow = false;
      input = "";
    } else if (input.length() < 96) input += c;
    else overflow = true;
  }
  delay(1);
}
