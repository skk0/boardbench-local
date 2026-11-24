#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <Wire.h>
#include <esp_heap_caps.h>
#include <esp_bt.h>

// All field names are constants and all values are produced locally by the sketch.
static void event(const char *part, const char *status, const char *format, ...) {
  char value[196];
  va_list args;
  va_start(args,format);
  vsnprintf(value,sizeof(value),format,args);
  va_end(args);
  Serial.printf("{\"bb\":1,\"part\":\"%s\",\"status\":\"%s\",\"value\":\"%s\"}\n",part,status,value);
}

#ifdef BOARDBENCH_ATK_BOX1
static uint8_t imuAddress;
static bool boxBus;

static bool readRegs(uint8_t addr,uint8_t reg,uint8_t *buf,size_t n) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(false)!=0 || Wire.requestFrom(addr,n)!=n) return false;
  for (size_t i=0;i<n;i++) buf[i]=Wire.read();
  return true;
}

static bool writeReg(uint8_t addr,uint8_t reg,uint8_t value) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission()==0;
}

static bool ack(uint8_t addr) {
  Wire.beginTransmission(addr);
  return Wire.endTransmission()==0;
}

static void setupBox() {
  // This build is exclusively for the original ATK-DNESP32S3B BOX.
  boxBus=Wire.begin(48,45,100000);
  Wire.setTimeOut(25);
  pinMode(0,INPUT_PULLUP);
  if (!boxBus) return;
  for (uint8_t addr : {0x6a,0x6b}) {
    uint8_t who;
    if (readRegs(addr,0,&who,1) && who==5) {
      // QMI8658A: little-endian auto-increment, +/-2 g, +/-256 dps.
      if (writeReg(addr,8,0) && writeReg(addr,2,0x40) && writeReg(addr,3,0x06) &&
          writeReg(addr,4,0x46) && writeReg(addr,8,3)) imuAddress=addr;
      break;
    }
  }
}

static void keys(bool force) {
  static int previous=-1;
  uint8_t port0;
  if (!boxBus || !readRegs(0x20,0,&port0,1)) {
    if(force)event("buttons","missing","XL9555 did not reply");
    previous=-1;
    return;
  }
  int bits=(digitalRead(0)==LOW) | (((port0&0x10)==0)<<1) | (((port0&0x08)==0)<<2);
  if (force || bits!=previous) event("buttons","sample","K0=%s  K1=%s  K2=%s",
    bits&1?"DOWN":"UP",bits&2?"DOWN":"UP",bits&4?"DOWN":"UP");
  previous=bits;
}

static int16_t wordLE(const uint8_t *b) { return int16_t(uint16_t(b[0]) | (uint16_t(b[1])<<8)); }
static void boxSample() {
  if(!boxBus) {event("i2c","error","I2C controller init failed");return;}
  bool hasExpander=ack(0x20);
  event("i2c",hasExpander?"ack":"missing","XL9555 @0x20 %s",hasExpander?"ACK":"no ACK");
  keys(true);
  uint8_t data[12];
  if(imuAddress && readRegs(imuAddress,0x35,data,12)) {
    event("imu","sample","a[g] %.3f %.3f %.3f | gyro[dps] %.2f %.2f %.2f",
      wordLE(data)/16384.f,wordLE(data+2)/16384.f,wordLE(data+4)/16384.f,
      wordLE(data+6)/128.f,wordLE(data+8)/128.f,wordLE(data+10)/128.f);
  } else event("imu","missing","QMI8658A WHO_AM_I or sample unavailable");

  const bool hasEeprom=ack(0x50);
  event("eeprom",hasEeprom?"ack":"missing","0x50 %s; no write/readback test",hasEeprom?"ACK":"no ACK");
  const bool hasCodec=ack(0x18);
  event("audio",hasCodec?"ack":"unavailable","%s",hasCodec?"0x18 ACK; audio quality unverified":"no 0x18 ACK; other codec revisions possible");
  uint8_t input[2],config[2];
  if(readRegs(0x20,0,input,2) && readRegs(0x20,6,config,2)) {
    event("lcd",config[0]&0x80?"idle":"control","backlight P0.7=%u, pin mode=%s; pixels unverified",
      (input[0]>>7)&1,config[0]&0x80?"input":"output");
    event("led","control","red P1.0=%u, mode=%s; optical output unverified",input[1]&1,config[1]&1?"input":"output");
    event("speaker",config[0]&0x20?"idle":"control","amp pin P0.5=%u, mode=%s; sound unverified",
      (input[0]>>5)&1,config[0]&0x20?"input":"output");
  }
}
#endif

static void telemetrySetup() {
#ifdef BOARDBENCH_ATK_BOX1
  setupBox();
#endif
}

static void telemetryTick() {
  static uint32_t lastSample=0;
  const uint32_t now=millis();
#ifdef BOARDBENCH_ATK_BOX1
  static uint32_t lastKey=0;
  if(now-lastKey>=45) {lastKey=now; keys(false);}
#endif
  if(now-lastSample<2000)return;
  lastSample=now;
  event("mcu","sample","%s | CPU %u MHz | uptime %lu ms",ESP.getChipModel(),ESP.getCpuFreqMHz(),(unsigned long)now);
  event("flash","sample","%u bytes, reported by ESP SDK",ESP.getFlashChipSize());
  event("sram","sample","internal free %u bytes",(unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
  event("psram",ESP.getPsramSize()?"sample":"unavailable","total %u, free %u bytes",ESP.getPsramSize(),ESP.getFreePsram());
  const bool wifi=WiFi.status()==WL_CONNECTED;
  if(wifi) event("wifi","sample","associated | RSSI %d dBm",WiFi.RSSI());
  else event("wifi","idle","not associated; SDK status=%d",int(WiFi.status()));
#if CONFIG_BT_ENABLED
  event("ble","control","BT controller state=%d (0 idle, 1 initialized, 2 enabled)",int(esp_bt_controller_get_status()));
#else
  event("ble","unavailable","Bluetooth controller disabled in this build");
#endif
  event("usb","control","diagnostic serial heartbeat | 115200 baud");
#ifdef BOARDBENCH_ATK_BOX1
  boxSample();
  static uint32_t lastCapabilities=0;
  if(!lastCapabilities || now-lastCapabilities>=6000) {
    lastCapabilities=now;
    event("mic","unavailable","microphone PCM driver not active in this monitor build");
    event("light","unavailable","no verified ambient sensor driver in this board profile");
    event("sd","unavailable","TF bus not mounted by this monitor");
    event("camera","unavailable","camera capture driver not active");
    event("touch","unavailable","touch controller not initialized");
    event("buzzer","unavailable","no acoustic feedback sensor");
    event("expansion","unavailable","external device and pinout unspecified");
  }
#endif
}
