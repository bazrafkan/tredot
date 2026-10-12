// The module's own texts, one table per language: those on its screen, and the reasons it sends
// to the Mac for the app to show (after "I wifi error: " and "UN"; the protocol tokens around them
// stay English). Texts that arrive from the Mac (titles, lines, options, replies) are already in
// the Mac's language and are drawn as they come.
//
// The Mac sends its language in the W greeting; it is kept in NVS (namespace "ui", key "lang") so
// the screen speaks it from boot, before the Mac connects. English is the default and fallback.
// The screen is ~22 characters wide: keep translations about as long as the English.
//
// Adding a language: a table like TEXTS_IT below, its code in LANG_CODES, the table in TEXTS.
#pragma once
#include <Preferences.h>

enum TextId {
  T_READY,
  T_THINKING,
  T_DONE,
  T_REPLY_IN_CLAUDE,
  T_NOTE_SAVED,
  T_APPROVE,
  T_MENU,
  T_LISTENING,
  T_RECORDING,
  T_TAP_TO_STOP,
  T_WHAT_NOW,
  T_TAP_TO_CLOSE,
  T_TALK_TO_CLAUDE,
  T_VOICE_NOTE,
  T_PHOTO,
  T_VIDEO,
  T_GRAPH,
  T_CLOSE,
  T_YES,
  T_NO,
  T_PAIR,
  T_WITH_THIS_MODULE,
  T_PAIRED,
  T_NOT_PAIRED,
  T_FIRMWARE_UPDATE,
  T_DO_NOT_UNPLUG,
  T_INSTALL,
  T_NEW_FIRMWARE,
  T_CURRENT_FIRMWARE,
  T_INSTALLED,
  T_RESTARTING,
  T_UPDATE_FAILED,
  T_KEEPING_OLD,
  T_NOT_UPDATED,
  T_NO_VIDEO_BLUETOOTH,
  T_TOO_SLOW,
  T_USE_CABLE_OR_WIFI,
  T_STARTING_CAMERA,
  T_NO_CAMERA,
  T_CHECK_CAMERA,
  T_CAMERA_CONNECTOR,
  T_VIDEO_SAVED,
  T_PHOTO_SAVED,
  T_PHOTO_ERROR,
  T_NEW,  // the version of an incoming firmware that did not say it
  // reasons sent to the Mac
  T_WIFI_WRONG_PASSWORD,
  T_WIFI_NOT_FOUND,
  T_WIFI_REJECTED,
  T_DECLINED,
  T_NOT_OVER_BLUETOOTH,
  T_BUSY_RECORDING,
  T_WEAK_SIGNAL,  // printf format: %d is the signal in dBm
  T_BAD_REQUEST,
  T_NO_UPDATE,
  T_BAD_CHUNK,
  T_HASH_MISMATCH,
  N_TEXTS
};

// In TextId order.
const char *const TEXTS_EN[] = {
  "ready",
  "Thinking…",
  "Done",
  "Reply in Claude",
  "Note saved",
  "Approve?",
  "≡ Menu",
  "Listening…",
  "Recording",
  "tap to stop",
  "What now?",
  "tap to close",
  "Talk to Claude",
  "Voice note",
  "Photo",
  "Video",
  "Graph",
  "Close",
  "Yes",
  "No",
  "Pair?",
  "with this module",
  "✓ Paired",
  "Not paired",
  "Firmware update",
  "do not unplug",
  "Install?",
  "firmware",
  "current",
  "installed",
  "restarting…",
  "failed",
  "keeping the old one",
  "Not updated",
  "No video on Bluetooth",
  "too slow: use the",
  "USB cable or Wi-Fi",
  "Starting camera…",
  "No camera",
  "check the camera",
  "board connector",
  "✓ Video saved",
  "✓ Photo saved",
  "✗ Photo error",
  "new",
  "wrong password?",
  "network not found (the module only sees 2.4 GHz networks)",
  "the router rejected the module",
  "declined on the module",
  "not over Bluetooth: use Wi-Fi",
  "module busy: recording",
  "weak Wi-Fi signal (%d dBm): move the module closer to the router or use USB",
  "bad request",
  "no update in progress",
  "bad chunk",
  "hash mismatch: firmware corrupted in transit",
};

const char *const TEXTS_IT[] = {
  "pronto",
  "Thinking…",  // as Claude Code shows it, in every language
  "Fatto",
  "Rispondi in Claude",
  "Nota salvata",
  "Procedo?",
  "≡ Menu",
  "Ti ascolto…",
  "Registrazione",
  "tocca per fermare",
  "Cosa faccio?",
  "tocca per chiudere",
  "Parla con Claude",
  "Nota vocale",
  "Foto",
  "Video",
  "Grafo",
  "Indietro",
  "Sì",
  "No",
  "Abbinare?",
  "a questo modulo",
  "✓ Abbinato",
  "Abbinamento annullato",
  "Aggiornamento",
  "non spegnere",
  "Installare?",
  "firmware",
  "ora",
  "installato",
  "riparto…",
  "non riuscito",
  "resta quello di prima",
  "Aggiornamento annullato",
  "Video senza cavo",
  "via Bluetooth no:",
  "usa il cavo o il Wi-Fi",
  "Accendo la fotocamera…",
  "Fotocamera assente",
  "controlla la scheda",
  "della fotocamera",
  "✓ Video salvato",
  "✓ Foto salvata",
  "✗ Foto non riuscita",
  "nuovo",
  "password sbagliata?",
  "rete non trovata (il modulo vede solo i 2,4 GHz)",
  "il router non accetta il modulo",
  "rifiutato sul modulo",
  "via Bluetooth no: usa il Wi-Fi",
  "modulo occupato: registra o riprende",
  "segnale Wi-Fi debole (%d dBm): avvicina il modulo al router o usa il cavo",
  "richiesta sbagliata",
  "nessun aggiornamento in corso",
  "pezzo sbagliato",
  "impronta diversa: firmware rovinato per strada",
};

static_assert(sizeof TEXTS_EN / sizeof *TEXTS_EN == N_TEXTS, "one text per TextId");
static_assert(sizeof TEXTS_IT / sizeof *TEXTS_IT == N_TEXTS, "one text per TextId");

const char *const LANG_CODES[] = {"en", "it"};
const char *const *const TEXTS[] = {TEXTS_EN, TEXTS_IT};
const int N_LANGS = sizeof TEXTS / sizeof *TEXTS;
static_assert(sizeof LANG_CODES / sizeof *LANG_CODES == N_LANGS, "one code per language table");

int language = 0;  // index into TEXTS; 0 = English

const char *tr(TextId id) { return TEXTS[language][id]; }

int langIndex(const String &code) {
  for (int i = 0; i < N_LANGS; i++)
    if (code == LANG_CODES[i]) return i;
  return -1;
}

void loadLanguage() {
  Preferences p;
  p.begin("ui", true);
  language = max(0, langIndex(p.getString("lang", "en")));
  p.end();
}

// Switches to the language with this code and stores it. Unknown or empty codes are ignored.
// Returns true if the language changed.
bool setLanguage(const String &code) {
  int i = langIndex(code);
  if (i < 0 || i == language) return false;
  language = i;
  Preferences p;
  p.begin("ui", false);
  p.putString("lang", code);
  p.end();
  return true;
}
