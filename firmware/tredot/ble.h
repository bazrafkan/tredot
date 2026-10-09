// The Bluetooth LE link: to the rest of the firmware it is a Stream, like USB serial.
//
// One service with two characteristics: RX, where the Mac writes its lines, and TX, where the
// module sends its own (notifications). Notifications go straight through NimBLE and are retried
// while the radio is full: the stock BLE library drops them in that case, and a lost line could be
// a "Yes" or a piece of a photo.
//
// Bluetooth on the Mac is slow (~10-15 kB/s, measured) and stalls for a few tenths of a second at
// times. So the screen never blocks, writers put bytes in a large queue (in PSRAM) and the ble-tx
// task sends them in full packets.
#pragma once
#include <BLEDevice.h>
#include <BLEServer.h>
#include "freertos/stream_buffer.h"
#include "host/ble_hs.h"

// Diagnostics on serial (# lines), only when the bridge is not on USB: there, a line written by
// another task would land in the middle of an encrypted frame and break it. And only if there is
// room: when nobody reads the serial port the buffer fills up and writes would block.
#define DIAG(...) do { if (channel != &Serial && Serial.availableForWrite() > 256) Serial.printf(__VA_ARGS__); } while (0)

// The same in mac/links.py
#define BLE_SERVICE "6f1c0001-3b5e-4c47-9a21-7d2f5e8a4c10"
#define BLE_RX      "6f1c0002-3b5e-4c47-9a21-7d2f5e8a4c10"  // Mac -> module
#define BLE_TX      "6f1c0003-3b5e-4c47-9a21-7d2f5e8a4c10"  // module -> Mac

class BleLink : public Stream {
 public:
  volatile bool connected = false;
  volatile uint16_t conn = 0;

  // Advertises only when paired: an unpaired module is used over USB (security.h)
  void begin(const char *name, bool visible) {
    this->visible = visible;
    queueMem = (uint8_t *)ps_malloc(QUEUE_SIZE + 1);
    queue = xStreamBufferCreateStatic(QUEUE_SIZE, 1, queueMem, &queueState);
    BLEDevice::init(name);
    BLEDevice::setMTU(517);  // the Mac decides how much to grant: bigger means fewer packets
    server = BLEDevice::createServer();
    server->setCallbacks(new ServerCallbacks(this));
    BLEService *service = server->createService(BLE_SERVICE);
    BLECharacteristic *rx = service->createCharacteristic(
        BLE_RX, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR);
    rx->setCallbacks(new RxCallbacks(this));
    tx = service->createCharacteristic(BLE_TX, BLECharacteristic::PROPERTY_NOTIFY);
    service->start();
    BLEAdvertising *adv = BLEDevice::getAdvertising();
    adv->addServiceUUID(BLE_SERVICE);
    adv->setScanResponse(true);  // name in the scan response: with the service it exceeds 31 bytes
    if (visible) BLEDevice::startAdvertising();
    // after init: ble-tx asks NimBLE for the packet size (before init it crashes)
    xTaskCreatePinnedToCore(sender, "ble-tx", 4096, this, 2, nullptr, 1);
    static ble_gap_event_listener listener;  // diagnostics: why the link drops
    ble_gap_event_listener_register(&listener, gapEvent, nullptr);
  }

  static int gapEvent(struct ble_gap_event *e, void *) {
    if (e->type == BLE_GAP_EVENT_DISCONNECT)
      DIAG("# ble: disconnected, reason 0x%x\n", e->disconnect.reason);
    else if (e->type == BLE_GAP_EVENT_CONN_UPDATE)
      DIAG("# ble: parameters updated (%d)\n", e->conn_update.status);
    return 0;
  }

  // ---------- Stream: reading (filled by the NimBLE task, drained by loop) ----------

  int available() override {
    portENTER_CRITICAL(&mux);
    int n = (head - tail + RX_SIZE) % RX_SIZE;
    portEXIT_CRITICAL(&mux);
    return n;
  }

  int read() override {
    portENTER_CRITICAL(&mux);
    int c = -1;
    if (head != tail) {
      c = rx[tail];
      tail = (tail + 1) % RX_SIZE;
    }
    portEXIT_CRITICAL(&mux);
    return c;
  }

  int peek() override {
    portENTER_CRITICAL(&mux);
    int c = head != tail ? rx[tail] : -1;
    portEXIT_CRITICAL(&mux);
    return c;
  }

  // ---------- Stream: writing, queued for ble-tx ----------

  size_t write(uint8_t c) override { return write(&c, 1); }

  size_t write(const uint8_t *p, size_t n) override {
    if (!connected) return n;  // nobody listening: like a serial port with no cable
    size_t sent = 0;  // queue full (256 kB: should not happen) = wait for ble-tx to drain it
    while (sent < n && connected)
      sent += xStreamBufferSend(queue, p + sent, n - sent, pdMS_TO_TICKS(100));
    return n;
  }

  size_t queued() { return queue ? xStreamBufferBytesAvailable(queue) : 0; }  // bytes still to send
  void flush() override {}  // ble-tx takes care of it

  void push(const uint8_t *p, size_t n) {  // from the NimBLE task: bytes from the Mac
    portENTER_CRITICAL(&mux);
    for (size_t i = 0; i < n; i++) {
      size_t next = (head + 1) % RX_SIZE;
      if (next == tail) break;  // full: does not happen, loop drains every few milliseconds
      rx[head] = p[i];
      head = next;
    }
    portEXIT_CRITICAL(&mux);
  }

  void report() {  // diagnostics on serial: what the Mac granted
    ble_gap_conn_desc d;
    uint8_t txPhy = 0, rxPhy = 0;
    if (!connected || ble_gap_conn_find(conn, &d) != 0) return;
    ble_gap_read_le_phy(conn, &txPhy, &rxPhy);
    DIAG("# ble: interval %.2f ms, latency %d, phy %d/%d, mtu %d, queued %u\n",
                  d.conn_itvl * 1.25, d.conn_latency, txPhy, rxPhy, ble_att_mtu(conn),
                  (unsigned)xStreamBufferBytesAvailable(queue));
  }

  void dropped() {  // the Mac went away: start clean and advertise again
    DIAG("# ble: disconnected\n");
    connected = false;  // ble-tx discards what is left in the queue
    portENTER_CRITICAL(&mux);
    head = tail = 0;
    portEXIT_CRITICAL(&mux);
    if (visible && !quiet) BLEDevice::startAdvertising();
  }

  // While the Mac uses Wi-Fi, Bluetooth stays quiet: same radio and antenna, advertising steals
  // airtime from Wi-Fi. It resumes as soon as Wi-Fi goes away (loop).
  void pause(bool paused) {
    if (paused == quiet || !visible || connected) return;
    quiet = paused;
    if (paused) BLEDevice::stopAdvertising();
    else BLEDevice::startAdvertising();
  }

  void setVisible(bool on) {  // after pairing (on) or unpairing (off)
    visible = on;
    quiet = false;
    if (on) BLEDevice::startAdvertising();
    else {
      BLEDevice::stopAdvertising();
      if (connected) ble_gap_terminate(conn, BLE_ERR_REM_USER_CONN_TERM);
    }
  }

 private:
  static const size_t RX_SIZE = 16384;
  static const size_t OUT_SIZE = 512;
  static const size_t QUEUE_SIZE = 256 * 1024;  // a whole photo, or ~20 s of compressed voice
  uint8_t rx[RX_SIZE];
  volatile size_t head = 0, tail = 0;
  portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;
  uint8_t *queueMem = nullptr;
  StaticStreamBuffer_t queueState;
  StreamBufferHandle_t queue = nullptr;
  BLEServer *server = nullptr;
  BLECharacteristic *tx = nullptr;
  bool visible = false;
  bool quiet = false;  // advertising paused because the Mac is on Wi-Fi (pause)

  size_t chunk() {
    if (!connected) return OUT_SIZE;
    uint16_t mtu = ble_att_mtu(conn);
    size_t room = mtu > 3 ? mtu - 3 : 20;
    return room < OUT_SIZE ? room : OUT_SIZE;
  }

  // The ble-tx task: a full packet from the queue, or whatever is there after 5 ms of quiet
  static void sender(void *arg) {
    BleLink *self = (BleLink *)arg;
    uint8_t out[OUT_SIZE];
    for (;;) {
      size_t size = self->chunk();
      size_t n = xStreamBufferReceive(self->queue, out, size, portMAX_DELAY);
      while (n < size) {
        size_t more = xStreamBufferReceive(self->queue, out + n, size - n, pdMS_TO_TICKS(5));
        if (!more) break;
        n += more;
      }
      if (self->connected) self->notify(out, n);  // disconnected: discard, the bridge resends everything
    }
  }

  void notify(const uint8_t *p, size_t n) {
    // Radio full (ENOMEM): wait until it frees up, as long as the link is up. Never skip a packet:
    // late is better than a broken photo or half a line. If the Mac stops answering, Bluetooth
    // itself closes the link after 4 s.
    for (int tries = 0; connected; tries++) {
      os_mbuf *om = ble_hs_mbuf_from_flat(p, n);
      if (om) {
        int rc = ble_gatts_notify_custom(conn, tx->getHandle(), om);  // NimBLE frees om
        if (rc == 0) {
          if (tries > 250) DIAG("# notify: %d attempts\n", tries);
          return;
        }
        if (rc != BLE_HS_ENOMEM && rc != BLE_HS_EBUSY) {
          DIAG("# notify: error %d\n", rc);
          return;
        }
      }
      delay(2);
    }
  }

  struct ServerCallbacks : BLEServerCallbacks {
    BleLink *link;
    ServerCallbacks(BleLink *l) : link(l) {}
    void onConnect(BLEServer *s, ble_gap_conn_desc *desc) override {
      link->conn = desc->conn_handle;
      // No parameter requests to the Mac (interval, 2 Mbit/s PHY, data length): together they
      // collide (error 0x2A), one stays pending and after 40 s the radio drops the link (0x22,
      // tested). macOS picks good parameters on its own.
      link->connected = true;
    }
    void onDisconnect(BLEServer *s, ble_gap_conn_desc *desc) override { link->dropped(); }
  };

  struct RxCallbacks : BLECharacteristicCallbacks {
    BleLink *link;
    RxCallbacks(BleLink *l) : link(l) {}
    void onWrite(BLECharacteristic *c, ble_gap_conn_desc *desc) override {
      String v = c->getValue();
      link->push((const uint8_t *)v.c_str(), v.length());
    }
  };
};
