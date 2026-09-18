#!/usr/bin/env python3
"""VLM OCR using olmOCR-2-7B-1025 (Allen AI, fine-tuned from Qwen2.5-VL-7B
specifically for document/OCR extraction -- not the general-purpose
Qwen2-VL-7B-Instruct chat model this pipeline started with).

Changes from the Qwen2-VL version:
  - Model/mmproj paths point at olmOCR-2 by default, still env-overridable
    (VLM_MODEL_PATH, VLM_MMPROJ_PATH).
  - Image resize target is now a fixed 1288px longest dimension -- this is
    a documented requirement of olmOCR-2 (not a quality/speed tunable like
    Qwen2-VL's was), so the old two-tier "normal vs aggressive compression"
    pre-screening logic is gone; every page is prepared the same way.
  - Prompt (vlm_ocr_prompt.txt) now explicitly instructs the model to
    ignore facing-page/binding/margin clutter rather than relying on a
    physical .crop sidecar -- this is the actual advantage a VLM has over
    pixel-segmentation OCR (see chat log: it correctly read a line
    Tesseract garbled next to handwritten marginalia). A .crop file still
    works if you have one and want to use it too; it's just no longer
    required for this path.
  - Retained: server load/unload, retry-with-restart-on-failure. This is
    still a 7B VLM and can still fail/hang on a bad page.

Output contract (added after the hard-page eval, see chat log):
  - raw_ocr.txt: the untouched model responses, written before any parsing
    -- the ground-truth fallback, matching the Tesseract path.
  - extracted.txt: a CANONICAL PAGE_NUMBER/HEADER/BODY_TEXT scaffold rebuilt
    from the parsed fields (downstream -- 2_translate.py, the PDF
    generators -- still parses this exact scaffold; it's just now
    well-formed and the page number is trustworthy).
  - page_numbers.txt: one reconciled page number per line, '[n]' where
    inferred. olmOCR-2 reads a real printed folio fine but will invent one
    from an engraving plate number or a library stamp, or convert 'VIII' to
    '8'; reconcile_page_numbers() keeps a reported number only when it
    agrees with the book's dominant page-index -> folio offset.
"""

import glob
import os
import re
import json
from collections import Counter
import fitz
import base64
import requests
import subprocess
import time
import shutil
from pathlib import Path
from PIL import Image, ImageOps
import io
import sys

import backend_config as bc
import _pageio

VLM_MODEL_PATH = os.environ.get("VLM_MODEL_PATH", "/models/allenai_olmOCR-2-7B-1025-Q6_K.gguf")
VLM_MMPROJ_PATH = os.environ.get("VLM_MMPROJ_PATH", "")
# Request "model" label -- ignored by a single-model llama-server, the
# routing key under llama-swap. Env-overridable, else the model file name.
VLM_MODEL_NAME = os.environ.get("VLM_MODEL_NAME") or Path(VLM_MODEL_PATH).stem


def _find_matching_mmproj(model_path_str: str) -> str:
    """
    Pick the mmproj file that actually belongs to VLM_MODEL_PATH, when
    multiple mmproj-*.gguf files for DIFFERENT models sit in the same
    directory (e.g. one per vision model you've tried). Confirmed the hard
    way that naive alphabetical-first selection is wrong here: filenames
    starting with an uppercase letter sort before ones starting with a
    lowercase letter in plain ASCII sort, which has nothing to do with
    which file matches the loaded model.

    Matches by counting shared '-'-separated tokens between the model's
    filename and each candidate mmproj's filename (after stripping the
    'mmproj-' prefix and '-f16'/'-bf16' suffix). If nothing scores above
    zero, refuses to guess and raises instead of silently loading a
    mismatched projector.
    """
    model_path = Path(model_path_str)
    model_dir = model_path.parent
    candidates = sorted(model_dir.glob("mmproj*.gguf"))
    if not candidates:
        raise FileNotFoundError(f"No mmproj*.gguf files found in {model_dir}")

    model_tokens = set(re.split(r"[-_.]", model_path.stem.lower()))
    scored = []
    for c in candidates:
        core = re.sub(r"-(f16|bf16|fp16)$", "", c.stem.lower())
        core = re.sub(r"^mmproj-", "", core)
        core_tokens = set(re.split(r"[-_.]", core))
        overlap = len(model_tokens & core_tokens)
        scored.append((overlap, c))
    scored.sort(key=lambda x: (-x[0], "bf16" in x[1].stem.lower(), str(x[1])))
    best_overlap, best = scored[0]

    if best_overlap == 0:
        names = ", ".join(c.name for c in candidates)
        raise FileNotFoundError(
            f"Found mmproj files ({names}) but none share a recognizable "
            f"name with the model file ({model_path.name}). Set "
            f"VLM_MMPROJ_PATH explicitly rather than risk loading the "
            f"wrong projector."
        )
    return str(best)


# Only needed to launch a local llama-server. In BACKEND_MODE=remote the
# GPU box owns the model + projector, /models here is empty (see
# scripts/setup.sh --remote), and this resolution would exit(1) at import.
if not VLM_MMPROJ_PATH and not bc.is_remote():
    try:
        VLM_MMPROJ_PATH = _find_matching_mmproj(VLM_MODEL_PATH)
    except FileNotFoundError as e:
        print(f"\n✗ ERROR: {e}\n")
        exit(1)
VLM_PROMPT_FILE = "/app/vlm_ocr_prompt.txt"
VLM_PREPROCESS = os.environ.get("VLM_PREPROCESS", "1") != "0"
# Documented olmOCR-2 requirement, not a tunable -- see model card.
VLM_MAX_DIMENSION = int(os.environ.get("VLM_MAX_DIMENSION", "1288"))

if not os.path.exists(VLM_PROMPT_FILE):
    print(f"\n✗ ERROR: Missing VLM prompt file: {VLM_PROMPT_FILE}\n")
    exit(1)

with open(VLM_PROMPT_FILE, "r", encoding="utf-8") as f:
    VLM_PROMPT_TEMPLATE = f.read().strip()


def _llama_server_env() -> dict:
    """A `docker exec` of this script inherits only the image-level
    LD_LIBRARY_PATH; llama-server links its backend impl .so
    (libllama-server-impl.so) at runtime from /usr/local/lib/llama-<suffix>/,
    a dir the entrypoint adds for the servers IT launches but that we don't
    get here -- so a server we spawn dies immediately with
    "cannot open shared object file". Restore it for the configured backend
    (validated; rocm714 errors clearly if it isn't built)."""
    suffix = bc.local_backend_suffix()
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = f"/usr/local/lib/llama-{suffix}:" + env.get("LD_LIBRARY_PATH", "")
    return env


def collapse_runs(text: str) -> str:
    """Defuse olmOCR-2 repetition loops: a run of the same short token
    (dot-leaders '. . . .', ornament '* * *', dashes) repeated many times
    gets clipped to a few. Leaves ordinary prose untouched. Applied to the
    raw model response before parsing (and by reprocess_extracted.py)."""
    if not text:
        return text
    # "x x x x ..." where x is 1-2 non-word chars, 8+ reps -> keep 4
    text = re.sub(r'((?:[^\s\w]{1,2}\s+){4})(?:[^\s\w]{1,2}\s+){4,}', r'\1', text)
    # a single char repeated 12+ times (e.g. "..............") -> keep 6
    text = re.sub(r'(.)\1{11,}', lambda m: m.group(1) * 6, text)
    # a whole line repeated 4+ times in a row -> keep 2
    text = re.sub(r'(?m)^(.+)\n(?:\1\n){3,}', r'\1\n\1\n', text)
    return text


def parse_vlm_response(raw: str) -> dict:
    """Split olmOCR-2's structured response into fields. The prompt asks for:
        PAGE_NUMBER: <n or none>
        HEADER: <text or none>
        BODY_TEXT:
        <body...>
    A missing or renamed field is tolerated; if there's no BODY_TEXT marker
    at all the whole response (minus any stray label lines) is kept as body
    so a malformed/truncated response never silently loses text.
    Returns {"page_raw": str, "header": str, "body": str} ("" == not present).
    """
    pn = re.search(r'^[ \t]*PAGE_NUMBER[ \t]*:[ \t]*(.+?)[ \t]*$', raw, re.I | re.M)
    hd = re.search(r'^[ \t]*HEADER[ \t]*:[ \t]*(.*?)[ \t]*'
                   r'(?=\n[ \t]*(?:BODY_TEXT|FIGURES|PAGE_NUMBER)[ \t]*:|\Z)',
                   raw, re.I | re.S | re.M)
    bd = re.search(r'^[ \t]*BODY_TEXT[ \t]*:[ \t]*\n?(.*)\Z', raw, re.I | re.S | re.M)

    def _clean(v: str) -> str:
        v = (v or "").strip().strip("[]").strip()
        if v.lower() in ("", "none", "n/a", "-", "not visible", "no page number"):
            return ""
        return v

    def _clean_header(v: str) -> str:
        """"" (none) / "text" / "left\\tright" for a two-part running head
        (topic left, sub-topic right -- olmOCR-2 emits these on a newline
        or across a wide gap). Steps 3/4 render the halves left-/right-
        aligned; translate_headers() translates each half."""
        v = (v or "").strip().strip("[]").strip()
        if not v or v.split("\n", 1)[0].strip().lower() in (
                "none", "n/a", "-", "not visible"):
            return ""
        parts = [p.strip() for p in re.split(r"\n+|[ \t]{3,}", v, maxsplit=1) if p.strip()]
        if (len(parts) == 2 and "\n" not in parts[1]
                and all(2 <= len(p) <= 48 for p in parts)):
            return parts[0] + "\t" + parts[1]
        return re.sub(r"\s+", " ", parts[0]) if parts else ""

    page_raw = _clean(pn.group(1) if pn else "")
    header = _clean_header(hd.group(1) if hd else "")

    # olmOCR-2 sometimes skips the PAGE_NUMBER field entirely and just opens
    # the response with the bare folio on its own line ("\n29\n\nPARADES ...").
    # Only salvage it when the field is truly absent -- an explicit
    # "PAGE_NUMBER: none" is a real "no folio here" signal, not this.
    if pn is None and not page_raw:
        lead = re.match(r'\s*([IVXLCDM\d]{1,4}\.?)\s*\n', raw, re.I)
        if lead:
            page_raw = _clean(lead.group(1))
    if bd:
        # BODY_TEXT runs to end-of-response OR to the FIGURES: marker
        # (alone on its line, or "FIGURES: none" / "FIGURES: plate | ..."
        # inline), whichever comes first.
        body = re.split(r'(?im)^[ \t]*FIGURES[ \t]*:', bd.group(1), 1)[0].strip()
    else:
        body = re.sub(r'(?ims)^[ \t]*FIGURES[ \t]*:.*\Z', '', raw)
        body = re.sub(r'^[ \t]*(PAGE_NUMBER|HEADER)[ \t]*:.*$', '', body,
                      flags=re.I | re.M).strip()
    return {"page_raw": page_raw, "header": header, "body": body,
            "figures": parse_figures_block(raw)}


def _norm_head(s: str) -> str:
    import unicodedata
    s = "".join(c for c in unicodedata.normalize("NFD", s)
                if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s.lower()).strip(" .-")


def strip_leaked_headmatter(body: str, known_headers: set) -> str:
    """Peel bookbinding apparatus off BODY_TEXT.

    TOP: the VLM sometimes drops the running header and/or the printed
    folio into the body, so the page opens with a stray "25" / "THE GUARD".

    BOTTOM: printer's signature marks -- a lone small number, or the title
    catchword (a short line matching one of the book's header words, e.g.
    "ESCRIME.") -- sit at the foot of the first page of each gathering.

    A line is apparatus when it is a bare roman/arabic number (<=4 digits,
    optional trailing '.', optional leading superscript letter) or its
    normalised form matches a header seen elsewhere in this book."""
    def _is_apparatus(s):
        return bool(re.fullmatch(r"[a-z]?[\dIVXLCDM]{1,4}\.?", s, re.I)) \
            or _norm_head(s) in known_headers

    lines = body.split("\n")
    while lines and (not lines[0].strip() or _is_apparatus(lines[0].strip())):
        lines.pop(0)
    # Tail: a signature is at most catchword + number, so strip at most 2
    # apparatus lines, and never strip the last line if it is the only
    # content left (a one-line page could be a legitimate short entry).
    for _ in range(2):
        while lines and not lines[-1].strip():
            lines.pop()
        if len(lines) > 1 and _is_apparatus(lines[-1].strip()):
            lines.pop()
        else:
            break
    return "\n".join(lines).strip()


def parse_figures_block(raw: str) -> list:
    """Parse the FIGURES: section of a VLM response into a list of
        {"kind": "plate"|"inset", "caption": str}
    'plate' = whole-page illustration, 'inset' = illustration sharing the
    page with text. Geometry is left to 3_english_only_pdf.py (the density
    profiler) -- the VLM only reliably gives presence + caption. Tolerant
    of a missing block, "none", and stray bullets/quotes."""
    figs = []

    # olmOCR-2 sometimes renders a figure it noticed as a Markdown image
    # ref in the BODY instead of a FIGURES: entry -- "![Schéma A.](http...)"
    # with a hallucinated URL, often followed by the caption on its own
    # line. Treat each as an inset; caption = the alt text, else the short
    # line right after it.
    for im in re.finditer(r'!\[([^\]]*)\]\([^)]*\)[ \t]*(?:\n[ \t]*([^\n]*))?',
                          raw):
        cap = im.group(1).strip()
        nxt = (im.group(2) or "").strip()
        if not cap and nxt and (len(nxt.split()) <= 6 or
                                re.match(r'(?i)(sch[ée]ma|fig|pl|planche)\b', nxt)):
            cap = nxt
        figs.append({"kind": "inset", "caption": re.sub(r'^\W+|\W+$', '', cap)})

    m = re.search(r'(?im)^[ \t]*FIGURES[ \t]*:[ \t]*\n?(.*)\Z', raw, re.S)
    if not m:
        return figs
    block = m.group(1)
    # olmOCR-2 sometimes emits the fields out of order and BODY_TEXT lands
    # AFTER the FIGURES block -- cut it off so a leaked caption/paragraph
    # isn't parsed as figure rows.
    block = re.split(r'(?im)^[ \t]*(?:BODY_TEXT|PAGE_NUMBER|HEADER)[ \t]*:',
                     block, 1)[0]
    for line in block.splitlines():
        line = line.strip().lstrip("-*•[] ").strip().strip('"').strip()
        low = line.lower().rstrip(".")
        if not low or low in ("none", "figures: none", "no figures",
                              "no caption", "* * *"):
            continue
        if line.startswith("!["):          # leaked Markdown image ref
            continue
        parts = [p.strip() for p in line.split("|")]
        kind = parts[0].lower().rstrip(":")
        if kind.startswith("plate") or "full page" in kind or "whole page" in kind:
            kind = "plate"
        elif kind.startswith("inset") or kind.startswith("inline"):
            kind = "inset"
        elif figs and _norm_head(line) == _norm_head(figs[-1]["caption"]):
            # The model repeated this same caption a second time with no
            # BODY_TEXT: label to route it there (usually because the page
            # is just a plate + its title, with nothing else to report) --
            # this repeat is typically a more faithful transcription of the
            # actual printed case than its own FIGURES: line. Prefer it.
            if line != figs[-1]["caption"]:
                figs[-1]["caption"] = line
            continue
        elif figs and re.match(r"[0-9a-zà-ÿ(]", line):
            # A follow-on line starting with a digit, lowercase, or an
            # open paren -- a caption's second line ("2e TEMPS : le
            # dérobement ...", "(La main de l'attaquant reste haute.)"),
            # not a fresh title ("FIG. 5. —", "PRISE DE FER ..."). Append it.
            if line.lower() not in figs[-1]["caption"].lower():
                figs[-1]["caption"] = (figs[-1]["caption"] + " " + line).strip()
            continue
        else:
            continue
        cap = "|".join(parts[1:]).strip() if len(parts) > 1 else ""
        if cap.lower() in ("no caption", "none", "n/a", "") or re.fullmatch(r"<.*>", cap):
            cap = ""   # empty, or the model echoed the "<caption>" placeholder
        figs.append({"kind": kind, "caption": cap})
    return figs


def _roman_to_int(s: str):
    s = s.strip().upper()
    if not s or not re.fullmatch(r'[IVXLCDM]+', s):
        return None
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = prev = 0
    for ch in reversed(s):
        v = vals[ch]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total or None


def _page_to_int(s: str):
    """Best-effort numeric value of a reported page-number string, accepting
    plain digits ('83'), digits embedded in noise, or a roman numeral
    ('VIII')."""
    if not s:
        return None
    m = re.search(r'\d+', s)
    if m:
        return int(m.group())
    return _roman_to_int(s)


def _fold_caption_only_body(parsed: list) -> None:
    """A page that is just a plate and its caption: olmOCR-2 routinely puts
    the caption in BODY_TEXT ("FIG. 6. — PRISE DE FER ... / 2ᵉ TEMPS : ...")
    -- sometimes AND in a FIGURES row, sometimes instead of one, sometimes
    with NO figure row at all. Fold that body into the figure caption so it
    is translated once (as a caption), and synthesise a plate when the VLM
    named a figure in the body but flagged nothing."""
    fig_lead = re.compile(r'(?i)^\s*(fig\.?|pl\.?|planche|sch[ée]ma)\b')
    for p in parsed:
        figs = p.get("figures") or []
        body = (p.get("body") or "").strip()
        if not body:
            continue
        words = body.split()
        cap = (figs[0].get("caption") or "").strip() if figs else ""
        caption_shaped = bool(
            re.match(r'(?i)\s*(fig\.?|pl\.?|planche|sch[ée]ma|\d+[eè]?r?\b)', body))
        # no figure row, but the whole body is a "FIG. N — ..." caption:
        # the VLM saw a plate and only transcribed its caption
        if not figs and fig_lead.match(body) and len(words) <= 40:
            p["figures"] = [{"kind": "plate",
                             "caption": re.sub(r'\s*\n\s*', ' — ', body).strip()}]
            p["body"] = ""
            continue
        if not figs:
            continue
        if not ((caption_shaped and len(words) <= 40)
                or (cap and _norm_head(cap)[:20] in _norm_head(body) and len(words) <= 25)
                or len(words) <= 10):
            continue
        # the body is the fuller text -- prefer it; only prepend the figure
        # row's caption when it adds something the body doesn't already carry
        nb = _norm_head(body)
        merged = body if (not cap or _norm_head(cap)[:25] in nb
                          or len(body) >= len(cap)) else f"{cap} {body}"
        figs[0]["caption"] = re.sub(r'\s*\n\s*', ' — ', merged).strip()
        del figs[1:]                      # one image, one (now complete) caption
        p["body"] = ""


# ---------------------------------------------------------------------------
# Structural salvages run after parse_vlm_response() and before header
# normalisation / folio reconciliation. Shared by the main step-1 flow and
# reprocess_extracted.py via postprocess_parsed_pages().
# ---------------------------------------------------------------------------

_LEAKED_HEAD_RE = re.compile(
    r"^\s*(?:"
    r"\(?(?P<num1>\d{1,4})\)?[.\s]+(?P<head1>[A-ZÀ-Þ][A-ZÀ-Þ0-9 '’.,\-]{3,}?)"
    r"|(?P<head2>[A-ZÀ-Þ][A-ZÀ-Þ0-9 '’.,\-]{3,}?)[.\s]+\(?(?P<num2>\d{1,4})\)?"
    r")\s*\.?\s*$")


def _looks_like_running_head(s: str) -> bool:
    s = s.strip().rstrip(".")
    letters = [c for c in s if c.isalpha()]
    if len(letters) < 4 or len(s) > 60:
        return False
    return (sum(c.isupper() for c in letters) >= 0.85 * len(letters)
            and bool(re.fullmatch(r"[A-ZÀ-Þ0-9 '’.,\-]+", s)))


def salvage_leaked_folio_header(parsed: list, known_headers: set) -> None:
    """olmOCR-2 sometimes dumps the printed folio + running head into
    BODY_TEXT as line 1 ("246 DES ATTAQUES SUR PRÉPARATIONS.") instead of
    the PAGE_NUMBER / HEADER fields -- Gomard does this on ~50 pages, which
    also empties PAGE_NUMBER so every folio ends up interpolated. Recover
    it: line 1 = <num> <ALL-CAPS HEAD> (either order) -> move the number to
    page_raw, the head to header, strip the line from body. Only fires when
    the caps run matches a header seen elsewhere in the book or otherwise
    looks like a running head."""
    for p in parsed:
        body = (p.get("body") or "").lstrip("\n")
        nl = body.find("\n")
        line1 = (body[:nl] if nl >= 0 else body).strip()
        rest = body[nl + 1:] if nl >= 0 else ""
        m = _LEAKED_HEAD_RE.match(line1)
        if not m:
            continue
        num = m.group("num1") or m.group("num2")
        head = (m.group("head1") or m.group("head2") or "").strip().rstrip(".")
        if not head or not (_norm_head(head) in known_headers
                            or _looks_like_running_head(head)):
            continue
        if not p.get("page_raw"):
            p["page_raw"] = num
        if not p.get("header"):
            p["header"] = head
        p["body"] = rest.lstrip("\n")


def dehyphenate_across_pages(parsed: list) -> None:
    """A word hyphenated across a page break in the original ("...déjà pu-"
    | "bliés, on ...") is otherwise translated as two fragments. When page
    i ends lowercase+hyphen and page i+1 starts lowercase, splice the
    completion fragment (plus any punctuation glued to it) up to close the
    word on page i. Capitalised continuations -- real hyphenated names like
    Sainct-Didier / La Boëssière -- are left alone."""
    for i in range(len(parsed) - 1):
        a = (parsed[i].get("body") or "").rstrip()
        if not re.search(r"[a-zà-ÿ]-\Z", a):
            continue
        b = parsed[i + 1].get("body") or ""
        m = re.match(r"([a-zà-ÿ]+[^\s\w]*)([\s\S]*)", b.lstrip())
        if not m:
            continue
        parsed[i]["body"] = a[:-1] + m.group(1)
        parsed[i + 1]["body"] = m.group(2).lstrip()


_GBOOKS_MARKERS = (
    "this is a digital copy of a book",
    "ceci est une copie numérique",
    "about google book search",
    "à propos de google recherche de livres",
    "usage guidelines",
    "consignes d’utilisation", "consignes d'utilisation",
    "à propos de ce livre",
    # Gallica / BnF equivalents
    "site gallica", "gallica.bnf.fr", "collections de la bnf",
    "les contenus accessibles sur le site gallica",
    "conditions de la réutilisation", "conditions d’utilisation de gallica",
)


def drop_google_boilerplate(parsed: list) -> None:
    """Google Books / Gallica prepend 2-3 pages of scanning boilerplate.
    Blank them so they are not translated into the output (the scan still
    shows on the side-by-side left page)."""
    for p in parsed[:8]:
        blob = ((p.get("header") or "") + " " + (p.get("body") or "")).lower()
        if any(mk in blob for mk in _GBOOKS_MARKERS):
            p["body"] = ""
            p["header"] = ""
            p["figures"] = []


_INSCRIPTION_RE = re.compile(
    r"hommage\s+de\s+l['’]?\s*(?:auteur|[ée]diteur)"
    r"|en\s+t[ée]moignage\s+(?:de|d['’])"
    r"|souvenir\s+(?:affectueux|amical|de\b)"
    r"|à\s+(?:mon(?:sieur)?|madame|mme|m\.)\s+.{0,30}\b"
    r"(?:le\s+)?(?:comte|comtesse|baron|marquis|général|colonel|docteur)\b",
    re.I)


def mark_handwritten_inscriptions(parsed: list) -> None:
    """A handwritten presentation dedication on a flyleaf ("Hommage de
    l'auteur à Monsieur le Comte ...") is OCR'd unreliably -- wrong month,
    signature mistranslated as a common noun. On an early short page,
    replace it with a marker; the scan carries the real thing."""
    for p in parsed[:15]:
        body = (p.get("body") or "").strip()
        if body and len(body.split()) <= 30 and _INSCRIPTION_RE.search(body):
            p["body"] = "[handwritten inscription — see original]"
            p["figures"] = []


_PLATE_CAPTION_RE = re.compile(
    r"(?im)^[ \t]*("
    r"planche\s+[ivxlcdm]+\.?.*"
    r"|fig(?:ure|\.)?\s+[ivxlcdm\d]+\b.*"
    r"|(?:première|deuxième|troisième|quatrième|cinquième|sixième|septième|huitième|"
    r"neuvième|dixième|\d+[eè]?)\s+position\b.*"
    r"|coup\s+(?:tiré|touché|de\s+temps)\b.*"
    r"|parade\s+de\s+\w+.*"
    r")[ \t]*$")

# A line shaped like a plate/figure caption title -- used to decide whether
# a HEADER that olmOCR-2 emitted on a figure page is really the caption
# (fold_caption_header_into_figure). Broader than _PLATE_CAPTION_RE (which
# gates plate *synthesis*); only applied on pages that already carry a
# figure, so the wider net is safe.
_CAPTION_SHAPE_RE = re.compile(
    r"(?i)^[ \t]*(?:"
    r"planche\s+[ivxlcdm\d]+\b"
    r"|pl\.\s*[ivxlcdm\d]+\b"
    r"|fig(?:ure|\.)?\s*[ivxlcdm\d]+\b"
    r"|sch[ée]ma\s+\w+\b"
    r"|(?:première|deuxième|troisième|quatrième|cinquième|sixième|septième|"
    r"huitième|neuvième|dixième|\d+[eè]?(?:me)?)\s+(?:position|mouvement)\b"
    r"|coup\s+(?:tir[ée]|touch[ée]|de\s+temps|port[ée]|d['’a-zà-ÿ]+)\b"
    r"|parade\s+(?:de\s+|d[eu]\s+)?[\wà-ÿ]+"
    r"|salut\b|en\s+garde\b|garde\b|développement\b|position\b"
    r"|.*\bposition,?\s+ou\b"
    r").*$")

_SCAN_APPARATUS_RE = re.compile(
    r"insufficient\s+contrast|contraste\s+insuffisant|mire\s+de\s+|test\s+chart"
    r"|résolution\s+insuffisante|texte\s+détérior|reliure\s+défectueuse"
    r"|pages?\s+manquantes?|auteur\s+du\s+texte"
    r"|biblioth[eè]que\s+[\wàâäéèêëïîôùûç.'’-]+\s+\d{3,}"
    r"|\bex[- ]?libris\b|n[°o]\s*d['’]?(?:inventaire|entrée)\b"
    r"|/\s*par\s+[A-ZÀ-Þ]",                      # BnF catalog "Title / par Author"
    re.I)
# a bare library shelfmark on its own line ("NF Z 43-120-14", "RES 8-V-1234")
_SHELFMARK_RE = re.compile(
    r"^(?=.*\d)(?=.*[A-Z])[A-Z0-9]{1,4}(?:[ .\-]+[A-Z0-9]{1,6}){2,5}$")


def drop_scan_apparatus(parsed: list) -> None:
    """Scanner target cards and library accession plates ("Contraste
    insuffisant", "Bibliothèque Larousse 15720", a bare shelfmark) get
    filed as a HEADER or a one-line body. Blank them so they are not
    translated or drawn as a running head. Gated on a near-empty page so a
    real chapter that just mentions a library is untouched."""
    for p in parsed:
        head = (p.get("header") or "").strip()
        body = (p.get("body") or "").strip()
        body1 = body.split("\n", 1)[0].strip()
        if _SCAN_APPARATUS_RE.search(head) or _SHELFMARK_RE.match(head):
            p["header"] = head = ""            # a marker in HEADER: never a running head
        is_junk = (_SCAN_APPARATUS_RE.search(f"{head}\n{body}")
                   or (body1 and _SHELFMARK_RE.match(body1) and len(body.split()) <= 4))
        if not is_junk:
            continue
        if len(body.split()) <= 15:
            p["header"] = ""
        if (_SCAN_APPARATUS_RE.search(body) or len(body.split()) <= 8
                or (body1 and _SHELFMARK_RE.match(body1))):
            p["body"] = ""
            p["figures"] = []


def fold_caption_header_into_figure(parsed: list) -> None:
    """olmOCR-2 files a plate's caption line in HEADER: (it is the most
    heading-shaped text on a plate page). Steps 3/4 then draw it as the
    running head while the figure caption renders separately -- the caption
    ends up split, and it is a section header on some plates but not
    others. On a page that carries a figure and little/no body, move a
    caption-shaped HEADER into the figure caption and clear HEADER, so the
    plate page has no running head and the caption renders as one unit."""
    for p in parsed:
        head = (p.get("header") or "").replace("\t", " — ").strip().rstrip(".")
        figs = p.get("figures") or []
        if not head or not figs:
            continue
        if len((p.get("body") or "").split()) > 25:
            continue
        if not _CAPTION_SHAPE_RE.match(head):
            continue
        f0 = figs[0]
        cap = (f0.get("caption") or "").strip().rstrip(".")
        nh, nc = _norm_head(head), _norm_head(cap)
        if not cap or nc in nh:
            f0["caption"] = head              # header is the fuller line
        elif nh not in nc:
            f0["caption"] = f"{head} — {cap}"  # genuinely different -> join
        p["header"] = ""


def _confirmed_header_keys(parsed: list) -> set:
    """Running-head roots seen on >= 2 pages, keyed by a two-part head's
    LEFT half only (see reconcile_headers()). Used only to LIFT a header
    from a matching body line when HEADER is empty -- that inference stays
    conservative (only a header seen elsewhere in the book is trusted
    enough to read off body-text pattern matching)."""
    from collections import Counter

    def _key(h):
        return _norm_head(h.split("\t", 1)[0] if "\t" in h else h)

    counts = Counter(_key(p["header"]) for p in parsed if (p.get("header") or "").strip())
    return {h for h, c in counts.items() if h and c >= 2}


def known_headmatter(parsed: list) -> set:
    """Normalized header VALUES safe to pass to strip_leaked_headmatter()
    as "known headmatter" -- only those whose root is confirmed (see
    _confirmed_header_keys()). A one-off header must keep its body
    duplicate intact until reconcile_headers() has run: stripping it here
    erases the exact evidence that function's section-opener promotion
    needs to see (P-review, Gomard "CONSULTÉS PAR L'AUTEUR." on src 21 --
    its only OTHER appearance, as a real HEADER: value on src 19, is a
    one-off too, so without this restriction the body duplicate gets
    stripped as "known headmatter" before it can be promoted, and a
    same-root sandwich-interpolation from unrelated neighbours wins
    instead)."""
    def _key(h):
        return _norm_head(h.split("\t", 1)[0] if "\t" in h else h)

    confirmed = _confirmed_header_keys(parsed)
    return {_norm_head(p["header"]) for p in parsed
            if p.get("header") and _key(p["header"]) in confirmed}


def reconcile_headers(parsed: list) -> None:
    """The header bar is for a genuine running header -- one repeated at
    the top of the page across a section, OR a one-page subsection's own
    title standing as its running head. Both are real: visual inspection
    of the scan (K-review-2, ``PARTICULARITÉS`` -- src 13) confirmed a
    single-occurrence header sits in the *exact* folio + title + rule
    position of every other running head; it's just that this particular
    section is one page long, so it can never repeat. A repeat-count
    threshold can't tell that apart from noise, and review feedback wants
    fidelity over an engineered "only show headers that repeat" rule.
    olmOCR-2's one real known failure mode here -- filing a plate's
    caption line in HEADER: -- is caught earlier and separately, by
    fold_caption_header_into_figure() (run just before this). So: trust
    whatever HEADER: this page reported, full stop.

      * HEADER empty but the body opens with a line that is exactly a
        CONFIRMED (repeated >= 2x) running head -> lift that line into
        HEADER. This direction stays conservative -- inferring a header
        from body-text pattern matching is only trusted when that header
        text is independently confirmed elsewhere in the book.
      * HEADER empty, body opens with a standalone, short, mostly
        uppercase line that ISN'T confirmed anywhere -> lift it anyway
        (P-review-3). Some section-openers (Kirchhoffer `TERMES
        TECHNIQUES`, `PARADES ET RIPOSTES`, `ROMPRE — DIVERS`; Gomard
        `CONSULTÉS PAR L'AUTEUR.`) are read by the model as `HEADER: none`
        with the title duplicated only in the body, EVERY time -- so they
        can never become "confirmed" no matter how many pages of that
        section exist. Visually these sit in the exact running-head
        position (confirmed against the scan for two of them already).
        `_looks_like_section_title()` is deliberately narrow (short,
        standalone, uppercase-heavy) so it doesn't also promote an
        ordinary glossary headword ("Croisé. — Parade qui consiste…",
        mixed case, dash-definition on the same or next line).
      * HEADER present but has no tab and isn't itself confirmed -> check
        whether it's an order-swapped two-part header (Kirchhoffer
        `LA MISE EN GARDE FLEURET` instead of `FLEURET\tLA MISE EN
        GARDE`): if its word sequence starts or ends with an
        already-confirmed root, split there and rebuild the canonical
        `root⇥remainder` form the rest of that run already uses.
      * A single page with HEADER empty and NOTHING in its body to lift
        either, sandwiched between two pages that both already carry the
        identical CONFIRMED header -> inherit it (mirrors
        reconcile_page_numbers()'s folio interpolation). Deliberately
        conservative: only a single-page gap, both neighbours must
        already agree. A gap next to a page whose own header was itself
        just inferred from a one-off section-opener title (previous
        bullet) won't match this and stays open -- that needs multi-hop
        lookback this doesn't attempt.

    Repetition (for the lift/interpolate above) is counted per *running-
    head root*: a two-part head ``LEFT\tRIGHT`` (section name + per-page
    topic, e.g. ``SABRE\tCORPS A CORPS``) keys on LEFT only -- the right
    half is unique to one page and must not fragment the count, or every
    plate page in a section loses its header (K-batch2 regression)."""
    def _key(h):
        return _norm_head(h.split("\t", 1)[0] if "\t" in h else h)

    confirmed = _confirmed_header_keys(parsed)

    # Canonical display spelling for each confirmed root, taken from
    # wherever it's already correctly cased elsewhere in the book -- so a
    # reordered header matches the casing the rest of its section uses,
    # not whatever casing this one stray instance happened to have.
    root_display = {}
    for p in parsed:
        h = (p.get("header") or "").strip()
        if not h:
            continue
        k = _key(h)
        if k in confirmed and k not in root_display:
            root_display[k] = (h.split("\t", 1)[0] if "\t" in h else h).strip()

    def _reorder_swapped_two_part(head):
        words = head.split()
        norm_words = _norm_head(head).split()
        if len(words) != len(norm_words) or len(words) < 2:
            return None
        for root_key, display in root_display.items():
            root_words = root_key.split()
            n = len(root_words)
            if not (0 < n < len(words)):
                continue
            if norm_words[:n] == root_words:
                return f"{display}\t{' '.join(words[n:])}"
            if norm_words[-n:] == root_words:
                return f"{display}\t{' '.join(words[:-n])}"
        return None

    def _looks_like_section_title(line: str) -> bool:
        s = line.strip()
        words = s.split()
        letters = [c for c in s if c.isalpha()]
        return (1 <= len(words) <= 6 and len(letters) >= 3
                and sum(c.isupper() for c in letters) >= 0.85 * len(letters))

    for p in parsed:
        head = (p.get("header") or "").strip()
        if head:
            # Try the reorder whenever there's no tab, regardless of
            # whether the swapped form is itself "confirmed" -- a
            # consistently-misordered leaf (K-review "LA MISE EN GARDE
            # FLEURET" read the same way on >=2 plate leaves) satisfies
            # its OWN count>=2 threshold as an accidental one-word-longer
            # root, which used to skip this check entirely.
            if "\t" not in head:
                reordered = _reorder_swapped_two_part(head)
                if reordered:
                    p["header"] = reordered
            continue
        body = p.get("body") or ""
        first, _, rest = body.lstrip("\n").partition("\n")
        first = first.strip()
        second, _, _ = rest.lstrip("\n").partition("\n")
        second = second.strip()
        # A big two-line opening title (Gomard: "LISTE DES OUVRAGES" /
        # "CONSULTÉS PAR L'AUTEUR.") is NOT a running head -- visual check
        # against the scan showed it's a body-level title block on the
        # section's one opening page, with no small margin header at all;
        # the genuine small running head (just "LISTE DES OUVRAGES") only
        # starts on the section's SECOND page, which the model reads on
        # its own. A single title-shaped line immediately followed by
        # ANOTHER title-shaped line means "compound title block, leave it
        # all in body" -- a genuine section-opener is followed by ordinary
        # prose, not a second title line.
        if first and (_key(first) in confirmed or _looks_like_section_title(first)) \
                and not (second and _looks_like_section_title(second)):
            p["header"] = first
            p["body"] = rest.lstrip("\n")

    # Sandwich interpolation: a page still headerless after everything
    # above, with both immediate neighbours already agreeing on the same
    # confirmed header, inherits it.
    n = len(parsed)
    for i, p in enumerate(parsed):
        if (p.get("header") or "").strip() or i == 0 or i == n - 1:
            continue
        prev_h = (parsed[i - 1].get("header") or "").strip()
        next_h = (parsed[i + 1].get("header") or "").strip()
        if prev_h and next_h and _key(prev_h) == _key(next_h) and _key(prev_h) in confirmed:
            p["header"] = prev_h

    # A page still headerless after everything above, flanked by one-page
    # interludes that were never themselves independently confirmed
    # (Kirchhoffer src 30: sits between "PARADES ET RIPOSTES" and "CONTRE
    # LE BRAS TENDU", each read natively on exactly one page -- neither
    # neighbour is trustworthy). The scan confirmed src 30's real running
    # head reverts to the parent section ("ÉPÉE"), reached by looking
    # further out. Inherit the nearest header that IS independently
    # confirmed (>=2x), searching outward in both directions and
    # preferring whichever is closer -- capped at a short gap so this
    # can't reach across an unrelated part of the book.
    _MAX_GAP = 4
    for i, p in enumerate(parsed):
        if (p.get("header") or "").strip():
            continue
        for dist in range(1, _MAX_GAP + 1):
            found = None
            for j in (i - dist, i + dist):
                if not (0 <= j < n):
                    continue
                h = (parsed[j].get("header") or "").strip()
                if h and _key(h) in confirmed:
                    found = root_display.get(_key(h), h.split("\t", 1)[0] if "\t" in h else h)
                    break
            if found:
                p["header"] = found
                break

    # A big two-line opening title block, printed within the body area on
    # a section's one opening page (Gomard: "HEADER: LISTE DES OUVRAGES",
    # body opens with "CONSULTÉS PAR L'AUTEUR.") -- not a running head,
    # confirmed against the scan. The model reads the FIRST line natively
    # into HEADER: itself here (unlike the promotion case above, which
    # only ever sees an empty HEADER:), so it's caught separately, and
    # deliberately LAST: the header value genuinely IS the real running
    # head on later pages of the same section (ordinary prose there,
    # confirmed by repetition) -- demoting first would just have the
    # nearest-confirmed gap fill above put it right back.
    #
    # Must NOT fire when the body's first line is a near-duplicate of the
    # header itself (normalises the same, punctuation aside) -- that's a
    # DIFFERENT, deliberately-fine book convention (Prévost src 21: the
    # header carries "DE LA MANIÈRE DE SE RELEVER." and the body repeats
    # it verbatim before its own subtitle -- "header and a body title
    # both is fine if that's what the book does", confirmed already).
    # The Gomard bug is specifically a header and a DIFFERENT second
    # title line together forming one compound block, not an echo.
    for p in parsed:
        head = (p.get("header") or "").strip()
        if not head or "\t" in head or not _looks_like_section_title(head):
            continue
        body = p.get("body") or ""
        b_first, _, _ = body.lstrip("\n").partition("\n")
        b_first = b_first.strip()
        if (b_first and _looks_like_section_title(b_first)
                and _key(b_first) != _key(head)):
            p["body"] = head + "\n\n" + body
            p["header"] = ""


def recover_plate_captions(parsed: list) -> None:
    """Gomard's plate captions run vertically and OCR grabs them
    unreliably. (1) A page whose body is short and opens with an explicit
    'Planche N.' / 'Fig. N.' line but has NO figure flagged -> synthesise
    a plate and use that line as the caption (unless a neighbour ±2
    already carries a plate with the same caption -- OCR read the same
    leaf twice under different folios). (2) A flagged figure with an empty
    caption -> borrow a caption-shaped line from this page's body or a
    neighbour's."""
    def _cap_line(body):
        m = _PLATE_CAPTION_RE.search(body or "")
        return m.group(1).strip() if m else ""

    _MISSED_PLATE_RE = re.compile(
        r"(?im)^[ \t]*(?:planche\s+[ivxlcdm]+\.|fig(?:ure|\.)?\s+[ivxlcdm\d]+\b)")

    def _neighbour_has_plate_cap(idx, norm_cap):
        for j in (idx - 2, idx - 1, idx + 1, idx + 2):
            if 0 <= j < len(parsed):
                for f in (parsed[j].get("figures") or []):
                    fc = _norm_head(f.get("caption") or "")
                    if fc and norm_cap and (fc[:40] in norm_cap or norm_cap[:40] in fc):
                        return True
        return False

    for idx, p in enumerate(parsed):
        body = p.get("body") or ""
        figs = p.get("figures") or []
        words = body.split()
        # (1) missed plate: an explicit "Planche N." / "Fig. N." line on a
        # short page with no figure flagged -> the whole (short) body is
        # the caption.
        if not figs and len(words) <= 45 and _MISSED_PLATE_RE.search(body):
            cap = re.sub(r"\s*\n\s*", " — ", body).strip()
            if not _neighbour_has_plate_cap(idx, _norm_head(cap)):
                p["figures"] = [{"kind": "plate", "caption": cap}]
                p["body"] = ""
            continue
        # (2) flagged figure, empty caption -> look here then at neighbours
        # (widen to +-2: a Gomard fold-out plate's caption is often on the
        # leaf before or after the image).
        if figs and not any((f.get("caption") or "").strip() for f in figs):
            for j in (idx, idx - 1, idx + 1, idx - 2, idx + 2):
                if 0 <= j < len(parsed):
                    nb_body = parsed[j].get("body") or ""
                    # A genuine caption living on a neighbouring leaf IS
                    # that leaf's whole (short) body -- not a sub-heading
                    # merely opening a page of real prose (P-review:
                    # "Parade de quarte." opens a page whose FULL body
                    # (33 words: that line + a definition paragraph + the
                    # next headword) is real prose, not a caption -- still
                    # wrongly borrowed at a 40-word cutoff, producing
                    # "PARADE DE SECONDE — Parade de quarte" instead of
                    # just "Parade de seconde". A real facing-leaf caption
                    # is just the caption, so this stays tight.
                    if j != idx and len(nb_body.split()) > 15:
                        continue
                    cap = _cap_line(nb_body)
                    if cap:
                        figs[0]["caption"] = cap
                        break


def _fix_consultes_pas(m):
    prefix, pas = m.group(1), m.group(2)
    if pas.isupper():
        repl = "PAR"
    elif pas[:1].isupper():
        repl = "Par"
    else:
        repl = "par"
    return prefix + repl


_OCR_PHRASE_FIXES = [
    # 'PAR' (by) misread as 'PAS' (not) -- a single-letter OCR confusion
    # (R/S) that recurs across a book's running head, and which the
    # translator then faithfully renders as a false negation (Gomard
    # "CONSULTÉS PAS L'AUTEUR." -> "NOT consulted by the author"). Only
    # the one word is replaced, case-preserving -- the running head is
    # often ALL CAPS, and a fixed-case replacement would flatten that.
    # (Python's re needs a fixed-width lookbehind, so the prefix is a
    # captured group instead, reattached in the replacement function.)
    (re.compile(r"(?i)(\bconsult[ée]s?\s+)(pas)(?=\s+l['’]auteur)"), _fix_consultes_pas),
]


def repair_source_ocr_misreads(parsed: list) -> None:
    """Narrow, evidence-based fixes for a specific recurring OCR misread
    -- not a general spellchecker. Runs first so the corrected text feeds
    every downstream pass (header-confirmation counting included)."""
    for p in parsed:
        for field in ("header", "body"):
            val = p.get(field) or ""
            if not val:
                continue
            for pat, repl in _OCR_PHRASE_FIXES:
                val = pat.sub(repl, val)
            p[field] = val


def postprocess_parsed_pages(parsed: list) -> None:
    """Run every Group-A structural salvage in order. Called once, right
    after parse_vlm_response() over the whole book. reconcile_headers() and
    fold_caption_header_into_figure() run separately, AFTER
    _fold_caption_only_body()."""
    repair_source_ocr_misreads(parsed)
    kh = {_norm_head(p["header"]) for p in parsed if p.get("header")}
    salvage_leaked_folio_header(parsed, kh)
    dehyphenate_across_pages(parsed)
    drop_google_boilerplate(parsed)
    drop_scan_apparatus(parsed)
    mark_handwritten_inscriptions(parsed)
    recover_plate_captions(parsed)


def reconcile_page_numbers(page_raws: list, is_plate: list = None) -> list:
    """olmOCR-2 reliably reads a real printed folio, but it will also invent
    a plausible-looking number from a stray mark -- an engraver's plate
    number inside an illustration, a library accession stamp -- or silently
    convert 'VIII' to '8'.

    A reported folio is TRUSTED when:
      * a neighbouring sheet's folio continues the printed sequence
        (i-1 -> v-1, or i+1 -> v+1), directly or across a short unread gap;
      * the VLM's own read equals what interpolation from the nearest anchor
        predicts (fixed-point, so a whole run rides off one anchor); or
      * MONOTONIC-CHAIN: two folio reads separated only by unnumbered leaves
        (blanks / unflagged plates) where the folio rises by 1..(sheet gap)
        -- this handles "text p.N, plate leaves, text p.N+1" books where
        sheet != folio so the strict-sequence check can't fire.

    Everything else is interpolated from the nearest trusted anchor and
    bracketed '[n]'; with no anchor at all, the plain sheet ordinal.

    `is_plate[i]` (a whole-page illustration -- no printed folio) lets the
    tight-gap fill skip plate leaves when checking the arithmetic. Never
    emits <= 0.
    """
    ints = [_page_to_int(p) for p in page_raws]
    n = len(ints)
    plate = [bool(is_plate[i]) if is_plate and i < len(is_plate) else False
             for i in range(n)]

    trusted = [False] * n
    for i, v in enumerate(ints):
        # A plate leaf's own "read" is its engraver's plate number
        # (Gomard: "Planche XVII" -> 17), not a printed folio -- never
        # trust it for itself, even when it happens to land where the
        # surrounding sequence's arithmetic would predict a real folio.
        # `plate[i]` already keeps it from breaking OTHER pages' gap
        # checks; this closes the matching gap where the plate leaf's own
        # bogus number could get trusted directly.
        if v is None or v <= 0 or plate[i]:
            continue
        for d in (1, 2, 3):
            lo_ok = (i - d >= 0 and ints[i - d] == v - d
                     and all(ints[k] is None for k in range(i - d + 1, i)))
            hi_ok = (i + d < n and ints[i + d] == v + d
                     and all(ints[k] is None for k in range(i + 1, i + d)))
            if lo_ok or hi_ok:
                trusted[i] = True
                break

    # Monotonic-chain: consecutive folio reads with only unnumbered leaves
    # between them, folio rising by at least 1 and at most the sheet gap.
    # Neither endpoint may itself be a plate leaf, for the same reason.
    reads = [i for i in range(n) if ints[i] and ints[i] > 0 and not plate[i]]
    for a, b in zip(reads, reads[1:]):
        if (all(ints[k] is None or plate[k] for k in range(a + 1, b))
                and 1 <= ints[b] - ints[a] <= (b - a)):
            trusted[a] = trusted[b] = True

    anchors = [i for i in range(n) if trusted[i]]
    if not anchors:
        return [f"[{i + 1}]" for i in range(n)]

    changed = True
    while changed:
        changed = False
        for i in range(n):
            if trusted[i] or ints[i] is None or ints[i] <= 0 or plate[i]:
                continue
            j = min(anchors, key=lambda a: abs(a - i))
            if ints[i] == ints[j] + (i - j):
                trusted[i] = True
                anchors = sorted(anchors + [i])
                changed = True

    # Tight-gap promotion: a run of non-trusted pages bracketed by two
    # trusted folios P .. Q, where Q - P == (non-plate slots in the run) + 1,
    # is filled in exactly -- the values are forced, so present them as real
    # ("303"), not "[303]", even where the read was a misread ("903"). A run
    # with an extra UNNUMBERED plate leaf the VLM didn't flag breaks the
    # arithmetic (Q-P too small) and stays bracketed -> suppressed.
    folio = [ints[i] if trusted[i] else None for i in range(n)]
    blank = [False] * n
    i = 0
    while i < n:
        if trusted[i]:
            i += 1
            continue
        lo = i
        while i < n and not trusted[i]:
            i += 1
        hi = i - 1                                          # run is [lo..hi]
        if lo - 1 >= 0 and hi + 1 < n and trusted[lo - 1] and trusted[hi + 1]:
            P, Q = folio[lo - 1], folio[hi + 1]
            nonplate = [k for k in range(lo, hi + 1) if not plate[k]]
            if P and Q and Q - P == len(nonplate) + 1:
                cur = P
                for k in range(lo, hi + 1):
                    if plate[k]:
                        continue
                    cur += 1
                    folio[k] = cur
                    trusted[k] = True
            elif P and Q and len(nonplate) > Q - P - 1:
                # more non-plate leaves in the run than the folio gap can
                # hold -> it contains unnumbered leaves (unflagged plates,
                # blanks) whose folio can't be pinned. Interpolating from
                # the nearest anchor seesaws a fabricated, self-
                # contradicting folio onto each ([17][18][19][20][13][14]
                # [15][16]); emit blank instead.
                for k in range(lo, hi + 1):
                    blank[k] = True

    anchors = sorted(k for k in range(n) if trusted[k])
    out = []
    for i in range(n):
        if trusted[i]:
            out.append(page_raws[i]
                       if folio[i] is None or _page_to_int(page_raws[i]) == folio[i]
                       else str(folio[i]))                  # printed / forced folio
            continue
        if blank[i] or plate[i]:            # inferred plate leaf -> no folio
            out.append("")
            continue
        j = min(anchors, key=lambda a: abs(a - i))          # nearest trusted sheet
        guess = (folio[j] or 0) + (i - j)                   # 1 sheet == 1 folio
        out.append(f"[{guess}]" if guess >= 1 else f"[{i + 1}]")
    return out


def preprocess_for_ocr(img: Image.Image) -> Image.Image:
    """Light contrast normalization before OCR. Historical scans are
    frequently faded/yellowed/low-contrast; autocontrast on the luminance
    channel tends to make text edges cleaner for the vision model."""
    if not VLM_PREPROCESS:
        return img
    gray = ImageOps.autocontrast(img.convert("L"), cutoff=1)
    return gray.convert("RGB")


# Returned by start_vlm_server() when BACKEND_MODE=remote -- there is no
# local process to manage, just an endpoint to talk to.
REMOTE_SERVER = object()


def start_vlm_server(restart=False):
    if bc.is_remote():
        base = bc.ocr_base_url()
        print(f"\n[remote] using VLM/OCR endpoint {base}")
        print("[remote] make sure the VISION model is loaded on that host.")
        try:
            r = requests.get(f"{base}/health", headers=bc.auth_headers(), timeout=5)
            if r.status_code == 200:
                print("[remote] /health OK\n")
                return REMOTE_SERVER
            print(f"[remote] /health returned HTTP {r.status_code}")
        except Exception as e:
            print(f"[remote] cannot reach {base}: {e}")
        return None

    if restart:
        print("\n🔄 RESTARTING VLM SERVER (clearing stuck state)")
    else:
        print("\n" + "="*60)
        print("LOADING VISION MODEL (olmOCR-2-7B-1025)")
        print("="*60 + "\n")
    # Claim port 11435: kill any llama-server already on it (a stale one from
    # a previous run, or start_servers.sh) so our Popen below is the server
    # this script actually manages, at the ctx-size it wants. Safe now that
    # the entrypoint no longer runs a server under `wait -n`.
    subprocess.run(["pkill", "-f", "llama-server.*11435"], stderr=subprocess.DEVNULL)
    time.sleep(5)

    print(f"Model:  {VLM_MODEL_PATH}")
    print(f"Mmproj: {VLM_MMPROJ_PATH}")
    print("Port:   11435 | GPU: Full offload\n")

    if not os.path.exists(VLM_MODEL_PATH):
        print(f"✗ Model not found: {VLM_MODEL_PATH}\n")
        return None
    if not os.path.exists(VLM_MMPROJ_PATH):
        print(f"✗ Projector not found: {VLM_MMPROJ_PATH}\n")
        return None

    model_size = os.path.getsize(VLM_MODEL_PATH) / (1024**3)
    mmproj_size = os.path.getsize(VLM_MMPROJ_PATH) / (1024**3)
    print(f"✓ Model verified ({model_size:.2f} GB)")
    print(f"✓ Projector verified ({mmproj_size:.2f} GB)\n")

    cmd = [
        "llama-server",
        "--model", VLM_MODEL_PATH,
        "--mmproj", VLM_MMPROJ_PATH,
        "--host", "0.0.0.0",
        "--port", "11435",
        "--ctx-size", "8192",
        "--n-gpu-layers", "999",
        "--threads", str(os.cpu_count()),
        "--parallel", "1"
    ]

    print("Starting server...\n")

    log_file = open("/tmp/vlm_server.log", "w")
    proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT,
                            env=_llama_server_env())

    dots = 0
    # 240s cold ceiling: a first llama-server load on a cleared shader cache
    # (RADV compiles every pipeline, ~2-3 min) used to blow the old 120s.
    # Warm (docker-compose volume persists the cache) it's ~60s. `restart`
    # only fires mid-run, cache already warm.
    max_wait = 60 if restart else 240
    for attempt in range(max_wait // 2):
        time.sleep(2)
        dots = (dots + 1) % 4
        elapsed = (attempt+1)*2
        sys.stdout.write(f"\r  Loading{'.' * dots}{' ' * (3-dots)} {elapsed}s")
        sys.stdout.flush()

        if proc.poll() is not None:
            print(f"\n\n✗ Server process exited (code {proc.returncode}) after {elapsed}s")
            print("--- tail of /tmp/vlm_server.log ---")
            try:
                print("".join(open("/tmp/vlm_server.log").readlines()[-15:]))
            except Exception:
                pass
            return None

        try:
            resp = requests.get(f"{bc.ocr_base_url()}/health", timeout=2)
            if resp.status_code == 200:
                print(f"\n\n✓ Vision server ready! (took {elapsed}s)")
                if not restart:
                    print("="*60)
                print()
                return proc
        except:
            pass

    print("\n\n✗ Server timeout")
    print("Check logs: docker exec booktranslator-translator-1 cat /tmp/vlm_server.log\n")
    proc.terminate()
    return None


def prepare_image_for_vlm(img: Image.Image, max_dimension: int = VLM_MAX_DIMENSION, quality: int = 92) -> tuple:
    """Resize to olmOCR-2's documented longest-dimension spec and encode.
    Quality can stay high since the image itself is intentionally small --
    this isn't a size/speed compromise the way Qwen2-VL's escalating
    compression was."""
    if img.width > max_dimension or img.height > max_dimension:
        ratio = min(max_dimension / img.width, max_dimension / img.height)
        new_size = (int(img.width * ratio), int(img.height * ratio))
        img = img.resize(new_size, Image.Resampling.LANCZOS)

    buffered = io.BytesIO()
    img.save(buffered, format="JPEG", quality=quality, optimize=True)
    img_bytes = buffered.getvalue()
    size_kb = len(img_bytes) / 1024
    img_b64 = base64.b64encode(img_bytes).decode()
    return img_b64, size_kb


def resolve_crop(src_path: str):
    """Optional, no longer the primary defense against margin clutter (the
    prompt now handles that directly -- see module docstring), but still
    honored if present in case a page needs it anyway."""
    crop_file = Path(src_path).with_suffix(".crop")
    if crop_file.exists():
        try:
            parts = [float(x.strip()) for x in crop_file.read_text(encoding="utf-8").strip().split(",")]
            if len(parts) == 4:
                return tuple(parts)
        except ValueError:
            pass
    return (0.0, 0.0, 0.0, 0.0)


def apply_crop_to_image(img: Image.Image, crop: tuple) -> Image.Image:
    left, top, right, bottom = crop
    w, h = img.size
    return img.crop((int(w * left), int(h * top), int(w * (1 - right)), int(h * (1 - bottom))))


def extract_page_with_vlm(image_b64: str, size_kb: float, page_num: int, total_pages: int) -> dict:
    """Extract text from page using VLM"""
    timeout = 300

    try:
        response = requests.post(
            f"{bc.ocr_base_url()}/v1/chat/completions",
            headers={"Content-Type": "application/json", **bc.auth_headers()},
            json={
                "model": VLM_MODEL_NAME,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VLM_PROMPT_TEMPLATE},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
                    ]
                }],
                "temperature": 0.1,
                "max_tokens": 4096,
                # Mild -- breaks the dot-leader / "* * *" repetition loops
                # olmOCR-2 falls into on some table-of-contents and ornament
                # pages without hurting legitimately repeated words.
                "repeat_penalty": 1.12
            },
            timeout=timeout
        )

        if response.status_code == 200:
            result = response.json()
            content = collapse_runs(result["choices"][0]["message"]["content"])
            print(f"  Page {page_num+1}/{total_pages} ✓ ({size_kb:.0f}KB)")
            return {"success": True, "content": content}

        elif response.status_code == 500:
            try:
                error_json = response.json()
                error_msg = error_json.get('error', {}).get('message', 'Unknown')
            except:
                error_msg = "Unknown"

            print(f"  Page {page_num+1}/{total_pages} ✗ HTTP 500: {error_msg}")
            return {"success": False, "error": error_msg, "retry": True}

        else:
            print(f"  Page {page_num+1}/{total_pages} ✗ HTTP {response.status_code}")
            return {"success": False, "error": f"HTTP {response.status_code}"}

    except requests.exceptions.Timeout:
        print(f"  Page {page_num+1}/{total_pages} ✗ Timeout after {timeout}s")
        return {"success": False, "error": "Timeout", "retry": True}
    except Exception as e:
        print(f"  Page {page_num+1}/{total_pages} ✗ {type(e).__name__}")
        return {"success": False, "error": str(e)}

print("\n" + "="*60)
print("STEP 1: VLM OCR EXTRACTION (olmOCR-2-7B-1025)")
print("="*60)
print(f"Preprocessing (grayscale+autocontrast): {'ON' if VLM_PREPROCESS else 'OFF'}")
print(f"Resize target (longest dimension): {VLM_MAX_DIMENSION}px")

vlm_proc = None

try:
    input_pdfs = [
        f for f in glob.glob("/data/books/*.pdf")
        if not os.path.exists(f"/data/processed/{Path(f).stem}_translated")
    ]

    if not input_pdfs:
        print("\n✓ No new books to process\n")
        exit(0)

    vlm_proc = start_vlm_server()

    if vlm_proc is None:
        print("✗ Cannot start VLM server\n")
        exit(1)

    for src_path in input_pdfs:
        stem = Path(src_path).stem
        work_dir = f"/data/processed/{stem}_translated"
        os.makedirs(work_dir, exist_ok=True)

        print(f"Processing: {stem}")

        ocr_pdf_path = f"{work_dir}/{stem}_OCR.pdf"
        shutil.copy2(src_path, ocr_pdf_path)
        print(f"✓ Original PDF copied as OCR PDF")

        crop = resolve_crop(src_path)
        if any(crop):
            print(f"  Using .crop override (left,top,right,bottom): {crop}")

        doc = fitz.open(src_path)
        total_pages = len(doc)
        print(f"Total pages: {total_pages}\n")

        extracted_pages = []
        error_count = 0

        for i in range(total_pages):
            page = doc[i]
            mat = fitz.Matrix(300/72, 300/72)
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            img = preprocess_for_ocr(img)
            if any(crop):
                img = apply_crop_to_image(img, crop)

            img_b64, size_kb = prepare_image_for_vlm(img)
            result = extract_page_with_vlm(img_b64, size_kb, i, total_pages)

            if not result["success"] and vlm_proc and vlm_proc is not REMOTE_SERVER:
                print(f"  → Restarting server and retrying page {i+1}...")
                vlm_proc.terminate()
                try:
                    vlm_proc.wait(timeout=5)
                except:
                    vlm_proc.kill()
                time.sleep(3)

                vlm_proc = start_vlm_server(restart=True)

                if vlm_proc:
                    # Last-resort retry at a smaller size in case the first
                    # attempt failed due to a load/VRAM hiccup rather than
                    # the page itself.
                    img_b64_small, size_kb_small = prepare_image_for_vlm(img, max_dimension=900, quality=80)
                    result = extract_page_with_vlm(img_b64_small, size_kb_small, i, total_pages)

            if result and result["success"]:
                extracted_pages.append(result["content"])
            else:
                error_count += 1
                error_msg = result.get("error", "Unknown") if result else "No response"
                extracted_pages.append(f"[VLM Error: {error_msg}]")

            time.sleep(2)

        doc.close()
        print()

        # Preserve the TRUE raw model responses permanently, before any
        # parsing -- same contract as the Tesseract path's raw_ocr.txt: the
        # ground truth to fall back on if the parsing/reconciliation below
        # turns out to have dropped or altered something.
        with open(f"{work_dir}/raw_ocr.txt", "w", encoding="utf-8") as f:
            f.write(_pageio.join_pages(extracted_pages))

        # Split olmOCR-2's PAGE_NUMBER/HEADER/BODY_TEXT scaffold into fields
        # and sequence-check the page number. Previously the raw scaffold
        # went straight downstream, so a hallucinated "PAGE_NUMBER: 57"
        # (read off an engraving plate) became a line handed to the
        # translator and printed on the side-by-side PDF.
        parsed = [parse_vlm_response(p) for p in extracted_pages]
        postprocess_parsed_pages(parsed)
        _known_heads = known_headmatter(parsed)
        for p in parsed:
            p["body"] = strip_leaked_headmatter(p["body"], _known_heads)
        _fold_caption_only_body(parsed)
        fold_caption_header_into_figure(parsed)
        reconcile_headers(parsed)
        _is_plate = [any(f.get("kind") == "plate" for f in (p.get("figures") or []))
                     for p in parsed]
        reconciled_nums = reconcile_page_numbers(
            [p["page_raw"] for p in parsed], _is_plate)

        canonical_pages = []
        for raw, fields, pnum in zip(extracted_pages, parsed, reconciled_nums):
            if raw.lstrip().startswith("[VLM Error"):
                canonical_pages.append(raw)          # keep the error marker intact
                continue
            canonical_pages.append(
                f"PAGE_NUMBER: {pnum}\n"
                f"HEADER: {fields['header'] or 'none'}\n"
                f"BODY_TEXT:\n{fields['body']}"
            )

        with open(f"{work_dir}/extracted.txt", "w", encoding="utf-8") as f:
            f.write(_pageio.join_pages(canonical_pages))

        # Per-page figure metadata for the English-only PDF (kind / vertical
        # extent / caption, as reported by the VLM). Captions are translated
        # into this file by 2_translate.py. Keyed by 0-based page index.
        figures_map = {str(i): fields["figures"]
                       for i, fields in enumerate(parsed) if fields.get("figures")}
        with open(f"{work_dir}/figures.json", "w", encoding="utf-8") as f:
            json.dump(figures_map, f, ensure_ascii=False, indent=1)
        n_fig = sum(len(v) for v in figures_map.values())
        print(f"  {n_fig} figure(s) flagged across {len(figures_map)} page(s)")

        # Parity with the Tesseract path: one reconciled page number per
        # line, '[n]' where it had to be inferred rather than read.
        with open(f"{work_dir}/page_numbers.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(reconciled_nums))

        if error_count > 0:
            print(f"⚠ Extraction complete with {error_count}/{total_pages} errors: {stem}\n")
        else:
            print(f"✓ Extraction complete (all {total_pages} pages successful): {stem}\n")

finally:
    if vlm_proc is REMOTE_SERVER:
        print("[remote] leaving the remote VLM/OCR server as-is\n")
    elif vlm_proc:
        print("="*60)
        print("UNLOADING VISION MODEL")
        print("="*60 + "\n")
        print("Shutting down vision server...")
        vlm_proc.terminate()
        try:
            vlm_proc.wait(timeout=10)
            print("✓ Vision server stopped (VRAM freed)\n")
        except:
            vlm_proc.kill()
            print("✓ Vision server killed (VRAM freed)\n")

print("="*60)
print("✓ VLM OCR COMPLETE")
print("="*60 + "\n")
