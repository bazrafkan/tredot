// Pairing and encryption with the Mac: the same as mac/security.py (full explanation there).
//
// Pairing: over USB the Mac sends PAIR <key> <name>; the module asks for confirmation by touch and
// stores the key in NVS. Session: on every W the module picks a random nonce, returns it in H and
// derives the keys (HKDF), one per direction. From then on every line is an AES-256-GCM frame:
// 0x00, length (3 bytes), counter (8 bytes), ciphertext, tag (16 bytes).
#pragma once
#include <Preferences.h>
#include "esp_random.h"
#include "mbedtls/base64.h"
#include "mbedtls/gcm.h"
#include "mbedtls/hkdf.h"
#include "mbedtls/md.h"

const size_t SEC_HEADER = 12, SEC_TAG = 16;

class Secure {
 public:
  Secure() {
    mbedtls_gcm_init(&out);
    mbedtls_gcm_init(&in);
  }

  bool paired = false;
  bool active = false;  // session open: only encrypted lines are sent and accepted
  String macName;

  void load() {
    Preferences p;
    p.begin("tredot", true);
    paired = p.getBytes("key", key, 32) == 32;
    macName = p.getString("mac", "");
    p.end();
  }

  void save(const uint8_t *k, const String &name) {
    Preferences p;
    p.begin("tredot", false);
    p.putBytes("key", k, 32);
    p.putString("mac", name);
    p.end();
    memcpy(key, k, 32);
    macName = name;
    paired = true;
    active = false;  // the Mac opens a new session right after
  }

  void forget() {
    Preferences p;
    p.begin("tredot", false);
    p.clear();
    p.end();
    memset(key, 0, sizeof key);
    paired = active = false;
  }

  // W <Mac nonce>: opens the session; returns the module nonce (base64) for H
  String start(const String &macNonceB64) {
    uint8_t salt[32];
    size_t got = 0;
    if (mbedtls_base64_decode(salt, 16, &got, (const uint8_t *)macNonceB64.c_str(),
                              macNonceB64.length()) != 0 || got != 16)
      return "";
    esp_fill_random(salt + 16, 16);
    uint8_t m2d[32], d2m[32];
    const mbedtls_md_info_t *sha = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    mbedtls_hkdf(sha, salt, 32, key, 32, (const uint8_t *)"tredot m2d", 10, m2d, 32);
    mbedtls_hkdf(sha, salt, 32, key, 32, (const uint8_t *)"tredot d2m", 10, d2m, 32);
    mbedtls_gcm_free(&out);
    mbedtls_gcm_free(&in);
    mbedtls_gcm_init(&out);
    mbedtls_gcm_init(&in);
    mbedtls_gcm_setkey(&out, MBEDTLS_CIPHER_ID_AES, d2m, 256);
    mbedtls_gcm_setkey(&in, MBEDTLS_CIPHER_ID_AES, m2d, 256);
    sent = 0;
    received = -1;
    active = true;
    return toBase64(salt + 16, 16);
  }

  // ---------- outgoing: one frame in pieces, never all in memory (photos) ----------

  void begin(Stream *s, size_t len) {
    uint8_t header[SEC_HEADER], iv[12];
    header[0] = 0;
    header[1] = len >> 16;
    header[2] = len >> 8;
    header[3] = len;
    for (int i = 0; i < 8; i++) header[4 + i] = sent >> (56 - 8 * i);
    memcpy(iv, "d2m", 4);
    memcpy(iv + 4, header + 4, 8);
    sent++;
    pendLen = 0;
    mbedtls_gcm_starts(&out, MBEDTLS_GCM_ENCRYPT, iv, 12);
    mbedtls_gcm_update_ad(&out, header, SEC_HEADER);
    s->write(header, SEC_HEADER);
  }

  // AES only gets multiples of 16 bytes: the ESP32 hardware AES computes a wrong tag with unaligned
  // updates (tested: photos, starting with "P\t", were rejected). The remainder waits in pend for
  // the next piece, or end().
  void part(Stream *s, const uint8_t *p, size_t n) {
    if (pendLen) {
      size_t k = min(n, (size_t)16 - pendLen);
      memcpy(pend + pendLen, p, k);
      pendLen += k;
      p += k;
      n -= k;
      if (pendLen < 16) return;
      update(s, pend, 16);
      pendLen = 0;
    }
    size_t aligned = n - n % 16;
    for (size_t off = 0; off < aligned; off += 512) update(s, p + off, min((size_t)512, aligned - off));
    pendLen = n - aligned;
    memcpy(pend, p + aligned, pendLen);
  }

  void part(Stream *s, const String &text) { part(s, (const uint8_t *)text.c_str(), text.length()); }

  void end(Stream *s) {
    if (pendLen) update(s, pend, pendLen);  // the last piece may be short
    pendLen = 0;
    uint8_t rest[16], tag[SEC_TAG];
    size_t olen = 0;
    mbedtls_gcm_finish(&out, rest, sizeof rest, &olen, tag, SEC_TAG);
    if (olen) s->write(rest, olen);
    s->write(tag, SEC_TAG);
  }

  // ---------- incoming: whole frame -> line, or false if forged or replayed ----------

  bool open(const uint8_t *frame, size_t size, String &line) {
    if (!active || size < SEC_HEADER + SEC_TAG) return false;
    size_t len = ((size_t)frame[1] << 16) | (frame[2] << 8) | frame[3];
    if (size != SEC_HEADER + len + SEC_TAG) return false;
    int64_t counter = 0;
    for (int i = 0; i < 8; i++) counter = (counter << 8) | frame[4 + i];
    if (counter <= received) return false;  // replayed: someone resends recorded lines
    uint8_t iv[12];
    memcpy(iv, "m2d", 4);
    memcpy(iv + 4, frame + 4, 8);
    uint8_t *plain = (uint8_t *)malloc(len + 1);
    if (!plain) return false;
    bool ok = mbedtls_gcm_auth_decrypt(&in, len, iv, 12, frame, SEC_HEADER,
                                       frame + SEC_HEADER + len, SEC_TAG,
                                       frame + SEC_HEADER, plain) == 0;
    if (ok) {
      received = counter;
      plain[len] = 0;
      line = String((const char *)plain);
    }
    free(plain);
    return ok;
  }

  void update(Stream *s, const uint8_t *p, size_t n) {
    uint8_t buf[512 + 16];
    size_t olen = 0;
    mbedtls_gcm_update(&out, p, n, buf, sizeof buf, &olen);
    if (olen) s->write(buf, olen);
  }

  static String toBase64(const uint8_t *p, size_t n) {
    char out[64];
    size_t olen = 0;
    mbedtls_base64_encode((uint8_t *)out, sizeof out, &olen, p, n);
    return String(out).substring(0, olen);
  }

  static bool fromBase64(const String &text, uint8_t *out, size_t n) {
    size_t got = 0;
    return mbedtls_base64_decode(out, n, &got, (const uint8_t *)text.c_str(), text.length()) == 0
           && got == n;
  }

 private:
  uint8_t key[32] = {0};
  mbedtls_gcm_context out, in;
  uint64_t sent = 0;
  int64_t received = -1;
  uint8_t pend[16];
  size_t pendLen = 0;
};
