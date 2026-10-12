"""Voice note: .wav -> transcription (Whisper, on the Mac) -> summary (claude -p) -> .md

The bridge runs it when a recording ends: notes.py <file.wav>. Keeps the module updated
(Transcribing… / Summarizing… / saved) and finally renames the .wav after the note's title.

    notes.py --actions <note.md>   runs the actions requested in an already saved note
"""

import array
import os
import re
import subprocess
import sys
import tempfile
import time
import wave
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audio  # noqa: E402
import knowledge  # noqa: E402
from config import CACHE, CONFIG, NOTES, tool  # noqa: E402
from hook import send, wrap  # noqa: E402
from i18n import LANG, t, variants  # noqa: E402

MODEL = CONFIG["whisper_model"]

# The note's headings, in the UI language. Notes written before a language change keep theirs,
# so headings are looked up in every language (heading()).
SUMMARY, KEY_POINTS, TO_DO = t("Summary"), t("Key points"), t("To do")
TRANSCRIPT, DONE = t("Transcript"), t("Done by Claude")

PROMPT = """Below is the automatic transcription of one of my voice notes.
It may contain recognition errors: fix them where the meaning is clear.

Write the note in Markdown, in the same language as the transcription, with exactly this
structure (keep these headings as they are):

# <short title, at most 6 words>

## {summary}
<3-5 sentences>

## {key_points}
- ...

## {to_do}
- [ ] ...
(omit this section if the note has nothing to do)

Reply with the Markdown only, no preamble. Do not copy the transcription: I add it myself.

At the end, on a line of its own, write <!-- actions: yes --> if in the note I explicitly ask
you (the assistant) to do something, for example "put it on the calendar" or "create an event";
otherwise <!-- actions: no -->. Reminders to myself ("I have to call Anna") do not count.

On one more line of its own list what the note talks about (people, projects, companies, places,
recurring topics; at most 6, 1-3 words each, spelled the way you would in every note), like this:
<!-- graph: Anna | Davide | Production -->

Transcription:
""".format(summary=SUMMARY, key_points=KEY_POINTS, to_do=TO_DO)


def show(state, title="", lines=()):
    try:
        send({"op": "state", "state": state, "title": title, "lines": list(lines)}, wait=2)
    except Exception:
        pass  # the module is optional: the note is saved anyway


def duration(path):
    with wave.open(path) as w:
        return w.getnframes() / w.getframerate()


SKIP_START = 0.3  # seconds: the PDM microphone pops at start-up, and that is not speech
FLAT, QUIET = 30, 80  # rms of the loudest half second; measured speech is 100-400

# Phrases Whisper makes up on silence (Italian, then English)
HALLUCINATIONS = ("grazie", "grazie a tutti", "grazie per la visione", "sottotitoli",
                  "amara.org", "iscriviti", "alla prossima",
                  "thank you", "thanks for watching", "subtitles by")


def loudest(path):
    """Level of the loudest half second, skipping the microphone start-up."""
    with wave.open(path) as w:
        rate = w.getframerate()
        pcm = array.array("h", w.readframes(w.getnframes()))[int(SKIP_START * rate):]
    best = 0
    for i in range(0, len(pcm), rate // 2):
        seg = pcm[i:i + rate // 2:4]
        if len(seg) < 100:
            break
        mean = sum(seg) / len(seg)
        best = max(best, int((sum((x - mean) ** 2 for x in seg) / len(seg)) ** 0.5))
    return best


def invented(text):
    t = text.lower().strip(" .!")
    return len(t) < 60 and any(h in t for h in HALLUCINATIONS)


def speech_language():
    """The language to transcribe in when there is no second opinion: "auto" means the Tredot
    language."""
    language = CONFIG["language"]
    return LANG if language == "auto" else language


def whisper(clean, language, folder):
    """Whisper on the cleaned audio -> (text, detected language or None)."""
    out = os.path.join(folder, "text-" + language)
    result = subprocess.run([tool("whisper-cli"), "-m", MODEL, "-l", language, "-f", clean, "-nt",
                             "--prompt", CONFIG["vocabulary"], "-otxt", "-of", out],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError("whisper-cli: " + (result.stderr.strip().splitlines() or ["?"])[-1])
    with open(out + ".txt", encoding="utf-8") as f:
        text = " ".join(f.read().split())
    detected = re.search(r"auto-detected language: (\w+)", result.stderr)
    return text, detected and detected.group(1)


def transcriptions(wav):
    """The recording's text as [(language, text)]: one, or two when the spoken language is uncertain.

    With language "auto", Whisper first detects the language itself. If it hears the Tredot language
    (the usual case) that is the transcription. If it hears another one, the audio is transcribed
    again in the Tredot language and both go to the language model, which picks the one that makes
    sense: on a few seconds of audio Whisper can be confidently wrong (an Italian note came out in
    French with p = 0.97), but a speaker of another language must still be understood.
    Without the Whisper model (still downloading), macOS transcribes (tredot-speech) in one pass.
    """
    with tempfile.TemporaryDirectory() as tmp:
        clean = os.path.join(tmp, "clean.wav")
        # the Sense microphone is quiet and picks up rumble: boost and clean it (audio.py)
        audio.clean(wav, clean)
        if not os.path.exists(MODEL):
            spare = tool("tredot-speech")
            if not os.path.isabs(spare):
                raise RuntimeError(t("Whisper model missing (Tredot downloads it): {path}", path=MODEL))
            result = subprocess.run([spare, clean, speech_language()], capture_output=True, text=True)
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or t("tredot-speech failed"))
            return [(speech_language(), " ".join(result.stdout.split()))]
        if CONFIG["language"] != "auto":
            return [(CONFIG["language"], whisper(clean, CONFIG["language"], tmp)[0])]
        text, detected = whisper(clean, "auto", tmp)
        if not detected or detected == LANG:
            return [(detected or LANG, text)]
        return [(LANG, whisper(clean, LANG, tmp)[0]), (detected, text)]


def for_agent(candidates):
    """The transcription as one text for an agent: with two, both, and why (the agent picks)."""
    if len(candidates) == 1:
        return candidates[0][1]
    return "\n".join(["(The spoken language was uncertain: two automatic transcriptions of the same "
                      "audio follow. Use the one that makes sense.)"] +
                     [f"[{language}] {text}" for language, text in candidates])


def transcribe(wav):
    """The recording's text for an agent (listen, Talk to Claude)."""
    return for_agent(transcriptions(wav))


# Actions asked for by voice. Reading the calendar is free; writing goes through the module: every
# create/update/delete triggers the permission hook and you approve (or deny) it with a tap.
CALENDAR = "mcp__claude_ai_Google_Calendar__"
READ_ONLY = [CALENDAR + t for t in
             ("list_calendars", "list_events", "get_event", "search_events", "suggest_time")]

ACT_PROMPT = """You are the assistant for my voice notes. It is now {now}, time zone {tz}.
Below is the transcription of one of my notes. Carry out ONLY the actions I explicitly ask you
to do (for example "put it on the calendar"). Do nothing that is not explicitly asked:
reminders to myself ("I have to call Anna") are not requests to you.

You have the Google Calendar tools. Before creating an event, check my schedule
(list_events or suggest_time) and pick a free slot that respects what I said.
Title and description in the language of the transcription.

At the end reply ONLY with a Markdown list, one item per action, with day, time and a link to
the event if there is one. If an action was denied or failed, say so in its item.
If there was nothing to do, reply exactly: No actions.

Transcription:
"""


def timezone():
    """The Mac's time zone by name (Europe/Rome), from the /etc/localtime symlink."""
    target = os.path.realpath("/etc/localtime")
    return target.split("zoneinfo/", 1)[1] if "zoneinfo/" in target else time.strftime("%Z")


def act(text):
    # TREDOT_SKIP: the module does not show this claude -p's state, but gets its permissions
    env = dict(os.environ, TREDOT_SKIP="1")
    now = datetime.now().strftime("%A %Y-%m-%d %H:%M")
    os.makedirs(NOTES, exist_ok=True)
    result = subprocess.run(
        [tool("claude"), "-p", "--tools", "", "--allowedTools", ",".join(READ_ONLY),
         "--no-session-persistence", "--output-format", "text"],
        input=ACT_PROMPT.format(now=now, tz=timezone()) + text, capture_output=True, text=True,
        env=env, timeout=600, cwd=NOTES,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(result.stderr.strip()[:200] or t("claude -p did not answer"))
    return result.stdout.strip()


def heading(md, text):
    """The "## text" heading as written in md, in whichever language, or None."""
    return next((f"## {h}" for h in variants(text) if f"## {h}" in md), None)


def with_actions(md_path, text):
    """Runs the note's actions and writes them into the .md, before the transcript."""
    show("note", t("Running actions…"), [t("approve on the module"), t("what it writes")])
    done = act(text)
    with open(md_path, encoding="utf-8") as f:
        md = f.read()
    transcript = heading(md, "Transcript")
    if transcript:
        md = md.replace(transcript, f"## {DONE}\n\n{done}\n\n{transcript}", 1)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    return done


CHOICE = """The spoken language was uncertain, so below are {count} automatic transcriptions of the
same audio in different languages: only one matches what I actually said, the others render it in
the wrong language. Use the one that makes sense, write the note in its language, and on a line of
its own write <!-- transcript: N --> with its number.

"""


def summarize(candidates):
    if len(candidates) == 1:
        prompt = PROMPT + candidates[0][1]
    else:
        listing = "\n\n".join(f"{n}. [{language}] {text}" for n, (language, text) in enumerate(candidates, 1))
        prompt = CHOICE.format(count=len(candidates)) + PROMPT + listing
    env = dict(os.environ, TREDOT_SKIP="1")
    result = subprocess.run(
        [tool("claude"), "-p", "--tools", "", "--no-session-persistence", "--output-format", "text"],
        input=prompt, capture_output=True, text=True, env=env, timeout=300, cwd=CACHE,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(result.stderr.strip()[:200] or t("claude -p did not answer"))
    return result.stdout.strip()


def slug(title):
    title = re.sub(r"[^\w\s-]", "", title, flags=re.UNICODE).strip()
    return re.sub(r"\s+", " ", title)[:60] or t("Note")


def main(wav):
    secs = duration(wav)
    if secs < 1:
        os.remove(wav)
        show("error", t("Empty note"), [t("under one second")])
        return
    length = t("{time} of audio", time=f"{int(secs // 60)}:{int(secs % 60):02d}")
    level = loudest(wav)
    if level < FLAT:
        show("error", t("Microphone muted?"),
             wrap(t("the audio is flat: check the camera board"), 22, 3))
        return  # keep the .wav, to see what arrived
    if level < QUIET:
        show("error", t("I heard nothing"), wrap(t("try speaking closer"), 22, 3))
        return

    show("note", t("Transcribing…"), [length])
    candidates = [(language, text) for language, text in transcriptions(wav)
                  if text and not invented(text)]
    if not candidates:
        show("error", t("I heard nothing"), wrap(t("try speaking closer"), 22, 3))
        return

    show("note", t("Summarizing…"), wrap(candidates[0][1], 22, 3))
    summary = summarize(candidates)

    wants_actions = "<!-- actions: yes -->" in summary
    chosen = re.search(r"<!-- transcript: (\d+) -->", summary)
    number = int(chosen.group(1)) if chosen else 1
    text = candidates[number - 1][1] if 1 <= number <= len(candidates) else candidates[0][1]
    graph = knowledge.LINE.search(summary)
    summary = re.sub(r"\s*<!-- (actions|transcript): \w+ -->\s*", "\n", summary)
    summary = knowledge.LINE.sub("", summary).strip()

    first = summary.splitlines()[0]
    title = first.lstrip("# ").strip() if first.startswith("#") else t("Voice note")
    stamp = datetime.fromtimestamp(os.path.getmtime(wav)).strftime("%Y-%m-%d %H%M")
    base = os.path.join(os.path.dirname(wav), f"{stamp} {slug(title)}")
    os.rename(wav, base + ".wav")

    when = datetime.fromtimestamp(os.path.getmtime(base + ".wav")).strftime("%Y-%m-%d %H:%M")
    wav_name = os.path.basename(base) + ".wav"
    body = summary.splitlines()
    body[1:1] = ["", f"*{when} · {length} · [audio]({wav_name.replace(' ', '%20')})*"]
    # the entities go last, after the transcript and the actions (and are what the graph reads)
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write("\n".join(body).rstrip() + f"\n\n## {TRANSCRIPT}\n\n" + text + "\n")
        if graph:
            f.write("\n" + graph.group(0).strip() + "\n")

    if wants_actions:
        with_actions(base + ".md", text)
    try:
        knowledge.build()  # the knowledge graph includes the new note
    except Exception:
        pass  # the graph is optional: the note is saved anyway
    show("saved", "", wrap(title, 22, 3))


def actions_for(md_path):
    """For a note already saved: notes.py --actions <note.md>"""
    with open(md_path, encoding="utf-8") as f:
        md = f.read()
    if heading(md, "Done by Claude"):
        raise RuntimeError(t("this note already has its actions"))
    transcript = heading(md, "Transcript")
    if not transcript:
        raise RuntimeError(t("this note has no transcript"))
    text = md.split(transcript, 1)[1].strip()
    print(with_actions(md_path, text))
    show("saved", t("Actions done"), wrap(os.path.basename(md_path)[16:-3], 22, 3))


if __name__ == "__main__":
    try:
        if sys.argv[1] == "--actions":
            actions_for(sys.argv[2])
        else:
            main(sys.argv[1])
    except Exception as e:
        print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {sys.argv[1:]}: {e!r}", file=sys.stderr)
        show("error", t("Note failed"), wrap(str(e), 22, 3))
