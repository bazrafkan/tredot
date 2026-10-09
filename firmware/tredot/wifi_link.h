// The Wi-Fi link: the module joins the home or office network, announces itself over mDNS
// (_tredot._tcp, with its id) and waits for the Mac over TCP. To the rest of the firmware it is a
// Stream, like USB serial.
//
// Network name and password arrive from the Mac only encrypted (N line, see tredot.ino) and are
// kept in NVS. One Mac at a time: a client that does not open an encrypted session within 5 s is
// closed, so an intruder on the network cannot hold the slot.
//
// As with Bluetooth, writers put bytes in a queue (PSRAM) and the wifi-tx task sends them: with the
// ESP32's small TCP buffer (5.7 kB) a slow network blocked the screen. Not a single byte is ever
// dropped: half an encrypted frame makes the Mac lose sync (tested).
#pragma once
#include <ESPmDNS.h>
#include <Preferences.h>
#include <WiFi.h>
#include "freertos/semphr.h"
#include "freertos/stream_buffer.h"
#include "texts.h"

const uint16_t WIFI_PORT = 7777;  // the Mac reads it from the mDNS announcement

// Why the router turned us away (WiFi.onEvent): without it, a wrong name or password left the
// module on "connecting" forever, silently (tested)
volatile uint8_t wifiReason = 0;
volatile bool wifiJoined = false;  // password accepted, no address yet: for the steps in the menu

class TcpLink : public Stream {
 public:
  // off | connecting | address | <IP address> | error: <why>, reported to the Mac (I wifi).
  // connecting = looking for the network; address = joined, asking the router for an address
  String state = "off";

  void begin(const String &id) {
    deviceId = id;
    queueMem = (uint8_t *)ps_malloc(QUEUE_SIZE + 1);
    queue = xStreamBufferCreateStatic(QUEUE_SIZE, 1, queueMem, &queueState);
    lock = xSemaphoreCreateMutex();
    xTaskCreatePinnedToCore(sender, "wifi-tx", 4096, this, 2, nullptr, 1);
    Preferences p;
    p.begin("wifi", true);
    ssid = p.getString("ssid", "");
    pass = p.getString("pass", "");
    p.end();
    WiFi.persistent(false);  // credentials are kept by us, in the "wifi" NVS namespace
    WiFi.setAutoReconnect(true);
    WiFi.onEvent([](arduino_event_id_t, arduino_event_info_t info) {
      wifiReason = info.wifi_sta_disconnected.reason;
      wifiJoined = false;
    }, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
    WiFi.onEvent([](arduino_event_id_t, arduino_event_info_t) { wifiJoined = true; },
                 ARDUINO_EVENT_WIFI_STA_CONNECTED);
    if (ssid.length()) connect();
  }

  void configure(const String &newSsid, const String &newPass) {
    Preferences p;
    p.begin("wifi", false);
    if (newSsid.length()) {
      p.putString("ssid", newSsid);
      p.putString("pass", newPass);
    } else {
      p.clear();
    }
    p.end();
    ssid = newSsid;
    pass = newPass;
    drop();
    MDNS.end();
    announced = false;
    WiFi.disconnect(true);
    if (ssid.length()) connect();
    else state = "off";
  }

  // From loop: network, announcement, incoming Mac. true if the state changed (to tell the Mac).
  // busy: an update is running, so no switching access point.
  bool poll(bool sessionOpen, bool busy = false) {
    String before = state;
    // Status, address and signal go through the radio task: asking on every loop (hundreds of
    // times a second) took time from the screen and the viewfinder. 4 times a second is enough.
    bool look = millis() - lastLook >= 250;
    if (look) lastLook = millis();
    if (ssid.length() && look) {
      if (WiFi.status() == WL_CONNECTED) {
        roam(busy);
        state = WiFi.localIP().toString();
        if (!announced) {
          WiFi.setSleep(false);  // awake: replies in a few ms instead of ~115 (tested)
          announce();
        }
      } else {
        state = wifiJoined ? "address" : why();
      }
    }
    if (announced) {
      WiFiClient incoming = server.accept();
      if (incoming) {
        // the slot is held only by a Mac with an open session; otherwise the newcomer wins
        if (connected() && sessionOpen) incoming.stop();
        else {
          xSemaphoreTake(lock, portMAX_DELAY);
          if (client) client.stop();
          client = incoming;
          client.setNoDelay(true);  // small lines (a "Yes") go out immediately
          linkUp = client.connected();
          xStreamBufferReset(queue);  // nothing left over from the previous Mac
          since = millis();
          xSemaphoreGive(lock);
        }
      }
      if (client && !sessionOpen && millis() - since > 5000) drop();  // held the slot without a session
    }
    return state != before;
  }

  bool connected() { return linkUp; }

  void drop() {
    xSemaphoreTake(lock, portMAX_DELAY);
    if (client) client.stop();
    linkUp = false;
    xSemaphoreGive(lock);
  }

  // ---------- Stream ----------
  int available() override {
    // connected() queries the network stack: called for every byte it slowed everything down
    // (a Wi-Fi update took 220 s instead of ~50). Only ask when there is nothing to read.
    if (!linkUp) return 0;
    int n = client.available();
    if (n > 0) return n;
    linkUp = client.connected();  // here, from loop: the state then holds for everyone
    return 0;
  }
  int read() override {
    int c = linkUp ? client.read() : -1;
    if (c >= 0) bytesIn++;
    return c;
  }

  void report() {  // diagnostics on serial (like BleLink::report)
    DIAG("# wifi: %s, queued %u, read %u, sent %u, rssi %d\n", linkUp ? "connected" : "free",
         (unsigned)xStreamBufferBytesAvailable(queue), (unsigned)bytesIn, (unsigned)bytesOut,
         WiFi.RSSI());
  }
  int peek() override { return linkUp ? client.peek() : -1; }
  size_t write(uint8_t c) override { return write(&c, 1); }
  size_t write(const uint8_t *p, size_t n) override {
    if (!linkUp) return n;  // nobody listening: like a serial port with no cable
    size_t sent = 0;  // queue full (256 kB: should not happen) = wait for wifi-tx to drain it
    while (sent < n && linkUp) sent += xStreamBufferSend(queue, p + sent, n - sent, pdMS_TO_TICKS(100));
    return n;
  }
  size_t queued() { return queue ? xStreamBufferBytesAvailable(queue) : 0; }  // bytes still to send
  void flush() override {}  // wifi-tx takes care of it

 private:
  String deviceId, ssid, pass;
  static const size_t QUEUE_SIZE = 256 * 1024;
  WiFiServer server{WIFI_PORT};
  WiFiClient client;
  volatile bool linkUp = false;
  volatile uint32_t bytesIn = 0, bytesOut = 0;
  uint32_t since = 0, weakSince = 0, lastLook = 0;
  bool announced = false;

  // Home-made roaming: the ESP32 sticks to the access point it joined at boot even after moving
  // to another room (-96 dBm, tested). Below -80 dBm for 30 s it reconnects, and connect() picks
  // the strongest access point.
  void roam(bool busy) {
    if (WiFi.RSSI() >= -80 || busy) {
      weakSince = 0;
      return;
    }
    if (!weakSince) weakSince = millis();
    if (millis() - weakSince < 30000) return;
    DIAG("# wifi: signal %d dBm for 30 s, looking for a better access point\n", WiFi.RSSI());
    drop();
    WiFi.disconnect();
    connect();
  }
  uint8_t *queueMem = nullptr;
  StaticStreamBuffer_t queueState;
  StreamBufferHandle_t queue = nullptr;
  SemaphoreHandle_t lock = nullptr;

  // The wifi-tx task: everything from the queue to the network, as long as the Mac is there. If
  // the network accepts nothing for 10 s the link is closed: the bridge reconnects with a new
  // session, with no gaps.
  static void sender(void *arg) {
    TcpLink *self = (TcpLink *)arg;
    static uint8_t out[8192];  // big writes: 1436 bytes at a time gave ~100 kB/s over Wi-Fi
    for (;;) {
      size_t n = xStreamBufferReceive(self->queue, out, sizeof out, portMAX_DELAY);
      xSemaphoreTake(self->lock, portMAX_DELAY);
      size_t sent = 0;
      uint32_t stuck = millis();
      while (sent < n && self->linkUp) {
        uint32_t t0 = millis();
        size_t k = self->client.write(out + sent, n - sent);
        if (millis() - t0 > 500) DIAG("# wifi: slow write %u ms (%u bytes)\n", (unsigned)(millis() - t0), (unsigned)k);
        sent += k;
        self->bytesOut += k;
        if (k) stuck = millis();
        else if (millis() - stuck > 10000) {
          self->client.stop();
          self->linkUp = false;
        } else {
          xSemaphoreGive(self->lock);
          delay(2);  // network full: let loop run and retry
          xSemaphoreTake(self->lock, portMAX_DELAY);
        }
      }
      xSemaphoreGive(self->lock);
    }
  }

  // The reason for the last disconnection, in words (WIFI_REASON_* codes in esp_wifi_types.h)
  String why() {
    switch (wifiReason) {
      case 2: case 15: case 202: case 204:
        return String("error: ") + tr(T_WIFI_WRONG_PASSWORD);
      case 201: case 210: case 211:
        return String("error: ") + tr(T_WIFI_NOT_FOUND);
      case 203:
        return String("error: ") + tr(T_WIFI_REJECTED);
      default:
        return "connecting";
    }
  }

  void connect() {
    state = "connecting";
    wifiReason = 0;
    wifiJoined = false;
    WiFi.mode(WIFI_STA);
    WiFi.setHostname(deviceId.c_str());
    // With several access points under one name (mesh, repeaters) the ESP32 takes the first one it
    // finds by default: scan every channel and pick the strongest signal
    WiFi.setScanMethod(WIFI_ALL_CHANNEL_SCAN);
    WiFi.setSortMethod(WIFI_CONNECT_AP_BY_SIGNAL);
    WiFi.begin(ssid.c_str(), pass.c_str());
    weakSince = 0;
  }

  void announce() {
    String host = deviceId;
    host.toLowerCase();  // tredot-b407.local
    MDNS.begin(host.c_str());
    MDNS.addService("tredot", "tcp", WIFI_PORT);
    MDNS.addServiceTxt("tredot", "tcp", "id", deviceId.c_str());
    server.begin();
    announced = true;
  }
};
