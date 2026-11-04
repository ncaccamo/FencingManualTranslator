#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""The decoration review sheet: a page to open in a browser, to choose which
decorative pictures the English-only PDF keeps. No server, no AI, nothing
online -- one HTML file with the pictures inside it.

    python3 /app/decoration_sheet.py <stem> [<stem> ...] | --all

Run step 3 first: it writes intermediate/decorations.json, the pictures it
took for decorations (no printed caption, no figure record) and the ones
books/<stem>.pictures already drops. The sheet is written next to the
book's PDFs:

    processed/<stem>_translated/<stem> — Decoration Review.html

Open it, mark each picture Keep or Drop (library stamps, labels, scraps:
Drop), then press "Save .pictures file" (or copy the text it shows) and
put it in books/<stem>.pictures. Rebuild the English-only PDF. Your
earlier choices are filled in from that file; pictures you haven't
decided on before are marked New, so after a code change only those need
a look.
"""
import base64
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _books


def read_pictures(path):
    """books/<stem>.pictures -> (text, off, drop tokens, kept ids)."""
    if not os.path.exists(path):
        return "", False, [], []
    text = open(path, encoding="utf-8").read()
    off, drop, kept = False, [], []
    for line in text.splitlines():
        line = line.split("#")[0].strip().lower()
        if re.match(r"^decoration\s*:\s*none$", line):
            off = True
        m = re.match(r"^(drop|kept)\s*:\s*(.+)$", line)
        if m:
            parts = [p for p in re.split(r"[,\s]+", m.group(2).strip()) if p]
            (drop if m.group(1) == "drop" else kept).extend(parts)
    return text, off, drop, kept


def token_pages(tok):
    """A drop token -> (first page, last page, picture or 0): "17", "17-20", "103.2"."""
    n, _, k = tok.partition(".")
    a, _, b = n.partition("-")
    return int(a), int(b or a), int(k) if k else 0


def build(stem):
    import fitz
    import quality_report
    F = f"{_books.PROCESSED}/{stem}_translated"
    I = f"{F}/intermediate"
    dp = f"{I}/decorations.json"
    if not os.path.exists(dp):
        print(f"✗ {stem}: no {dp} -- run 3_english_only_pdf.py {stem} first")
        return False
    data = json.load(open(dp, encoding="utf-8"))
    ocr = next((os.path.join(I, f) for f in os.listdir(I) if f.endswith(".pdf") and "OCR" in f), None)
    if not ocr:
        print(f"✗ {stem}: no scan PDF in {I}")
        return False
    src = fitz.open(ocr)
    pm = quality_report.page_map(F, len(src))
    text, off, drop, kept = read_pictures(f"{_books.BOOKS}/{stem}.pictures")
    items = []
    for p in data["pictures"]:
        i, k, box = p["scan"] - 1, p["k"], p["box"]
        pg = src[i]
        r = pg.rect
        clip = fitz.Rect(r.x0 + box[0] * r.width, r.y0 + box[1] * r.height,
                         r.x0 + box[2] * r.width, r.y0 + box[3] * r.height)
        z = min(300 / max(clip.width, 1), 220 / max(clip.height, 1), 2.5)
        crop = pg.get_pixmap(matrix=fitz.Matrix(z, z), clip=clip)
        z2 = 130 / r.width
        full = pg.get_pixmap(matrix=fitz.Matrix(z2, z2))
        pid = f"{p['scan']}.{k}"
        covered = [t for t in drop if (lambda a, b, kk: a <= p["scan"] <= b and kk in (0, k))(*token_pages(t))]
        items.append({
            "id": pid, "scan": p["scan"], "k": k, "box": box,
            "where": f"pdf {pm[p['scan']]}" if p["scan"] in pm else f"scan {p['scan']}",
            "figure": p["figure"], "drop": bool(covered),
            "new": not covered and pid not in kept,
            "crop": base64.b64encode(crop.tobytes("jpeg", jpg_quality=70)).decode(),
            "full": base64.b64encode(full.tobytes("jpeg", jpg_quality=60)).decode()})
    src.close()
    items.sort(key=lambda x: (not x["new"], x["scan"], x["k"]))
    meta = {}
    if os.path.exists(f"{_books.BOOKS}/{stem}.meta.json"):
        meta = json.load(open(f"{_books.BOOKS}/{stem}.meta.json", encoding="utf-8"))
    name = f"{meta.get('author_short') or stem.split('_')[0].replace('-', ' ').title()} {stem.split('_')[1] if '_' in stem else ''}".strip()
    payload = {"stem": stem, "name": name, "off": off, "tokens": drop, "text": text, "items": items}
    page = TEMPLATE.replace("__TITLE__", html.escape(name)).replace(
        "__DATA__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    out = f"{F}/{stem} — Decoration Review.html"
    open(out, "w", encoding="utf-8").write(page)
    n_new = sum(x["new"] for x in items)
    print(f"✓ {stem}: {len(items)} picture(s), {n_new} new, "
          f"{sum(x['drop'] for x in items)} dropped now → {os.path.basename(out)}")
    return True


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Decoration Review — __TITLE__</title>
<style>
:root { --bg: #f4f6f8; --surface: #fff; --ink: #1b2430; --muted: #5c6878; --line: #d9dee5;
  --accent: #2f5d8a; --accent-soft: #e3ecf5; --keep: #2e7d5b; --keep-soft: #e2f2ea; --drop: #8a2f3c; --drop-soft: #f6e3e6; }
@media (prefers-color-scheme: dark) { :root { --bg: #12171d; --surface: #1a2129; --ink: #e3e8ee; --muted: #9aa6b4;
  --line: #2c3540; --accent: #8fb8e0; --accent-soft: #1f3245; --keep: #6cc39b; --keep-soft: #18352a;
  --drop: #e58f9c; --drop-soft: #3b1f25; color-scheme: dark } }
* { box-sizing: border-box }
body { background: var(--bg); color: var(--ink); font: 15px/1.5 system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; padding: 20px 16px 60px }
.wrap { max-width: 1100px; margin: 0 auto; display: flex; flex-direction: column; gap: 14px }
h1 { font: 650 26px/1.2 Georgia, serif; margin: 0 }
.lede { color: var(--muted); max-width: 75ch; margin: 4px 0 0 }
.bar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 5;
  background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px }
.bar label { display: flex; gap: 8px; align-items: center; cursor: pointer }
.count { color: var(--muted); font-size: 14px } .count b { color: var(--ink) }
.btn { border: 1px solid var(--accent); background: var(--accent); color: var(--surface); border-radius: 6px; padding: 6px 12px; font: 600 13px system-ui, sans-serif; cursor: pointer }
.btn.ghost { background: transparent; color: var(--accent) }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 10px; display: flex; flex-direction: column; gap: 8px }
.card.drop { border-color: var(--drop); background: var(--drop-soft) } .card.drop .crop img { opacity: .45 }
.pics { display: flex; gap: 8px; align-items: flex-start }
.crop { flex: 1; min-width: 0; display: flex; justify-content: center; align-items: center; min-height: 120px; background: #fff; border-radius: 4px }
.crop img { max-width: 100%; max-height: 220px; display: block }
.page { position: relative; width: 64px; flex: none } .page img { width: 64px; display: block; border: 1px solid var(--line) }
.page i { position: absolute; border: 2px solid #d62d2d }
.meta { display: flex; justify-content: space-between; align-items: center; gap: 8px; font-size: 13px; color: var(--muted) }
.meta b { color: var(--ink) }
.tag { font-size: 11px; font-weight: 600; letter-spacing: .05em; text-transform: uppercase; padding: 1px 6px; border-radius: 4px; margin-left: 6px }
.tag.new { background: var(--accent-soft); color: var(--accent) } .tag.fig { background: var(--keep-soft); color: var(--keep) }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 6px; overflow: hidden }
.seg button { border: 0; background: transparent; color: var(--ink); padding: 5px 12px; font: 500 13px system-ui, sans-serif; cursor: pointer }
.seg button[aria-pressed="true"].k { background: var(--keep-soft); color: var(--keep); font-weight: 600 }
.seg button[aria-pressed="true"].d { background: var(--drop-soft); color: var(--drop); font-weight: 600 }
button:focus-visible, input:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px }
.out { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; display: flex; flex-direction: column; gap: 8px }
.out textarea { width: 100%; min-height: 140px; font: 13px/1.4 ui-monospace, Menlo, Consolas, monospace; background: var(--bg); color: var(--ink); border: 1px solid var(--line); border-radius: 6px; padding: 8px }
.row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center }
.note { color: var(--muted); font-size: 14px }
</style></head><body>
<div class="wrap">
  <header>
    <h1>Decoration review — __TITLE__</h1>
    <p class="lede">The decorative pictures the English-only PDF draws (no printed caption, no figure record), each cropped from the scan page shown beside it, plus any picture the book's <code>.pictures</code> file already drops. Mark <b>Drop</b> for library stamps, labels, scraps and anything that isn't part of the book. <b>New</b> marks pictures you haven't decided on before. When you're done, save the <code>.pictures</code> file below into <code>books/</code> and rebuild the English-only PDF.</p>
  </header>
  <div class="bar"><label><input type="checkbox" id="none"> Leave out every decoration in this book</label><span class="count" id="count"></span></div>
  <main class="grid" id="grid"></main>
  <section class="out">
    <b id="fname"></b>
    <textarea id="text" readonly spellcheck="false"></textarea>
    <div class="row"><button class="btn" id="save">Save .pictures file</button><button class="btn ghost" id="copy">Copy text</button><span class="note" id="msg"></span></div>
    <p class="note">Put the file in the repository's <code>books/</code> folder (replacing the old one), then rebuild: <code>python3 /app/3_english_only_pdf.py <span id="stem2"></span></code></p>
  </section>
</div>
<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById("data").textContent);
const KEY = "decor-" + D.stem;
const choice = {}; let none = D.off;
for (const it of D.items) choice[it.id] = it.drop ? "drop" : "keep";
try { const s = JSON.parse(localStorage.getItem(KEY) || "null");
  if (s && s.text === D.text) { Object.assign(choice, s.choice || {}); none = !!s.none; } } catch (e) {}
const esc = s => String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const span = t => { const [n, k] = t.split("."); const [a, b] = n.split("-"); return [+a, +(b || a), k ? +k : 0]; };
const covers = (t, it) => { const [a, b, k] = span(t); return a <= it.scan && it.scan <= b && (k === 0 || k === it.k); };
const isDrop = it => choice[it.id] === "drop" || (none && !it.figure);
function pictures() {
  // the book's tokens kept where every picture they cover is still dropped; a
  // token covering a picture now kept is split into what is still dropped
  const shown = D.items, drops = [];
  for (const t of D.tokens) {
    const cov = shown.filter(it => covers(t, it));
    if (cov.every(it => choice[it.id] === "drop")) { drops.push(t); continue; }
    const [a, b] = span(t);
    for (let p = a; p <= b; p++) if (!shown.some(it => it.scan === p) && span(t)[2] === 0) drops.push(String(p));
    for (const it of cov) if (choice[it.id] === "drop") drops.push(it.id);
  }
  for (const it of shown) if (choice[it.id] === "drop" && !D.tokens.some(t => covers(t, it)) && !drops.includes(it.id)) drops.push(it.id);
  const key = t => { const [a, , k] = span(t); return a * 1000 + k; };
  const uniq = [...new Set(drops)].sort((x, y) => key(x) - key(y));
  const kept = shown.filter(it => choice[it.id] === "keep").map(it => it.id);
  const keepLines = D.text.split("\n").filter(l => !/^\s*(drop|kept|decoration)\s*:/i.test(l));
  while (keepLines.length && !keepLines[keepLines.length - 1].trim()) keepLines.pop();
  const head = keepLines.length ? keepLines : [
    `# Decorative pictures in the English-only PDF (${D.name}).`,
    "# From the decoration review sheet (decoration_sheet.py). Scan page.picture (n-th picture down the page).",
    "# drop: left out whatever it is; kept: reviewed and kept (docs/per-book-files.md)."];
  const lines = [...head];
  if (none) lines.push("decoration: none");
  if (uniq.length) lines.push("drop: " + uniq.join(", "));
  if (kept.length) lines.push("kept: " + kept.join(", "));
  return lines.join("\n") + "\n";
}
function render() {
  const g = document.getElementById("grid"); g.innerHTML = "";
  document.getElementById("none").checked = none;
  for (const it of D.items) {
    const drop = isDrop(it), [x0, y0, x1, y1] = it.box;
    const c = document.createElement("article"); c.className = "card" + (drop ? " drop" : "");
    c.innerHTML = `<div class="pics"><div class="crop"><img alt="picture on ${esc(it.where)}" src="data:image/jpeg;base64,${it.crop}"></div>
      <div class="page"><img alt="" src="data:image/jpeg;base64,${it.full}"><i style="left:${x0*100}%;top:${y0*100}%;width:${(x1-x0)*100}%;height:${(y1-y0)*100}%"></i></div></div>
      <div class="meta"><span><b>${esc(it.where)}</b>${it.k > 1 ? ` · picture ${it.k}` : ""}${it.new ? '<span class="tag new">New</span>' : ""}${it.figure ? '<span class="tag fig">Figure</span>' : ""}</span>
      <span class="seg"><button class="k" aria-pressed="${!drop}">Keep</button><button class="d" aria-pressed="${drop}">Drop</button></span></div>`;
    const [kb, db] = c.querySelectorAll(".seg button");
    kb.disabled = db.disabled = none && !it.figure;
    kb.onclick = () => set(it.id, "keep"); db.onclick = () => set(it.id, "drop");
    g.appendChild(c);
  }
  const n = D.items.filter(isDrop).length, nn = D.items.filter(it => it.new).length;
  document.getElementById("count").innerHTML = `<b>${D.items.length}</b> pictures · <b>${nn}</b> new · <b>${n}</b> marked Drop`;
  document.getElementById("text").value = pictures();
}
function set(id, v) { choice[id] = v; remember(); const y = scrollY; render(); scrollTo(0, y); }
function remember() { try { localStorage.setItem(KEY, JSON.stringify({ text: D.text, choice, none })); } catch (e) {} }
document.getElementById("none").onchange = e => { none = e.target.checked; remember(); render(); };
document.getElementById("fname").textContent = `books/${D.stem}.pictures`;
document.getElementById("stem2").textContent = D.stem;
const msg = t => { document.getElementById("msg").textContent = t; };
document.getElementById("save").onclick = () => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([pictures()], { type: "text/plain" }));
  a.download = D.stem + ".pictures"; document.body.appendChild(a); a.click(); a.remove();
  msg("Saved to your downloads folder: move it into books/.");
};
document.getElementById("copy").onclick = async () => {
  const ta = document.getElementById("text");
  try { await navigator.clipboard.writeText(ta.value); msg("Copied."); }
  catch (e) { ta.focus(); ta.select(); msg("Selected: press Ctrl+C to copy."); }
};
render();
</script></body></html>
"""


if __name__ == "__main__":
    names = _books.select(sys.argv[1:], __doc__, writes=False)
    ok = [build(_books.stem_of(n)) for n in names]
    sys.exit(0 if all(ok) else 1)
