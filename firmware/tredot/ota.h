// Firmware update from the bridge (OTA), over Wi-Fi or USB and only encrypted: only the paired Mac
// can update the module. Not over Bluetooth: 3.6 MB would take ~5 minutes. The Mac side is in
// mac/bridge.py (Device.update).
//
//   Mac    -> U <bytes> <sha256> <version>   start: the module prepares the other app slot
//   module -> UK <bytes received>  ready / received so far (the Mac stays at most 3 chunks ahead)
//   Mac    -> u <base64>           a 3 kB chunk
//   Mac    -> UE                   end: the module checks the hash and asks for confirmation on
//                                  the screen; on "Yes" it installs and restarts into the new firmware
//   module -> UD | UN <reason>     done | not done (the previous firmware is untouched)
//   Mac    -> UA                   cancel
//
// Until confirmed, the new firmware only lives in the other slot: if anything goes wrong midway
// (link lost, hash mismatch, "No", module unplugged) the previous one boots.
//
// The new firmware boots "on trial": if it crashes before declaring itself good (markValid), the
// bootloader rolls back to the previous one on the next boot.
#pragma once
#include <Update.h>
#include "esp_ota_ops.h"
#include "mbedtls/base64.h"
#include "mbedtls/sha256.h"
#include "texts.h"

// The Arduino core marks every firmware valid as soon as it boots, so rollback would never trigger
bool verifyRollbackLater() { return true; }

class Ota {
 public:
  bool running = false;
  size_t total = 0, done = 0;
  String version;  // of the incoming firmware, for the question on the screen

  String start(size_t size, const String &hexSha, const String &newVersion) {
    version = newVersion.length() ? newVersion : tr(T_NEW);
    abort();
    if (size == 0 || hexSha.length() != 64) return String("UN\t") + tr(T_BAD_REQUEST);
    for (int i = 0; i < 32; i++) expected[i] = strtoul(hexSha.substring(2 * i, 2 * i + 2).c_str(), nullptr, 16);
    if (!Update.begin(size)) return String("UN\t") + Update.errorString();
    mbedtls_sha256_init(&sha);
    mbedtls_sha256_starts(&sha, 0);
    total = size;
    done = 0;
    running = true;
    return "UK\t0";
  }

  String chunk(const String &b64) {
    if (!running) return String("UN\t") + tr(T_NO_UPDATE);
    static uint8_t buf[4096];
    size_t n = 0;
    if (mbedtls_base64_decode(buf, sizeof buf, &n, (const uint8_t *)b64.c_str(), b64.length()) != 0 ||
        done + n > total) {
      abort();
      return String("UN\t") + tr(T_BAD_CHUNK);
    }
    if (Update.write(buf, n) != n) {
      String why = Update.errorString();
      abort();
      return "UN\t" + why;
    }
    mbedtls_sha256_update(&sha, buf, n);
    done += n;
    return "UK\t" + String(done);
  }

  // Everything arrived? "" if the hash matches (ask for confirmation), otherwise the UN line to send
  String verify() {
    if (!running) return String("UN\t") + tr(T_NO_UPDATE);
    uint8_t got[32];
    mbedtls_sha256_finish(&sha, got);
    if (done != total || memcmp(got, expected, 32) != 0) {
      abort();
      return String("UN\t") + tr(T_HASH_MISMATCH);
    }
    return "";
  }

  // After "Yes" on the screen: the new firmware boots on the next restart
  String install() {
    running = false;
    if (!Update.end()) return String("UN\t") + Update.errorString();
    return "UD";
  }

  void abort() {
    if (running) {
      Update.abort();
      mbedtls_sha256_free(&sha);
    }
    running = false;
  }

  // The running firmware declares itself good: no more rollback to the previous one
  static void markValid() {
    static bool done = false;
    if (done) return;
    done = true;
    esp_ota_mark_app_valid_cancel_rollback();
  }

 private:
  uint8_t expected[32];
  mbedtls_sha256_context sha;
};
