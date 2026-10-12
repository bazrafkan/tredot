"""The knowledge graph of the voice notes: notes and the things they talk about (people, projects,
places, topics), as a page you can look at.

    knowledge.py build      rebuilds the page from the notes (the bridge does it after each note)
    knowledge.py open       the same, adds the entities to notes that lack them, opens the page
    knowledge.py prepare    the same without opening it: the app shows the page in its own window
    knowledge.py module     a summary on the module's screen (the most mentioned entities)
    knowledge.py backfill   only adds the entities to old notes

Each note lists its entities in a line of its own, <!-- graph: Anna | Davide | Production -->
(notes.py asks Claude for it). Two notes that share an entity are linked through it. The page is
one self-contained file: the data, and force-graph (MIT, vendor/) drawing it, so it works offline.
"""

import glob
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import CACHE, NOTES, SUPPORT, tool  # noqa: E402
from i18n import t  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(SUPPORT, "knowledge", "graph.html")
LIB = os.path.join(HERE, "vendor", "force-graph.min.js")
LINE = re.compile(r"^<!-- graph: (.*?) -->[ \t]*$", re.M)
MAX_ENTITIES = 6

ENTITIES_PROMPT = """For each note below (id, title, summary) list the entities it talks about:
people, projects, companies, places, recurring topics. At most {max} per note, short (1-3 words),
as they would be named in every note (the same person is always spelled the same way), in the
language of the note. No dates, no generic words ("note", "work").

Reply ONLY with JSON: {{"<id>": ["Anna", "Production", ...], ...}}

"""


def entities_line(names):
    clean = []
    for name in names:
        name = " ".join(str(name).replace("|", " ").replace("-->", " ").split())
        if name and name.casefold() not in {c.casefold() for c in clean}:
            clean.append(name)
    return f"<!-- graph: {' | '.join(clean[:MAX_ENTITIES])} -->" if clean else ""


def parse(path):
    with open(path, encoding="utf-8") as f:
        md = f.read()
    title = next((line.lstrip("# ").strip() for line in md.splitlines() if line.startswith("# ")),
                 os.path.basename(path)[16:-3])
    found = LINE.search(md)
    summary = ""
    sections = re.split(r"^## .*$", md, flags=re.M)
    if len(sections) > 1:
        summary = " ".join(sections[1].split())[:400]
    stamp = re.match(r"(\d{4}-\d\d-\d\d) (\d\d)(\d\d)", os.path.basename(path))
    when = f"{stamp.group(1)} {stamp.group(2)}:{stamp.group(3)}" if stamp else ""
    names = [n.strip() for n in found.group(1).split("|") if n.strip()] if found else None
    return {"path": path, "title": title, "summary": summary, "when": when, "entities": names}


def notes():
    return [parse(p) for p in sorted(glob.glob(os.path.join(NOTES, "*.md")))]


def graph(items):
    """{"nodes": [...], "links": [...]}: a node per note and per entity, a link per mention."""
    nodes, links, entities = [], [], {}
    for n, item in enumerate(items):
        nodes.append({"id": f"n{n}", "kind": "note", "name": item["title"], "when": item["when"],
                      "summary": item["summary"], "url": "file://" + item["path"].replace(" ", "%20")})
        for name in item["entities"] or []:
            key = name.casefold()
            if key not in entities:
                entities[key] = {"id": f"e{len(entities)}", "kind": "entity", "name": name, "count": 0}
            entities[key]["count"] += 1
            links.append({"source": f"n{n}", "target": entities[key]["id"]})
    return {"nodes": nodes + list(entities.values()), "links": links}


def build():
    items = notes()
    data = graph(items)
    with open(LIB, encoding="utf-8") as f:
        lib = f.read()
    page = (TEMPLATE.replace("/*LIB*/", lib.replace("</script", "<\\/script"))
            .replace("/*DATA*/", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
            .replace("%TITLE%", t("Knowledge graph"))
            .replace("%EMPTY%", t("No notes yet"))
            .replace("%HINT%", t("Click a note to read it; drag to move; scroll to zoom"))
            .replace("%OPEN%", t("Open the note")))
    os.makedirs(os.path.dirname(PAGE), exist_ok=True)
    with open(PAGE, "w", encoding="utf-8") as f:
        f.write(page)
    return len(data["nodes"])


def backfill():
    """Asks Claude, in one call, for the entities of the notes that have none; returns how many."""
    missing = [i for i in notes() if i["entities"] is None][:40]
    if not missing or not os.path.isabs(tool("claude")):
        return 0
    listing = "\n".join(f'{n}. {i["title"]}: {i["summary"]}' for n, i in enumerate(missing, 1))
    result = subprocess.run(
        [tool("claude"), "-p", "--tools", "", "--no-session-persistence", "--output-format", "text"],
        input=ENTITIES_PROMPT.format(max=MAX_ENTITIES) + listing, capture_output=True, text=True,
        env=dict(os.environ, TREDOT_SKIP="1"), timeout=300, cwd=CACHE)
    reply = re.search(r"\{.*\}", result.stdout, re.S)
    if result.returncode or not reply:
        return 0
    try:
        found = json.loads(reply.group(0))
    except ValueError:
        return 0
    done = 0
    for n, item in enumerate(missing, 1):
        line = entities_line(found.get(str(n)) or [])
        with open(item["path"], encoding="utf-8") as f:
            md = f.read()
        with open(item["path"], "w", encoding="utf-8") as f:
            f.write(md.rstrip() + "\n\n" + (line or "<!-- graph:  -->") + "\n")
        done += 1
    return done


TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>%TITLE%</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#fff;--fg:#1d1d1f;--mute:#6e6e73;--note:#0a84ff;--ent:#ff9f0a;--line:#c7c7cc;--card:#f5f5f7}
@media (prefers-color-scheme:dark){:root{--bg:#1c1c1e;--fg:#f5f5f7;--mute:#98989d;--note:#64d2ff;--ent:#ffd60a;--line:#48484a;--card:#2c2c2e}}
html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);font:14px -apple-system,system-ui,sans-serif}
#g{position:fixed;inset:0}
header{position:fixed;top:12px;left:16px;right:16px;pointer-events:none}
h1{font-size:16px;margin:0}header p{margin:2px 0 0;color:var(--mute);font-size:12px}
#card{position:fixed;left:16px;right:16px;bottom:16px;max-width:420px;background:var(--card);border-radius:12px;padding:12px 14px;display:none}
#card b{display:block}#card small{color:var(--mute)}#card p{margin:6px 0}#card a{color:var(--note)}
#empty{position:fixed;inset:0;display:none;align-items:center;justify-content:center;color:var(--mute)}
</style></head><body>
<div id="g"></div><header><h1>%TITLE%</h1><p>%HINT%</p></header>
<div id="card"></div><div id="empty">%EMPTY%</div>
<script>/*LIB*/</script>
<script>
const data=/*DATA*/;
if(!data.nodes.length)document.getElementById('empty').style.display='flex';
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const card=document.getElementById('card');
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const g=ForceGraph()(document.getElementById('g')).graphData(data)
 .nodeRelSize(5).nodeVal(n=>n.kind==='entity'?1+n.count:2)
 .nodeColor(n=>css(n.kind==='entity'?'--ent':'--note')).linkColor(()=>css('--line'))
 .backgroundColor(css('--bg'))
 .nodeLabel(n=>esc(n.name))
 .nodeCanvasObjectMode(()=>'after')
 .nodeCanvasObject((n,ctx,k)=>{if(k<0.7&&n.kind==='note')return;ctx.font=(11/k)+'px -apple-system,sans-serif';
   ctx.fillStyle=css('--fg');ctx.textAlign='center';ctx.fillText(n.name.slice(0,28),n.x,n.y+8/k+4)})
 .onNodeClick(n=>{
   if(n.kind==='note'){card.innerHTML='<b>'+esc(n.name)+'</b><small>'+esc(n.when)+'</small><p>'+esc(n.summary)+
     '</p><a href="'+n.url+'">%OPEN%</a>'}
   else{const about=data.links.filter(l=>(l.target.id||l.target)===n.id).length;
     card.innerHTML='<b>'+esc(n.name)+'</b><small>'+about+' note</small>'}
   card.style.display='block'})
 .onBackgroundClick(()=>card.style.display='none');
</script></body></html>
"""


def summary():
    """The graph in a few lines for the module's screen: counts and the most mentioned entities."""
    data = graph(notes())
    entities = sorted((n for n in data["nodes"] if n["kind"] == "entity"),
                      key=lambda n: -n["count"])
    count = len(data["nodes"]) - len(entities)
    if not count:
        return t("No notes yet")
    head = t("{notes} notes, {topics} topics", notes=count, topics=len(entities))
    top = "\n".join(f'{e["name"]} ({e["count"]})' for e in entities[:8])
    return head + ("\n\n" + top if top else "")


CENTER, RADIUS = (120, 108), 62  # where the graph goes on the round screen (240 x 240 pixels)
MAX_NODES = 64  # what the module accepts


def layout(data, steps=200):
    """Positions on the module's screen (Fruchterman-Reingold, always the same result): the
    entities mentioned most and the notes around them: [[x, y, radius, kind]], [[i, j]] and the names."""
    linked = {end for l in data["links"] for end in (l["source"], l["target"])}  # alone says nothing
    nodes = sorted((n for n in data["nodes"] if n["id"] in linked),
                   key=lambda n: (n["kind"] != "entity", -n.get("count", 0)))[:MAX_NODES]
    index = {n["id"]: i for i, n in enumerate(nodes)}
    edges = [[index[l["source"]], index[l["target"]]] for l in data["links"]
             if l["source"] in index and l["target"] in index]
    count = len(nodes)
    pos = [[math.cos(2.4 * i) * (1 + i % 5), math.sin(2.4 * i) * (1 + i % 5)] for i in range(count)]
    k = 1.0
    for step in range(steps):
        cool = 1 - step / steps
        move = [[0.0, 0.0] for _ in range(count)]
        for a in range(count):
            for b in range(a + 1, count):
                dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
                d = math.hypot(dx, dy) or 0.01
                push = k * k / d
                for who, sign in ((a, 1), (b, -1)):
                    move[who][0] += sign * dx / d * push
                    move[who][1] += sign * dy / d * push
        for a, b in edges:
            dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
            d = math.hypot(dx, dy) or 0.01
            pull = d * d / k
            move[a][0] -= dx / d * pull
            move[a][1] -= dy / d * pull
            move[b][0] += dx / d * pull
            move[b][1] += dy / d * pull
        for i in range(count):  # gravity keeps separate groups together, close enough to see
            move[i][0] -= pos[i][0] * 0.4
            move[i][1] -= pos[i][1] * 0.4
            d = math.hypot(*move[i]) or 0.01
            step_len = min(d, 0.5 * cool + 0.02)
            pos[i][0] += move[i][0] / d * step_len
            pos[i][1] += move[i][1] / d * step_len
    far = max((math.hypot(*p) for p in pos), default=1) or 1
    out = []
    for n, (x, y) in zip(nodes, pos):
        radius = 3 if n["kind"] == "note" else min(4 + n["count"], 8)
        out.append([round(CENTER[0] + x / far * (RADIUS - radius)),
                    round(CENTER[1] + y / far * (RADIUS - radius)), radius,
                    0 if n["kind"] == "note" else 1])
    return out, edges, [n["name"] for n in nodes]


def module():
    """The graph on the module's screen: drawn, or as text if its firmware cannot."""
    from hook import send
    data = graph(notes())
    nodes, edges, names = layout(data)
    send({"op": "graph", "title": t("Graph"), "nodes": nodes, "edges": edges,
          "names": names, "text": summary()}, wait=5)


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else "build"
    if what == "module":
        module()
        print(json.dumps({"ok": "ok"}))
        return
    if what in ("backfill", "open", "prepare"):
        backfill()
    if what != "backfill":
        count = build()
        if what == "open":
            subprocess.run(["open", PAGE], check=True)
        print(json.dumps({"ok": t("{count} nodes", count=count), "page": PAGE}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # the menu shows the reason
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
