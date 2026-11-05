#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""The review page: every item of a book's review report as a card to
decide, in a browser. No server, no AI, nothing online -- one HTML file and
a folder of page pictures next to the book's PDFs:

    python3 /app/review_page.py <stem> [<stem> ...] | --all

    processed/<stem>_translated/<stem> — Review.html
    processed/<stem>_translated/<stem> — Review files/   (page pictures)
    processed/<stem>_translated/<stem> — Review cards.json (the same cards,
                                     for another review tool)

Each card shows the problem, the side-by-side PDF page (the words it is
about outlined in red; the English-only page as well where only that PDF is
affected), and the page's English text to copy from. Answer each card:

    Fix          -- with a find / replace on the page's English, applied by
                    apply_review.py; or a note saying what to change
    Fine as is   -- the report stops counting it as a page to check
    Later        -- undecided

Press "Save .review file" and put books/<stem>.review in books/ (replace the
old one). Then:

    python3 pipeline/apply_review.py <stem>   on the host: find / replace -> [[fix]]
    python3 /app/apply_overrides.py <stem>    in the container; rebuild the PDFs

Your earlier answers are filled in from books/<stem>.review; cards you
haven't answered are marked New. An answer whose item the report no longer
lists (fixed, or the text changed) is kept in the file, shown at the end.
Rebuild the PDFs before making the page: the pictures and pdf page numbers
are read from them.
"""
import glob
import html
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _books


def _quote(detail: str) -> str:
    """The first quoted text in a report item ('...' or "..."), cleaned for
    a search of the PDF page: no tags, no ellipsis, one line, <= 40 chars."""
    m = re.search(r"'((?:[^'\\]|\\.){6,})'|\"((?:[^\"\\]|\\.){6,})\"", detail)
    if not m:
        return ""
    q = re.sub(r"<[^>]+>|\\n.*|…|\.\.\.$", "", m.group(1) or m.group(2)).strip()
    q = re.split(r"\s+(?:→|--)\s+", q)[0]
    return q[:40].strip()


def _boxes(page, q: str) -> list:
    """Where q is on a PDF page, as fractions of the page: [[x0,y0,x1,y1]]."""
    if not q:
        return []
    r = page.rect
    hits = page.search_for(q) or (page.search_for(q[:20]) if len(q) > 20 else [])
    return [[round((b.x0 - r.x0) / r.width, 4), round((b.y0 - r.y0) / r.height, 4),
             round((b.x1 - r.x0) / r.width, 4), round((b.y1 - r.y0) / r.height, 4)] for b in hits[:6]]


def build(stem: str) -> bool:
    import fitz
    import _pageio
    import quality_report as Q
    F = f"{_books.PROCESSED}/{stem}_translated"
    I = f"{F}/intermediate"
    if not os.path.exists(f"{I}/translation.txt"):
        print(f"✗ {stem}: not translated yet")
        return False
    issues = Q.check_book(F)              # fresh, with the answers in books/ applied
    Q.write_report(F, stem, issues)
    tr = _pageio.split_pages(open(f"{I}/translation.txt", encoding="utf-8").read())
    pm = Q.page_map(F, len(tr))
    import _overrides
    answers = _overrides.load_review(stem)
    sbs = glob.glob(f"{F}/*Side by Side*.pdf")
    eo = glob.glob(f"{F}/*English Only*.pdf")
    sbs = fitz.open(sbs[0]) if sbs else None
    eo = fitz.open(eo[0]) if eo else None
    out_dir = f"{F}/{stem} — Review files"
    os.makedirs(out_dir, exist_ok=True)
    for old in glob.glob(f"{out_dir}/*.jpg"):
        os.remove(old)
    rendered = set()

    def picture(doc, tag, k):
        name = f"{tag}-{k + 1}.jpg"
        if (tag, k) not in rendered:
            pg = doc[k]
            z = (1100 if tag == "sbs" else 620) / pg.rect.width
            pg.get_pixmap(matrix=fitz.Matrix(z, z)).save(f"{out_dir}/{name}", jpg_quality=62)
            rendered.add((tag, k))
        return f"{os.path.basename(out_dir)}/{name}"

    eo_text = [p.get_text() for p in eo] if eo else []
    cards = []
    for x in issues:
        detail = x["detail"] if isinstance(x["detail"], str) else "; ".join(
            f"{k} on scan {', '.join(map(str, v))}" for k, v in x["detail"].items())
        s = x["scan"]
        cid = Q.card_id(x)
        c = {"id": cid, "scan": s, "pdf": pm.get(s), "level": x["level"], "kind": x["kind"],
             "detail": detail, "scope": "eo" if "English-only PDF" in detail else "both",
             "shots": []}
        q = _quote(detail)
        if sbs is not None and c["pdf"]:
            k = c["pdf"] - 1
            c["shots"].append({"what": f"Side-by-side PDF, pdf {c['pdf']}", "src": picture(sbs, "sbs", k),
                               "boxes": _boxes(sbs[k], q)})
        if eo is not None and c["scope"] == "eo" and q and s:
            # the English-only sheet with the quoted words nearest the scan's
            # place in the book (that PDF has no page map)
            want = (s - 1) / max(len(tr), 1) * len(eo)
            near = sorted((abs(k - want), k) for k, t in enumerate(eo_text) if q[:20] in t)
            if near:
                k = near[0][1]
                c["shots"].append({"what": f"English-only PDF, sheet {k + 1}", "src": picture(eo, "eo", k),
                                   "boxes": _boxes(eo[k], q)})
        if 0 < s <= len(tr):
            c["english"] = tr[s - 1].strip()
        a = answers.get(cid)
        if a:
            c["answer"] = {k: a[k] for k in ("answer", "note", "find", "replace") if k in a}
        cards.append(c)
    order = {Q.CHECK: 0, "BOOK": 1, "TERMS": 1, Q.NOTE: 2, Q.ACCEPTED: 3}
    cards.sort(key=lambda c: (order.get(c["level"], 2), c["pdf"] or 0, c["scan"]))
    live = {c["id"] for c in cards}
    gone = [a for k, a in answers.items() if k not in live]
    meta = {}
    for d in (_books.BOOKS, f"{_books.BOOKS}/_skip"):
        if os.path.exists(f"{d}/{stem}.meta.json"):
            meta = json.load(open(f"{d}/{stem}.meta.json", encoding="utf-8"))
            break
    name = f"{meta.get('author_short') or stem.split('_')[0].replace('-', ' ').title()} " \
           f"{stem.split('_')[1] if '_' in stem else ''}".strip()
    payload = {"stem": stem, "name": name, "cards": cards, "gone": gone}
    page = TEMPLATE.replace("__TITLE__", html.escape(name)).replace(
        "__DATA__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))
    out = f"{F}/{stem} — Review.html"
    open(out, "w", encoding="utf-8").write(page)
    # the same cards for another review tool (pictures in the files folder);
    # its answers come back through apply_review.py --answers
    with open(f"{F}/{stem} — Review cards.json", "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    n_open = sum(1 for c in cards if c["level"] == Q.CHECK and "answer" not in c)
    print(f"✓ {stem}: {len(cards)} card(s), {n_open} check(s) unanswered, "
          f"{len(rendered)} page picture(s) → {os.path.basename(out)}")
    return True


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Review — __TITLE__</title>
<style>
:root { --bg: #f4f6f8; --surface: #fff; --ink: #1b2430; --muted: #5c6878; --line: #d9dee5;
  --accent: #2f5d8a; --accent-soft: #e3ecf5; --ok: #2e7d5b; --ok-soft: #e2f2ea; --fix: #8a5a12; --fix-soft: #f7ecd9;
  --check: #8a2f3c; --check-soft: #f6e3e6; }
@media (prefers-color-scheme: dark) { :root { --bg: #12171d; --surface: #1a2129; --ink: #e3e8ee; --muted: #9aa6b4;
  --line: #2c3540; --accent: #8fb8e0; --accent-soft: #1f3245; --ok: #6cc39b; --ok-soft: #18352a;
  --fix: #e2b15e; --fix-soft: #3a2c14; --check: #e58f9c; --check-soft: #3b1f25; color-scheme: dark } }
* { box-sizing: border-box }
body { background: var(--bg); color: var(--ink); font: 15px/1.5 system-ui, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; padding: 20px 16px 60px }
.wrap { max-width: 1180px; margin: 0 auto; display: flex; flex-direction: column; gap: 14px }
h1 { font: 650 26px/1.2 Georgia, serif; margin: 0 }
.lede { color: var(--muted); max-width: 80ch; margin: 4px 0 0 }
.bar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 5;
  background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 10px 14px }
.count { color: var(--muted); font-size: 14px } .count b { color: var(--ink) }
select { font: 14px system-ui, sans-serif; padding: 4px 6px; border-radius: 6px; border: 1px solid var(--line); background: var(--surface); color: var(--ink) }
.btn { border: 1px solid var(--accent); background: var(--accent); color: var(--surface); border-radius: 6px; padding: 6px 12px; font: 600 13px system-ui, sans-serif; cursor: pointer }
.btn.ghost { background: transparent; color: var(--accent) }
.card { background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--line); border-radius: 8px; padding: 12px 14px; display: flex; flex-direction: column; gap: 10px }
.card.CHECK { border-left-color: var(--check) } .card.a-fix { background: var(--fix-soft) } .card.a-fine { background: var(--ok-soft) }
.head { display: flex; flex-wrap: wrap; gap: 6px 10px; align-items: baseline; font-size: 14px; color: var(--muted) }
.head b { color: var(--ink); font-size: 15px }
.tag { font-size: 11px; font-weight: 600; letter-spacing: .05em; text-transform: uppercase; padding: 1px 6px; border-radius: 4px; background: var(--accent-soft); color: var(--accent) }
.tag.CHECK { background: var(--check-soft); color: var(--check) } .tag.eo { background: var(--fix-soft); color: var(--fix) }
.detail { margin: 0; overflow-wrap: anywhere }
.shots { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-start }
.shot { flex: 1 1 520px; max-width: 860px; margin: 0 }
.shot.eo { flex: 0 1 330px }
.shot .pic { position: relative } .shot img { width: 100%; display: block; border: 1px solid var(--line); border-radius: 4px; background: #fff }
.shot i { position: absolute; border: 2px solid #d62d2d; border-radius: 2px; pointer-events: none }
.shot figcaption { font-size: 12px; color: var(--muted) }
.seg { display: inline-flex; border: 1px solid var(--line); border-radius: 6px; overflow: hidden }
.seg button { border: 0; background: transparent; color: var(--ink); padding: 6px 14px; font: 500 13px system-ui, sans-serif; cursor: pointer }
.seg button[aria-pressed="true"] { background: var(--accent-soft); color: var(--accent); font-weight: 650 }
.ans { display: flex; flex-direction: column; gap: 8px }
.ans label { font-size: 13px; color: var(--muted); display: flex; flex-direction: column; gap: 3px }
.ans textarea, .ans input { width: 100%; font: 13px/1.4 ui-monospace, Menlo, Consolas, monospace; background: var(--bg); color: var(--ink); border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px }
.ans textarea { min-height: 48px }
.fr { display: grid; grid-template-columns: 1fr 1fr; gap: 8px } @media (max-width: 640px) { .fr { grid-template-columns: 1fr } }
details pre { white-space: pre-wrap; font: 13px/1.45 ui-monospace, Menlo, Consolas, monospace; background: var(--bg); border: 1px solid var(--line); border-radius: 6px; padding: 8px; max-height: 320px; overflow: auto; margin: 6px 0 0 }
summary { cursor: pointer; color: var(--accent); font-size: 13px }
.warn { color: var(--check); font-size: 13px }
button:focus-visible, input:focus-visible, textarea:focus-visible, select:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px }
.out { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; display: flex; flex-direction: column; gap: 8px }
.out textarea { width: 100%; min-height: 160px; font: 13px/1.4 ui-monospace, Menlo, Consolas, monospace; background: var(--bg); color: var(--ink); border: 1px solid var(--line); border-radius: 6px; padding: 8px }
.row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center }
.note { color: var(--muted); font-size: 14px }
.empty { color: var(--muted); padding: 20px; text-align: center }
</style></head><body>
<div class="wrap">
  <header>
    <h1>Review — __TITLE__</h1>
    <p class="lede">Every item of the book's review report, each with its page. Words the item is about are outlined in red. Answer <b>Fix</b> (a find / replace on the page's English, or a note saying what to change), <b>Fine as is</b> (the report stops listing it), or <b>Later</b>. Then save the <code>.review</code> file below into <code>books/</code>.</p>
  </header>
  <div class="bar">
    <div class="row">
      <select id="show" aria-label="Which cards">
        <option value="open">Unanswered</option><option value="all">All</option><option value="done">Answered</option>
      </select>
      <select id="kind" aria-label="Kind"><option value="">Every kind</option></select>
      <select id="level" aria-label="Level"><option value="">Checks and notes</option><option value="CHECK">Checks only</option></select>
    </div>
    <span class="count" id="count"></span>
  </div>
  <main id="list" style="display:flex;flex-direction:column;gap:12px"></main>
  <section class="out">
    <b id="fname"></b>
    <textarea id="text" readonly spellcheck="false" aria-label="The .review file"></textarea>
    <div class="row"><button class="btn" id="save">Save .review file</button><button class="btn ghost" id="copy">Copy text</button><span class="note" id="msg"></span></div>
    <p class="note">Put the file in the repository's <code>books/</code> folder (replacing the old one), then run <code>python3 pipeline/apply_review.py <span class="stem"></span></code> on the host, from the repository (find / replace answers → the overrides file), <code>python3 /app/apply_overrides.py <span class="stem"></span></code> in the container, and rebuild the PDFs.</p>
  </section>
</div>
<script type="application/json" id="data">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById("data").textContent);
const KEY = "review-" + D.stem;
const ans = {};
for (const c of D.cards) if (c.answer) ans[c.id] = Object.assign({}, c.answer);
try { const s = JSON.parse(localStorage.getItem(KEY) || "null");
  if (s && s.ids === D.cards.map(c => c.id).join()) Object.assign(ans, s.ans || {}); } catch (e) {}
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const tq = s => JSON.stringify(String(s)).replace(/\u007f/g, "\\u007f");    // a TOML basic string
const kinds = [...new Set(D.cards.map(c => c.kind))].sort();
for (const k of kinds) { const o = document.createElement("option"); o.value = o.textContent = k; document.getElementById("kind").appendChild(o); }
function fileText() {
  const L = [`# Review answers for ${D.name} (books/${D.stem}.review), from the review page (review_page.py).`,
    "# answer: fix / fine (as is) / later. find + replace on a fix: apply_review.py writes it as a [[fix]].", ""];
  const one = (a, c) => {
    L.push("[[card]]", `id = ${tq(c.id)}`);
    if (c.scan) L.push(`scan = ${c.scan}`);
    if (c.pdf) L.push(`pdf = ${c.pdf}`);
    L.push(`kind = ${tq(c.kind)}`, `issue = ${tq(String(c.detail || c.issue || "").slice(0, 240))}`, `answer = ${tq(a.answer)}`);
    for (const k of ["note", "find", "replace"]) if (a[k]) L.push(`${k} = ${tq(a[k])}`);
    L.push("");
  };
  for (const c of D.cards) if (ans[c.id] && ans[c.id].answer) one(ans[c.id], c);
  if (D.gone.length) L.push("# answers to items the report no longer lists (kept for the record)", "");
  for (const g of D.gone) one(g, g);
  return L.join("\n");
}
function shot(s, eo) {
  const boxes = (s.boxes || []).map(([x0, y0, x1, y1]) =>
    `<i style="left:${x0*100}%;top:${y0*100}%;width:${(x1-x0)*100}%;height:${(y1-y0)*100}%"></i>`).join("");
  return `<figure class="shot${eo ? " eo" : ""}"><div class="pic"><a href="${esc(s.src)}" target="_blank" rel="noopener"><img loading="lazy" alt="${esc(s.what)}" src="${esc(s.src)}"></a>${boxes}</div><figcaption>${esc(s.what)}${s.boxes && s.boxes.length ? "" : " (the words weren't found on the page to outline)"}</figcaption></figure>`;
}
function card(c) {
  const a = ans[c.id] || {};
  const el = document.createElement("article");
  el.className = `card ${c.level} ${a.answer ? "a-" + a.answer : ""}`;
  const where = c.scan ? (c.pdf ? `pdf ${c.pdf}` : `scan ${c.scan}`) : "Whole book";
  el.innerHTML = `<div class="head"><b>${esc(where)}</b><span class="tag ${esc(c.level)}">${esc(c.level === "ACCEPTED" ? "fine as is" : c.level.toLowerCase())}</span>
      <span>${esc(c.kind)}</span><span class="tag ${c.scope === "eo" ? "eo" : ""}">${c.scope === "eo" ? "English-only PDF" : "Both PDFs"}</span>${c.answer ? "" : '<span class="tag">New</span>'}</div>
    <p class="detail">${esc(c.detail)}</p>
    <div class="shots">${c.shots.map(s => shot(s, s.src.includes("/eo-"))).join("")}</div>
    ${c.english ? `<details><summary>The page's English text (copy the words to fix from here)</summary><pre>${esc(c.english)}</pre></details>` : ""}
    <div class="ans"><span class="seg" role="group" aria-label="Answer">
        <button data-v="fix">Fix</button><button data-v="fine">Fine as is</button><button data-v="later">Later</button></span>
      <div class="fixbox" hidden>
        <div class="fr"><label>Find (exact words on this page's English)<input class="find" spellcheck="false"></label>
          <label>Replace with<input class="replace" spellcheck="false"></label></div>
        <span class="warn" hidden></span></div>
      <label>Note<textarea class="note"></textarea></label></div>`;
  for (const b of el.querySelectorAll(".seg button")) {
    b.setAttribute("aria-pressed", String(a.answer === b.dataset.v));
    b.onclick = () => { const v = ans[c.id] && ans[c.id].answer === b.dataset.v ? "" : b.dataset.v;
      ans[c.id] = Object.assign(ans[c.id] || {}, { answer: v }); if (!v) delete ans[c.id]; save(); redraw(el, c); };
  }
  const fb = el.querySelector(".fixbox"); fb.hidden = a.answer !== "fix";
  const f = el.querySelector(".find"), r = el.querySelector(".replace"), n = el.querySelector(".note"), w = el.querySelector(".warn");
  f.value = a.find || ""; r.value = a.replace || ""; n.value = a.note || "";
  const check = () => { const ok = !f.value || !c.english || c.english.includes(f.value);
    w.hidden = ok; w.textContent = ok ? "" : "These words aren't on this page's English exactly: apply_review.py will skip the fix."; };
  check();
  const upd = k => e => { ans[c.id] = Object.assign(ans[c.id] || { answer: "fix" }, { [k]: e.target.value }); check(); save(); };
  f.oninput = upd("find"); r.oninput = upd("replace"); n.oninput = e => {
    if (!ans[c.id] && !e.target.value) return;
    ans[c.id] = Object.assign(ans[c.id] || { answer: "later" }, { note: e.target.value }); save(); };
  return el;
}
function redraw(el, c) { const y = scrollY; el.replaceWith(card(c)); scrollTo(0, y); count(); }
function visible(c) {
  const v = document.getElementById("show").value, k = document.getElementById("kind").value, l = document.getElementById("level").value;
  const done = !!(ans[c.id] && ans[c.id].answer) || c.level === "ACCEPTED";
  return (v === "all" || (v === "open") !== done) && (!k || c.kind === k) && (!l || c.level === l);
}
function render() {
  const list = document.getElementById("list"); list.innerHTML = "";
  const shown = D.cards.filter(visible);
  for (const c of shown) list.appendChild(card(c));
  if (!shown.length) list.innerHTML = '<p class="empty">No cards here. Choose "All" to see every card.</p>';
  count();
}
function count() {
  const checks = D.cards.filter(c => c.level === "CHECK"), open = checks.filter(c => !(ans[c.id] && ans[c.id].answer));
  document.getElementById("count").innerHTML = `<b>${D.cards.length}</b> cards · <b>${open.length}</b> of ${checks.length} checks unanswered`;
  document.getElementById("text").value = fileText();
}
function save() { try { localStorage.setItem(KEY, JSON.stringify({ ids: D.cards.map(c => c.id).join(), ans })); } catch (e) {} count(); }
for (const id of ["show", "kind", "level"]) document.getElementById(id).onchange = render;
document.getElementById("fname").textContent = `books/${D.stem}.review`;
for (const s of document.querySelectorAll(".stem")) s.textContent = D.stem;
const msg = t => { document.getElementById("msg").textContent = t; };
document.getElementById("save").onclick = () => {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([fileText()], { type: "text/plain" }));
  a.download = D.stem + ".review"; document.body.appendChild(a); a.click(); a.remove();
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
