#!/usr/bin/env python3
# Step 1: OCR + cleanup (v3)
# Changes from v1:
#   - Raw OCR text is now preserved permanently (raw_ocr.txt) BEFORE any
#     cleanup runs, so bad output can always be traced back to "OCR was
#     wrong" vs "cleanup heuristics mangled it."
#   - Per-book OCR language via an optional sidecar `<stem>.lang` file,
#     instead of always OCRing against 6 languages at once.
#   - Header/footer stripping now requires a line to actually repeat across
#     multiple pages near the top/bottom before removing it, instead of
#     blindly chopping the top/bottom 15% of every page.
#   - Ligature replacement bug fixed (v1's fi/fl/ff replacements were
#     accidental no-ops: source and target strings were identical).
# Changes from v2 (validated against a real 7-page sample, see chat log):
#   - Per-book crop sidecar (<stem>.crop) to trim facing-page/binding bleed
#     before OCR -- confirmed this bleed was producing scattered garbage
#     tokens in OCR output.
#   - Forces Tesseract PSM 3 (full automatic layout analysis) -- confirmed
#     dramatically cleaner than leaving PSM unset or forcing PSM 6.
#   - Removed --oversample by default -- confirmed it was blowing up images
#     5-6x for no quality gain on scans with pixel-count-as-pagesize PDFs.
#   - AGGRESSIVE_CLEANUP now defaults OFF, and its two most dangerous
#     regexes were deleted outright: confirmed they were corrupting common
#     French words (anything like "vous ébranler" -> "vousébranler") on
#     correctly-spaced real OCR text, not just a rare edge case.
import glob
import os
import subprocess
import fitz
import re
from pathlib import Path
from collections import Counter

os.environ["TESSDATA_PREFIX"] = "/usr/share/tesseract-ocr/5/tessdata"

# ---------------------------------------------------------------------------
# Tunables (all overridable via env vars so you can A/B test without editing
# this file)
# ---------------------------------------------------------------------------
DEFAULT_LANG = os.environ.get("OCR_DEFAULT_LANG", "fra+lat+eng")
AGGRESSIVE_CLEANUP = os.environ.get("OCR_AGGRESSIVE_CLEANUP", "0") != "0"
HEADER_FOOTER_BAND = int(os.environ.get("OCR_HEADER_FOOTER_BAND", "5"))
HEADER_FOOTER_MIN_FRAC = float(os.environ.get("OCR_HEADER_FOOTER_MIN_FRAC", "0.25"))
# Deliberately NOT defaulted on. Tested against a real sample: many library
# scans (Gallica etc.) set the PDF's page size directly from pixel count
# instead of true physical inches, which makes ocrmypdf compute a wildly
# low "effective DPI" and blow the image up 5-6x for no real gain in detail
# -- just slower OCR. Only set this (e.g. OCR_OVERSAMPLE_DPI=400) if you
# have scans you know are genuinely low native resolution.
OVERSAMPLE_DPI = os.environ.get("OCR_OVERSAMPLE_DPI", "")

LIGATURE_MAP = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl",
    "\ufb03": "ffi", "\ufb04": "ffl",
    "\ufb05": "st", "\ufb06": "st",
    "ſ": "s",
}


def resolve_crop(src_path: str):
    """
    Per-book crop margins, for scans where the frame includes the facing
    page, binding, or a photographer's hand/edge (common in phone photos
    of an open book, vs. a flatbed library scan). Confirmed via direct
    testing: this kind of bleed gets OCR'd as garbage tokens scattered
    through the output (stray short "words" like fragments of the facing
    page), even though the actual body text recognition is otherwise fine.
    Put a file next to the PDF named `<stem>.crop` containing four
    comma-separated fractions: left,top,right,bottom -- each is the
    fraction of the page dimension to trim from that edge before OCR.
    Example (trim 10% off the left edge only): 0.10,0.0,0.0,0.0
    Only applies when OCR actually runs (a page with no existing text
    layer, or --force-ocr) -- pages with a usable existing text layer are
    kept as-is via --skip-text and never touch this.
    """
    crop_file = Path(src_path).with_suffix(".crop")
    if crop_file.exists():
        try:
            parts = [float(x.strip()) for x in crop_file.read_text(encoding="utf-8").strip().split(",")]
            if len(parts) == 4:
                return tuple(parts)
        except ValueError:
            print(f"  ⚠ Could not parse {crop_file.name}, expected 'left,top,right,bottom' -- ignoring")
    return (0.0, 0.0, 0.0, 0.0)


def apply_crop(src_path: str, out_path: str, crop: tuple):
    """Non-destructively crop each page's visible region (CropBox) before
    handing the PDF to ocrmypdf. Does not touch the underlying image data,
    just what OCR/rendering is allowed to see."""
    left, top, right, bottom = crop
    doc = fitz.open(src_path)
    for page in doc:
        r = page.rect
        new_rect = fitz.Rect(
            r.x0 + r.width * left,
            r.y0 + r.height * top,
            r.x1 - r.width * right,
            r.y1 - r.height * bottom,
        )
        page.set_cropbox(new_rect)
    doc.save(out_path)
    doc.close()


def resolve_language(src_path: str) -> str:
    """
    Per-book OCR language override. Put a file next to the PDF with the
    same stem and a `.lang` extension, containing Tesseract language codes
    joined with '+', e.g.:
        /data/books/Capo_Ferro_1610.pdf
        /data/books/Capo_Ferro_1610.lang   <- containing just: ita
    Mixing many languages (the old default of 6 at once, always) measurably
    hurts Tesseract's accuracy -- only combine what's actually on the page
    (e.g. a French treatise quoting Latin -> "fra+lat").
    """
    lang_file = Path(src_path).with_suffix(".lang")
    if lang_file.exists():
        lang = lang_file.read_text(encoding="utf-8").strip()
        if lang:
            return lang
    return DEFAULT_LANG


def normalize_line(line: str):
    """Normalize a line for cross-page repetition matching. Digits are
    collapsed to '#' because running headers/footers often contain a page
    number that changes per page but the surrounding text doesn't."""
    s = re.sub(r"\d+", "#", line.strip().lower())
    s = re.sub(r"[^\w\s#]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) > 1 else None


def build_repeated_line_sets(pages: list, band: int = HEADER_FOOTER_BAND,
                              min_frac: float = HEADER_FOOTER_MIN_FRAC):
    """
    Identify lines that recur near the top/bottom of many pages (running
    headers, footers, printed folio numbers) so only THOSE get removed --
    never a blind percentage cut that eats real body text on short pages.
    """
    top_counter, bot_counter = Counter(), Counter()
    total = len(pages)
    for text in pages:
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        if not lines:
            continue
        for l in lines[:band]:
            norm = normalize_line(l)
            if norm:
                top_counter[norm] += 1
        for l in lines[-band:]:
            norm = normalize_line(l)
            if norm:
                bot_counter[norm] += 1
    threshold = max(2, int(total * min_frac))
    top_repeated = {l for l, c in top_counter.items() if c >= threshold}
    bot_repeated = {l for l, c in bot_counter.items() if c >= threshold}
    return top_repeated, bot_repeated


def remove_headers_footers(text: str, top_repeated: set, bot_repeated: set,
                            band: int = HEADER_FOOTER_BAND) -> str:
    """Remove only lines within the top/bottom band that match a pattern
    repeated across the book."""
    lines = text.splitlines()
    n = len(lines)
    if n < 4:
        return text
    lone_number_re = re.compile(r"^[\divxlc]{1,6}$", re.IGNORECASE)
    out = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            out.append("")
            continue
        in_band = i < band or i >= n - band
        # A short, standalone digit/roman-numeral line right at the top or
        # bottom edge is almost always a folio number, never body text --
        # but this check is deliberately restricted to the band so it can
        # never eat a numbered item elsewhere on the page.
        if in_band and lone_number_re.match(s):
            continue
        norm = normalize_line(s)
        if i < band and norm and norm in top_repeated:
            continue
        if i >= n - band and norm and norm in bot_repeated:
            continue
        out.append(s)
    result = "\n".join(out)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def extract_page_number_from_page(page_text: str) -> str:
    lines = page_text.strip().splitlines()
    for line in lines[:10]:
        s = line.strip()
        numbers = re.findall(r"\b(\d{1,4})\b", s)
        for num in numbers:
            if len(s.replace(num, "").strip()) < 20:
                return num
        if re.match(r"^[IVXLCivxlc]+$", s) and 1 <= len(s) <= 6:
            return s
    for line in lines[-10:]:
        s = line.strip()
        numbers = re.findall(r"\b(\d{1,4})\b", s)
        for num in numbers:
            if len(s.replace(num, "").strip()) < 20:
                return num
        if re.match(r"^[IVXLCivxlc]+$", s) and 1 <= len(s) <= 6:
            return s
    return None


def cleanup_extracted_text(text: str) -> str:
    """Line-joining, hyphenation, ligatures. AGGRESSIVE_CLEANUP gates the
    heuristic language-specific accent/apostrophe guesses separately, since
    those are the most likely to help on one book and hurt on another."""
    cleaned = text

    cleaned = re.sub(r"(?i)hosted\s+by\s+google|google\s+books|digitized\s+by|internet\s+archive", "", cleaned)
    cleaned = re.sub(r"https?://\S+|www\.\S+\.\w+", "", cleaned)

    lines = cleaned.split("\n")
    result_lines = []
    current_para = []
    for line in lines:
        line = line.strip()
        if not line:
            if current_para:
                result_lines.append(" ".join(current_para))
                current_para = []
            result_lines.append("")
        else:
            current_para.append(line)
    if current_para:
        result_lines.append(" ".join(current_para))
    cleaned = "\n".join(result_lines)

    # Fix hyphenated line-break words: "esca-\nrmouche" -> "escarmouche"
    cleaned = re.sub(r"([a-zA-ZÀ-ÿ])\-\s+([a-zA-ZÀ-ÿ])", r"\1\2", cleaned)

    # Real ligature fixes (v1's fi/fl/ff replacements were accidental no-ops)
    for lig, rep in LIGATURE_MAP.items():
        cleaned = cleaned.replace(lig, rep)

    # Punctuation spacing -- structural, language-agnostic, low risk
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)
    cleaned = re.sub(r"([.,;:!?])([a-zA-Z])", r"\1 \2", cleaned)

    # REMOVED (v3): two regexes used to live here that collapsed whitespace
    # around ANY standalone accented character or letter-then-accented-vowel
    # pair, intended to fix OCR that isolates an accented letter from its
    # word. Confirmed via direct testing against real OCR'd French text that
    # they are not a narrow fix -- they systematically destroy correct
    # spacing on ordinary common words:
    #   "amener à tromper"  -> "ameneràtromper"   (à is simply the word "to")
    #   "vous ébranler"     -> "vousébranler"
    #   "et écrire"         -> "etécrire"
    #   "cet état"          -> "cetétat"
    #   "vous êtes"         -> "vousêtes"
    # Any word starting with é/è/ê/à (être, écrire, état, épée, écarter...)
    # following another word triggers this, which is extremely common in
    # French. There is no reliable way to distinguish "OCR accidentally
    # inserted a space here" from "this is just where a space belongs" with
    # a context-free regex, so these have been deleted rather than gated.
    if AGGRESSIVE_CLEANUP:
        # The apostrophe-joining fixes below are narrower (they require an
        # actual apostrophe character already present) and less prone to
        # this failure mode, but are still unverified against real OCR
        # output -- keep an eye on raw_ocr.txt if you suspect these are
        # doing anything wrong either.
        cleaned = re.sub(r"d\s+'\s*", "d'", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"l\s+'\s*", "l'", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"s\s+'\s*", "s'", cleaned, flags=re.IGNORECASE)
        fixes = [
            ("é e", "ée"), ("é s", "és"),
            ("l ép", "l'ép"), ("l ad", "l'ad"), ("d un", "d'un"),
        ]
        for bad, good in fixes:
            cleaned = cleaned.replace(bad, good)

    cleaned = re.sub(r" {2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# ---------------------------------------------------------------------------
# Main OCR loop
# ---------------------------------------------------------------------------
input_pdfs = [
    f for f in glob.glob("/data/books/*.pdf")
    if not os.path.exists(f"/data/processed/{Path(f).stem}_translated")
]

if not input_pdfs:
    print("No new books to OCR")
    exit(0)

for src_path in input_pdfs:
    stem = Path(src_path).stem
    work_dir = f"/data/processed/{stem}_translated"
    os.makedirs(work_dir, exist_ok=True)
    ocr_pdf = f"{work_dir}/{stem}_OCR.pdf"

    lang = resolve_language(src_path)
    print(f"OCRing → {stem}  [language: {lang}]")
    if lang == DEFAULT_LANG and not Path(src_path).with_suffix(".lang").exists():
        print(f"  (no {stem}.lang file found -- using default '{DEFAULT_LANG}'. "
              f"For non-French/Latin sources, add {stem}.lang next to the PDF "
              f"containing e.g. 'ita' or 'deu' for better accuracy.)")

    crop = resolve_crop(src_path)
    ocr_input = src_path
    if any(crop):
        print(f"  Applying crop (left,top,right,bottom fractions): {crop}")
        cropped_path = f"{work_dir}/{stem}_cropped_input.pdf"
        apply_crop(src_path, cropped_path, crop)
        ocr_input = cropped_path
    elif not Path(src_path).with_suffix(".crop").exists():
        print(f"  (no {stem}.crop file -- OCRing full page. If the scan shows the "
              f"facing page, binding, or a hand/edge in frame, add {stem}.crop "
              f"with e.g. '0.10,0.0,0.0,0.0' to trim it before OCR -- confirmed to "
              f"remove margin-bleed garbage tokens without needing to touch this script again.)")

    ocrmypdf_cmd = [
        "ocrmypdf",
        "--skip-text",
        "--output-type", "pdf",
        "--language", lang,
        "--deskew",
        "--clean",
        # PSM 3 (full automatic layout analysis) confirmed via direct testing
        # to isolate the real text column and discard marginal noise far
        # better than leaving this unset or forcing PSM 6.
        "--tesseract-pagesegmode", "3",
    ]
    if OVERSAMPLE_DPI:
        ocrmypdf_cmd += ["--oversample", OVERSAMPLE_DPI]
    ocrmypdf_cmd += [ocr_input, ocr_pdf]

    subprocess.run(ocrmypdf_cmd, check=True)

    # Extract raw text straight off the OCR'd PDF -- no cleanup applied yet.
    doc = fitz.open(ocr_pdf)
    raw_pages = [page.get_text("text") for page in doc]
    doc.close()

    # Preserve TRUE raw OCR permanently. This is the ground truth to fall
    # back on if cleanup heuristics turn out to have mangled something.
    with open(f"{work_dir}/raw_ocr.txt", "w", encoding="utf-8") as f:
        f.write("\n---\n".join(raw_pages))

    print(f"Cleaning text → {stem}")
    stage1_pages = [cleanup_extracted_text(p) for p in raw_pages]

    # Build book-wide repeated-line sets ONCE, then strip only genuine
    # running headers/footers/page-numbers from each page.
    top_repeated, bot_repeated = build_repeated_line_sets(stage1_pages)
    if top_repeated or bot_repeated:
        print(f"  Detected {len(top_repeated)} repeated header pattern(s), "
              f"{len(bot_repeated)} repeated footer pattern(s) across the book")

    cleaned_pages = []
    page_numbers = []
    for idx, page_text in enumerate(stage1_pages):
        page_num = extract_page_number_from_page(raw_pages[idx])
        if not page_num:
            page_num = f"[{idx + 1}]"
        page_numbers.append(page_num)

        cleaned = remove_headers_footers(page_text, top_repeated, bot_repeated)
        cleaned_pages.append(cleaned)
        print(f"  Page {idx+1}: detected page number = {page_num}", end="\r")

    print()

    with open(f"{work_dir}/extracted.txt", "w", encoding="utf-8") as f:
        f.write("\n---\n".join(cleaned_pages))

    with open(f"{work_dir}/page_numbers.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(page_numbers))

    print(f"OCR complete: {stem}")
    print(f"  Raw OCR preserved at: {work_dir}/raw_ocr.txt")
    print(f"  Detected page numbers: {', '.join(page_numbers[:5])}{'...' if len(page_numbers) > 5 else ''}\n")
