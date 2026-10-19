#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
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
# Italian books: the same test against Italian function words (counted
# separately -- the two lists share "la", "le", "un" -- and the larger wins)
_IT = set("il lo la i gli le di da del della dei delle degli che non è sono per con una "
          "un uno nel nella nei si ma come più anche questo questa quello essere al alla".split())


def still_french(text: str, min_fr: int = 5) -> bool:
    w = [x.lower() for x in re.findall(r"[^\W\d_]+", re.sub(r"<i>.*?</i>|«[^»]*»|\"[^\"]*\"", " ", text))]
    fr = max(sum(x in _FR for x in w), sum(x in _IT for x in w))
    en = sum(x in _EN for x in w)
    return fr >= min_fr and fr > 2 * en

# Terms the model is known to render against the glossary. (source regex,
# wrong-English regex, what it should be). Only fires when the SOURCE has
# the term, so a legitimate English word elsewhere is left alone.
TERM_RULES = [
    # coup = the whole offensive action -> attack (or hit: the touch that
    # lands); never "blow" (operator decision 2026-09-28, replacing the
    # pass-8 "blow"). botte = its finish -> thrust (Gomard pdf 158).
    # "throws" only as a noun for coups ("the throws", "simple throws", a
    # THROWS heading) -- "he throws himself onto the point" is correct English
    (r"\bcoups?\b", r"(?:\b(?:the|these|those|such|all|two|three|simple|compound|straight|single|of)"
     r"\s+throws\b|\b(?-i:THROWS)\b|\b(?:simple|compound) throw\b|"
     # "blow" is right where coup isn't the fencing action: hammer blows
     # (La Boëssière's mounting chapter), "coups et blessures" (the legal
     # "blows and wounds"), a coup sec on the blade ("a sharp blow"), a bull's
     # horn, "coups du hasard"
     r"(?<!hammer )(?<!sharp )(?<!clean )(?<!light )\bblows?\b"
     r"(?!\s+(?:of\s+(?:the\s+)?(?:hammer|horn|chance)|and\s+wounds|on\s+the\s+(?:opponent|blade)|with\s+a\s+(?:stick|cane))))",
     "coup → attack"),
    (r"\bbottes?\b", r"\bthrusts?\s*/\s*hits?\b", "botte → thrust"),
    # a thrust is not a cut, and a cut is not a "blow" (Gomard pdf 43,
    # Vauresmont pdf 115: "point cut", "cutting blow"; 2026-10-02)
    (r"\bcoups? de pointe\b", r"\bpoint[- ]cuts?\b", "coup de pointe → thrust"),
    (r"\bcoups? de (?:taille|tranchant)\b", r"\bcutting blows?\b", "coup de taille → cut"),
    (r"\bplanches?\b", r"\bplanks?\b", "planche → plate"),
    (r"\btac[- ]au[- ]tac\b", r"\btac[- ]to[- ]the[- ]tac\b", "du tac au tac → tac-au-tac"),
    (r"\brompre|\brompez|\brompu", r"\bromp(?!re\b|ez\b|u\b)", "rompre → break ground"),
    # of a blade only -- "fil de soie" is real silk thread
    (r"\bsoie\b", r"\bsilk\b(?=.{0,40}\b(?:blade|hilt|handle|guard|pommel|grip)\b)"
     r"|\b(?:blade|hilt|handle|guard|pommel|grip)\b.{0,40}\bsilk\b", "soie → tang"),
]
# Italian books only (glossary 2026-09-30, translation_prompt_it.txt) -- a
# French book has its own "volant" (garde volante) and quotes Italian titles
# in its bibliography. colpo: the strike -> hit, the whole action ->
# attack; never "blow" (same exceptions as coup)
TERM_RULES_IT = [
    (r"\bcolp[oi]\b|\burt[oi]\b", r"(?<!hammer )(?<!sharp )(?<!clean )(?<!light )\bblows?\b"
     r"(?!\s+(?:of\s+(?:the\s+)?(?:hammer|horn|chance)|and\s+wounds))", "colpo → hit / attack"),
    # misura -> measure; "distance" only where the book says distanza
    (r"^(?![\s\S]*\bdistanz)[\s\S]*\bmisura\b", r"\bdistances?\b", "misura → measure"),
    (r"\bsciabol", r"\bsabres?\b", "sciabola → saber (US spelling)"),
    (r"\bsciabolat", r"\bsab(?:er|re)[- ](?:blows?|strokes?)\b", "sciabolata → cut"),
    (r"\bcolp[oi] di punta\b", r"\bpoint[- ]cuts?\b", "colpo di punta → thrust"),
    (r"\bcolp[oi] di taglio\b", r"\bcutting blows?\b", "colpo di taglio → cut"),
    (r"\bpicch?i?o\b|\bpicco\b", r"\b(?:pecks?|knocks?|woodpeckers?)\b", "picchio → beat"),
    # except "azioni volanti sul ferro" -> "flying actions on the blade"
    # (glossary 2026-10-11; Barbasetti scan 90) and "punta volante" -> "flying
    # point", as the French books' "pointe volante" (Masiello 1887 scan 129)
    (r"\bvolant[ei]\b", r"\bflying\b(?!\s+(?:<i>)?cartocc)(?!\s+points?\b)(?![^.;]{0,30}\bon\s+the\s+blade\b)",
     "volante → off the blade"),
    (r"\bfil[oi]\b", r"\b(?:threads?|wires?)\b", "filo → glide / edge"),
    (r"\brispost[ae]\b", r"\b(?:responses?|repl(?:y|ies)|answers?)\b(?!\s+to\s+(?:the|his|a)\s+letter)",
     "risposta → riposte"),
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
GARBLES = r"\b(?:thusts?|throughs|rapises?|riponses?|rompers?|rippostes?|(?-i:[A-Z]{2,}[a-z]{2,}[A-Z]*))\b"
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
                                     or (len(first.split()) <= 8 and first.rstrip().endswith("."))
                                     # a plate label joined to its caption on one
                                     # line, as decided ("Plate V. — Attack ...")
                                     or bool(re.match(r"(?i)(?:plate|pl\.)\s+[ivxlcdm\d]+\.?\s+—", first)))
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
    for fr, wrong, rule in _rules:
        if re.search(fr, src, re.I):
            w = re.search(wrong, re.sub(r"<i>.*?</i>", "", body), re.I)
            if w:
                out.append((CHECK, "wording", f"{w.group(0)!r} against the glossary ({rule})"))
    # one of the author's footnotes missing from the English (the count is
    # step 2's note_bodies: a marker opening a line, or any marker in the
    # footnote block, followed by the note's first word)
    def notes(x):
        body, _, block = (x or "").partition("___FOOTNOTES___")
        mark, word = r"\((?:\d{1,2}|\*{1,3})\)", r"[ \t.]*(?:<i>|[«“\"])?[^\W\d_]"
        return (len(re.findall(r"(?m)^[ \t]*" + mark + word, body))
                + len(re.findall(mark + word, block)))
    if notes(t) < notes(src):
        out.append((CHECK, "translation", f"a footnote of the source is missing "
                    f"({notes(src)} in the source, {notes(t)} in the English)"))
    # leftover French: a whole paragraph the model didn't translate
    for para in re.split(r"\n\s*\n", body):
        if _words(para) >= 8 and still_french(para):
            out.append((CHECK, "translation", "paragraph left (mostly) untranslated: "
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
        # anywhere in the top strip: the running head can sit before the
        # page number on that line (the book's places, 2026-10-18)
        if not re.search(r"\(cont\.\)", head):
            scan += 1
            out[scan] = p + 1
    return out if scan == n else {}


_rules = TERM_RULES      # the book's language's list, set by check_book()


def _edits(a: str, b: str) -> int:
    """Letters to change, add or drop to turn a into b."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# numbers in words: heads that differ in one ("QUARTO" / "QUINTO", "FIFTH" /
# "SIXTH") are different heads, not a misreading
_ORDINALS = set("""PRIMO PRIMA SECONDO SECONDA TERZO TERZA QUARTO QUARTA QUINTO QUINTA
SESTO SESTA SETTIMO SETTIMA OTTAVO OTTAVA NONO NONA DECIMO DECIMA UNDICESIMO DODICESIMO
PREMIER PREMIÈRE PREMIERE DEUXIÈME DEUXIEME SECOND SECONDE TROISIÈME TROISIEME QUATRIÈME
QUATRIEME CINQUIÈME CINQUIEME SIXIÈME SIXIEME SEPTIÈME SEPTIEME HUITIÈME HUITIEME NEUVIÈME
NEUVIEME DIXIÈME DIXIEME FIRST THIRD FOURTH FIFTH SIXTH SEVENTH EIGHTH NINTH TENTH
ELEVENTH TWELFTH ONE TWO THREE FOUR FIVE SIX SEVEN EIGHT NINE TEN UNO DUE TRE QUATTRO
CINQUE SEI SETTE OTTO NOVE DIECI UN DEUX TROIS QUATRE CINQ SEPT HUIT NEUF DIX""".split())


def head_variants(where: dict, hmap: dict) -> list:
    """Running heads shown one or two letters apart are usually one head the
    OCR misread: "FERDINANDO MASIEMLO" / "MASIELLO" / "MASIello". Compared
    as shown in the English (headers.json), so a misreading the translation
    already smoothed over is no issue, and punctuation-only differences
    ("INTRODUCTION" / "INTRODUCTION.") are ignored; capitals are not (they
    show). Which spelling is right
    is the reviewer's call -- the commoner one can be the misreading
    (Barbasetti: "BARBASSETTI" on 63 pages, right on 11) -- so this only
    reports, once per group; the decision is a [headers] line in the
    overrides file. Heads differing in a number are different heads."""
    def norm(h):
        h = h.replace("\u2019", "'").replace("\t", " ")
        return re.sub(r"\s+", " ", re.sub(r"[.,:;!?]", "", h)).strip()
    shown = defaultdict(list)                     # English as shown -> scans
    for h, scans in where.items():
        shown[(hmap.get(h) or h).strip()] += scans
    by_norm = defaultdict(list)
    for en, scans in shown.items():
        by_norm[norm(en)] += scans
    keys = sorted(by_norm, key=lambda k: -len(by_norm[k]))
    def nums(h):                                  # numbers, in figures or words
        return re.findall(r"\d+|\b[IVXLC]+\b", h) + \
            [w for w in re.findall(r"[A-ZÀ-Ý]+", h.upper()) if w in _ORDINALS]
    group = {k: k for k in keys}                  # each variant -> commonest
    for x, k in enumerate(keys):
        for c in keys[:x]:
            if group[c] != c or len(min(k, c, key=len)) < 6 or nums(k) != nums(c):
                continue
            if k.upper() == c.upper() and not any(
                    re.search(r"[A-Z]{2,}[a-z]|[a-z][A-Z]", x) for x in (k, c)):
                continue                          # "THE ÉPÉE" / "The Épée": style, not a misreading
            if abs(len(k) - len(c)) <= 2 and _edits(k.upper(), c.upper()) <= 2:
                group[k] = c
                break
    out = []
    for c in keys:
        members = [k for k in keys if group[k] == c]
        if len(members) < 2:
            continue
        variants = "; ".join(f"{k!r} ({len(by_norm[k])})" for k in members)
        first = min(s for k in members[1:] for s in by_norm[k])
        out.append((CHECK, first, f"running head shown in {len(members)} spellings: "
                    f"{variants} -- likely one head, misread or translated two ways; "
                    "set one English for every spelling under [headers]"))
    return out


def _ocr_head(raw_page: str) -> bool:
    m = re.search(r"(?im)^[ \t]*HEADER[ \t]*:[ \t]*(.*)$", raw_page or "")
    v = (m.group(1).strip().strip("[]") if m else "").lower()
    return bool(v) and v not in ("none", "n/a", "-")


def folio_shaped_heads(pages: list, raw: list) -> tuple:
    """(pages, of) when the book looks like one that prints no running heads
    and the detector's page_header box is its page number; () otherwise.
    Counted over pages whose confident head boxes are all at most ~3
    characters wide (pixel width <= 5x height): the OCR reads no head on
    at least 80% of them. Measured 2026-10-05 over 19 books: Bruno 94%,
    Florio 90%, Lambertini 86% (all print only page numbers, scan-checked);
    every book with real running heads 66% or less (LaMarche)."""
    small = none = 0
    for i, p in enumerate(pages):
        st = [b for b in p["boxes"] if b[0] == "page_header" and b[1] >= 0.5 and _layout._horiz(b)]
        if not st or i >= len(raw) or not p.get("w"):
            continue
        if all(_layout._wid(b) * p["w"] <= 5 * _layout._hgt(b) * p["h"] for b in st):
            small += 1
            none += not _ocr_head(raw[i])
    return (none, small) if small >= 20 and none >= 0.8 * small else ()


def check_book(folder: str) -> list:
    global _rules
    _rules = TERM_RULES_IT if _ns["book_language"](
        os.path.basename(folder)[:-len("_translated")]) == "Italian" else TERM_RULES
    I = f"{folder}/intermediate"
    ex = _pageio.split_pages(open(f"{I}/extracted.txt", encoding="utf-8").read())
    tr = _pageio.split_pages(open(f"{I}/translation.txt", encoding="utf-8").read())
    pn = open(f"{I}/page_numbers.txt", encoding="utf-8").read().split("\n")
    import _render
    hmap = _render.header_map(I)          # with two-part heads either way round
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
            if cap and not f.get("decided") and _render.describes_picture(cap):
                # not printed: the OCR model described the picture. The PDFs
                # leave it out; if the book does print a caption here, set it
                add(s, NOTE, "caption", f"recorded caption left out -- it describes the "
                    f"picture ({cap[:50]!r}); if a caption is printed, set [pages.{s}] caption")
            elif cap and not (f.get("caption_en") or "").strip():
                add(s, CHECK, "caption", f"plate caption not translated: {cap[:50]!r}")

    # the second, caption-only reading of the page (verify_captions.py):
    # a printed caption step 1 didn't record, or recorded differently
    _cr = f"{I}/caption_read.json"
    if os.path.exists(_cr):
        import verify_captions as _vc
        try:
            _dec = {p for p, v in _overrides.load(os.path.basename(folder)[:-len("_translated")])
                    ["pages"].items() if "caption" in v or "caption_en" in v}   # either is a decision
        except _overrides.OverridesError:
            _dec = set()
        for s_, d in json.load(open(_cr, encoding="utf-8")).items():
            s_ = int(s_)
            if s_ in _dec:
                continue
            kept = [r for r in d.get("recorded", []) if not _render.describes_picture(r)]
            head = (re.search(r"(?m)^HEADER: (.*)$", ex[s_ - 1]) or [None, ""])[1] if s_ <= len(ex) else ""
            found = [" ".join(f_.split()) for f_ in d.get("found", []) if not _vc.is_noise(f_, head)]
            if not found:
                continue
            # one item per page: a plate with several captioned figures
            # (Enrichetti scan 143) is one thing to fix, not eight
            shown = " | ".join(found)
            shown = shown if len(shown) <= 110 else shown[:107] + "..."
            if kept:
                # usually the recorded one is wrong (Masiello 1887 scan 487:
                # "Ensuite de lever" for the sideways "Invito di terza")
                add(s_, CHECK, "caption", f"recorded caption {kept[0][:45]!r} differs from what "
                    f"is printed: {shown!r} -- set [pages.{s_}] caption to the printed one")
            else:
                add(s_, CHECK, "caption", f"caption printed here but not recorded: {shown!r} "
                    f"-- set [pages.{s_}] caption if it belongs to the picture")

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

    # formula notation the OCR wrote in LaTeX and plain_math() (step 1) could
    # not turn into plain text
    for i, t in enumerate(tr):
        m = re.search(r"\\[(\[].{0,40}", _body(t or ""))
        if m:
            add(i + 1, CHECK, "ocr", f"formula left in LaTeX notation: {m.group(0)!r}")

    # a sentence still split across a page break in the English: the page
    # ends mid-sentence and the next page with text continues in lowercase.
    # Step 1 joins every split sentence onto one page (join_split_sentences);
    # what's left -- a table across the break, a margin note inside the
    # fragment -- is listed so nothing is translated as two halves unseen.
    def _english(t):
        b = re.sub(r"\u27e6MARGIN:[^\u27e7]*\u27e7", "", _body(t or ""))
        b = re.sub(r"\[\[(?:TABLE|DIVIDER)\]\]", "", b).strip()
        return "" if b in ("", "[Empty page]") else b
    eng = [_english(t) for t in tr]
    for i in range(len(eng) - 1):
        a = eng[i]
        if not a or re.search(r"[.!?;:\u00bb\u201d\")\]\u2026*]\s*\Z", a):
            continue
        j = i + 1 if eng[i + 1] or i + 2 >= len(eng) else i + 2
        if re.match(r"[a-z]", eng[j] or ""):
            add(i + 1, CHECK, "split", f"sentence split across the page break: "
                f"\u2026{a[-40:]!r} | {eng[j][:40]!r}\u2026")

    # running heads vs. the layout detector (intermediate/layout.json)
    # (not in a book set to print none -- [book] running_heads = false: the
    # detector's head there is the page number, so it has no say)
    lay = _layout.load(folder)
    if lay and os.path.exists(f"{I}/raw_ocr.txt") and not _layout.no_running_heads(folder):
        raw = _pageio.split_pages(open(f"{I}/raw_ocr.txt", encoding="utf-8").read())
        pages = [lay["pages"].get(str(i), {"boxes": []}) for i in range(len(ex))]
        band = _layout.header_band(pages)
        folio_heads = folio_shaped_heads(pages, raw)
        if folio_heads:
            add(0, "BOOK", "running heads", f"on {folio_heads[0]} of {folio_heads[1]} pages "
                "the detector's running head is only a few characters wide and the OCR "
                "reads no head there: it is probably the page number. If the book prints "
                "no running heads, add `[book]` `running_heads = false` to the overrides "
                "file and rerun step 1 (`reprocess_extracted.py`); until then those pages "
                "are not listed below.")
        # a page whose running head the reviewer set in the overrides file
        # ([pages.N] header, "" = none) is decided -- not re-flagged (Jacob
        # scan 49, La Boëssière scan 10: titles, not running heads)
        try:
            decided = {s for s, v in _overrides.load(os.path.basename(folder)[:-len("_translated")])
                       ["pages"].items() if "header" in v}
        except _overrides.OverridesError:
            decided = set()
        for i, e in enumerate(ex):
            if i + 1 in decided:
                continue
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
                # a plate's printed plate number ("Pl. 6.") reads as a page
                # head to the detector; a plate page shows no running head
                # (La Boëssière's 18 plates)
                if not any(f.get("kind") == "plate" for f in figs.get(str(i), [])) \
                        and not folio_heads:
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

    # --- 2026-10-18: checks for what the reviewer found by eye -------------
    # (a) the same short source line (a heading, a lesson label) translated
    # two ways in the book: Barbasetti "I. TEMPO" -> "I. TIME" / "I. TEMPO"
    # (38 of 86), "Azioni composte" -> compound / composite actions
    plain = lambda x: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", x)).strip()
    seen_as = defaultdict(lambda: defaultdict(list))
    for i, (e, t) in enumerate(zip(ex, tr)):
        sl = [plain(l) for l in clean_vlm_extraction(e).split("\n") if l.strip()] if e.strip() else []
        el = [plain(l) for l in _body(t).split("\n") if l.strip() and not l.startswith("[[")]
        if len(sl) != len(el):
            continue
        for a, b in zip(sl, el):
            if 1 <= len(a.split()) <= 6 and len(re.findall(r"[^\W\d_]", a)) >= 3:
                seen_as[a.upper().rstrip(".:")][b.upper().rstrip(".:")].append(i + 1)
    for a, ens in seen_as.items():
        # a variant sharing no word with the usual one is a page whose lines
        # are shifted against the source ("I. TEMPO" -> "STUDENT"), not a
        # second translation
        ens = {re.sub(r"†\d+", "", k).strip(): v for k, v in ens.items()}
        top = max(ens, key=lambda k: len(ens[k])) if ens else ""
        tw = {w for w in re.findall(r"[^\W\d_]{3,}", top)}
        ens = {k: v for k, v in ens.items() if k == top or tw & set(re.findall(r"[^\W\d_]{3,}", k))}
        if len(ens) > 1 and sum(len(v) for v in ens.values()) >= 3:
            ways = "; ".join(f"{k!r} ({len(v)})" for k, v in sorted(ens.items(), key=lambda kv: -len(kv[1])))
            first = min(min(v) for v in ens.values())
            add(first, CHECK, "consistency", f"{a!r} is translated {len(ens)} ways: {ways} -- "
                "pick one (a code rule or [[fix]])")

    # (b) a source word left in italics that isn't a kept term: italic in the
    # English, the same word in the source, rare in the book, not in the
    # prompt's kept terms -- Jacob-André *baleine*, Kirchhoffer "has *remisé*"
    kept = set()
    for pf in glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "translation_prompt*.txt")):
        ptxt = open(pf, encoding="utf-8").read()
        keep_rule = re.search(r"2\. KEEP.*?(?=\n3\. )", ptxt, re.S)   # the kept terms, listed plain
        for chunk in [m.group(1) for m in re.finditer(r"<i>([^<]+)</i>", ptxt)] + \
                ([keep_rule.group(0)] if keep_rule else []):
            kept |= {w.lower() for w in re.findall(r"[^\W\d_][\w'’-]*", chunk)}

    def is_kept(w):                       # plurals and inflections: croisés, épées, appuntate
        stems = {w, w.rstrip("s"), w[:-1] + "a", w[:-1] + "o", w[:-1] + "e", w[:-2] + "a", w[:-2] + "o"}
        return bool(stems & kept)
    import _pagekind
    glossary = {k for k, v in _pagekind.book_page_kinds(
        [clean_vlm_extraction(e) if e.strip() else "" for e in ex]).items() if v == "glossary"}
    ital = defaultdict(list)
    for i, (e, t) in enumerate(zip(ex, tr)):
        if i in glossary:                 # headwords kept in italics there (rule 7b)
            continue
        src_words = {w.lower() for w in re.findall(r"[^\W\d_][\w'’-]*", e)}
        for m in re.finditer(r"<i>([^<\s]+)</i>", _body(t)):
            w = m.group(1).strip(".,;:!?").lower()
            if len(w) >= 4 and w in src_words and not is_kept(w):
                ital[w].append(i + 1)
    odd = {w: v for w, v in ital.items() if len(v) <= 2}
    if len(odd) > 12:                     # one entry for a long list (Marchionni: archaic arms)
        add(0, "BOOK", "kept term", f"{len(odd)} words left in the original in italics that are "
            "not kept terms -- translate them, or add them to the kept terms: " +
            ", ".join(f"{w} (scan {v[0]})" for w, v in sorted(odd.items(), key=lambda kv: kv[1][0])))
    else:
        for w, v in sorted(odd.items(), key=lambda kv: kv[1][0]):
            add(v[0], CHECK, "kept term", f"<i>{w}</i> left in the original, but it is not one of the "
                f"kept terms{' (also scan ' + str(v[1]) + ')' if len(v) > 1 else ''} -- translate it, "
                "or add it to the kept terms")

    # (c) table cells still in the source language (tables.json)
    tables = json.load(open(f"{I}/tables.json", encoding="utf-8")) if os.path.exists(f"{I}/tables.json") else {}
    for k, v in tables.items():
        for en in v.get("en") or []:
            if not en:
                continue
            left = [plain(c) for c in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", en, re.S)
                    if still_french(plain(c), min_fr=2)]
            if left:
                add(int(k) + 1, CHECK, "table", f"{len(left)} table cell(s) left untranslated, "
                    f"e.g. {left[0][:50]!r}")

    # running heads left in French: one entry per distinct head, on its
    # first page, listing where else it appears
    where = defaultdict(list)
    for i, e in enumerate(ex):
        m = re.search(r"(?m)^HEADER: (.*)$", e)
        h = m.group(1).strip() if m else ""
        if h and h.lower() != "none":
            where[h].append(i + 1)
    for level, scan, detail in head_variants(where, hmap):
        add(scan, level, "header", detail)
    for h, scans in where.items():
        en = hmap.get(h, "")
        g = re.search(GARBLES, en, re.I)
        if g:
            add(scans[0], CHECK, "header", f"running head {en!r}: misspelling "
                f"{g.group(0)!r} (on {len(scans)} page(s))")
        for fr, wrong, rule in _rules:
            w = re.search(fr, h, re.I) and re.search(wrong, en, re.I)
            if w:
                add(scans[0], CHECK, "header", f"running head {en!r}: {w.group(0)!r} "
                    f"against the glossary ({rule}; on {len(scans)} page(s))")
        if not en or (en.strip() == h.strip() and still_french(h.replace("\t", " "), min_fr=2)):
            more = f" (also on {len(scans) - 1} more page(s), through scan {scans[-1]})" \
                if len(scans) > 1 else ""
            add(scans[0], CHECK, "header",
                (f"running head {h!r} left untranslated: {en!r}" if en
                 else f"running head {h!r} was never translated") + more
                + " -- set its English under [headers] in the overrides file")

    # hand-edited pages (books/<stem>.edits/, edit_page.py): a person has
    # read them, so the machine checks are dropped -- unless the page's
    # source text changed after the edit was made
    try:
        edits = _overrides.load_edits(os.path.basename(folder)[:-len("_translated")])
    except _overrides.OverridesError as e:
        add(0, "BOOK", "hand edits", str(e))
        edits = {}
    if edits:
        fp = _ns["page_fingerprint"]
        stale = {s for s, e in edits.items()
                 if s <= len(ex) and e["source"] and e["source"] != fp(ex[s - 1])}
        issues[:] = [x for x in issues if x["scan"] not in edits or x["level"] in ("TERMS", "BOOK")]
        for s in sorted(stale):
            add(s, CHECK, "hand edit", f"the page's source text changed after it was edited "
                f"by hand -- compare books/{os.path.basename(folder)[:-len('_translated')]}"
                f".edits/scan-{s:04d}.txt with the page")
    return issues


def write_report(folder: str, stem: str, issues: list) -> str:
    I = f"{folder}/intermediate"
    ex_n = len(_pageio.split_pages(open(f"{I}/extracted.txt", encoding="utf-8").read()))
    pn = open(f"{I}/page_numbers.txt", encoding="utf-8").read().split("\n")
    pmap = page_map(folder, ex_n)
    ov = _overrides.load(stem)
    by_page = defaultdict(list)
    terms = [x for x in issues if x["level"] == "TERMS"]
    book = [x for x in issues if x["level"] == "BOOK"]
    issues = [x for x in issues if x["level"] not in ("TERMS", "BOOK")]
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
    if book:
        L += ["## About this book", ""] + [f"- **{x['kind']}**: {x['detail']}" for x in book] + [""]
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
    import _books
    main([_books.stem_of(n) for n in _books.select(sys.argv[1:], __doc__, writes=False)])
