// The links to the Mac: USB serial, Bluetooth LE and Wi-Fi.
//
// The module listens on all of them at once. A bridge that connects sends "W <protocol> ...": the
// firmware decides whether that link becomes the active one (setActive) and answers with the H
// greeting. The stream carries text lines (ending in newline) and, once the session is encrypted,
// binary frames (starting with 0x00, see security.h): pollLinks separates them.
#pragma once
#include <Arduino.h>

const int MAX_LINKS = 3;
const size_t MAX_LINE = 6000;  // an agent M line with 40 lines of text fits comfortably
const size_t MAX_FRAME = 12 + MAX_LINE + 16;

struct Link {
  const char *name;
  Stream *stream;
  String buf;
  uint8_t *frame;  // encrypted frame being received (in PSRAM)
  size_t frameLen, frameNeed;  // frameNeed = 0: not inside a frame
};

Link links[MAX_LINKS];
int nLinks = 0;
int active = 0;
Stream *channel = nullptr;  // the active link: sendLine and sendJpeg write here

void addLink(const char *name, Stream *s) {
  if (nLinks >= MAX_LINKS) return;
  Link &l = links[nLinks];
  l.name = name;
  l.stream = s;
  l.buf.reserve(256);
  l.frame = (uint8_t *)ps_malloc(MAX_FRAME);
  l.frameLen = l.frameNeed = 0;
  if (nLinks == 0) channel = s;
  nLinks++;
}

void setActive(int i) {
  active = i;
  channel = links[i].stream;
}

// Reads every link: onLine(line, link index) for each text line, onFrame(bytes, size, link index)
// for each encrypted frame.
template <typename L, typename F>
void pollLinks(L onLine, F onFrame) {
  for (int i = 0; i < nLinks; i++) {
    Link &l = links[i];
    while (l.stream->available()) {
      uint8_t c = l.stream->read();
      if (l.frameNeed) {  // inside a frame: count bytes, newlines mean nothing here
        l.frame[l.frameLen++] = c;
        if (l.frameLen == 12) {
          size_t len = ((size_t)l.frame[1] << 16) | (l.frame[2] << 8) | l.frame[3];
          l.frameNeed = 12 + len + 16;
          if (l.frameNeed > MAX_FRAME) l.frameLen = l.frameNeed = 0;  // not a real frame
        } else if (l.frameLen == l.frameNeed) {
          onFrame(l.frame, l.frameLen, i);
          l.frameLen = l.frameNeed = 0;
        }
      } else if (c == 0 && l.buf.length() == 0) {  // a frame starts
        l.frame[0] = 0;
        l.frameLen = 1;
        l.frameNeed = 12;
      } else if (c == '\n') {
        onLine(l.buf, i);
        l.buf = "";
      } else if (c != '\r' && l.buf.length() < MAX_LINE) {
        l.buf += (char)c;
      }
    }
  }
}
