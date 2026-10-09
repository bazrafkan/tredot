// Tredot firmware: XIAO ESP32-S3 Sense + Seeed Round Display. Shows what the coding agent is doing
// and lets the user approve its actions by touch; records voice notes, photos and video.
//
// Line protocol, UTF-8, fields separated by TAB; lists inside a field (text lines, options)
// separated by 0x1F. The Mac does the text layout.
//   Mac -> module
//     S <state> <title> <lines> <session>   state: idle | think | tool | done | wait | note | saved | error
//                                            (saved: empty title = the localized "Note saved")
//     A <id> <seconds> <title> <lines> <session> <options>   permission request
//     C <id>                  request closed elsewhere
//     L <lines>               the agent's text as it appears (last lines), under "Thinking…"
//     M <title> <lines>       agent reply, in pages: a tap turns the page
//     X photo <prompt>        the agent wants a photo: viewfinder with the prompt, a tap shoots
//     X listen <question>     the agent wants a spoken answer: records until a tap
//     W <protocol> <nonce> <lang>   the bridge connected over this link: it becomes the active
//                             link and the module answers with H. If paired, <nonce> (random, from
//                             the Mac) opens the encrypted session (security.h). <lang>: the Mac's
//                             language for the module texts (en | it, texts.h); missing or unknown
//                             keeps the current one
//     PAIR <key> <name>       pairing, USB only: the module asks for confirmation by touch
//     UNPAIR                  remove the pairing (encrypted only); the module also forgets Wi-Fi
//     N <ssid> <password>     Wi-Fi network (both base64; empty = forget), encrypted only
//     U / u / UE / UA         firmware update, encrypted only and not over Bluetooth (ota.h)
//     SS                      screenshot: what the screen shows right now (mirror.h)
//   module -> Mac
//     H <protocol> <firmware> <mic> <id> <session>   greeting, always plaintext. session: the
//                             module nonce (paired), "-" (not paired: plaintext, USB only),
//                             "boot" (just started: the Mac starts over with W)
//     PAIRED <id> / PAIRNO / UNPAIRED <id>   result of PAIR and UNPAIR, plaintext
//     I cam <sensor>          the first time the camera starts: OV2640, OV3660, ...
//     I wifi <state>          off | connecting | address | <IP address> | error: <why> (wifi_link.h)
//     R <id> <index>          option confirmed (0 = first); -1 = not shown, the agent decides
//     VB [agent] / V <base64> / VE   recording: start (agent = talking to the agent, none = voice
//                             note), PCM audio 16 kHz 16 bit mono, end
//     VA <base64>             IMA ADPCM audio (adpcm.h), instead of V over Bluetooth
//     XN                      the module is busy: no photo or listening for the agent
//     K <count>               M reply received in full, with that many lines: the ack for the Mac
//     P <base64>              JPEG photo
//     MB / F <ms> <base64> / V <base64> / ME   video: start, JPEG frame with timestamp, audio
//                             (as above), end
//     T <x> <y>               raw touch, for diagnostics
//     SS <width> <height> <base64>   screenshot: RGB565 pixels, little-endian, row by row
//
// Every link (USB serial, Bluetooth LE, Wi-Fi TCP) is a Stream; see links.h.

// The Mac checks PROTOCOL in the H greeting: bump it on any change to lines or fields that an
// older bridge would not understand. FIRMWARE changes with every release.
#define PROTOCOL 6
#define FIRMWARE "1.1.0"

#define U8G2_USE_LARGE_FONTS
#include <U8g2lib.h>  // without it, Arduino_GFX does not enable the Unifont fonts
#include <Arduino_GFX_Library.h>
#include <Wire.h>
#include <ESP_I2S.h>
#include "base64.h"
#include "esp_camera.h"
#include "img_converters.h"
#include <JPEGDEC.h>  // bitbank2, Apache 2.0: JPEG decoding with the S3 SIMD instructions
#include "texts.h"
#include "mirror.h"
#include "links.h"
#include "ble.h"
#include "adpcm.h"
#include "security.h"
#include "wifi_link.h"
#include "ota.h"

BleLink ble;  // the Bluetooth link (ble.h); the USB one is Serial
Secure sec;   // pairing and encrypted session (security.h)
TcpLink tcp;  // the Wi-Fi link (wifi_link.h)
Ota ota;      // firmware update from the bridge (ota.h)
String deviceId;  // Tredot-xxxx, the same as the Bluetooth name
uint8_t pairKey[32];  // key received with PAIR, waiting for confirmation by touch

#define TFT_DC   4   // D3
#define TFT_CS   2   // D1
#define TFT_SCK  7   // D8
#define TFT_MOSI 9   // D10
#define TFT_MISO 8   // D9
#define TFT_BL   43  // D6 (backlight)
#define TOUCH_INT 44 // D7
#define TOUCH_ADDR 0x2e
#define MIC_CLK  42  // PDM microphone on the Sense board
#define MIC_DATA 41
#define MIC_RATE 16000

// Camera on the Sense board (from the CameraWebServer example, CAMERA_MODEL_XIAO_ESP32S3)
#define CAM_XCLK 10
#define CAM_SIOD 40
#define CAM_SIOC 39
#define CAM_VSYNC 38
#define CAM_HREF 47
#define CAM_PCLK 13
const int CAM_D[8] = {15, 17, 18, 16, 14, 12, 11, 48};  // Y2..Y9

// Colors of the Claude Code dark theme
#define C_BG     0x0000
#define C_TEXT   0xEF3B  // #E8E6DC
#define C_DIM    0x8C50  // #8A8880
#define C_FAINT  0x2945
#define C_ORANGE 0xDBAA  // #D97757
#define C_GREEN  0x4DCC  // #4EBA65
#define C_RED    0xE289  // #E5534B

MirrorBus *bus = new MirrorBus(TFT_DC, TFT_CS, TFT_SCK, TFT_MOSI, TFT_MISO);  // keeps a copy: SS
Arduino_GFX *gfx = new Arduino_GC9A01(bus, GFX_NOT_DEFINED, 0, true);


enum Mode { M_IDLE, M_THINK, M_TOOL, M_DONE, M_WAIT, M_ASK, M_NOTE, M_SAVED, M_ERROR, M_REC,
            M_MENU, M_PHOTO, M_VIDEO, M_REPLY };
Mode mode = M_IDLE;
String title, session;
String lines[4];
int nLines = 0;
String options[4];
int nOptions = 0;
String askId;
int selected = -1;
// Touchable rows of the last list drawn (request or menu): must fit the longest one (the menu).
const int MAX_ROWS = 6;
int optTop[MAX_ROWS], optBottom[MAX_ROWS];
uint32_t askStart, askMillis, modeSince;
int lastArcDeg = -1;
uint32_t turnStart = 0;
Mode behind = M_IDLE;  // the agent state while menu, note, photo or video own the screen

I2SClass mic;
bool micOk = false;
uint32_t recStart = 0;
volatile int recPeak = 0;
const uint32_t REC_MAX_MS = 30UL * 60 * 1000;
const uint32_t VIDEO_MAX_MS = 10UL * 60 * 1000;
const int MENU_ROW = 188;  // the "≡ Menu" row at the bottom

// Audio: a separate task reads the microphone into this ring buffer and loop drains it to the
// link, so no audio is lost while a video frame is being sent.
const size_t RING = 64 * 1024;  // 2 seconds
uint8_t *ring;
volatile size_t ringHead = 0, ringTail = 0;
volatile bool audioOn = false;

bool camOk = false;
uint8_t *preview, *crop;  // 320x240 decoded, 240x240 cropped for the screen

const TextId MENU[] = {T_TALK_TO_CLAUDE, T_VOICE_NOTE, T_PHOTO, T_VIDEO, T_CLOSE};
const int N_MENU = 5;

// The agent: what it asked for (shown over viewfinder or recording) and its reply, in pages
bool recForAgent = false;
String prompt;
const int MAX_REPLY = 40, PAGE = 7;
String replyTitle, reply[MAX_REPLY];
int nReply = 0, replyPage = 0;
bool replyPending = false;  // arrived while the screen was busy
int lastElapsed = -1;

const int LINE_H = 18;    // Unifont: 16 px + 2 of spacing
const int OPT_H = 24;     // option rows are taller: they are touch targets
const int SAFE_R = 108;   // text stays inside this circle; the countdown ring is outside

void drawOptions(const String labels[], int n, int y, int sel, int rowH = OPT_H);
int optionAt(int y, int n);
void startRecording(bool agent);
void startCamera(Mode m);
void pill(const String &text, int top, uint16_t color);
void sendScreenshot();
void showUpdate(const String &first, const String &second);

// ---------- UTF-8 text ----------

uint32_t nextCodepoint(const String &s, int &i) {
  uint8_t c = s[i++];
  if (c < 0x80) return c;
  int extra = c >= 0xF0 ? 3 : c >= 0xE0 ? 2 : 1;
  uint32_t cp = c & (0x3F >> extra);
  while (extra-- && i < (int)s.length()) cp = (cp << 6) | (s[i++] & 0x3F);
  return cp;
}

int glyphWidth(uint32_t cp) {
  bool wide = (cp >= 0x1100 && cp <= 0x115F) || (cp >= 0x2E80 && cp <= 0xA4CF) ||
              (cp >= 0xAC00 && cp <= 0xD7A3) || (cp >= 0xF900 && cp <= 0xFAFF) ||
              (cp >= 0xFF00 && cp <= 0xFF60) || cp >= 0x1F300;
  return wide ? 16 : 8;
}

int textWidth(const String &s) {
  int w = 0, i = 0;
  while (i < (int)s.length()) w += glyphWidth(nextCodepoint(s, i));
  return w;
}

// Clips the text to maxPx, ending with "…" when cut.
String clip(const String &s, int maxPx) {
  if (textWidth(s) <= maxPx) return s;
  int w = 0, i = 0, cut = 0;
  while (i < (int)s.length()) {
    int g = glyphWidth(nextCodepoint(s, i));
    if (w + g > maxPx - 8) break;
    w += g;
    cut = i;
  }
  return s.substring(0, cut) + "…";
}

// Usable width of the circle for the row from top to top+h.
int chordAt(int top, int h) {
  int dy = max(abs(top - 120), abs(top + h - 120));
  if (dy >= SAFE_R) return 0;
  return 2 * (int)sqrt((float)(SAFE_R * SAFE_R - dy * dy));
}

void drawLine(const String &s, int top, int h, uint16_t color) {
  String t = clip(s, chordAt(top, h));
  int w = textWidth(t);
  gfx->setTextColor(color);
  gfx->setCursor(120 - w / 2, top + (h - 16) / 2 + 13);  // Unifont baseline
  gfx->print(t);
}

int splitList(const String &field, String out[], int max) {
  int n = 0, start = 0;
  if (!field.length()) return 0;
  while (n < max) {
    int sep = field.indexOf('\x1f', start);
    if (sep < 0) { out[n++] = field.substring(start); break; }
    out[n++] = field.substring(start, sep);
    start = sep + 1;
  }
  return n;
}

// ---------- drawing ----------

void backlight(uint8_t level) { analogWrite(TFT_BL, level); }

// ---------- the three dots (Tredot) ----------
// In an arc as in the app icon. While the agent thinks each one wanders on its own (Lissajous
// curves with different frequencies: they drift apart and come back together); during a tool they
// light up in turn; in other states they stand still in the state's color. Only the three circles
// are erased and redrawn, not a rectangle: no flicker.
const float DOTS_ARC[3][2] = {{-0.68f, -0.2f}, {0, 0.32f}, {0.68f, -0.2f}};
const int DOTS_U = 26;  // pixels per unit: the arc is ~36 px wide
#define D_STILL 0       // still, in the given color
#define D_LOGO 1        // still, the first one orange: the icon
#define D_THINK 2       // each one wanders
#define D_TOOL 3        // light up in turn
#define D_BLINK 4       // blinking: waiting for you
int dotsStyle = D_LOGO, dotsY = 58;
uint16_t dotsColor = C_TEXT;
int dotX[3], dotY[3], dotR[3];
bool dotsShown = false;  // false after render() clears the screen: nothing to erase

void drawDots() {
  float t = millis() / 1000.0f;
  int x[3], y[3], r[3];
  uint16_t c[3];
  for (int i = 0; i < 3; i++) {
    float fx = DOTS_ARC[i][0], fy = DOTS_ARC[i][1], fr = 0.2f;
    c[i] = dotsColor;
    if (dotsStyle == D_LOGO) c[i] = i == 0 ? C_ORANGE : C_TEXT;
    if (dotsStyle == D_THINK) {
      fx = 0.62f * sinf(t * (1.3f + 0.41f * i) + 2.1f * i);
      fy = 0.55f * sinf(t * (1.9f + 0.29f * i) + 1.3f * i + 1);
      fr = 0.15f + 0.05f * sinf(t * (2.7f + i));
      c[i] = i == 0 ? C_ORANGE : C_TEXT;
    } else if (dotsStyle == D_TOOL) {
      bool lit = (millis() / 333) % 3 == (uint32_t)i;
      c[i] = lit ? C_ORANGE : C_DIM;
      fr = lit ? 0.24f : 0.18f;
    } else if (dotsStyle == D_BLINK) {
      c[i] = (millis() / 500) % 2 == 0 ? dotsColor : C_FAINT;  // like blinkOn(), defined below
    }
    x[i] = 120 + (int)lroundf(fx * DOTS_U);
    y[i] = dotsY + (int)lroundf(fy * DOTS_U);
    r[i] = max(2, (int)lroundf(fr * DOTS_U));
  }
  if (dotsShown)
    for (int i = 0; i < 3; i++) gfx->fillCircle(dotX[i], dotY[i], dotR[i] + 1, C_BG);
  for (int i = 0; i < 3; i++) {
    gfx->fillCircle(x[i], y[i], r[i], c[i]);
    dotX[i] = x[i]; dotY[i] = y[i]; dotR[i] = r[i];
  }
  dotsShown = true;
}

void dots(int style, int cy, uint16_t color = C_TEXT) {
  dotsStyle = style;
  dotsY = cy;
  dotsColor = color;
  drawDots();
}

bool dotsMoving() { return mode == M_THINK || mode == M_TOOL || mode == M_NOTE || mode == M_WAIT; }

// Row with the elapsed time of the current step, as in Claude Code
int elapsedTop() { return 106 + max(nLines, mode == M_THINK ? 0 : 1) * LINE_H + 6; }

void drawElapsed() {
  int secs = (millis() - turnStart) / 1000;
  if (secs == lastElapsed) return;
  lastElapsed = secs;
  String t = secs < 60 ? String(secs) + "s" : String(secs / 60) + "m " + String(secs % 60) + "s";
  if (session.length()) t += " · " + session;
  int top = elapsedTop();
  gfx->fillRect(0, top, 240, LINE_H, C_BG);
  drawLine(t, top, LINE_H, C_DIM);
}

void drawBlock(int top) {
  for (int i = 0; i < nLines; i++) drawLine(lines[i], top + i * LINE_H, LINE_H, C_TEXT);
}

void renderAsk() {
  // Few options, taller rows: easier to hit (Yes/No at 24 px were too close together)
  int optH = nOptions <= 2 ? 36 : nOptions == 3 ? 30 : OPT_H;
  // When space runs out, drop the secondary rows first.
  int detail = nLines;
  bool showSession = true, showQuestion = true;
  auto height = [&]() {
    return (showSession ? LINE_H : 0) + LINE_H + detail * LINE_H +
           (showQuestion ? LINE_H + 4 : 4) + nOptions * optH;
  };
  if (height() > 196) showQuestion = false;
  if (height() > 196) showSession = false;
  while (height() > 196 && detail > 1) detail--;

  int y = 120 - height() / 2;
  if (showSession) { drawLine(session, y, LINE_H, C_DIM); y += LINE_H; }
  drawLine("⏺ " + title, y, LINE_H, C_ORANGE);
  y += LINE_H;
  for (int i = 0; i < detail; i++) {
    String s = lines[i];
    if (i == detail - 1 && detail < nLines) s += " …";
    drawLine(s, y, LINE_H, C_TEXT);
    y += LINE_H;
  }
  if (showQuestion) { drawLine(tr(T_APPROVE), y + 2, LINE_H, C_DIM); y += LINE_H; }
  drawOptions(options, nOptions, y + 4, selected, optH);
}

bool canOpenMenu() {
  return mode == M_IDLE || mode == M_DONE || mode == M_THINK || mode == M_TOOL ||
         mode == M_SAVED || mode == M_ERROR;
}

// Menu, note, photo, video and agent reply own the screen; agent states wait behind them
bool localMode() {
  return mode == M_MENU || mode == M_REC || mode == M_PHOTO || mode == M_VIDEO || mode == M_REPLY;
}

void drawMenuRow() {
  if (canOpenMenu()) drawLine(tr(T_MENU), MENU_ROW, LINE_H, C_DIM);
}

void drawOptions(const String labels[], int n, int y, int sel, int rowH) {
  n = min(n, MAX_ROWS);
  for (int i = 0; i < n; i++) {
    String label = String(i == sel ? "❯ " : "  ") + (i + 1) + ". " + labels[i];
    optTop[i] = y;
    optBottom[i] = y + rowH;
    drawLine(label, y, rowH, i == sel ? C_ORANGE : C_TEXT);
    y += rowH;
  }
}

// Blinking red dot: recording is visible at a glance
bool blinkOn() { return (millis() / 500) % 2 == 0; }

void drawRecTime() {
  gfx->fillCircle(120, 62, 9, blinkOn() ? C_RED : C_BG);
  gfx->drawCircle(120, 62, 9, C_RED);
  uint32_t secs = (millis() - recStart) / 1000;
  char t[12];
  snprintf(t, sizeof(t), "%u:%02u", (unsigned)(secs / 60), (unsigned)(secs % 60));
  gfx->fillRect(60, 112, 120, LINE_H, C_BG);
  drawLine(t, 112, LINE_H, C_TEXT);
  // microphone level: a bar that follows the voice
  int w = min(160, recPeak * 160 / 12000);
  gfx->fillRect(40, 140, 160, 6, C_FAINT);
  gfx->fillRect(40, 140, w, 6, C_RED);
  recPeak = recPeak * 2 / 3;
}

void render() {
  gfx->fillScreen(C_BG);
  gfx->setTextSize(1);
  lastArcDeg = -1;
  dotsShown = false;
  switch (mode) {
    case M_IDLE:
      backlight(40);
      dots(D_LOGO, 96);
      drawLine(tr(T_READY), 116, LINE_H, C_DIM);
      break;
    case M_THINK:
      backlight(255);
      dots(D_THINK, 58);
      drawLine(tr(T_THINKING), 84, LINE_H, C_ORANGE);
      drawBlock(106);  // the last lines the agent is writing
      lastElapsed = -1;
      drawElapsed();
      break;
    case M_TOOL:
      backlight(255);
      dots(D_TOOL, 58);
      drawLine("⏺ " + title, 86, LINE_H, C_TEXT);
      drawBlock(106);
      lastElapsed = -1;
      drawElapsed();
      break;
    case M_DONE:
      backlight(255);
      dots(D_STILL, 50, C_GREEN);
      drawLine(tr(T_DONE), 70, LINE_H, C_GREEN);
      drawBlock(94);  // the start of the final answer
      drawLine(session, 94 + nLines * LINE_H + 6, LINE_H, C_DIM);
      break;
    case M_WAIT:
      backlight(255);
      // The title comes from the Mac ("Reply in Gemini" for Gemini CLI, hook.py), and below it
      // what the agent is asking about (the tool, from the bridge), if any
      nLines = min(nLines, 3);
      dots(D_BLINK, nLines ? 62 : 80, C_ORANGE);
      drawLine(title.length() ? title : String(tr(T_REPLY_IN_CLAUDE)), nLines ? 84 : 102, LINE_H, C_ORANGE);
      drawBlock(106);
      drawLine(session, (nLines ? 106 + nLines * LINE_H + 4 : 124), LINE_H, C_DIM);
      break;
    case M_ASK:
      backlight(255);
      renderAsk();
      break;
    case M_NOTE:
      backlight(255);
      dots(D_THINK, 62);
      drawLine(title, 96, LINE_H, C_ORANGE);
      drawBlock(118);
      break;
    case M_SAVED:
      backlight(255);
      dots(D_STILL, 58, C_GREEN);
      drawLine(title.length() ? title : String(tr(T_NOTE_SAVED)), 80, LINE_H, C_GREEN);
      drawBlock(104);
      break;
    case M_ERROR:
      backlight(255);
      dots(D_STILL, 58, C_RED);
      drawLine(title, 80, LINE_H, C_RED);
      drawBlock(104);
      break;
    case M_REC:
      backlight(255);
      if (prompt.length()) drawLine(prompt, 84, LINE_H, C_ORANGE);
      else drawLine(tr(recForAgent ? T_LISTENING : T_RECORDING), 84, LINE_H, C_RED);
      drawRecTime();
      drawLine(tr(T_TAP_TO_STOP), 160, LINE_H, C_DIM);
      break;
    case M_MENU: {
      backlight(255);
      String labels[N_MENU];
      for (int i = 0; i < N_MENU; i++) labels[i] = tr(MENU[i]);
      drawLine(tr(T_WHAT_NOW), 28, LINE_H, C_DIM);
      drawOptions(labels, N_MENU, 50, -1, 30);  // tall rows: easy to slip onto the next one
      break;
    }
    case M_REPLY: {
      backlight(255);
      int pages = (nReply + PAGE - 1) / PAGE;
      drawLine(replyTitle, 30, LINE_H, C_ORANGE);
      for (int i = 0; i < PAGE && replyPage * PAGE + i < nReply; i++)
        drawLine(reply[replyPage * PAGE + i], 52 + i * LINE_H, LINE_H, C_TEXT);
      String foot = pages > 1 ? String(replyPage + 1) + "/" + pages + (replyPage + 1 < pages ? " ›" : "")
                              : String(tr(T_TAP_TO_CLOSE));
      drawLine(foot, 184, LINE_H, C_DIM);
      break;
    }
    case M_PHOTO:
    case M_VIDEO:
      backlight(255);  // loop draws the viewfinder, frame by frame
      break;
  }
  drawMenuRow();
  modeSince = millis();
}

// Ring that shrinks as the time to answer runs out
void drawCountdown() {
  uint32_t elapsed = millis() - askStart;
  int deg = askMillis ? min<uint32_t>(360, elapsed * 360 / askMillis) : 0;
  if (deg == lastArcDeg) return;
  if (lastArcDeg < 0) gfx->fillArc(120, 120, 119, 117, 0, 360, C_ORANGE);
  if (deg > 0) gfx->fillArc(120, 120, 119, 117, 0, deg, C_FAINT);
  lastArcDeg = deg;
}

// ---------- link ----------

// One line at a time on the link: loop and cameraTask both write, and two interleaved encrypted
// frames would make the Mac lose sync
SemaphoreHandle_t sendLock = xSemaphoreCreateRecursiveMutex();

struct SendGuard {
  SendGuard() { xSemaphoreTakeRecursive(sendLock, portMAX_DELAY); }
  ~SendGuard() { xSemaphoreGiveRecursive(sendLock); }
};

// A line to the Mac: encrypted if the session is open
void sendLine(const String &s) {
  SendGuard guard;
  if (sec.active) {
    sec.begin(channel, s.length());
    sec.part(channel, s);
    sec.end(channel);
  } else {
    channel->print(s);
    channel->print('\n');
  }
}

void sendPlain(const String &s) {  // always plaintext: greeting and pairing
  SendGuard guard;
  channel->print(s);
  channel->print('\n');
}

int split(const String &line, String f[], int max) {
  int n = 0, start = 0;
  while (n < max) {
    int tab = line.indexOf('\t', start);
    if (tab < 0) { f[n++] = line.substring(start); break; }
    f[n++] = line.substring(start, tab);
    start = tab + 1;
  }
  return n;
}

void handleLine(const String &line) {
  String f[7];
  int n = split(line, f, 7);
  if (n == 0) return;
  if (ota.running) {  // during an update the screen is its own; the agent decides permissions
    if (f[0] == "A" && n >= 2) sendLine("R\t" + f[1] + "\t-1");
    return;
  }
  if (f[0] == "S" && n >= 2) {
    if (mode == M_ASK) return;  // the request stays until answered or closed with C
    String s = f[1];
    bool recording = localMode();
    title = n > 2 ? f[2] : "";
    nLines = splitList(n > 3 ? f[3] : "", lines, 4);
    session = n > 4 ? f[4] : "";
    Mode next = s == "think" ? M_THINK : s == "tool" ? M_TOOL : s == "done" ? M_DONE
              : s == "wait" ? M_WAIT : s == "note" ? M_NOTE : s == "saved" ? M_SAVED
              : s == "error" ? M_ERROR : M_IDLE;
    // The time is that of the current step, as in Claude Code: it restarts with every action
    if (next == M_THINK || next == M_TOOL) turnStart = millis();
    if (recording) { behind = next; return; }  // shown when the local mode ends
    mode = next;
    render();
  } else if (f[0] == "A" && n >= 7) {
    if (mode == M_REC || mode == M_PHOTO || mode == M_VIDEO) {
      sendLine("R\t" + f[1] + "\t-1");  // never interrupt a recording: the agent decides
      return;
    }
    if (mode == M_MENU) mode = behind;
    askId = f[1];
    askMillis = f[2].toInt() * 1000UL;
    title = f[3];
    nLines = splitList(f[4], lines, 4);
    session = f[5];
    nOptions = splitList(f[6], options, 4);
    selected = -1;
    askStart = millis();
    mode = M_ASK;
    render();
  } else if (f[0] == "M" && n >= 3) {
    replyTitle = f[1];
    nReply = splitList(f[2], reply, MAX_REPLY);
    replyPage = 0;
    sendLine("K\t" + String(nReply));
    if (mode == M_ASK || mode == M_REC || mode == M_PHOTO || mode == M_VIDEO) {
      replyPending = true;  // shown as soon as the screen is free
      return;
    }
    if (mode != M_REPLY && mode != M_MENU) behind = mode;
    mode = M_REPLY;
    render();
  } else if (f[0] == "X" && n >= 2) {
    bool busy = mode == M_ASK || mode == M_REC || mode == M_PHOTO || mode == M_VIDEO;
    if (busy || (f[1] == "listen" && !micOk)) { sendLine("XN"); return; }
    if (mode != M_REPLY && mode != M_MENU) behind = mode;
    prompt = n > 2 ? f[2] : "";
    if (f[1] == "photo") startCamera(M_PHOTO);
    else if (f[1] == "listen") startRecording(true);
    else sendLine("XN");
  } else if (f[0] == "SS") {
    sendScreenshot();
  } else if (f[0] == "L" && n >= 2) {
    if (mode != M_THINK) return;  // the text only matters while the agent is writing
    nLines = splitList(f[1], lines, 4);
    render();
  } else if (f[0] == "C" && n >= 2) {
    if (mode == M_ASK && f[1] == askId) { mode = M_THINK; render(); }
  }
}

void sendHello(const String &session) {
  sendPlain(String("H\t") + PROTOCOL + "\t" FIRMWARE "\t" + (micOk ? "ok" : "no") + "\t" + deviceId +
            "\t" + session);
}

// The Mac's language, from W: the current screen switches at once. Not the viewfinder: it has no
// texts of its own, and redrawing would erase the agent's prompt over it.
void applyLanguage(const String &code) {
  if (setLanguage(code) && mode != M_PHOTO && mode != M_VIDEO) render();
}

// W: the bridge connected over this link. Paired: open the encrypted session, on any link.
// Not paired: USB only, in plaintext.
void onGreeting(const String &line, int link) {
  String f[4];
  int n = split(line, f, 4);
  // The bridge is back: an update left halfway will never finish. Over Wi-Fi the closed connection
  // shows it; over USB the port stays open, so only this greeting does.
  if (ota.running) {
    ota.abort();
    mode = M_IDLE;
    render();
  }
  String session;
  if (sec.paired) {
    session = sec.start(n >= 3 ? f[2] : "");
    if (!session.length()) return;  // a bridge without encryption: ignore it
  } else if (link == 0) {
    sec.active = false;
    session = "-";
  } else {
    return;  // not paired: only USB is accepted
  }
  setActive(link);
  applyLanguage(n >= 4 ? f[3] : "");
  sendHello(session);
  if (sec.active) sendLine("I\twifi\t" + tcp.state);  // the Mac learns the Wi-Fi state at once
}

void startPairing(const String &line) {
  String f[3];
  if (split(line, f, 3) < 3 || !Secure::fromBase64(f[1], pairKey, 32)) return;
  setActive(0);
  askId = "pair";  // the request screen, but the module decides (onAskTap)
  askMillis = 60000;
  askStart = millis();
  title = tr(T_PAIR);
  nLines = 2;
  lines[0] = f[2];
  lines[1] = tr(T_WITH_THIS_MODULE);
  session = "";
  options[0] = tr(T_YES);
  options[1] = tr(T_NO);
  nOptions = 2;
  selected = -1;
  mode = M_ASK;
  render();
}

void endInstall(bool yes) {  // the answer to the update's "Install?" question
  askId = "";
  if (!yes) {
    ota.abort();
    sendLine(String("UN\t") + tr(T_DECLINED));
    mode = M_IDLE;
    render();
    pill(tr(T_NOT_UPDATED), 24, C_DIM);
    return;
  }
  String reply = ota.install();
  sendLine(reply);
  if (reply == "UD") {
    showUpdate(tr(T_INSTALLED), tr(T_RESTARTING));
    delay(1500);  // time for UD to leave the Bluetooth and Wi-Fi queues
    ESP.restart();
  }
  showUpdate(tr(T_UPDATE_FAILED), tr(T_KEEPING_OLD));
}

void endPairing(bool yes) {
  askId = "";
  if (yes) {
    sec.save(pairKey, lines[0]);
    ble.setVisible(true);
    sendPlain("PAIRED\t" + deviceId);
  } else {
    sendPlain("PAIRNO");
  }
  memset(pairKey, 0, sizeof pairKey);
  mode = M_IDLE;
  render();
  pill(tr(yes ? T_PAIRED : T_NOT_PAIRED), 24, yes ? C_GREEN : C_DIM);
}

void showUpdate(const String &first, const String &second) {
  mode = M_NOTE;
  title = tr(T_FIRMWARE_UPDATE);
  nLines = 2;
  lines[0] = first;
  lines[1] = second;
  session = "";
  render();
}

void onUpdate(const String &line) {
  String f[3];
  split(line, f, 3);
  if (f[0] == "U") {
    if (channel == &ble) {  // 3.6 MB over Bluetooth take ~5 minutes: Wi-Fi only (or USB with esptool)
      sendLine(String("UN\t") + tr(T_NOT_OVER_BLUETOOTH));
      return;
    }
    if (localMode()) {  // never interrupt a recording
      sendLine(String("UN\t") + tr(T_BUSY_RECORDING));
      return;
    }
    // On a weak signal the update would break off: harmless (the previous firmware stays until the
    // new one has fully arrived and been verified), but a waste of time
    if (channel == &tcp && WiFi.RSSI() < -80) {
      char why[160];
      snprintf(why, sizeof why, tr(T_WEAK_SIGNAL), (int)WiFi.RSSI());
      sendLine(String("UN\t") + why);
      return;
    }
    String g[4];
    split(line, g, 4);
    String reply = ota.start(f[1].toInt(), f[2], g[3]);
    sendLine(reply);
    if (ota.running) showUpdate("0%", tr(T_DO_NOT_UNPLUG));
  } else if (f[0] == "u") {
    int before = ota.total ? ota.done * 100 / ota.total : 0;
    sendLine(ota.chunk(f[1]));
    int now = ota.total ? ota.done * 100 / ota.total : 0;
    if (ota.running && now / 5 != before / 5) showUpdate(String(now) + "%", tr(T_DO_NOT_UNPLUG));
    if (!ota.running) showUpdate(tr(T_UPDATE_FAILED), tr(T_KEEPING_OLD));
  } else if (f[0] == "UE") {
    String reply = ota.verify();
    if (reply.length()) {
      sendLine(reply);
      showUpdate(tr(T_UPDATE_FAILED), tr(T_KEEPING_OLD));
      return;
    }
    askId = "ota";  // the request screen, but the module decides (onAskTap)
    askMillis = 60000;
    askStart = millis();
    title = tr(T_INSTALL);
    nLines = 2;
    lines[0] = String(tr(T_NEW_FIRMWARE)) + " " + ota.version;
    lines[1] = String(tr(T_CURRENT_FIRMWARE)) + " " FIRMWARE;
    session = "";
    options[0] = tr(T_YES);
    options[1] = tr(T_NO);
    nOptions = 2;
    selected = -1;
    mode = M_ASK;
    render();
  } else {  // UA
    ota.abort();
    mode = M_IDLE;
    render();
  }
}

String fromBase64Text(const String &b64) {
  uint8_t out[160];
  size_t got = 0;
  if (mbedtls_base64_decode(out, sizeof out - 1, &got, (const uint8_t *)b64.c_str(), b64.length()))
    return "";
  out[got] = 0;
  return String((const char *)out);
}

void pollChannel() {
  pollLinks(
      [](const String &line, int link) {
        if (line.startsWith("W")) onGreeting(line, link);
        else if (line.startsWith("PAIR\t") && link == 0) startPairing(line);  // USB only
        else if (!sec.paired && link == 0 && link == active) handleLine(line);  // not paired
        // other plaintext is ignored: once paired, only encrypted lines count
      },
      [](const uint8_t *frame, size_t size, int link) {
        String line;
        if (link != active || !sec.open(frame, size, line)) return;  // forged, replayed or stale
        if (line == "UNPAIR") {
          sec.forget();
          ble.setVisible(false);
          tcp.configure("", "");  // that Mac provided the network: forget it too
          sendPlain("UNPAIRED\t" + deviceId);
        } else if (line.startsWith("U\t") || line.startsWith("u\t") || line == "UE" || line == "UA") {
          onUpdate(line);
        } else if (line.startsWith("N\t")) {
          String f[3];
          split(line, f, 3);
          tcp.configure(fromBase64Text(f[1]), fromBase64Text(f[2]));
          sendLine("I\twifi\t" + tcp.state);
        } else {
          handleLine(line);
        }
      });
}

// ---------- touch ----------

bool readTouch(int &x, int &y) {
  if (digitalRead(TOUCH_INT) != LOW) return false;
  uint8_t t[5] = {0};
  if (Wire.requestFrom(TOUCH_ADDR, 5) != 5) return false;
  Wire.readBytes(t, 5);
  if (t[0] != 0x01) return false;
  x = t[2];
  y = t[4];
  return true;
}

// The row under the finger; the 3 px margin applies only when the touch misses every row,
// otherwise one row's margin would steal touches from its neighbor.
int optionAt(int y, int n) {
  for (int i = 0; i < n; i++)
    if (y >= optTop[i] && y < optBottom[i]) return i;
  for (int i = 0; i < n; i++)
    if (y >= optTop[i] - 3 && y < optBottom[i] + 3) return i;
  return -1;
}

void onAskTap(int y) {
  int hit = optionAt(y, nOptions);
  if (hit < 0) return;
  if (hit != selected) {  // first tap selects
    selected = hit;
    render();
    return;
  }
  if (askId == "pair") {  // the module decides pairing, not the agent
    endPairing(hit == 0);
    return;
  }
  if (askId == "ota") {  // and installing new firmware
    endInstall(hit == 0);
    return;
  }
  sendLine("R\t" + askId + "\t" + String(hit));  // second tap confirms
  askId = "";
  mode = replyPending ? M_REPLY : M_THINK;
  replyPending = false;
  render();
}

// ---------- audio ----------

void audioTask(void *) {
  static uint8_t buf[512];  // 256 samples = 16 ms
  int32_t dc = 0;
  bool first = true;
  for (;;) {
    if (!audioOn) { first = true; vTaskDelay(10); continue; }
    size_t got = mic.readBytes((char *)buf, sizeof(buf));
    if (!got) continue;
    // The PDM output has a DC offset that is not voice: remove it with a slow running average,
    // so the level bar stays empty when the microphone picks up nothing.
    int16_t *pcm = (int16_t *)buf;
    for (size_t i = 0; i < got / 2; i++) {
      if (first) { dc = (int32_t)pcm[i] << 8; first = false; }
      dc += (((int32_t)pcm[i] << 8) - dc) >> 10;
      int v = constrain((int)pcm[i] - (int)(dc >> 8), -32768, 32767);
      pcm[i] = v;
      if (abs(v) > recPeak) recPeak = abs(v);
    }
    for (size_t i = 0; i < got; i++) {  // ring full: the newest audio is dropped
      size_t next = (ringHead + 1) % RING;
      if (next == ringTail) break;
      ring[ringHead] = buf[i];
      ringHead = next;
    }
  }
}

Adpcm adpcm;  // state carries over between blocks; each block repeats it in its header

void startAudio() {
  ringTail = ringHead;
  adpcm = Adpcm();
  recPeak = 0;
  audioOn = micOk;
}

// Sends the buffered audio; with all=true also the last partial block
void drainAudio(bool all = false) {
  static uint8_t out[960];  // 480 samples; multiple of 3 so base64 chunks stay independent
  static uint8_t packed[243];  // 3 + 480/2: also a multiple of 3
  bool compress = channel == &ble;  // Bluetooth cannot carry raw PCM (~43 kB/s)
  size_t block = compress ? 960 : 480;
  for (;;) {
    size_t avail = (ringHead + RING - ringTail) % RING;
    if (avail < block && !(all && avail)) return;
    size_t n = min(avail, block) & ~(size_t)3;  // whole samples, an even number of them
    if (n == 0) { ringTail = ringHead; return; }
    for (size_t i = 0; i < n; i++) out[i] = ring[(ringTail + i) % RING];
    ringTail = (ringTail + n) % RING;
    if (compress) sendLine("VA\t" + base64::encode(packed, adpcm.encode((int16_t *)out, n / 2, packed)));
    else sendLine("V\t" + base64::encode(out, n));
  }
}

void stopAudio() {
  audioOn = false;
  delay(30);
  drainAudio(true);
}

// ---------- leaving the local modes ----------

void leaveLocal() {
  prompt = "";
  if (replyPending) {  // an agent reply was waiting for the screen
    replyPending = false;
    mode = M_REPLY;
  } else {
    mode = behind == M_DONE || behind == M_SAVED || behind == M_ERROR ? M_IDLE : behind;
  }
  render();
}

void startRecording(bool agent) {
  recForAgent = agent;
  mode = M_REC;
  recStart = millis();
  startAudio();
  sendLine(agent ? "VB\tagent" : "VB");
  render();
}

void stopRecording() {
  stopAudio();
  sendLine("VE");
  leaveLocal();
}

// ---------- camera ----------

bool cameraOn() {
  if (camOk) return true;
  camera_config_t c = {};
  c.pin_pwdn = -1;
  c.pin_reset = -1;
  c.pin_xclk = CAM_XCLK;
  c.pin_sccb_sda = CAM_SIOD;
  c.pin_sccb_scl = CAM_SIOC;
  c.sccb_i2c_port = 1;  // touch uses port 0
  c.pin_d0 = CAM_D[0]; c.pin_d1 = CAM_D[1]; c.pin_d2 = CAM_D[2]; c.pin_d3 = CAM_D[3];
  c.pin_d4 = CAM_D[4]; c.pin_d5 = CAM_D[5]; c.pin_d6 = CAM_D[6]; c.pin_d7 = CAM_D[7];
  c.pin_vsync = CAM_VSYNC;
  c.pin_href = CAM_HREF;
  c.pin_pclk = CAM_PCLK;
  c.xclk_freq_hz = 20000000;
  c.ledc_timer = LEDC_TIMER_1;  // timer 0 drives the backlight
  c.ledc_channel = LEDC_CHANNEL_7;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size = FRAMESIZE_UXGA;  // buffers must fit a full-resolution photo
  c.jpeg_quality = 12;
  c.fb_count = 2;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;
  if (esp_camera_init(&c) != ESP_OK) return false;
  sensor_t *s = esp_camera_sensor_get();
  s->set_framesize(s, FRAMESIZE_VGA);  // viewfinder and video: 640x480
  if (s->id.PID == OV3660_PID) { s->set_vflip(s, 1); s->set_brightness(s, 1); }
  static bool told = false;  // the sensor varies by batch: tell the Mac once
  if (!told) {
    told = true;
    const char *name = s->id.PID == OV2640_PID ? "OV2640" : s->id.PID == OV3660_PID ? "OV3660"
                     : s->id.PID == OV5640_PID ? "OV5640" : nullptr;
    sendLine(String("I\tcam\t") + (name ? name : String(s->id.PID, HEX)));
  }
  camOk = true;
  return true;
}

void cameraOff() {
  if (camOk) esp_camera_deinit();
  camOk = false;
}

void dropFrames(int n) {
  while (n--) {
    camera_fb_t *fb = esp_camera_fb_get();
    if (fb) esp_camera_fb_return(fb);
  }
}

// Camera diagnostics (# lines on serial, every 3 s): where the time of each frame goes
uint32_t camWait = 0, camSend = 0, camShow = 0, camFrames = 0, camBytes = 0, camSince = 0;

// During video, capture and sending run in a separate task (cameraTask, on the other core): in line
// with the viewfinder they stalled ~285 ms on every refresh (7 frames/s, measured). The viewfinder,
// in loop, draws the latest frame whenever it has time.
const size_t VF_MAX = 64 * 1024;
uint8_t *vfJpeg = nullptr;  // the latest frame for the viewfinder (in PSRAM)
volatile size_t vfLen = 0;
volatile bool vfFresh = false;  // a new frame is waiting to be drawn
volatile bool camRun = false;  // cameraTask captures and sends
SemaphoreHandle_t camLock = xSemaphoreCreateMutex();  // stopCamera waits for the frame in flight

uint32_t vfDecode = 0, vfRotate = 0, vfDraw = 0, vfShown = 0;  // viewfinder diagnostics

// The label over the viewfinder (video time, agent prompt) lives in this rectangle, which the
// viewfinder never paints: drawing the image and then the label over it made the label flicker on
// every frame. holeW = 0: no label.
// The hole has rounded corners (radius holeR): row by row, the label background and the viewfinder
// split the pixels with the same inset, so the corners show the live image (a square hole left
// stale green pixels there).
int holeX = 0, holeY = 0, holeW = 0, holeH = 0, holeR = 0;

// how far the rounded edge is inset at row y (counted from the top of the hole)
int holeInset(int y) {
  float dy;
  if (y < holeR) dy = holeR - y - 0.5f;
  else if (y >= holeH - holeR) dy = y - (holeH - holeR) + 0.5f;
  else return 0;
  return (int)roundf(holeR - sqrtf(holeR * holeR - dy * dy));
}

// the label background: exactly the pixels the viewfinder leaves alone
void fillHole(uint16_t color) {
  for (int y = 0; y < holeH; y++) {
    int inset = holeInset(y);
    gfx->drawFastHLine(holeX + inset, holeY + y, holeW - 2 * inset, color);
  }
}

void drawAround(const uint16_t *img) {
  if (!holeW) {
    gfx->draw16bitRGBBitmap(0, 0, (uint16_t *)img, 240, 240);
    return;
  }
  if (holeY > 0) gfx->draw16bitRGBBitmap(0, 0, (uint16_t *)img, 240, holeY);
  for (int y = holeY; y < holeY + holeH; y++) {  // label rows: only the sides (and corners)
    int inset = holeInset(y - holeY);
    int left = holeX + inset, right = holeX + holeW - inset;
    if (left > 0) gfx->draw16bitRGBBitmap(0, y, (uint16_t *)img + y * 240, left, 1);
    if (right < 240) gfx->draw16bitRGBBitmap(right, y, (uint16_t *)img + y * 240 + right, 240 - right, 1);
  }
  int below = holeY + holeH;
  if (below < 240) gfx->draw16bitRGBBitmap(0, below, (uint16_t *)img + below * 240, 240, 240 - below);
}

// The viewfinder decodes with JPEGDEC: jpg2rgb565 (TJpgDec) took ~270 ms per frame, 1-2 refreshes
// a second (measured). Each decoded block is copied into the 320x240 buffer, little-endian RGB565.
JPEGDEC jpegDecoder;

int previewBlock(JPEGDRAW *d) {
  uint16_t *dst = (uint16_t *)preview;
  for (int row = 0; row < d->iHeight && d->y + row < 240; row++)
    memcpy(&dst[(d->y + row) * 320 + d->x], &d->pPixels[row * d->iWidth],
           min(d->iWidthUsed, 320 - d->x) * 2);
  return 1;
}

// Half scale (320x240): quarter scale was smooth but blurry (120x120 scaled up 2x)
bool decodeHalf(const uint8_t *jpeg, size_t len) {
  if (!jpegDecoder.openRAM((uint8_t *)jpeg, len, previewBlock)) return false;
  jpegDecoder.setPixelType(RGB565_LITTLE_ENDIAN);
  bool ok = jpegDecoder.decode(0, 0, JPEG_SCALE_HALF);
  jpegDecoder.close();
  return ok;
}

// Draws a 640x480 JPEG in the circle: decodes at 320x240, rotates 90 degrees clockwise (the sensor
// is mounted sideways on the Sense) and crops the central 240x240. The Mac applies the same
// rotation to saved photos and videos.
void showJpeg(const uint8_t *jpeg, size_t len) {
  uint32_t t0 = micros();
  if (!decodeHalf(jpeg, len)) return;
  uint32_t t1 = micros();
  const uint16_t *src = (const uint16_t *)preview;
  uint16_t *dst = (uint16_t *)crop;
  // In 16x16 tiles: row by row, the reads jumped around PSRAM on every pixel (very slow); tiles
  // keep what is read and written in the cache
  for (int ty = 0; ty < 240; ty += 16)
    for (int tx = 0; tx < 240; tx += 16)
      for (int y = ty; y < ty + 16; y++)
        for (int x = tx; x < tx + 16; x++) dst[y * 240 + x] = src[(239 - x) * 320 + (y + 40)];
  uint32_t t2 = micros();
  drawAround(dst);
  vfDecode += t1 - t0; vfRotate += t2 - t1; vfDraw += micros() - t2; vfShown++;
}

// Sends a JPEG as base64 in pieces, without keeping an encoded copy in memory
// One line made of a head and binary data in base64, sent in pieces: no encoded copy in memory
void sendBase64(const String &head, const uint8_t *data, size_t len) {
  SendGuard guard;
  if (sec.active) sec.begin(channel, head.length() + 4 * ((len + 2) / 3));  // base64 length
  if (sec.active) sec.part(channel, head);
  else channel->print(head);
  for (size_t off = 0; off < len; off += 3072) {  // multiple of 3: independent base64 pieces
    size_t n = min((size_t)3072, len - off);
    String chunk = base64::encode(data + off, n);
    if (sec.active) sec.part(channel, chunk);
    else channel->print(chunk);
  }
  if (sec.active) sec.end(channel);
  else channel->print('\n');
}

void sendJpeg(const String &head, camera_fb_t *fb) { sendBase64(head, fb->buf, fb->len); }

void sendScreenshot() {
  if (!bus->pixels) return;
  sendBase64(String("SS\t") + MirrorBus::W + "\t" + MirrorBus::H + "\t", (const uint8_t *)bus->pixels,
             MirrorBus::W * MirrorBus::H * sizeof(uint16_t));
}

void pill(const String &text, int top, uint16_t color) {
  int w = textWidth(text) + 20;
  gfx->fillRoundRect(120 - w / 2, top, w, LINE_H + 4, 10, C_BG);
  drawLine(text, top + 2, LINE_H, color);
}

// The agent prompt over the photo viewfinder: drawn once, the viewfinder paints around it
void promptPill() {
  if (!prompt.length() || holeW) return;
  int w = textWidth(prompt) + 20;
  holeX = 120 - w / 2; holeY = 24; holeW = w; holeH = LINE_H + 4; holeR = 10;
  fillHole(C_BG);
  drawLine(prompt, 26, LINE_H, C_ORANGE);
}

// At the top of the viewfinder: blinking red dot + time, as on camcorders
void recBadge() {
  uint32_t secs = (millis() - recStart) / 1000;
  static int32_t drawn = -1;  // seconds*2 + dot lit: redraw only when it changes
  int32_t now = secs * 2 + (blinkOn() ? 1 : 0);
  if (now == drawn && holeW) return;
  drawn = now;
  char t[12];
  snprintf(t, sizeof(t), "%u:%02u", (unsigned)(secs / 60), (unsigned)(secs % 60));
  int w = 80;  // fixed: going from 9:59 to 10:00 leaves no leftovers
  int x = 120 - w / 2;
  holeX = x; holeY = 20; holeW = w; holeH = 24; holeR = 12;
  fillHole(C_BG);
  gfx->fillCircle(x + 13, 32, 6, blinkOn() ? C_RED : C_BG);
  gfx->drawCircle(x + 13, 32, 6, C_RED);
  gfx->setTextColor(C_TEXT);
  gfx->setCursor(x + 25, 37);
  gfx->print(t);
}

void startCamera(Mode m) {
  if (m == M_VIDEO && channel == &ble) {  // ~8 frames of 30 kB a second: too much for BLE
    mode = M_ERROR;
    title = tr(T_NO_VIDEO_BLUETOOTH);
    nLines = 2;
    lines[0] = tr(T_TOO_SLOW);
    lines[1] = tr(T_USE_CABLE_OR_WIFI);
    render();
    return;
  }
  gfx->fillScreen(C_BG);
  drawLine(tr(T_STARTING_CAMERA), 112, LINE_H, C_DIM);
  if (!cameraOn()) {
    mode = M_ERROR;
    title = tr(T_NO_CAMERA);
    nLines = 2;
    lines[0] = tr(T_CHECK_CAMERA);
    lines[1] = tr(T_CAMERA_CONNECTOR);
    render();
    return;
  }
  mode = m;
  recStart = millis();
  holeW = 0;  // no label over the viewfinder until one is drawn
  if (m == M_VIDEO) {
    startAudio();
    sendLine("MB");
    vfFresh = false;
    camSince = millis();
    camRun = true;  // from here cameraTask captures and sends
  }
  render();
}

// A tap closes: back to the previous screen, with a short confirmation on top
void stopCamera() {
  bool video = mode == M_VIDEO;
  if (video) {
    camRun = false;
    xSemaphoreTake(camLock, portMAX_DELAY);  // the frame in flight finishes, then nothing more
    xSemaphoreGive(camLock);
    stopAudio();
    sendLine("ME");
  }
  cameraOff();
  leaveLocal();
  if (video) pill(tr(T_VIDEO_SAVED), 24, C_GREEN);
}

void takePhoto() {
  gfx->fillScreen(C_TEXT);  // flash: the shot was taken
  sensor_t *s = esp_camera_sensor_get();
  // 1024x768 over Bluetooth (about half the data): at ~10 kB/s 1600x1200 takes 8 seconds
  s->set_framesize(s, channel == &ble ? FRAMESIZE_XGA : FRAMESIZE_UXGA);
  dropFrames(2);  // the sensor settles on the new resolution
  camera_fb_t *fb = esp_camera_fb_get();
  bool ok = fb != nullptr;
  if (ok) {
    sendJpeg("P\t", fb);
    esp_camera_fb_return(fb);
  }
  cameraOff();
  leaveLocal();
  pill(tr(ok ? T_PHOTO_SAVED : T_PHOTO_ERROR), 24, ok ? C_GREEN : C_RED);
}


// Above this many queued bytes a frame is skipped (viewfinder only): when the network is slower
// than the camera the 256 kB queue filled up and every other writer (voice, screen) blocked: loop
// fell to 3 iterations a second and the viewfinder froze (measured). This way the recording gets
// the frames the network can carry. 160 kB (~0.5 s): at 64 kB short Wi-Fi hiccups left gaps of up
// to 0.6 s in the video; blocking only happens with the queue full (256 kB).
const size_t VIDEO_BACKLOG = 160 * 1024;
uint32_t camSkipped = 0;

size_t backlog() {
  if (channel == &tcp) return tcp.queued();
  if (channel == &ble) return ble.queued();
  return 0;  // USB: Serial blocks by itself, and keeps up
}

void cameraTask(void *) {
  for (;;) {
    if (!camRun) { vTaskDelay(5); continue; }
    xSemaphoreTake(camLock, portMAX_DELAY);
    if (camRun) {
      uint32_t t0 = micros();
      camera_fb_t *fb = esp_camera_fb_get();
      if (fb) {
        uint32_t t1 = micros();
        if (backlog() < VIDEO_BACKLOG) sendJpeg("F\t" + String(millis() - recStart) + "\t", fb);
        else camSkipped++;
        uint32_t t2 = micros();
        if (!vfFresh && fb->len <= VF_MAX) {
          memcpy(vfJpeg, fb->buf, fb->len);
          vfLen = fb->len;
          vfFresh = true;
        }
        esp_camera_fb_return(fb);
        camWait += t1 - t0; camSend += t2 - t1; camBytes += fb->len; camFrames++;
      }
    }
    xSemaphoreGive(camLock);
  }
}

void videoReport() {
  if (millis() - camSince < 3000) return;
  uint32_t dt = millis() - camSince;
  DIAG("# video: %u frames/s captured, %u skipped (%u kB, wait %u ms, send %u ms); viewfinder %u/s "
       "(decode %u ms, rotate %u ms, draw %u ms)\n",
       (unsigned)(camFrames * 1000 / dt), (unsigned)camSkipped, (unsigned)(camFrames ? camBytes / camFrames / 1024 : 0),
       (unsigned)(camFrames ? camWait / camFrames / 1000 : 0), (unsigned)(camFrames ? camSend / camFrames / 1000 : 0),
       (unsigned)(vfShown * 1000 / dt), (unsigned)(vfShown ? vfDecode / vfShown / 1000 : 0),
       (unsigned)(vfShown ? vfRotate / vfShown / 1000 : 0), (unsigned)(vfShown ? vfDraw / vfShown / 1000 : 0));
  camWait = camSend = camFrames = camBytes = camSkipped = vfDecode = vfRotate = vfDraw = vfShown = 0;
  camSince = millis();
}

void cameraLoop() {
  if (mode == M_VIDEO) {  // only the viewfinder here: cameraTask captures and sends
    if (vfFresh) {
      showJpeg(vfJpeg, vfLen);
      vfFresh = false;
      recBadge();
    }
    videoReport();
    if (millis() - recStart > VIDEO_MAX_MS) stopCamera();
    return;
  }
  // photo viewfinder
  uint32_t t0 = micros();
  camera_fb_t *fb = esp_camera_fb_get();
  if (!fb) return;
  uint32_t t1 = micros();
  showJpeg(fb->buf, fb->len);
  promptPill();  // what the agent wants to see
  camWait += t1 - t0; camShow += micros() - t1; camBytes += fb->len; camFrames++;
  if (millis() - camSince > 3000 && camFrames) {
    DIAG("# camera: %u frames/s, %u kB each; per frame: sensor wait %u ms, viewfinder %u ms\n",
         (unsigned)(camFrames * 1000 / (millis() - camSince)), (unsigned)(camBytes / camFrames / 1024),
         (unsigned)(camWait / camFrames / 1000), (unsigned)(camShow / camFrames / 1000));
    camWait = camShow = camFrames = camBytes = 0;
    camSince = millis();
  }
  esp_camera_fb_return(fb);
}

void onMenuTap(int y) {
  int hit = optionAt(y, N_MENU);
  if (hit == 0 && micOk) startRecording(true);
  else if (hit == 1 && micOk) startRecording(false);
  else if (hit == 2) startCamera(M_PHOTO);
  else if (hit == 3) startCamera(M_VIDEO);
  else if (hit == 4) leaveLocal();
}

void pollTouch() {
  // The CHSC6X pulses INT while the finger rests on the screen: a new touch counts only after
  // 150 ms without readings.
  static uint32_t lastSeen = 0;
  int x, y;
  bool now = readTouch(x, y);
  bool fresh = now && millis() - lastSeen > 150;
  if (now) lastSeen = millis();
  if (!fresh) return;
  sendLine("T\t" + String(x) + "\t" + String(y));
  if (mode == M_ASK && millis() - modeSince > 300) onAskTap(y);
  else if (mode == M_REC && millis() - modeSince > 500) stopRecording();
  else if (mode == M_MENU && millis() - modeSince > 300) onMenuTap(y);
  else if (mode == M_REPLY && millis() - modeSince > 300) {
    if (++replyPage * PAGE >= nReply) leaveLocal(); else render();
  }
  else if (mode == M_PHOTO && millis() - modeSince > 500) takePhoto();
  else if (mode == M_VIDEO && millis() - modeSince > 800) stopCamera();
  else if (canOpenMenu() && y >= MENU_ROW - 8) { behind = mode; mode = M_MENU; render(); }
  else if (mode == M_DONE || mode == M_SAVED || mode == M_ERROR) { mode = M_IDLE; render(); }
}

// ---------- main ----------

void setup() {
  Serial.setTxBufferSize(16384);  // photos and video go through here
  // The stock RX buffer is 256 bytes: an agent reply arriving while the screen was drawing
  // overflowed and was lost
  Serial.setRxBufferSize(16384);
  Serial.begin(115200);
  pinMode(TFT_BL, OUTPUT);
  pinMode(TOUCH_INT, INPUT_PULLUP);
  Wire.begin();
  bus->allocate();  // before the first pixel, so the screenshot copy is complete
  gfx->begin(80000000);  // 80 MHz: a viewfinder frame took ~39 ms to draw at 40 MHz
  gfx->setFont(u8g2_font_unifont_h_utf8);
  gfx->setUTF8Print(true);
  randomSeed(esp_random());
  mic.setPinsPdmRx(MIC_CLK, MIC_DATA);
  micOk = mic.begin(I2S_MODE_PDM_RX, MIC_RATE, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
  ring = (uint8_t *)ps_malloc(RING);
  preview = (uint8_t *)ps_malloc(320 * 240 * 2);
  crop = (uint8_t *)ps_malloc(240 * 240 * 2);
  xTaskCreatePinnedToCore(audioTask, "audio", 4096, nullptr, 5, nullptr, 0);
  vfJpeg = (uint8_t *)ps_malloc(VF_MAX);
  // on the other core from loop (which draws the viewfinder), below audio in priority
  xTaskCreatePinnedToCore(cameraTask, "camera", 8192, nullptr, 2, nullptr, 0);
  addLink("usb", &Serial);
  char name[16];  // Tredot-xxxx: the last digits of the chip id, to tell two modules apart
  snprintf(name, sizeof name, "Tredot-%04X", (uint16_t)(ESP.getEfuseMac() >> 32));
  deviceId = name;
  sec.load();
  loadLanguage();
  ble.begin(name, sec.paired);  // Bluetooth advertises only when a Mac is paired
  addLink("ble", &ble);
  tcp.begin(deviceId);  // Wi-Fi, if the Mac provided a network
  addLink("wifi", &tcp);
  render();
  sendHello("boot");
}

void loop() {
  if (channel == &ble && !ble.connected) {  // the Mac on Bluetooth went away
    setActive(0);
    sec.active = false;  // the session was its own: on USB a new one opens with W
  }
  if (channel == &tcp && !tcp.connected()) {  // the Mac on Wi-Fi went away
    setActive(0);
    sec.active = false;
  }
  if (ota.running && !sec.active) {  // the Mac went away in the middle of an update
    ota.abort();
    mode = M_IDLE;
    render();
  }
  // new firmware declares itself good once it has talked to the Mac, or after 2 minutes without a crash
  if (sec.active || millis() > 120000) Ota::markValid();
  if (tcp.poll(channel == &tcp && sec.active, ota.running) && sec.active) sendLine("I\twifi\t" + tcp.state);
  ble.pause(channel == &tcp && sec.active);  // Wi-Fi in use: Bluetooth stops advertising
  if (mode == M_ASK && askId == "pair" && millis() - askStart > askMillis) endPairing(false);
  if (mode == M_ASK && askId == "ota" && millis() - askStart > askMillis) endInstall(false);
  static uint32_t lastReport = 0;  // link diagnostics on serial, every 3 s
  static uint32_t loops = 0;  // loop iterations: measures screen smoothness
  loops++;
  if (channel != &Serial && millis() - lastReport > 3000) {
    DIAG("# loop: %u/s, mode %d\n", (unsigned)(loops * 1000 / (millis() - lastReport)), (int)mode);
    loops = 0;
    lastReport = millis();
    if (channel == &ble) ble.report();
    else tcp.report();
  }
  pollChannel();
  pollTouch();
  if (mode == M_REC || mode == M_VIDEO) drainAudio();
  if (mode == M_REC && millis() - recStart > REC_MAX_MS) stopRecording();
  if (mode == M_PHOTO || mode == M_VIDEO) cameraLoop();

  static uint32_t lastDots = 0;  // the dots: ~30 frames a second, only the three circles
  if (dotsMoving() && millis() - lastDots >= 33) {
    lastDots = millis();
    drawDots();
  }
  static uint32_t lastTick = 0;
  if (millis() - lastTick >= 120) {
    lastTick = millis();
    if (mode == M_THINK || mode == M_TOOL) drawElapsed();
    if (mode == M_ASK) drawCountdown();
    if (mode == M_REC) drawRecTime();
    // after 5 minutes with no news, "Done" goes back to idle and dims the backlight
    if ((mode == M_DONE || mode == M_SAVED) && millis() - modeSince > 300000UL) {
      mode = M_IDLE;
      render();
    }
  }
  if (!localMode()) delay(5);  // when recording, microphone and camera set the pace
}
