# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Step 1: reading the OCR model's reply for one page -- the PAGE_NUMBER /
HEADER / BODY_TEXT fields and the FIGURES block -- after clipping its
repetition loops. Also the running-head key (_norm_head) the other _ocr_*
modules compare heads by."""

import re


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


def has_header_field(raw: str) -> bool:
    return any(l.lstrip().upper().startswith("HEADER") for l in (raw or "").split("\n"))


def has_page_number_field(raw: str) -> bool:
    """Did the OCR answer the PAGE_NUMBER question at all? A malformed reply
    (plain text, no fields) says nothing about whether a number is printed
    -- Jacob scans 77/115/127 print 29/67/79 and the reply just omitted
    the field."""
    return bool(re.search(r"(?im)^[ \t]*PAGE_NUMBER[ \t]*:", raw or ""))
