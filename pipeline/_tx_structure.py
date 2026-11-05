# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Page structure in step 2's English: section dividers and table markers put
back where the source has them, sub-heading spacing, definition entries, a
dropped part title, punctuation at the page's edges, caption clean-up."""

import re

from _tx_case import match_case
from _tx_common import FN_HEADER


# A section divider printed between paragraphs -- stars, an asterism, a row
# of bullets or a tilde rule (Joseph-Renaud's ⁂, LaMarche's * * *). The OCR
# writes it as its own line on many pages; the translation dropped it (the
# model, and a clean-up that took it for Markdown). Reviewer, 2026-10-18:
# keep a divider, drawn centred with space around it (_render).
DIVIDER = "[[DIVIDER]]"
_DIVIDER_SRC = re.compile(r"^\s*(?:[*★☆✶✱•·~]\s*){2,}\s*$|^\s*[⁂]\s*$|^\s*~{3,}\s*$")


def restore_dividers(text: str, src: str) -> str:
    """Put each divider of the source back as a [[DIVIDER]] line, before
    the English line that has as many lines above it as the source line
    after the divider has -- only when the English has exactly the source's
    lines (else nothing: a divider in the wrong place is worse than none)."""
    if not src or not text:
        return text
    src_lines = [l for l in re.sub(r"<[^>]+>", "", src).split("\n") if l.strip()]
    marks = [k for k, l in enumerate(src_lines) if _DIVIDER_SRC.match(l)]
    if not marks:
        return text
    body, sep, notes = text.partition(FN_HEADER)
    en = body.split("\n")
    en_idx = [n for n, l in enumerate(en) if l.strip() and l.strip() != DIVIDER]
    words = [l for l in src_lines if not _DIVIDER_SRC.match(l)]
    if len(en_idx) != len(words) or DIVIDER in body:
        return text
    for j in sorted(marks, reverse=True):
        before = sum(1 for k in range(j) if not _DIVIDER_SRC.match(src_lines[k]))
        if before == 0 or before >= len(en_idx):
            continue
        at = en_idx[before]
        en[at:at] = ["", DIVIDER, ""]
    return "\n".join(en) + sep + notes


# Margin (shoulder) notes: no text heuristic here any more. The old
# label_margin_notes() guessed that a short, sentence-ending paragraph near the
# end of a page was a margin note and deleted it from the page (2026-09-18
# review decision). Guessing from text shape can't tell a note from a
# sub-heading, a signature or a closing sentence: Qwen3.8's paragraphing
# made it delete Prévost's "Le rôle du médecin." heading outright. Removed
# 2026-09-25 -- the pipeline must never silently lose source text. Proper
# classification belongs to the layout detector, which sees where a block
# sits on the page (docs/STATUS.md).


def _looks_like_subheading(s: str) -> bool:
    s = s.strip()
    if re.search(r"[.!?:;,]$", s):
        return False
    words = s.split()
    letters = [c for c in s if c.isalpha()]
    return (1 <= len(words) <= 8 and len(s) <= 55 and len(letters) >= 3
            and sum(c.isupper() for c in letters) >= 0.85 * len(letters))


def fix_subheading_spacing(text: str) -> str:
    """A run-in sub-heading ("LES GRANDS ESCRIMEURS") often comes back
    jammed against the paragraph above it with a blank line *below* — the
    reverse of how it should read. Move the blank: gap before the
    sub-heading, tight to the paragraph it introduces.

    NOTE: a stop-gap. Superseded once the heading-hierarchy feature lands
    (docs/STATUS.md, "Heading hierarchy") -- that will space headings from a real
    signal instead of this all-caps heuristic. Guarded tight (line above
    ends a sentence, line below opens one) so a mis-fire can't glue two
    real paragraphs together."""
    lines = text.split("\n")
    out, i, n = [], 0, len(lines)
    while i < n:
        cur = lines[i]
        if (out and out[-1].strip()
                and re.search(r"[.!?»”\"')\]]\s*$", out[-1])
                and _looks_like_subheading(cur)
                and i + 2 < n and not lines[i + 1].strip()
                and lines[i + 2].strip() and lines[i + 2].lstrip()[:1].isupper()):
            out += ["", cur]        # blank before; skip the blank after
            i += 2
            continue
        out.append(cur)
        i += 1
    return "\n".join(out)


_DEF_ENTRY_RE = re.compile(
    r"(?m)^([ \t]*)(<i>)?([a-zà-ÿ])([\wà-ÿ’'\- ]{0,40}?)(</i>)?([ \t]*\.[ \t]*[—–-][ \t])")


def _capitalize_definition_entries(text: str) -> str:
    """A glossary / definition-list entry headword is capitalised in the
    source ("Riposte. — ...", "Coup d'arrêt. — ..."). A kept term
    kept lowercase-italic loses that capital ("<i>riposte</i>. — ...",
    Kirchhoffer TERMES TECHNIQUES). Restore it: upper-case the first
    letter of any line shaped "<short headword>. — <definition>",
    preserving an <i> wrapper."""
    def _up(m):
        head = m.group(4) or ""
        if len(head.split()) > 3:
            return m.group(0)
        return (m.group(1) + (m.group(2) or "") + m.group(3).upper()
                + head + (m.group(5) or "") + m.group(6))
    return _DEF_ENTRY_RE.sub(_up, text)


_CAPTION_JUNK = re.compile(
    r"biblioth[eè]que\s+nationale|british\s+museum|library\s+of\s+congress"
    r"|\b\d{5,}\b|high\s+holborn|\blondres\b|\bimp\.\b|\blith\.\b|\bdel\.\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}\s+\d{4}\b",
    re.I)


def _clean_caption(cap: str) -> str:
    """Strip Markdown image refs, hallucinated scene descriptions and
    library/printer stamps from a raw figure caption."""
    if not cap:
        return cap
    cap = re.sub(r"!?\[[^\]]*\]\([^)]*\)", "", cap)          # ![alt](url)
    cap = re.sub(r"\((?:two|a|an|the)\s+fencers?\b[^)]*\)", "", cap, flags=re.I)
    # hallucinated scene description ("Image of a hand holding a sword in
    # the sixth position", "Photograph of a man in fencing attire ...") --
    # drop any such segment, keep a real caption line that follows it.
    _SCENE = (r"(?:an?\s+)?(?:image|photo(?:graph)?|drawing|illustration|picture"
              r"|sketch|depiction|engraving|figure)\s+(?:of|showing|depicting)\b")
    cap = " ".join(
        s for s in re.split(r"(?<=[.!?])\s+|\n+", cap)
        if not re.match(r"(?i)\s*" + _SCENE, s)).strip()
    cap = re.sub(r"(?i)^\s*" + _SCENE + r"[^.\n]*", "", cap).strip()
    cap = re.sub(r"(?i)\bcorrect(?:ing|ed)?\b[^.:]*\btranslation\b[^.:]*[.:]", "", cap)
    cap = re.sub(r"(?i)\bbased on the (?:given|critical) rules?\b[^.]*\.?", "", cap)
    # model commentary leaking into a caption: a "(NOTE: ...)" aside (often
    # long, sometimes unclosed) and the "GIVEN THE ... RULES, ... THUS:
    # <restated line>" tail it appends (Kirchhoffer Fig. 7).
    cap = re.sub(r"(?is)\s*[\(\[]\s*note\s*:.*?(?:[\)\]]|$)", "", cap)
    cap = re.sub(r"(?is)\s*\b(?:given the (?:critical|provided|above) rules?"
                 r"|there (?:appears|seems) to be an error"
                 r"|strictly translating the provided text)\b.*$", "", cap)
    cap = re.sub(r"(?is)\s*,?\s*\bthus\s*:\s*$", "", cap)
    cap = re.sub(r"^\s*[—–\-]\s*", "", cap.strip())
    # a trailing empty-caption placeholder the VLM tacked on ("... (La
    # Garde) none", "... au tac none")
    cap = re.sub(r"(?i)[\s—–-]*\b(?:none|no\s+caption|n/?a)\s*\.?\s*$", "", cap).strip()
    cap = re.sub(r"\s{2,}", " ", cap).strip(" —–-")
    if _CAPTION_JUNK.search(cap):
        return ""
    return cap.strip()


_PART_ORDINALS_EN = {
    "première": "First", "premiere": "First",
    "deuxième": "Second", "deuxieme": "Second", "seconde": "Second",
    "troisième": "Third", "troisieme": "Third",
    "quatrième": "Fourth", "quatrieme": "Fourth",
    "cinquième": "Fifth", "cinquieme": "Fifth",
    "sixième": "Sixth", "sixieme": "Sixth",
    "septième": "Seventh", "septieme": "Seventh",
    "huitième": "Eighth", "huitieme": "Eighth",
    "neuvième": "Ninth", "neuvieme": "Ninth",
    "dixième": "Tenth", "dixieme": "Tenth",
}

_PART_TITLE_RE = re.compile(
    r"(?im)^\s*(" + "|".join(_PART_ORDINALS_EN) + r")\s+partie\s*$")


def restore_dropped_part_title(candidate: str, source: str) -> str:
    """A "<Ordinal> PARTIE" division title as the body's own first line
    (Prévost: PREMIÈRE/DEUXIÈME/QUATRIÈME/CINQUIÈME/SIXIÈME PARTIE, each
    confirmed dropped from the translated output on every single
    occurrence in the book -- 5/5 -- while the very next title line on
    the same page translates fine) gets silently omitted by the
    translation model, treating it like a structural marker rather than
    body text to translate. Narrow, evidence-based fix (like
    _OCR_PHRASE_FIXES in step 1): if the source's first line is exactly
    this shape and the candidate's own first line doesn't already
    contain "part", prepend the correct translation instead of trying to
    re-run the whole page."""
    m = _PART_TITLE_RE.match((source or "").lstrip("\n").split("\n", 1)[0])
    if not m:
        return candidate
    first_out = (candidate or "").lstrip("\n").split("\n", 1)[0]
    if re.search(r"(?i)\bpart\b", first_out):
        return candidate
    ordinal_en = _PART_ORDINALS_EN[m.group(1).lower()]
    title = m.group(0).strip()
    en_title = match_case(title, f"{ordinal_en} Part")
    return f"{en_title}\n\n{(candidate or '').lstrip(chr(10))}"


_SENT_END = tuple('.!?:;»)"”’…—')


def match_boundary_punctuation(dst: str, src: str) -> str:
    """A page is one leaf of a longer work; it can begin or end mid-sentence.
    The model tends to "finish" a dangling sentence with a full stop (and
    the next page then opens with what looks like a fragment). If the
    SOURCE page doesn't end on sentence-ending punctuation, strip whatever
    terminal punctuation the model tacked on -- and add NOTHING; the page
    just ends on its last word and continues on the next. If the source
    page STARTS lower-case (a continuation) but the translation capitalised
    its first word, lower-case it back."""
    if not dst or not src:
        return dst
    s = src.rstrip()
    d = dst.rstrip()
    tail = dst[len(d):]
    # a trailing hyphen / en-dash at a page foot is hyphenation the
    # cross-page de-hyphenation couldn't join (next page starts with a
    # capital) -- never terminal punctuation, so strip it too.
    if s and (s[-1] in "-–"
              or (s[-1] not in _SENT_END
                  and not re.search(r'\b(?:etc|cf|ibid|op)\.$', s))):
        d2 = re.sub(r'[\s.…—–-]+$', '', d)
        if d2:
            d = d2
    dst = d + tail
    s0 = src.lstrip()
    if s0[:1].islower():
        dst = re.sub(r'^(\s*)([A-Z])(?=[a-zà-ÿ])',
                     lambda m: m.group(1) + m.group(2).lower(), dst, count=1)
    return dst


def restore_table_markers(text: str, src: str) -> str:
    """Put back each "[[TABLE]]" the translation dropped, after as many
    paragraphs as precede it in the source (a table that opens the page goes
    first, before its footnotes). Paragraphs are blank-line separated; the
    model keeps them one for one closely enough for a table's place."""
    paras = [p for p in re.split(r"\n\s*\n", src.strip()) if p.strip()]
    want, before = [], 0
    for p in paras:
        if p.strip() == "[[TABLE]]":
            want.append(before)
        else:
            before += 1
    missing = len(want) - text.count("[[TABLE]]")
    if missing <= 0:
        return text
    out = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    # each marker the model kept answers the source marker nearest its place
    left, n_text = list(want), 0
    for p in out:
        if p.strip() == "[[TABLE]]":
            if left:
                left.remove(min(left, key=lambda w: abs(w - n_text)))
        else:
            n_text += 1
    for pos in sorted(left, reverse=True):
        n_text = 0
        at = len(out)
        for k, p in enumerate(out):
            if n_text == pos:
                at = k
                break
            if p.strip() != "[[TABLE]]":
                n_text += 1
        out.insert(at, "[[TABLE]]")
    return "\n\n".join(out)
