# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Step 1: clean-ups of one page's parsed text -- scan-library boilerplate and
stamps, handwritten inscriptions, image descriptions, known OCR misreads,
math markup, plate leaves and their captions."""

import re

from _ocr_parse import _norm_head


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

_PLATE_LEAF_RE = re.compile(r"(?i)^\s*planche\s+[ivxlcdm\d]+\b")


def is_plate_leaf(body: str) -> bool:
    """True when body's own first line is a "Planche <numeral>" plate
    label -- reused by reconcile_page_numbers()'s is_plate exclusion so a
    plate the VLM never flagged with a FIGURES: block (no figure entry
    at all -- Gomard "Planche XIV./XV.": body opens directly with the
    plate line, HEADER empty, no figure) still gets its own engraver's
    plate number excluded from folio trust, same as an explicitly-
    flagged plate. Narrower than _CAPTION_SHAPE_RE on purpose: that regex
    also matches ordinary in-body caption/description lines ("Parade de
    sixte...", "Coup tiré...") that legitimately open a normal text
    page's body too -- only the "Planche N." branch specifically means
    "this whole leaf IS a plate."""
    first, _, _ = (body or "").strip().partition("\n")
    return bool(_PLATE_LEAF_RE.match(first.strip()))


_SCAN_APPARATUS_RE = re.compile(
    r"insufficient\s+contrast|contraste\s+insuffisant|mire\s+de\s+|test\s+chart"
    r"|résolution\s+insuffisante|texte\s+détérior|reliure\s+défectueuse"
    r"|pages?\s+manquantes?|auteur\s+du\s+texte"
    r"|biblioth[eè]que\s+[\wàâäéèêëïîôùûç.'’-]+\s+\d{3,}"
    r"|\bex[- ]?libris\b|n[°o]\s*d['’]?(?:inventaire|entrée)\b"
    r"|/\s*par\s+[A-ZÀ-Þ]"                       # BnF catalog "Title / par Author"
    # owners' stamps in these copies (2026-09-28 review): Jacob / Spinnewyn
    # "INSTITUT NATIONAL DES SPORTS", inventory numbers, "CENTRE DE
    # DOCUMENTATION", the round "SOUS-OFFICIERS … ESCRIME" stamp; La
    # Boëssière's "HARVARD COLLEGE LIBRARY"
    r"|institut\w*\s+(?:national\s+)?d(?:u|es)\s+sports?|inventaire\s+(?:g[ée]n[ée]ral|particulier)"
    r"|biblioth[eè]que\s+centrale|centre\s+de\s+documentation|[ée]cole\s+sup[ée]rieure\s+d['’]\s*[ée]ducation"
    r"|\bcollege\s+library\b|\bcollege\s+of\s+officers\b|sous[- ]officiers",
    re.I)
# a line of a stamp block: the pattern above, an accession number
# ("554037", "N° d'entrée … 20238."), or a stamp's short follow-on line
_STAMP_LINE_RE = re.compile(r"^\s*(?:\d{5,}\.?|et\s+de\s+l['’]\s*[ée]ducation\s+physique"
                            r"|classement\s*\.{2,}.*)\s*$", re.I)
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
        # Remove the stamp's own lines, keep the rest of the page (Jacob scan
        # 10: the printed edition notice "IL A ÉTÉ TIRÉ À PART …" sat above a
        # library stamp and the whole page used to be blanked with it).
        lines = body.split("\n")
        keep = [l for l in lines if not ((_SCAN_APPARATUS_RE.search(l) and len(l.split()) <= 8)
                                          or re.search(r"/\s*par\s+[A-ZÀ-Þ]", l)   # catalogue line, any length
                                          or _STAMP_LINE_RE.match(l)
                                          or (_SHELFMARK_RE.match(l.strip()) and len(l.split()) <= 4))]
        rest = "\n".join(keep).strip()
        # what's left must read as text, not a stamp's leftovers ("Inventaire
        # général :" / "Bibliothèque Centrale :" labels, "8936")
        if len(re.findall(r"[^\W\d_]{3,}", rest)) < 4:
            rest = ""
        p["body"] = rest
        if not rest:
            p["figures"] = []


def fold_caption_header_into_figure(parsed: list) -> None:
    """olmOCR-2 files a plate's caption line in HEADER: (it is the most
    heading-shaped text on a plate page). Steps 3/4 then draw it as the
    running head while the figure caption renders separately -- the caption
    ends up split, and it is a section header on some plates but not
    others. On a page that carries a figure and little/no body, move a
    caption-shaped HEADER into the figure caption and clear HEADER, so the
    plate page has no running head and the caption renders as one unit.

    A "Planche N." / "Fig. N." header is never a running head either way
    -- it reads as a caption, so it belongs in body text (review
    feedback, 2026-09-18: put plate numbers in body, "it reads like a
    caption so therefore body text"). When the page has NO flagged figure
    at all (the VLM missed the illustration -- Gomard "Planche V./VI.":
    HEADER carries the plate number, body is just a plain description
    with no FIGURES: block), there's no figure caption to fold it into --
    prepend it to body instead of leaving it stuck as a header."""
    for p in parsed:
        head = (p.get("header") or "").replace("\t", " — ").strip().rstrip(".")
        figs = p.get("figures") or []
        if not head or not _CAPTION_SHAPE_RE.match(head):
            continue
        if not figs:
            if len((p.get("body") or "").split()) > 40:
                continue
            p["body"] = ((p.get("header") or "").strip() + "\n\n"
                         + (p.get("body") or "")).rstrip()
            p["header"] = ""
            continue
        if len((p.get("body") or "").split()) > 25:
            continue
        f0 = figs[0]
        cap = (f0.get("caption") or "").strip().rstrip(".")
        nh, nc = _norm_head(head), _norm_head(cap)
        if not cap or nc in nh:
            f0["caption"] = head              # header is the fuller line
        elif nh not in nc:
            f0["caption"] = f"{head} — {cap}"  # genuinely different -> join
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
        # ...but not a text page with figures set in it: its body opens
        # mid-sentence in lowercase, continuing the page before (Spinnewyn
        # scans 57 / 101 -- folded whole into a "caption", which then lost
        # the page its folio and turned its running head into a title).
        # Every real plate page opens "Planche N." / "Fig. N." / a caption.
        if (not figs and len(words) <= 45 and _MISSED_PLATE_RE.search(body)
                and not re.match(r"\s*[a-zà-ÿ]", body)):
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
                    # a neighbour that is itself a plate owns its caption
                    # (Gomard Planche 1re got Planche II's "Troisième
                    # Position, ou la garde.")
                    if j != idx and (parsed[j].get("figures") or re.match(
                            r"(?i)\s*(?:planche|pl\.|fig)", (parsed[j].get("header") or "") + " " + nb_body)):
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


def _fix_case_preserving(word):
    def _repl(m):
        s = m.group(0)
        if s.isupper():
            return word.upper()
        if s[:1].isupper():
            return word.capitalize()
        return word
    return _repl


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
    # 'Planche' (plate) misread as 'Plonche' (A/O confusion) -- not a
    # real French word, so the translator treats it as one anyway and
    # renders it "Plunge" (as if related to "plonger", to dive) instead
    # of "Plate" (Gomard pdf 379: "Plonche XIX." -> "Plunge XIX."). Every
    # other "Planche N." header in the same book reads and translates
    # correctly; this is a one-leaf OCR miss, not a systemic misread.
    (re.compile(r"(?i)\bplonche\b"), _fix_case_preserving("planche")),
    # glossary headwords, now kept in the original in the English (rule
    # 7b), so a misread shows; each checked against the scan, 2026-10-13.
    # 'Rompre' read 'Rompire' in two books (Kirchhoffer pdf 40,
    # Prévost-Jollivet pdf 318, both a bold or italic headword).
    (re.compile(r"(?i)\brompire\b"), _fix_case_preserving("rompre")),
    # Gomard pdf 112: "S'écraser, se dit d'un tireur ..."
    (re.compile(r"(?i)\bécraiser\b"), _fix_case_preserving("écraser")),
    # Masiello 1887 pdf 737: "Frase, è lo sviluppo alternativo ..." (italic
    # s read as v); only as an entry's headword
    (re.compile(r"(?m)^([ \t]*)Frave(?=,)"), lambda m: m.group(1) + "Frase"),
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


_IMAGE_DESCRIPTION_RE = re.compile(
    r"\s*\[(?:the |this )?(?:page|image|illustration|picture|photo(?:graph)?|drawing)\b[^\]]{15,}\]\s*",
    re.I)


def drop_image_descriptions(parsed: list) -> None:
    """The OCR model sometimes describes a full-page picture instead of
    transcribing ("[The page contains a black and white illustration of a
    person in fencing attire …]", Spinnewyn scan 156). That's not the
    book's text: the page becomes a plate with no caption."""
    for p in parsed:
        body = p.get("body") or ""
        if body.strip() and _IMAGE_DESCRIPTION_RE.fullmatch(body):
            p["body"] = ""
            if not p.get("figures"):
                p["figures"] = [{"kind": "plate", "caption": ""}]


_GREEK = {"alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
          "varepsilon": "ε", "theta": "θ", "lambda": "λ", "mu": "μ", "nu": "ν", "pi": "π",
          "rho": "ρ", "sigma": "σ", "tau": "τ", "phi": "φ", "varphi": "φ", "omega": "ω",
          "Delta": "Δ", "Omega": "Ω", "Sigma": "Σ", "Pi": "Π", "Phi": "Φ"}
_MATH_WORDS = {**_GREEK, "cdot": "·", "times": "×", "prime": "′", "pm": "±",
               "approx": "≈", "neq": "≠", "leq": "≤", "geq": "≥"}
_SUB = str.maketrans("0123456789+-=()aeoxhklmnpstijruv", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓₕₖₗₘₙₚₛₜᵢⱼᵣᵤᵥ")
_SUP = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")


def _plain_math(expr: str):
    """One LaTeX formula as plain printed text, or None if it can't be
    rendered faithfully in plain text."""
    e = expr.strip()
    e = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"\1/\2", e)
    e = re.sub(r"\\sqrt\{([^{}]+)\}", r"√\1", e)
    e = re.sub(r"\\([A-Za-z]+)", lambda m: _MATH_WORDS.get(m.group(1), m.group(0)), e)
    e = re.sub(r"\\[ ,;:!]", " ", e)                     # LaTeX spacing
    e = e.replace("{}", "")
    e = re.sub(r"_\{([0-9+\-=()aeoxhklmnpstijruv]+)\}|_([0-9aeoxhklmnpstijruv])", lambda m: (m.group(1) or m.group(2)).translate(_SUB), e)
    e = re.sub(r"\^\{([0-9+\-=()n]+)\}|\^([0-9n])", lambda m: (m.group(1) or m.group(2)).translate(_SUP), e)
    e = re.sub(r"\s+", " ", e).strip()
    return None if re.search(r"[\\{}_^]", e) else e


def plain_math(parsed: list) -> None:
    """The OCR model writes letters and symbols set like a formula in LaTeX:
    "\\( PF \\)" for the lever arm PF, "\\( \\omega'_2 \\)" (Masiello's
    mechanics, ~245 places in his three books). Rewritten as plain printed
    text -- PF, ω′₂, V² -- so the translation and the PDFs carry what the
    book prints. A formula that has no faithful plain form is left as it
    is (the review report lists it)."""
    pat = re.compile(r"\\\((.+?)\\\)|\\\[(.+?)\\\]", re.S)
    for p in parsed:
        for key in ("body",):
            t = p.get(key) or ""
            if "\\(" in t or "\\[" in t:
                p[key] = pat.sub(lambda m: _plain_math(m.group(1) or m.group(2)) or m.group(0), t)
