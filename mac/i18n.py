"""The language of the texts people read: the module's screen, notes, errors shown by the app.

English is the source language: each English text is also the key of its translations. Adding a
language means adding one table to TABLES (texts missing from a table stay English).

LANG is chosen once, at import: TREDOT_LANG (the app passes its own localization to every
process it starts), then `ui_language` in the configuration unless "auto", then the Mac's first
preferred language (hooks run by Claude Code are not started by the app), else English.

Standard library only: it is copied into the Claude Code plugin with hook.py.
"""

import os
import subprocess

from config import CONFIG

DEFAULT = "en"

IT = {
    # module screen: tool names in permission requests and tool states (hook.py)
    "Approval": "Approvazione",
    "Question": "Domanda",
    "Show": "Mostra",
    "Listen": "Ascolta",
    "Photo": "Foto",
    "Status": "Stato",
    "Create event": "Crea evento",
    "Edit event": "Modifica evento",
    "Delete event": "Cancella evento",
    "Reply to event": "Rispondi a evento",
    "Send email": "Invia email",
    "Draft email": "Bozza email",
    "Write file": "Scrive file",
    "Edit file": "Modifica file",
    "Read file": "Legge file",
    "Read files": "Legge file",
    "Find files": "Cerca file",
    "Search text": "Cerca nel testo",
    "Folder": "Cartella",
    "Web search": "Cerca sul web",
    "Memory": "Memoria",
    # module screen: permission options, states (hook.py, module.py)
    "Yes": "Sì",
    "Yes, always: {what}": "Sì, sempre: {what}",
    "Yes, don't ask again": "Sì, non chiedere più",
    "Yes, all edits": "Sì, tutte le modifiche",
    "Yes, add folder": "Sì, e aggiungi cartella",
    "Reply in Gemini": "Rispondi in Gemini",
    "Claude error": "Errore di Claude",
    "agent": "agente",
    "Approve": "Approva",
    "Deny": "Rifiuta",
    # module screen: bridge
    "Update the Mac app": "Aggiorna l'app del Mac",
    "the firmware uses": "il firmware usa il",
    "protocol {version},": "protocollo {version},",
    "the bridge {protocol}": "il ponte il {protocol}",
    # bridge errors, shown by the app
    "the module is not paired or not connected": "il modulo non è abbinato o non è collegato",
    "needs the USB cable": "serve il cavo USB",
    "no answer from the module": "nessuna risposta dal modulo",
    "the module is not connected": "il modulo non è collegato",
    "refused on the module": "rifiutato sul modulo",
    "not over Bluetooth (too slow): connect it to Wi-Fi or with the cable":
        "via Bluetooth no (troppo lento): collegalo al Wi-Fi o col cavo",
    "the module stopped responding": "il modulo non risponde più",
    "firmware not found: {error}": "firmware non trovato: {error}",
    # voice notes (notes.py): screen
    "Running actions…": "Eseguo le azioni…",
    "approve on the module": "conferma sul modulo",
    "what it writes": "quello che scrive",
    "Empty note": "Nota vuota",
    "under one second": "meno di un secondo",
    "Microphone muted?": "Microfono muto?",
    "the audio is flat: check the camera board": "l'audio è piatto: controlla la scheda della fotocamera",
    "I heard nothing": "Non ho sentito niente",
    "try speaking closer": "prova a parlare più vicino",
    "Transcribing…": "Trascrivo…",
    "Summarizing…": "Riassumo…",
    "{time} of audio": "{time} di audio",
    "Actions done": "Azioni fatte",
    "Note failed": "Errore nella nota",
    # voice notes: the note itself
    "Voice note": "Nota vocale",
    "Note": "Nota",
    "Summary": "Riassunto",
    "Key points": "Punti chiave",
    "To do": "Da fare",
    "Transcript": "Trascrizione",
    "Done by Claude": "Fatto da Claude",
    # voice notes: errors (shown on the screen)
    "Whisper model missing (Tredot downloads it): {path}":
        "manca il modello di Whisper (lo scarica Tredot): {path}",
    "tredot-speech failed": "tredot-speech non riuscito",
    "claude -p did not answer": "claude -p non ha risposto",
    "this note already has its actions": "questa nota ha già le sue azioni",
    "this note has no transcript": "questa nota non ha la trascrizione",
    # video (video.py)
    "Empty video": "Video vuoto",
    "no frames received": "nessun fotogramma arrivato",
    "Video failed": "Errore nel video",
    # the agent (agent.py, mcp_server.py)
    "I didn't hear anything. Try again closer.": "Non ho sentito niente. Riprova più vicino.",
    "Something went wrong: {errors}": "Qualcosa non ha funzionato: {errors}",
    "(no answer)": "(nessuna risposta)",
    "The agent stopped on an error: see agent.log.":
        "L'agente si è fermato per un errore: guarda agent.log.",
    "Error": "Errore",
    "# Conversation with the agent, {date}": "# Conversazione con l'agente, {date}",
    "You": "Tu",
    # connecting agents (integrations.py), shown by the app
    "Claude Code is not installed on this Mac": "Claude Code non è installato su questo Mac",
    "Tredot is in Claude Code. New sessions get it; restart the open ones.":
        "Tredot è in Claude Code. Vale per le sessioni nuove: quelle aperte vanno riavviate.",
    "Claude Desktop is not installed on this Mac": "Claude Desktop non è installato su questo Mac",
    "the extension is missing from the app": "l'estensione non è dentro l'app",
    "Confirm with “Install” in Claude Desktop. New chats then have Tredot.":
        "In Claude Desktop conferma con «Installa». Poi Tredot c'è nelle chat nuove.",
    "Gemini CLI is not installed on this Mac": "Gemini CLI non è installato su questo Mac",
    "Tredot is in Gemini CLI, with its state on the screen. New sessions get it.":
        "Tredot è in Gemini CLI, con lo stato sullo schermo. Vale per le sessioni nuove.",
    # the knowledge graph page (knowledge.py)
    "Knowledge graph": "Grafo delle note",
    "Graph": "Grafo",
    "No notes yet": "Ancora nessuna nota",
    "Click a note to read it; drag to move; scroll to zoom":
        "Clicca una nota per leggerla; trascina per spostare; scorri per ingrandire",
    "Open the note": "Apri la nota",
    "{count} nodes": "{count} nodi",
    "{notes} notes, {topics} topics": "{notes} note, {topics} temi",
}

TABLES = {"en": {}, "it": IT}


def normalize(code):
    """"it-IT", "it_IT", "IT" -> "it"; None if not a supported language."""
    lang = str(code or "").strip().strip('",').replace("_", "-").split("-")[0].lower()
    return lang if lang in TABLES else None


def mac_language():
    """The first of the Mac's preferred languages, e.g. "it-IT" (None if it cannot be read)."""
    try:
        out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True,
                             text=True, timeout=2).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    entries = [x.strip().strip('",') for x in out.splitlines() if x.strip() not in ("(", ")", "")]
    return entries[0] if entries else None


def detect():
    explicit = os.environ.get("TREDOT_LANG")
    if explicit:
        return normalize(explicit) or DEFAULT
    configured = str(CONFIG.get("ui_language") or "auto")
    if configured != "auto":
        return normalize(configured) or DEFAULT
    return normalize(mac_language()) or DEFAULT


LANG = detect()


def translate(text, lang, **values):
    out = TABLES.get(lang, {}).get(text, text)
    return out.format(**values) if values else out


def t(text, **values):
    """text (English) in LANG, with {name} placeholders filled from values."""
    return translate(text, LANG, **values)


def variants(text):
    """text in every language, English first: to recognize headings of notes written before a
    language change."""
    return [text] + [table[text] for table in TABLES.values() if text in table]
