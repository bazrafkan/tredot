// A copy of what the display shows, for screenshots (SS in the protocol).
//
// The panel keeps its image in its own memory and cannot be read back over this board's SPI, so
// the bus is wrapped: every pixel sent to the panel is also written here, following the panel's
// own address window (CASET/RASET, then RAMWR and the pixel stream). That catches text, fills,
// bitmaps and the viewfinder alike, whatever drawing path the GFX library takes.
#pragma once
#include <Arduino_GFX_Library.h>

class MirrorBus : public Arduino_ESP32SPI {
 public:
  static const uint16_t W = 240, H = 240;
  uint16_t *pixels = nullptr;  // W x H, RGB565 as sent to the panel; in PSRAM

  using Arduino_ESP32SPI::Arduino_ESP32SPI;

  void allocate() { pixels = (uint16_t *)ps_calloc(W * H, sizeof(uint16_t)); }

  void writeCommand(uint8_t c) override {
    drawing = c == RAMWR;
    if (drawing) { x = x0; y = y0; }
    Arduino_ESP32SPI::writeCommand(c);
  }
  void writeC8D8(uint8_t c, uint8_t d) override {
    drawing = false;
    Arduino_ESP32SPI::writeC8D8(c, d);
  }
  void writeC8D16(uint8_t c, uint16_t d) override {
    drawing = false;
    Arduino_ESP32SPI::writeC8D16(c, d);
  }
  void writeC8D16D16(uint8_t c, uint16_t a, uint16_t b) override {
    if (c == CASET) { x0 = a; x1 = b; }
    if (c == RASET) { y0 = a; y1 = b; }
    drawing = false;
    Arduino_ESP32SPI::writeC8D16D16(c, a, b);
  }
  void write16(uint16_t p) override {
    if (drawing) put(p);
    Arduino_ESP32SPI::write16(p);
  }
  void writeRepeat(uint16_t p, uint32_t n) override {
    if (drawing)
      for (uint32_t i = 0; i < n; i++) put(p);
    Arduino_ESP32SPI::writeRepeat(p, n);
  }
  void writePixels(uint16_t *data, uint32_t n) override {
    if (drawing)
      for (uint32_t i = 0; i < n; i++) put(data[i]);
    Arduino_ESP32SPI::writePixels(data, n);
  }
  void writeBytes(uint8_t *data, uint32_t n) override {  // big-endian pixel pairs
    if (drawing)
      for (uint32_t i = 0; i + 1 < n; i += 2) put((data[i] << 8) | data[i + 1]);
    Arduino_ESP32SPI::writeBytes(data, n);
  }
  void writeIndexedPixels(uint8_t *data, uint16_t *index, uint32_t n) override {
    if (drawing)
      for (uint32_t i = 0; i < n; i++) put(index[data[i]]);
    Arduino_ESP32SPI::writeIndexedPixels(data, index, n);
  }
  void writeIndexedPixelsDouble(uint8_t *data, uint16_t *index, uint32_t n) override {
    if (drawing)
      for (uint32_t i = 0; i < n; i++) { put(index[data[i]]); put(index[data[i]]); }
    Arduino_ESP32SPI::writeIndexedPixelsDouble(data, index, n);
  }

 private:
  static const uint8_t CASET = 0x2A, RASET = 0x2B, RAMWR = 0x2C;  // GC9A01 commands
  bool drawing = false;
  uint16_t x0 = 0, x1 = W - 1, y0 = 0, y1 = H - 1, x = 0, y = 0;

  // the panel's auto-increment: left to right inside the window, then the next row
  inline void put(uint16_t p) {
    if (pixels && x < W && y < H) pixels[y * W + x] = p;
    if (++x > x1) {
      x = x0;
      if (++y > y1) y = y0;
    }
  }
};
