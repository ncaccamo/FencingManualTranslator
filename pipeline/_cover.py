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
  * pipeline revision -- PIPELINE_VERSION env if set (run_full_pipeline.sh
    exports it), else a git short SHA if a repo is reachable, else omitted.
"""
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

import _render

GITHUB_URL = "github.com/ncaccamo/BookTranslator"

DISCLOSURE = (
    "A machine translation, created on local hardware and shared for open "
    "study. It is not a replacement for a scholarly human translation."
)
METHOD = (
    "Page images were transcribed, then translated page by page under a "
    "fixed prompt; figures and layout are carried from the scan."
)
CONVENTIONS = (
    "Italic words are technical terms kept in the original language. Where "
    "the English rendering is a judgement call, the original term is given "
    "in a footnote."
)
RIGHTS = "This translation is released under CC0 1.0 (public domain dedication)."

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
    if not models:
        return _UNRECORDED_MODEL
    label = lambda m: (_UNRECORDED_MODEL if m == "" else
                       default if m == "bt-translate" or m == os.environ.get("MODEL_NAME")
                       else re.sub(r"^(?:%s)" % "|".join(_VENDOR_PREFIXES), "", m))
    counts = Counter(label(m) for m in models).most_common()
    if len(counts) == 1:
        return counts[0][0]
    return ", ".join(f"{m} ({n} pages)" for m, n in counts)


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
    v = os.environ.get("PIPELINE_VERSION", "").strip()
    if v:
        return v
    try:
        out = subprocess.run(
            ["git", "-C", "/app", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return ""


def _meta(stem):
    books_dir = os.environ.get("BOOKS_DIR", "/data/books")
    try:
        return json.loads(
            (Path(books_dir) / f"{stem}.meta.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _join(sep, *parts):
    return sep.join(p for p in (str(x).strip() for x in parts) if p and p != "None")


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
    byline = _join("  ·  ", meta.get("author"), meta.get("year"),
                   meta.get("publisher"))
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

    rule()

    # --- this translation ---------------------------------------------
    block(DISCLOSURE, 10, italic=True, gap_after=12)

    block("OCR model: "
          + (_model_label("VLM_MODEL_PATH") or "olmOCR-2-7B-1025-Q6_K"),
          9, gap_after=3)
    block("Translation model: " + _translation_models(stem),
          9, gap_after=3)

    ver = _pipeline_version()
    today = datetime.now().strftime("%B %d, %Y")
    block(f"Pipeline {ver}  ·  generated {today}" if ver
          else f"Generated {today}", 9, gap_after=10)

    block(METHOD, 9, gap_after=8)
    block(CONVENTIONS, 9, gap_after=12)

    rule()

    # --- rights / contributions -------------------------------------
    block(RIGHTS, 9, gap_after=3)
    block(GITHUB_URL + "  —  corrections and issues welcome", 9, gap_after=0)

    # --- footer id ------------------------------------------------------
    page.insert_text((x0, page_h - margin + 6), "pipeline id: " + stem,
                     fontsize=7.5, fontname=_render.ROMAN, color=(0.5, 0.5, 0.5))
