#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Test: can a choice question confirm a hidden page number better than the
open margin read (read_folios.py), without inventing numbers on pages that
print none? (docs/STATUS.md open item 8.) Runs in the container.

    python3 /data/evals/folio_choice_test.py sample [--n 100]   CPU
        picks hidden ("[n]") pages across all books, roman front matter
        first, and writes label sheets (top + bottom margins) to
        /data/evals/reports/folio_choice/sheets/ plus sample.json.
        A person labels each page in /data/evals/gold/folio_labels.json:
        {"<stem>:<scan>": "II" | "117" | "none" | "?"}   ("?" = skipped)
    python3 /data/evals/folio_choice_test.py run                GPU (OCR model)
        asks three questions per labelled page, two margins each:
          open     -- "what page number is printed here?" on the sharper crop
          choice   -- "is it one of p-1, p, p+1?" (p = the sequence's value)
          control  -- the same, but every value offered is wrong (p+3..p+5):
                      picking one means it repeats the question, not the page
        Answers go to results.json (resumable).
    python3 /data/evals/folio_choice_test.py score
        per method: right / wrong on pages that print a number, and false
        numbers on pages that print none -- the figure that decides it.

The crop: each margin band is cut in thirds stacked vertically, so the
model sees it at ~1.8x the resolution of read_folios.py's full-width strip.
"""
import json
import os
import random
import re
import sys

PIPE = "/app"
PROC = "/data/processed"
OUT = "/data/evals/reports/folio_choice"
LABELS = "/data/evals/gold/folio_labels.json"
BAND = 0.12

ROMAN = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
         (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]


def to_roman(n, upper):
    s = ""
    for v, r in ROMAN:
        while n >= v:
            s, n = s + r, n - v
    return s.upper() if upper else s


def roman_value(s):
    s = s.lower().rstrip(".").replace("j", "i")
    if not s or not re.fullmatch(r"[ivxlcdm]+", s):
        return None
    vals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    t = 0
    for a, b in zip(s, s[1:] + " "):
        t += -vals[a] if b != " " and vals[b] > vals[a] else vals[a]
    return t


def norm(ans):
    """An answer as a comparable value: int for arabic / roman, 'none',
    'other', or None (unparseable)."""
    a = (ans or "").strip().strip(".").strip()
    if re.fullmatch(r"(?i)none|no(ne)?\b.*|no page number.*", a):
        return "none"
    if re.fullmatch(r"(?i)other.*", a):
        return "other"
    if re.fullmatch(r"\d{1,4}", a):
        return int(a)
    r = roman_value(a)
    return r if r else None


def books():
    return sorted(d[:-len("_translated")] for d in os.listdir(PROC)
                  if d.endswith("_translated") and os.path.exists(f"{PROC}/{d}/intermediate/page_numbers.txt"))


def predicted(nums, i):
    """The sequence's value for hidden page i (0-based): the bracketed number,
    in roman when the nearest shown neighbour is roman. (value, as text)."""
    v = int(re.sub(r"\D", "", nums[i]) or 0)
    for d in range(1, 8):
        for j in (i - d, i + d):
            if 0 <= j < len(nums) and nums[j].strip() and not nums[j].startswith("["):
                if roman_value(nums[j].strip()):
                    return v, to_roman(v, nums[j].strip()[0].isupper())
                return v, str(v)
    return v, str(v)


def load_ns():
    sys.path.insert(0, PIPE)
    src = open(f"{PIPE}/1_ocr_and_extract_vlm.py", encoding="utf-8").read()
    ns = {"__name__": "_folios"}
    exec(compile(src.split('\nprint("\\n" + "="*60)\nprint("STEP 1')[0], "step1", "exec"), ns)
    return ns


def page_image(fitz, Image, stem, scan):
    doc = fitz.open(f"{PROC}/{stem}_translated/intermediate/{stem}_OCR.pdf")
    pix = doc[scan - 1].get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
    return Image.frombytes("RGB", [pix.width, pix.height], pix.samples)


_LAYOUT = {}


def text_box(stem, scan):
    """The page's printed area from the layout detector (intermediate/
    layout.json, 0-based keys): union of its boxes except pictures (a stamp
    or vignette above a chapter opener would push the top band up into
    empty margin -- Jacob-André scans 25, 311), as fractions (x0, y0, x1,
    y1); None if it found no text (a blank page or a plate)."""
    if stem not in _LAYOUT:
        p = f"{PROC}/{stem}_translated/intermediate/layout.json"
        _LAYOUT[stem] = json.load(open(p, encoding="utf-8"))["pages"] if os.path.exists(p) else {}
    boxes = [b[2] for b in (_LAYOUT[stem].get(str(scan - 1)) or {}).get("boxes", []) if b[0] != "picture"]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def bands(img, stem=None, scan=None):
    """Top and bottom margin bands. Anchored to the printed area when the
    layout detector found one: a photographed page sits inside a wide
    background, so a fixed share of the image height can end above the
    page's own head line (Jacob-André scan 15: the 13% strip of
    read_folios.py stops short of "III"). Each band runs from well outside
    the printed area to just inside its first / last line, and a little
    wider than it. Blank page (no boxes): fixed bands, a larger share."""
    W, H = img.size
    tb = text_box(stem, scan) if stem else None
    if not tb:
        b = int(H * 0.2)
        return img.crop((0, 0, W, b)), img.crop((0, H - b, W, H))
    x0, y0, x1, y1 = tb
    xa, xb = int(max(0, x0 - 0.08) * W), int(min(1, x1 + 0.08) * W)
    top = img.crop((xa, int(max(0, y0 - 0.09) * H), xb, int(min(1, y0 + 0.035) * H)))
    bot = img.crop((xa, int(max(0, y1 - 0.035) * H), xb, int(min(1, y1 + 0.09) * H)))
    return top, bot


def thirds(Image, band):
    """A full-width band cut in thirds, stacked: same content, ~1.8x larger
    after the model's resize to 1288 px."""
    w3 = band.width // 3
    out = Image.new("RGB", (w3, 3 * band.height + 40), "white")
    for k in range(3):
        out.paste(band.crop((k * w3, 0, (k + 1) * w3, band.height)), (0, k * (band.height + 20)))
    return out


def sample(n):
    ns = load_ns()
    rnd = random.Random(20261009)
    roman, other = [], []
    for stem in books():
        nums = open(f"{PROC}/{stem}_translated/intermediate/page_numbers.txt", encoding="utf-8").read().split("\n")
        for i, x in enumerate(nums):
            if x.startswith("["):
                v, txt = predicted(nums, i)
                if v <= 0:
                    continue
                (roman if not txt.isdigit() else other).append({"stem": stem, "scan": i + 1, "pred": txt})
    rnd.shuffle(roman)
    rnd.shuffle(other)
    # every book represented, roman pages first (the known weak spot)
    pick = []
    for pool, cap in ((roman, 4), (other, 3)):     # per book: up to 4 roman, 3 arabic
        count = {}
        for p in pool:
            if len(pick) < n and count.get(p["stem"], 0) < cap:
                pick.append(p)
                count[p["stem"]] = count.get(p["stem"], 0) + 1
    pick.sort(key=lambda p: (p["stem"], p["scan"]))
    for k, p in enumerate(pick, 1):
        p["id"] = k
    os.makedirs(f"{OUT}/sheets", exist_ok=True)
    json.dump(pick, open(f"{OUT}/sample.json", "w", encoding="utf-8"), indent=1)
    n_sheets = draw_sheets(ns, pick)
    print(f"{len(pick)} pages ({sum(not p['pred'].isdigit() for p in pick)} roman) from "
          f"{len({p['stem'] for p in pick})} books -> {OUT}/sample.json, {n_sheets} sheets")


def draw_sheets(ns, pages, per_sheet=4):
    """Label sheets: each page's two margin bands (the crops the model is
    asked about, before the thirds cut), with id / book / scan / the
    sequence's value. Old sheets are removed first."""
    from PIL import ImageDraw
    Image = ns["Image"]
    os.makedirs(f"{OUT}/sheets", exist_ok=True)
    for f in os.listdir(f"{OUT}/sheets"):
        os.remove(f"{OUT}/sheets/{f}")
    W = 1400
    for s0 in range(0, len(pages), per_sheet):
        rows = []
        for p in pages[s0:s0 + per_sheet]:
            img = page_image(ns["fitz"], Image, p["stem"], p["scan"])
            for tag, b in zip(("top", "bottom"), bands(img, p["stem"], p["scan"])):
                b = b.resize((W, max(1, int(b.height * W / b.width))))
                lab = Image.new("RGB", (W, 34), (255, 236, 160))
                ImageDraw.Draw(lab).text((8, 8), f"#{p['id']}  {p['stem']}  scan {p['scan']}  "
                                         f"sequence says {p['pred']}  -- {tag}", fill="black")
                rows += [lab, b]
        sheet = Image.new("RGB", (W, sum(r.height for r in rows) + 6 * len(rows)), (90, 90, 90))
        y = 0
        for r in rows:
            sheet.paste(r, (0, y))
            y += r.height + 6
        sheet.save(f"{OUT}/sheets/sheet_{s0 // per_sheet + 1:02d}.png")
    return (len(pages) + per_sheet - 1) // per_sheet


def redraw(only_eye_labelled=True):
    """Redraw the sheets from sample.json (after a crop change): by default
    only the pages labelled by eye -- the positive set's labels come from
    the sequence / overrides -- plus the HARD pages, to check the crop."""
    pick = json.load(open(f"{OUT}/sample.json", encoding="utf-8"))
    pages = [p for p in pick if not p.get("positive_set") or (p["stem"], p["scan"]) in HARD]
    print(f"{draw_sheets(load_ns(), pages)} sheets for {len(pages)} pages -> {OUT}/sheets/")

HARD = {("jacob-andre_1887_jeuepee", 14): "II",     # OCR "12", strip read NONE (set by override)
        ("jacob-andre_1887_jeuepee", 15): "III"}    # OCR "103" under a stamp, strip read NONE


def add_positives():
    """Pages that print a number, to measure misses: per book one roman and
    one arabic page whose number is shown (the sequence corroborates it, so
    the label is that number), plus the known hard pages (HARD). Appended to
    sample.json; their labels go straight into the labels file (source:
    the sequence or a reviewer's override, not a look at the sheet)."""
    pick = json.load(open(f"{OUT}/sample.json", encoding="utf-8"))
    have = {(p["stem"], p["scan"]) for p in pick}
    labels = json.load(open(LABELS, encoding="utf-8")) if os.path.exists(LABELS) else {}
    rnd = random.Random(20261009)
    new = [{"stem": s, "scan": n, "pred": v} for (s, n), v in HARD.items()]
    for stem in books():
        nums = open(f"{PROC}/{stem}_translated/intermediate/page_numbers.txt", encoding="utf-8").read().split("\n")
        shown = [(i + 1, x.strip()) for i, x in enumerate(nums) if x.strip() and not x.startswith("[")]
        rom = [t for t in shown if roman_value(t[1]) and not t[1].isdigit()]
        ara = [t for t in shown if t[1].isdigit()]
        for pool in (rom, ara):
            if pool:
                scan, v = rnd.choice(pool)
                new.append({"stem": stem, "scan": scan, "pred": v})
    k = max(p["id"] for p in pick)
    added = 0
    for p in new:
        if (p["stem"], p["scan"]) in have:
            continue
        k += 1
        p["id"], p["positive_set"] = k, True
        pick.append(p)
        labels[f"{p['stem']}:{p['scan']}"] = p["pred"]
        added += 1
    json.dump(pick, open(f"{OUT}/sample.json", "w", encoding="utf-8"), indent=1)
    json.dump(labels, open(LABELS, "w", encoding="utf-8"), indent=1, sort_keys=True)
    print(f"{added} positive page(s) added ({sum(not p['pred'].isdigit() for p in new)} roman)")


def offer(pred, shift):
    """The values offered: p-1, p, p+1 (shift 0) or all wrong (shift 4: p+3..p+5),
    in the sequence's form (roman case kept)."""
    rv = roman_value(pred) if not pred.isdigit() else None
    v = rv or int(pred)
    vals = [v - 1 + shift, v + shift, v + 1 + shift]
    vals = [x for x in vals if x > 0]
    if rv:
        return [to_roman(x, pred[0].isupper()) for x in vals]
    return [str(x) for x in vals]


OPEN = ("This image shows one margin of a page of an old printed book, cut into three pieces "
        "stacked one above the other. If a page number is printed in it (arabic like 117, or "
        "roman like xiv or viij), answer with that page number exactly as printed. If no page "
        "number is printed, answer NONE. Answer with the page number or NONE only.")
CHOICE = ("This image shows one margin of a page of an old printed book, cut into three pieces "
          "stacked one above the other. Is one of these page numbers printed in it: {opts}? "
          "Answer with that page number exactly as printed. If a different page number is "
          "printed, answer OTHER. If no page number is printed, answer NONE. Answer with one "
          "word only.")


def ask(ns, img, prompt):
    import requests
    bc = ns["bc"]
    b64, _ = ns["prepare_image_for_vlm"](img)
    try:
        r = requests.post(f"{bc.ocr_base_url()}/v1/chat/completions",
                          headers={"Content-Type": "application/json", **bc.auth_headers()},
                          json={"model": ns["VLM_MODEL_NAME"], "temperature": 0, "max_tokens": 12,
                                "messages": [{"role": "user", "content": [
                                    {"type": "text", "text": prompt},
                                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}]},
                          timeout=120)
        return r.json()["choices"][0]["message"]["content"].strip() if r.status_code == 200 else None
    except Exception:
        return None


def run():
    labels = json.load(open(LABELS, encoding="utf-8"))
    pick = [p for p in json.load(open(f"{OUT}/sample.json", encoding="utf-8"))
            if labels.get(f"{p['stem']}:{p['scan']}", "?") != "?"]
    ns = load_ns()
    if ns["start_vlm_server"]() is None:
        sys.exit("✗ cannot reach / start the OCR server")
    res_p = f"{OUT}/results.json"
    res = json.load(open(res_p, encoding="utf-8")) if os.path.exists(res_p) else {}
    todo = [p for p in pick if f"{p['stem']}:{p['scan']}" not in res]
    print(f"{len(todo)} page(s) to ask ({len(pick) - len(todo)} done), 6 questions each")
    for p in todo:
        img = page_image(ns["fitz"], ns["Image"], p["stem"], p["scan"])
        r = {}
        for tag, b in zip(("top", "bottom"), bands(img, p["stem"], p["scan"])):
            t = thirds(ns["Image"], b)
            r[f"open_{tag}"] = ask(ns, t, OPEN)
            r[f"choice_{tag}"] = ask(ns, t, CHOICE.format(opts=", ".join(offer(p["pred"], 0))))
            r[f"control_{tag}"] = ask(ns, t, CHOICE.format(opts=", ".join(offer(p["pred"], 4))))
        if any(v is None for v in r.values()):
            print(f"  {p['stem']} scan {p['scan']}: no answer -- skipped (rerun to retry)")
            continue
        res[f"{p['stem']}:{p['scan']}"] = r
        print(f"  {p['stem']} scan {p['scan']} ({p['pred']}): " + "  ".join(f"{k}={v!r}" for k, v in r.items()), flush=True)
        json.dump(res, open(res_p, "w", encoding="utf-8"), indent=1)


def verdict(method, r, pred, offered):
    """What a method would do to the page: the number it would show, or None.
    open: shows the hidden number only if a margin names exactly it (as
    apply_folio_witness does). choice / control: shows it if a margin
    answers one of the offered values that equals the sequence's value --
    for control nothing offered is right, so any offered value counts as
    a number shown wrongly."""
    pv = roman_value(pred) if not pred.isdigit() else int(pred)
    got = [norm(r[f"{method}_{t}"]) for t in ("top", "bottom")]
    if method == "control":
        ov = {roman_value(o) if not o.isdigit() else int(o) for o in offered}
        hit = [g for g in got if isinstance(g, int) and g in ov]
        return hit[0] if hit else None
    return pv if pv in got else None


def score():
    labels = json.load(open(LABELS, encoding="utf-8"))
    res = json.load(open(f"{OUT}/results.json", encoding="utf-8"))
    pick = {f"{p['stem']}:{p['scan']}": p for p in json.load(open(f"{OUT}/sample.json", encoding="utf-8"))}
    rows = []
    for m in ("open", "choice", "control"):
        tp = fn = fp = wrong = npos = nneg = 0
        bad = []
        for k, r in res.items():
            lab, p = labels.get(k, "?"), pick[k]
            if lab == "?":
                continue
            offered = offer(p["pred"], 4 if m == "control" else 0)
            shown = verdict(m, r, p["pred"], offered)
            truth = None if lab == "none" else norm(lab)
            if truth is None:
                nneg += 1
                if shown is not None:
                    fp += 1
                    bad.append(f"{k} prints none, would show {shown}")
            else:
                npos += 1
                if shown is None:
                    fn += 1
                elif shown == truth:
                    tp += 1
                else:
                    wrong += 1
                    bad.append(f"{k} prints {lab}, would show {shown}")
        rows.append((m, npos, tp, fn, wrong, nneg, fp, bad))
    print(f"{'method':8} {'prints a number':>16} {'shown right':>12} {'missed':>7} {'shown wrong':>12} "
          f"{'prints none':>12} {'false number':>13}")
    for m, npos, tp, fn, wrong, nneg, fp, _ in rows:
        print(f"{m:8} {npos:>16} {tp:>12} {fn:>7} {wrong:>12} {nneg:>12} {fp:>13}")
    print("\ncontrol: every value offered is wrong -- any number shown means it repeats the question.")
    for m, *_, bad in rows:
        for b in bad[:15]:
            print(f"  {m}: {b}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "sample":
        sample(int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 100)
    elif cmd == "sheets":
        redraw()
    elif cmd == "add-positives":
        add_positives()
    elif cmd == "run":
        run()
    elif cmd == "score":
        score()
    else:
        sys.exit(__doc__)
