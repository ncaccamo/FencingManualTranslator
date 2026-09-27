#!/usr/bin/env python3
"""Per-book review report: every page the pipeline itself has reason to
doubt, with the reason, by side-by-side pdf page number. No model, no GPU.

    python3 /app/quality_report.py <stem> [<stem> ...]   (or --all)

Writes `processed/<stem>_translated/<stem> — Review Report.md` (for you) and
`intermediate/quality_report.json` (for tools). Review the listed pages,
record decisions in books/<stem>.overrides.toml (manual III.5b), and use
`2_translate.py --pages` for pages that need retranslating.

Two levels: CHECK = likely wrong; NOTE = worth a glance. A check earns its
place only if it catches real problems without burying them -- each one
here was measured against pages found wrong by hand in batch-2 review.
"""
import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

import _layout
import _overrides
import _pageio

# step 2's helpers, without starting a translation server
_src = open(os.path.join(os.path.dirname(__file__), "2_translate.py"), encoding="utf-8").read()
_ns = {"__name__": "_tx_prefix"}
exec(compile(_src.split('\nprint("\\n" + "="*60)\nprint("STEP 2')[0], "2_translate.py", "exec"), _ns)
clean_vlm_extraction = _ns["clean_vlm_extraction"]
contains_too_much_non_english = _ns["contains_too_much_non_english"]
FN_HEADER = _ns["FN_HEADER"]

CHECK, NOTE = "CHECK", "NOTE"

# "Still French?" for the report: French function words must clearly
# outnumber English ones. (step 2's contains_too_much_non_english() is a
# quick gate tuned to reject whole outputs; on single paragraphs it also
# fires on English with quoted French, names, and short headings.)
_FR = set("le la les des du de et est sont qui que dans pour avec une un sur pas ne se il "
          "elle au aux ce cette son sa ses leur leurs nous vous on en par plus".split())
_EN = set("the and of to is are in that with for on it by as be this from his their which "
          "was were one an at or not but have has its".split())


def still_french(text: str, min_fr: int = 5) -> bool:
    w = [x.lower() for x in re.findall(r"[^\W\d_]+", re.sub(r"<i>.*?</i>|«[^»]*»|\"[^\"]*\"", " ", text))]
    fr = sum(x in _FR for x in w)
    en = sum(x in _EN for x in w)
    return fr >= min_fr and fr > 2 * en

# Terms the model is known to render against the glossary. (source regex,
# wrong-English regex, what it should be). Only fires when the SOURCE has
# the term, so a legitimate English word elsewhere is left alone.
TERM_RULES = [
    # coup = the whole action -> blow; botte = its finish -> thrust (Gomard's own
    # definition, pdf 158). Noun uses only: "they throw themselves" is fine.
    # "throws" only as a noun for coups ("the throws", "simple throws", a
    # THROWS heading) -- "he throws himself onto the point" is correct English
    (r"\bcoups?\b", r"(?:\b(?:the|these|those|such|all|two|three|simple|compound|straight|single|of)"
     r"\s+throws\b|\b(?-i:THROWS)\b|(?<!\ba )\bsingle blows?\b|\b(?:simple|compound) throw\b)",
     "coup → blow"),
    (r"\bbottes?\b", r"\bthrusts?\s*/\s*hits?\b", "botte → thrust"),
    (r"\bplanches?\b", r"\bplanks?\b", "planche → plate"),
    (r"\btac[- ]au[- ]tac\b", r"\btac[- ]to[- ]the[- ]tac\b", "du tac au tac → tac-au-tac"),
    (r"\brompre|\brompez|\brompu", r"\bromp(?!re\b|ez\b|u\b)", "rompre → break ground"),
    # of a blade only -- "fil de soie" is real silk thread
    (r"\bsoie\b", r"\bsilk\b(?=.{0,40}\b(?:blade|hilt|handle|guard|pommel|grip)\b)"
     r"|\b(?:blade|hilt|handle|guard|pommel|grip)\b.{0,40}\bsilk\b", "soie → tang"),
]
# Misspellings the model has produced repeatedly (fix_known_garbles() repairs
# new output; this catches output translated before the repair existed).
# Book-wide consistency: a French phrase rendered several ways across the
# book. Not an error on any one page -- a choice to make once ([terms] in
# the overrides file) and apply with `--pages` to the minority pages.
CONSISTENCY = [
    ("coups simples / composés", r"\bcoups?\s+(?:simples?|composés?)\b",
     r"\b(?:simple|compound)\s+(thrusts?|blows?|throws?|strikes?|attacks?|hits?)\b"),
    ("botte (de quarte …)", r"\bbottes?\s+de\s+\w+",
     r"\b(hits?|thrusts?|lunges?|finish(?:es)?)\s+(?:of|in|to)\s+(?:prime|seconde|tierce|quarte|quinte|sixte|septime|octave)\b"),
]
GARBLES = r"\b(?:thusts?|throughs|rapises?|rompers?|rippostes?|(?-i:[A-Z]{2,}[a-z]{2,}[A-Z]*))\b"
META = re.compile(r"(?i)\b(?:here is the translation|translated text:|as an ai\b|"
                  r"i cannot translate|please provide the text|i'm sorry)")


def _body(tr: str) -> str:
    """English page without the footnote block and pipeline markers."""
    t = tr.split("\n" + FN_HEADER + "\n", 1)[0]
    return re.sub(r"(?m)^\[[^\]\n]*\]\s*$", "", t)


def _norm_title(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", re.sub(r"</?i>", "", s or ""))
    return re.sub(r"[^a-z]+", "", "".join(c for c in s if not unicodedata.combining(c)).lower())


def lead_title(body: str) -> str:
    """The first line of a page body if it stands alone as a title: short,
    followed by a blank line (or nothing), and either mostly capitals or a
    short sentence-case heading ("Du dégagement."). Else ""."""
    lines = [l.strip() for l in (body or "").strip("\n").split("\n")]
    lines = [l for l in lines if not l.startswith("⟦MARGIN")]
    if not lines or not lines[0]:
        return ""
    first = lines[0]
    if len(lines) > 1 and lines[1]:
        return ""                                     # runs on: a paragraph, not a title
    plain = re.sub(r"</?i>", "", first)
    letters = [c for c in plain if c.isalpha()]
    words = plain.split()
    if len(letters) < 4 or len(words) > 10 or plain.lower() == "none" \
            or re.search(r"[,;]$", plain) or plain.startswith("FIGURES"):
        return ""
    caps = sum(c.isupper() for c in letters) >= 0.6 * len(letters)
    heading = len(words) <= 6 and plain[:1].isupper() and plain.rstrip().endswith(".")
    return first if caps or heading else ""


def title_issues(raw_body: str, header: str, ex_body: str, tr: str,
                 own_header: str = "", captions: str = "") -> list:
    """A body title the OCR read that the page lost: dropped by the page
    parse, or dropped in translation. [(level, kind, detail)]. Added after
    14 Gomard chapter titles went missing (a margin-note copy check) and
    only a hand review caught it."""
    out = []
    t = lead_title(raw_body)
    if t:
        # printed twice (running head AND title below it, Gomard's chapter
        # openers): the OCR reported it as its own HEADER too -- then the
        # body copy must survive, the header alone doesn't count
        twice = _norm_title(own_header) == _norm_title(t)
        # a whole line near the top of the body (not a margin note, which
        # can repeat a title's words: "Valeur de la riposte."), the header
        # unless printed twice, or a figure caption (plate labels move there)
        lines = [l for l in (ex_body or "").strip("\n").split("\n")
                 if l.strip() and not l.lstrip().startswith("⟦MARGIN")][:6]
        have = {_norm_title(l) for l in lines} | {_norm_title(captions)} \
            | (set() if twice else {_norm_title(header)} | {_norm_title(x) for x in header.split("\t")})
        nt = _norm_title(t)
        if nt and nt not in have and not any(nt in c for c in (_norm_title(captions),) if c):
            out.append((CHECK, "title", f"the OCR read the title {t!r} at the top of the "
                        "page, but the page text lost it"))
    et = lead_title(ex_body)
    if et:
        tl = [l for l in _body(tr).strip("\n").split("\n")
              if l.strip() and not l.startswith("⟦MARGIN")]
        first = re.sub(r"</?i>", "", tl[0]) if tl else ""
        letters = [c for c in first if c.isalpha()]
        is_head = bool(letters) and (sum(c.isupper() for c in letters) >= 0.6 * len(letters)
                                     or (len(first.split()) <= 8 and first.rstrip().endswith(".")))
        if not is_head:
            out.append((CHECK, "title", f"body title {et!r} is not a heading in the "
                        "translation (missing, or lowercased into the text)"))
    return out


def _words(s: str) -> int:
    return len(re.findall(r"[^\W\d_]+", re.sub(r"</?i>", "", s)))


def translation_issues(src: str, t: str) -> list:
    """The per-page checks on a translation: [(level, kind, detail)].
    `src` is the page's French body, `t` its translation.txt entry. Shared
    by the review report and the translation model selection test
    (evals/translation_model_test.py)."""
    out = []
    body = _body(t)
    if t.lstrip().startswith("[low-confidence translation"):
        out.append((CHECK, "translation", "flagged low-confidence by step 2's quality gate"))
    if t.lstrip().startswith("[translation unavailable"):
        out.append((CHECK, "translation", "no usable translation -- the French is shown instead"))
    if t.lstrip().startswith("[oversized OCR page"):
        out.append((CHECK, "ocr", "OCR ran away (repetition); only the start was translated"))
    if META.search(body):
        out.append((CHECK, "translation", f"model chatter in the text: "
            f"{META.search(body).group(0)!r}"))
    m = re.search(GARBLES, body, re.I)
    if m:
        out.append((CHECK, "wording", f"misspelling the model is known for: {m.group(0)!r}"))
    for fr, wrong, rule in TERM_RULES:
        if re.search(fr, src, re.I):
            w = re.search(wrong, re.sub(r"<i>.*?</i>", "", body), re.I)
            if w:
                out.append((CHECK, "wording", f"{w.group(0)!r} against the glossary ({rule})"))
    # leftover French: a whole paragraph the model didn't translate
    for para in re.split(r"\n\s*\n", body):
        if _words(para) >= 8 and still_french(para):
            out.append((CHECK, "translation", "paragraph left (mostly) in French: "
                f"{para.strip()[:60]!r}"))
            break
    # length: an omission or a runaway, on pages with enough text to judge
    # whole page incl. the footnote block: the author's own footnotes
    # move there, so body-only would read as an omission
    # tables are left to the scan, images are placeholders: not text
    sw = _words(re.sub(r"(?is)<table\b.*?(?:</table\s*>|\Z)|!\[[^\]]*\]\([^)]*\)", " ", src))
    tw = _words(re.sub(r"(?m)^\[[^\]\n]*\]\s*$|^___FOOTNOTES___$|\[margin note\]", "", t))
    if sw >= 40 and not t.lstrip().startswith("["):
        r = tw / sw
        if r < 0.7:
            out.append((CHECK, "translation", f"English is {r:.0%} the length of the "
                f"French ({tw} vs {sw} words) -- something may be missing"))
        elif r > 1.8:
            out.append((NOTE, "translation", f"English is {r:.0%} the length of the "
                f"French ({tw} vs {sw} words) -- repetition or additions?"))
    lab = re.match(r"\s*(?:planche|pl\.|fig\.?|figure)\s+([IVXLC\d]+)\b", src, re.I)
    if lab and not t.lstrip().startswith("[") and not re.match(
            r"\s*(?:<i>)?(?:plate|pl\.|fig\.?|figure)\s+" + lab.group(1) + r"\b", t, re.I):
        out.append((CHECK, "translation", f"the page's label {lab.group(0).strip()!r} "
            "is missing from the English"))
    n_src, n_en = src.count("⟦MARGIN:"), t.count("⟦MARGIN:")
    if n_en < n_src:
        out.append((CHECK, "margin note", f"{n_src - n_en} of the page's {n_src} margin "
                    "note(s) missing from the English"))
    if "[margin note]" in t:
        out.append((NOTE, "layout", "a paragraph was moved to the notes as a margin note "
            "(heuristic) -- confirm it is one"))
    return out


def page_map(folder: str, n: int) -> dict:
    """scan page -> side-by-side pdf page, read off the built PDF (a page
    that overflows adds a '(cont.)' sheet, shifting everything after it)."""
    pdfs = glob.glob(f"{folder}/*Side by Side*.pdf")
    if not pdfs:
        return {}
    try:
        import fitz
    except ImportError:
        return {}
    d = fitz.open(pdfs[0])
    out, scan = {}, 0
    for p in range(1, len(d)):                          # sheet 1 is the cover
        r = d[p].rect
        head = d[p].get_text(clip=fitz.Rect(r.width / 2, 0, r.width, 60)).lstrip()
        if not re.match(r"(?:Page \S+\s+)?\(cont\.\)", head):
            scan += 1
            out[scan] = p + 1
    return out if scan == n else {}


def check_book(folder: str) -> list:
    I = f"{folder}/intermediate"
    ex = _pageio.split_pages(open(f"{I}/extracted.txt", encoding="utf-8").read())
    tr = _pageio.split_pages(open(f"{I}/translation.txt", encoding="utf-8").read())
    pn = open(f"{I}/page_numbers.txt", encoding="utf-8").read().split("\n")
    hmap = json.load(open(f"{I}/headers.json", encoding="utf-8")) \
        if os.path.exists(f"{I}/headers.json") else {}
    figs = json.load(open(f"{I}/figures.json", encoding="utf-8")) \
        if os.path.exists(f"{I}/figures.json") else {}
    issues = []
    raw = _pageio.split_pages(open(f"{I}/raw_ocr.txt", encoding="utf-8").read()) \
        if os.path.exists(f"{I}/raw_ocr.txt") else []

    def add(scan, level, kind, detail):
        issues.append({"scan": scan, "level": level, "kind": kind, "detail": detail})

    if len(ex) != len(tr):
        add(0, CHECK, "alignment", f"extracted.txt has {len(ex)} pages, translation.txt "
            f"{len(tr)} -- translations are attached to the wrong scans")
        return issues

    for i, (e, t) in enumerate(zip(ex, tr)):
        s = i + 1
        src = clean_vlm_extraction(e) if e.strip() else ""
        body = _body(t)
        if "VLM Error" in e:
            add(s, CHECK, "ocr", "the OCR model failed on this page")
        for level, kind, detail in translation_issues(src, t):
            add(s, level, kind, detail)
        if raw and i < len(raw):
            rb = raw[i].split("BODY_TEXT:", 1)[1] if "BODY_TEXT:" in raw[i] else ""
            h = (re.search(r"(?m)^HEADER: (.*)$", e) or [None, ""])[1].strip()
            eb = e.split("BODY_TEXT:", 1)[1] if "BODY_TEXT:" in e else ""
            oh = re.search(r"(?im)^[ \t]*HEADER[ \t]*:[ \t]*(.*)$", raw[i])
            caps = " ".join(f.get("caption") or "" for f in figs.get(str(i), []))
            for level, kind, detail in title_issues(rb, h, eb, t, oh.group(1) if oh else "", caps):
                add(s, level, kind, detail)
        if i and src.strip() and len(src) > 80:
            prev = clean_vlm_extraction(ex[i - 1]) if ex[i - 1].strip() else ""
            norm = lambda x: re.sub(r"\W+", "", x.lower())[:300]
            if norm(src) == norm(prev):
                add(s, NOTE, "scan", f"same text as scan {s - 1} -- the source scanned "
                    "this leaf twice")
        if src.count("[?]") >= 3:
            add(s, NOTE, "ocr", f"{src.count('[?]')} illegible spots marked by the OCR")
        for f in figs.get(str(i), []):
            cap = (f.get("caption") or "").strip()
            if cap and not (f.get("caption_en") or "").strip():
                add(s, CHECK, "caption", f"plate caption not translated: {cap[:50]!r}")

    # terminology consistency (book level, reported separately)
    for name, fr, en in CONSISTENCY:
        seen = defaultdict(list)
        for i, (e, t) in enumerate(zip(ex, tr)):
            if re.search(fr, clean_vlm_extraction(e) if e.strip() else "", re.I):
                for m in re.finditer(en, re.sub(r"</?i>", "", _body(t)), re.I):
                    seen[m.group(1).lower().rstrip("s").replace("finishe", "finish")].append(i + 1)
        if len(seen) > 1:
            issues.append({"scan": 0, "level": "TERMS", "kind": name,
                           "detail": {k: sorted(set(v)) for k, v in seen.items()}})

    # running heads vs. the layout detector (intermediate/layout.json)
    lay = _layout.load(folder)
    if lay and os.path.exists(f"{I}/raw_ocr.txt"):
        raw = _pageio.split_pages(open(f"{I}/raw_ocr.txt", encoding="utf-8").read())
        pages = [lay["pages"].get(str(i), {"boxes": []}) for i in range(len(ex))]
        band = _layout.header_band(pages)
        for i, e in enumerate(ex):
            m = re.search(r"(?im)^[ \t]*HEADER[ \t]*:[ \t]*(.*)$", raw[i]) if i < len(raw) else None
            v = (m.group(1).strip().strip("[]") if m else "").lower()
            vlm_h = bool(v) and v not in ("none", "n/a", "-")
            printed, unsure, why = _layout.running_head(pages[i], band, vlm_h)
            h = (re.search(r"(?m)^HEADER: (.*)$", e) or [None, ""])[1].strip()
            shown = bool(h) and h.lower() != "none"
            if unsure:
                add(i + 1, NOTE, "layout", "the layout detector and the OCR disagree on "
                    "whether this page has a running head")
            elif printed and not shown:
                add(i + 1, CHECK, "layout", "the page prints a running head, but none is shown")
            elif shown and not printed:
                add(i + 1, CHECK, "layout", f"running head {h.replace(chr(9), ' / ')!r} is "
                    "shown, but the page prints none (title page, plate, or opener?)")

    # page numbers: a shown folio must rise, and by no more than the sheets
    last = None
    for i, v in enumerate(pn[:len(ex)]):
        if not v or v.startswith("[") or not v.isdigit():
            continue
        if last is not None:
            li, lv = last
            if int(v) <= lv:
                add(i + 1, CHECK, "page number", f"shows p. {v} after p. {lv} "
                    f"(scan {li + 1}) -- goes backwards; one of the two is wrong")
                add(li + 1, CHECK, "page number", f"shows p. {lv}, but scan {i + 1} "
                    f"after it shows p. {v} -- one of the two is wrong")
            elif int(v) - lv > i - li:
                add(i + 1, CHECK, "page number", f"shows p. {v}, only {i - li} scan "
                    f"page(s) after p. {lv} -- skips ahead")
        last = (i, int(v))

    # running heads left in French: one entry per distinct head, on its
    # first page, listing where else it appears
    where = defaultdict(list)
    for i, e in enumerate(ex):
        m = re.search(r"(?m)^HEADER: (.*)$", e)
        h = m.group(1).strip() if m else ""
        if h and h.lower() != "none":
            where[h].append(i + 1)
    for h, scans in where.items():
        en = hmap.get(h, "")
        g = re.search(GARBLES, en, re.I)
        if g:
            add(scans[0], CHECK, "header", f"running head {en!r}: misspelling "
                f"{g.group(0)!r} (on {len(scans)} page(s))")
        for fr, wrong, rule in TERM_RULES:
            w = re.search(fr, h, re.I) and re.search(wrong, en, re.I)
            if w:
                add(scans[0], CHECK, "header", f"running head {en!r}: {w.group(0)!r} "
                    f"against the glossary ({rule}; on {len(scans)} page(s))")
        if not en or (en.strip() == h.strip() and still_french(h.replace("\t", " "), min_fr=2)):
            more = f" (also on {len(scans) - 1} more page(s), through scan {scans[-1]})" \
                if len(scans) > 1 else ""
            add(scans[0], CHECK, "header",
                (f"running head {h!r} left in French: {en!r}" if en
                 else f"running head {h!r} was never translated") + more
                + " -- set its English under [headers] in the overrides file")
    return issues


def write_report(folder: str, stem: str, issues: list) -> str:
    I = f"{folder}/intermediate"
    ex_n = len(_pageio.split_pages(open(f"{I}/extracted.txt", encoding="utf-8").read()))
    pn = open(f"{I}/page_numbers.txt", encoding="utf-8").read().split("\n")
    pmap = page_map(folder, ex_n)
    ov = _overrides.load(stem)
    by_page = defaultdict(list)
    terms = [x for x in issues if x["level"] == "TERMS"]
    issues = [x for x in issues if x["level"] != "TERMS"]
    for x in issues:
        by_page[x["scan"]].append(x)
    n_check = sum(1 for x in issues if x["level"] == CHECK)
    pages_check = sorted({x["scan"] for x in issues if x["level"] == CHECK})
    kinds = Counter(x["kind"] for x in issues if x["level"] == CHECK)

    def where(s):
        folio = pn[s - 1] if 0 < s <= len(pn) else ""
        bits = [f"pdf {pmap[s]}" if s in pmap else None, f"scan {s}",
                f"p. {folio}" if folio and not folio.startswith("[") else None]
        return " · ".join(b for b in bits if b)

    L = [f"# Review report — {stem}", "",
         f"{ex_n} pages. **{len(pages_check)} page(s) to check** "
         f"({n_check} issue(s)); {sum(1 for x in issues if x['level'] == NOTE)} note(s).",
         "", "Record decisions in `books/" + stem + ".overrides.toml` (manual III.5b); "
         "retranslate with `2_translate.py --pages " + stem + " <scan pages>`.", ""]
    if not pmap:
        L += ["*The side-by-side PDF is missing or out of date, so pdf page numbers "
              "are not shown — rebuild it and rerun this report.*", ""]
    if kinds:
        L += ["| Kind | Checks |", "|---|---|"] + [f"| {k} | {n} |" for k, n in kinds.most_common()] + [""]
    if terms:
        L += ["## Terminology to settle", "",
              "One French phrase, several English renderings across the book. Pick one, "
              "add it under `[terms]` in the overrides file, and retranslate the other "
              "pages with `--pages`.", ""]
        for x in terms:
            parts = sorted(x["detail"].items(), key=lambda kv: -len(kv[1]))
            L.append(f"- **{x['kind']}**: " + "; ".join(
                f"*{k}* on {len(v)} page(s) (scan {', '.join(map(str, v))})"
                for k, v in parts))
        L.append("")
    L += ["## Pages to check", ""]
    for s in sorted(by_page):
        items = sorted(by_page[s], key=lambda x: x["level"] != CHECK)
        if items[0]["level"] != CHECK:
            continue
        L.append(f"- **{where(s)}**" + ("  *(has overrides)*" if s in ov["pages"] else ""))
        L += [f"    - {x['level']} · {x['kind']}: {x['detail']}" for x in items]
    notes = [s for s in sorted(by_page) if all(x["level"] == NOTE for x in by_page[s])]
    if notes:
        L += ["", "## Notes", ""]
        for s in notes:
            L.append(f"- {where(s)}: " + "; ".join(f"{x['kind']} — {x['detail']}" for x in by_page[s]))
    out = f"{folder}/{stem} — Review Report.md"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    with open(f"{I}/quality_report.json", "w", encoding="utf-8") as fh:
        json.dump(issues, fh, ensure_ascii=False, indent=1)
    return out


def main(stems):
    for stem in stems:
        folder = f"/data/processed/{stem}_translated"
        if not os.path.exists(f"{folder}/intermediate/translation.txt"):
            print(f"✗ {stem}: not translated yet")
            continue
        issues = check_book(folder)
        out = write_report(folder, stem, issues)
        pages = len({x["scan"] for x in issues if x["level"] == CHECK})
        print(f"✓ {stem}: {pages} page(s) to check -> {out}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    if sys.argv[1:] == ["--all"]:
        main(sorted(os.path.basename(f)[:-len("_translated")]
                    for f in glob.glob("/data/processed/*_translated")))
    else:
        main(sys.argv[1:])
