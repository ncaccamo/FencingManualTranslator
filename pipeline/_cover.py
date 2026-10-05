# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Shared AI-disclosure cover page for the English-only (step 3) and
side-by-side (step 4) PDFs.

One renderer so the two covers can't drift -- they did once: a hard-coded
"Qwen2.5-7B-Instruct" string outlived the swap to the 14B and sat wrong on
every cover until someone noticed.

What comes from where:
  * bibliographic head (title / author / year / publisher / digitisation
    source) -- books/<stem>.meta.json, written from books/library.md when a
    scan is acquired. Absent -> the cover falls back to the stem and a
    generic public-domain line.
  * model names -- read live from VLM_MODEL_PATH / TRANSLATION_MODEL_PATH
    at render time (never hard-coded).
  * translation revision + date -- step 2's per-page record
    (intermediate/translated_from.json "stamps"): a partial retranslation
    leaves the other pages on the revision and date they were made with.
  * PDF revision + date -- at render time (_version.pipeline_version()).
  * hand corrections -- pages a person changed ([[fix]] in the overrides
    file, whole-page edits in books/<stem>.edits/), and meta.json
    "edited_by" when the editor wants to be named.
"""
import json
import os
import re
from datetime import datetime
from pathlib import Path

import _render
import _version

GITHUB_URL = "github.com/ncaccamo/FencingManualTranslator"

DISCLOSURE = (
    "A machine translation, created on local hardware and shared for open "
    "study. It is not a replacement for a scholarly human translation."
)
METHOD = (
    "Each page was transcribed from the scan and translated separately; "
    "figures and layout follow the original."
)
CONVENTIONS = (
    "Italic words are technical terms left in the original language. Where "
    "no English term fits exactly, a footnote gives the original."
)
# The terms the translation is shared under. No name: the tool is credited
# and linked above, and other operators' runs carry this line too.
RIGHTS = ("This translation may be shared and adapted for non-commercial use, "
          "with credit, under the same terms (CC BY-NC-SA 4.0).")

_DEFAULT_ORIG_RIGHTS = "The original work is in the public domain."
_VENDOR_PREFIXES = ("allenai_", "bartowski_", "TheBloke_", "Qwen_")
# What translated every page before step 2 recorded the model per page
# (2026-09-25): a fixed fact, so changing the default model never
# re-credits an older book to the new one.
_UNRECORDED_MODEL = "Qwen2.5-14B-Instruct-Q4_K_M"


def _translation_models(book):
    """Which model(s) translated this book, from step 2's per-page record
    (intermediate/translated_from.json): "gemma4 (320 pages), Qwen2.5-… (12
    pages)". The llama-swap key / compose name map to the configured model
    file; pages (or books) with no record to _UNRECORDED_MODEL."""
    import json
    from collections import Counter
    default = _model_label("TRANSLATION_MODEL_PATH") or "Qwen3.8-27B.i1-Q4_K_S"
    p = f"/data/processed/{book}_translated/intermediate/translated_from.json"
    try:
        models = json.load(open(p, encoding="utf-8")).get("models") or []
    except (OSError, ValueError):
        models = []
    models = [m for m in models if m != "-"]      # pages that needed no model
    if not models:
        return _UNRECORDED_MODEL
    label = lambda m: (_UNRECORDED_MODEL if m == "" else
                       # the llama-swap key, current or its pre-rename name
                       default if m in ("fmt-translate", "bt-translate") or m == os.environ.get("MODEL_NAME")
                       else re.sub(r"^(?:%s)" % "|".join(_VENDOR_PREFIXES), "", m))
    counts = Counter(label(m) for m in models).most_common()
    if len(counts) == 1:
        return counts[0][0]
    return ", ".join(f"{m} ({_pages(n)})" for m, n in counts)


def _model_label(env_var):
    """Basename of the model file, minus '.gguf' and a vendor prefix. The
    quantization tag is kept -- it's part of the provenance (Q6_K OCR,
    Q4_K_M translation reproduce differently from f16)."""
    p = os.environ.get(env_var, "")
    if not p:
        return ""
    name = Path(p).name
    if name.endswith(".gguf"):
        name = name[:-5]
    for pre in _VENDOR_PREFIXES:
        if name.startswith(pre):
            name = name[len(pre):]
            break
    return name


def _pipeline_version():
    return _version.pipeline_version()


def _long_date(iso):
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%B %-d, %Y")


def _date_range(isos):
    """"September 30, 2026" / "September 26–30, 2026" /
    "September 26 – October 2, 2026" / across years, both in full."""
    a, b = min(isos), max(isos)
    if a == b:
        return _long_date(a)
    da, db = (datetime.strptime(x, "%Y-%m-%d") for x in (a, b))
    if da.year != db.year:
        return f"{_long_date(a)} – {_long_date(b)}"
    if da.month != db.month:
        return f"{da.strftime('%B %-d')} – {db.strftime('%B %-d')}, {db.year}"
    return f"{da.strftime('%B %-d')}–{db.day}, {db.year}"


_MAX_REV_GROUPS = 3


def _pages(n):
    return f"{n} page" if n == 1 else f"{n} pages"


def provenance_line(models, stamps):
    """The cover's translation line from step 2's per-page record: which
    pipeline revision translated the pages, and when. One revision -> no
    page counts; several (a partial retranslation) -> one group each, oldest
    first; pages translated before the record existed -> "not recorded",
    never a guessed revision. A stamp is [rev, date] or, filled in afterwards
    from the evidence (step 2's model record + file dates), ["", earliest,
    latest] -- shown as the date range with no revision. Pages that needed
    no model ("-") don't count."""
    from collections import defaultdict
    stamps = (list(stamps or []) + [None] * len(models))[:len(models)]
    groups, count, unrec = defaultdict(list), defaultdict(int), 0
    for m, t in zip(models, stamps):
        if m == "-":
            continue
        if not t or not t[1]:
            unrec += 1
        else:
            groups[t[0] or ""].extend(d for d in t[1:3] if d)
            count[t[0] or ""] += 1
    if not groups:
        return "Translated with FencingManualTranslator (revision and date not recorded)"
    order = sorted(groups, key=lambda r: (max(groups[r]), min(groups[r])))
    if len(order) > _MAX_REV_GROUPS:               # a long history: fold the oldest
        old = order[:len(order) - _MAX_REV_GROUPS + 1]
        folded = [d for r in old for d in groups[r]]
        n_folded = sum(count[r] for r in old)
        order = order[len(old):]
    else:
        folded, n_folded = [], 0
    # no revision: the dates alone
    label = lambda r: f"{r}, {_date_range(groups[r])}" if r else _date_range(groups[r])
    if len(order) == 1 and not folded and not unrec:
        r = order[0]
        return (f"Translated with FencingManualTranslator {r}, "
                f"{_date_range(groups[r])}" if r else
                f"Translated with FencingManualTranslator, {_date_range(groups[r])}")
    parts = []
    if folded:
        parts.append(f"earlier revisions, {_date_range(folded)} ({_pages(n_folded)})")
    parts += [f"{label(r)} ({_pages(count[r])})" for r in order]
    if unrec:
        parts.append(f"not recorded ({_pages(unrec)})")
    return "Translated with FencingManualTranslator: " + "; ".join(parts)


def _hand_corrections(book, meta):
    """The disclosure that a person changed the machine English: how many
    pages ([[fix]] edits and whole-page edits, _overrides.py), and by whom
    when meta.json names an editor ("edited_by"). "" when nobody did."""
    import _overrides
    n = len(_overrides.hand_corrected_pages(book))
    if not n:
        return ""
    who = str(meta.get("edited_by", "")).strip()
    return (f"Corrected by hand on {n} page{'s' if n != 1 else ''}"
            + (f", edited by {who}." if who else "."))


def _translation_provenance(book):
    p = f"/data/processed/{book}_translated/intermediate/translated_from.json"
    try:
        d = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return provenance_line(d.get("models") or [], d.get("stamps") or [])


def _meta(stem):
    books_dir = os.environ.get("BOOKS_DIR", "/data/books")
    for d in (Path(books_dir), Path(books_dir) / "_skip"):     # _skip: kept after its run
        try:
            return json.loads((d / f"{stem}.meta.json").read_text(encoding="utf-8"))
        except Exception:
            continue
    return {}


def _join(sep, *parts):
    return sep.join(p for p in (str(x).strip() for x in parts) if p and p != "None")


def _dates(meta):
    """The byline's date part, for placing the book in time: the year the
    work first appeared, and -- when the scan is a later edition -- which
    edition it is. meta.json: year_first, year_edition, edition, first_as,
    publisher (the scan's edition's publisher); the old single `year` field
    is still read when year_first is missing."""
    first = str(meta.get("year_first") or meta.get("year") or "").strip()
    ed_year = str(meta.get("year_edition", "")).strip()
    label = str(meta.get("edition", "")).strip()
    pub = str(meta.get("publisher", "")).strip()
    first_as = str(meta.get("first_as", "")).strip()
    if ed_year and ed_year != first:
        head = f"First published {first}" if first else ""
        if head and first_as:
            head += f" (as {first_as})"
        return [head, "This edition: " + _join(", ", label, pub, ed_year)]
    if label:
        return [first, "This edition: " + _join(", ", label, pub)]
    return [first, pub]


def render(doc, stem, *, page_w, page_h, margin):
    """Append one cover page to `doc` for book `stem`."""
    meta = _meta(stem)
    page = doc.new_page(width=page_w, height=page_h)
    col_w = min(page_w - 2 * margin, 460)
    x0 = margin
    y = [96.0]

    def block(text, size, *, gap_after=10.0, italic=False, color=(0, 0, 0)):
        if italic:
            text = f"<i>{text}</i>"
        _, ny = _render.draw(page, (x0, y[0], x0 + col_w, page_h - margin),
                             _render.layout(text, col_w, size), size, color=color)
        y[0] = ny + gap_after

    def rule(gap=14.0):
        y[0] += gap * 0.45
        page.draw_line((x0, y[0]), (x0 + col_w, y[0]), width=0.4,
                       color=(0.6, 0.6, 0.6))
        y[0] += gap * 0.55

    # --- bibliographic head ------------------------------------------------
    page.insert_text((x0, y[0]), meta.get("title") or stem,
                     fontsize=17, fontname=_render.BOLD)
    y[0] += 26
    # the title in English, for people to refer to the book by (meta.json
    # title_en, hand-written: the title page itself is often abbreviated)
    if str(meta.get("title_en", "")).strip():
        y[0] -= 8
        block(meta["title_en"].strip(), 13, gap_after=12)
    # the full title as printed on the title page, and in English, when it
    # says more than the short title above (meta.json title_full, _en)
    if str(meta.get("title_full", "")).strip():
        block(meta["title_full"].strip(), 9.5, italic=True, gap_after=6)
        if str(meta.get("title_full_en", "")).strip():
            block(meta["title_full_en"].strip(), 9.5, gap_after=12)
        else:
            y[0] += 9
    byline = _join("  ·  ", meta.get("author"), *_dates(meta))
    if byline:
        block(byline, 10.5, italic=True, gap_after=16)
    else:
        y[0] += 8

    # --- the original ----------------------------------------------------
    block(meta.get("rights") or _DEFAULT_ORIG_RIGHTS, 9.5, gap_after=4)
    src = _join(" ", meta.get("source"), meta.get("source_id"))
    url = str(meta.get("source_url", "")).strip()
    if src or url:
        line = "Digitised from: " + src if src else "Digitised source"
        if url:
            line += "  —  " + url
        block(line, 9.5, gap_after=4)
    if str(meta.get("source_terms", "")).strip():
        block(meta["source_terms"].strip(), 9, italic=True, gap_after=4)
    # pages patched in from another copy (e.g. a page missing from this
    # scan): the scan is then no longer the digitisation named above alone
    if str(meta.get("source_changes", "")).strip():
        block("Changes to this scan: " + meta["source_changes"].strip(),
              9, gap_after=4)

    rule()

    # --- this translation ---------------------------------------------
    block(DISCLOSURE, 10, italic=True, gap_after=12)

    block("OCR model: "
          + (_model_label("VLM_MODEL_PATH") or "olmOCR-2-7B-1025-Q6_K"),
          9, gap_after=3)
    block("Translation model: " + _translation_models(stem),
          9, gap_after=3)

    block(_translation_provenance(stem), 9, gap_after=3)
    hand = _hand_corrections(stem, meta)
    if hand:
        block(hand, 9, gap_after=3)
    ver = _pipeline_version()
    today = datetime.now().strftime("%B %-d, %Y")
    block(f"PDF generated {today} with FencingManualTranslator {ver}" if ver
          else f"PDF generated {today}", 9, gap_after=10)

    block(METHOD, 9, gap_after=8)
    block(CONVENTIONS, 9, gap_after=12)

    rule()

    # --- rights / contributions -------------------------------------
    block(RIGHTS, 9, gap_after=3)
    block(GITHUB_URL, 9, gap_after=0)

    # --- footer id ------------------------------------------------------
    page.insert_text((x0, page_h - margin + 6), "pipeline id: " + stem,
                     fontsize=7.5, fontname=_render.ROMAN, color=(0.5, 0.5, 0.5))
