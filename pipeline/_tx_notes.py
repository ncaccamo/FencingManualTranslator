# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Footnotes in step 2's English: the model's <fn> source references become
numbered translator's notes (†N), and the book's own footnotes move into the
page's note block."""

import re

from _tx_common import FN_HEADER, _LANG_REF, _strip_accents
from _tx_terms import _footnote_ref, _footnoted
from _glossary_fr import _FOOTNOTED_FR_REFS
from _glossary_it import _FOOTNOTED_IT_REFS


FN_MARK = "†"          # dagger, used as the footnote reference mark

FN_MAX_PER_PAGE = 4


def pull_footnotes(text: str, source_text: str = "", seen: set = None, lang: str = "French"):
    """Turn the model's inline `phrase<fn>note</fn>` tags into `†N` markers
    plus a trailing ___FOOTNOTES___ block (the header can't collide with
    the page separator). A note whose text was already used earlier in the
    book -- tracked in `seen` -- is dropped to its bare phrase, so a
    footnoted-term source reference like "Fr. liement" appears once per book.
    `source_text` is unused; kept for call-site compatibility.
    Returns (text, n_notes)."""
    if seen is None:
        seen = set()
    numbered = []

    _footnoted_refs = {t for t, _ in _footnoted(lang=lang)} | (_FOOTNOTED_IT_REFS if lang == "Italian" else _FOOTNOTED_FR_REFS)
    ab, label = _LANG_REF.get(lang, _LANG_REF["French"])

    def take(m):
        note = re.sub(r"</?fn>", "", re.sub(r"\s+", " ", m.group(1))).strip()
        # accept only a bare source reference ("Fr. liement" / "French:
        # liement", "It. legamento") for a footnoted term -- the model
        # sometimes footnotes a kept term or puts the English in the note.
        # "French in the original" after a quotation the model translated
        # from a third language (prompt rule 0): kept on every quotation
        q = re.fullmatch(r"(French|Italian|Latin|Spanish|German|Greek|English) in the original\.?", note, re.I)
        # never the book's own language: a French quotation in a French book
        # is just the text (LaMarche scan 56 got three such notes)
        if q and q.group(1).lower() == lang.lower():
            return ""
        if q:
            if len(numbered) >= FN_MAX_PER_PAGE:
                return ""
            numbered.append(f"{q.group(1).capitalize()} in the original")
            return f"{FN_MARK}{len(numbered)}"
        ref = re.sub(rf"^(?:{ab}\.|{label.lower()}:)\s*", "", note, flags=re.I).lower().rstrip(".").replace("\u2019", "'")
        ref = _footnote_ref(lang=lang).get(ref, ref)  # "vous loger" -> "se loger"
        if not note or ref not in _footnoted_refs:
            return ""                       # drop the tag, keep the phrase
        note = f"{label}: {ref}"            # canonical rendered form
        key = _strip_accents(ref)
        if key in seen or len(numbered) >= FN_MAX_PER_PAGE:
            return ""
        seen.add(key)
        numbered.append(note)
        return f"{FN_MARK}{len(numbered)}"

    body = re.sub(r"<fn>(.*?)</fn>", take, text,
                  flags=re.DOTALL | re.IGNORECASE).strip()
    body = re.sub(r"</?fn>", "", body)       # sweep any stray/dangling tag
    if not numbered:
        return body, 0
    lines = [f"{FN_MARK}{i} {n}" for i, n in enumerate(numbered, 1)]
    return f"{body}\n\n{FN_HEADER}\n" + "\n".join(lines), len(lines)


# NOTE: an earlier "glossary mode" kept every headword of a "termes
# techniques" section in the source language (italic) and translated only
# the definition. Dropped 2026-09-01: leaving the term untranslated left
# the reader without the English word the body text uses for it. A
# glossary section now goes through the normal term policy like body text
# (the facing page still carries the original). See docs/model-quirks.md.


def extract_source_footnotes(text: str, state: dict) -> str:
    """Pull the book's own numbered footnotes ("(1) L'origine de ...") out
    of the body and into the ___FOOTNOTES___ block, so a reader can tell
    body from note. A footnote that runs off the bottom of a page (no
    terminal punctuation) sets state['open']; the next page's leading
    lower-case paragraph is then taken as its continuation and rendered in
    that page's note strip, prefixed '…'."""
    parts = text.split("\n" + FN_HEADER + "\n", 1)
    body = parts[0]
    existing = parts[1] if len(parts) > 1 else ""
    paras = re.split(r"\n\s*\n", body.strip())
    keep, notes = [], []

    _NOTE_START = re.compile(r"^\(?(\d{1,2})[.)]\s+\S")

    def _leads_lowercase(s):
        # The first actual letter, skipping a leading <i>/<b>-style tag --
        # a continuation can open on a kept term ("<i>riposte</i>, but…"),
        # and "<" isn't a letter so a bare [:1].islower() check misses it
        # (Gomard p220/221 footnote stitch).
        m = re.match(r"(?:<[^>]+>)*(\S)", s)
        return bool(m) and m.group(1).islower()

    if state.get("open") and paras:
        if _leads_lowercase(paras[0]) and not _NOTE_START.match(paras[0].strip()):
            notes.append("… " + re.sub(r"\s+", " ", paras.pop(0)).strip())
            state["open"] = False
        elif (len(paras) > 1 and _leads_lowercase(paras[-1])
              and not _NOTE_START.match(paras[-1].strip())):
            # The continuation isn't always the very next thing OCR
            # reads -- it can be deferred to the FOOT of this page,
            # after the page's own unrelated new body text (P-review,
            # Gomard p220/221: footnote (1) on "tac au tac" truncates on
            # p220, and its remainder lands as the LAST paragraph of
            # p221, below several paragraphs of p221's own body).
            notes.append("… " + re.sub(r"\s+", " ", paras.pop()).strip())
            state["open"] = False

    # A bare "1. .. 2. .." run at the FOOT of the page is the book's own
    # footnote block (Prévost sets notes this way, not "(1)"). Guard
    # against a numbered drill/procedure: not when the first body
    # paragraph is itself numbered, not when the line above the run ends
    # with ':', require the run sequential from 1, and either >=2 notes or
    # a single substantial (>=12-word) one.
    _tail = len(paras)
    while _tail > 0 and _NOTE_START.match(paras[_tail - 1].strip()):
        _tail -= 1
    _tnums = [int(_NOTE_START.match(paras[k].strip()).group(1))
              for k in range(_tail, len(paras))]
    _treat_tail = (
        0 < _tail < len(paras)
        and not _NOTE_START.match(paras[0].strip())
        and not paras[_tail - 1].rstrip().endswith(":")
        and _tnums == list(range(1, len(_tnums) + 1))
        and (len(_tnums) >= 2 or len(paras[-1].split()) >= 12))

    for _idx, p in enumerate(paras):
        s = re.sub(r"\s+", " ", p).strip()
        _is_tail_note = _treat_tail and _idx >= _tail
        if _is_tail_note or re.match(r"\(\d{1,2}\)[ . ]", s):
            notes.append(s)
            state["open"] = not re.search(r"[.!?»”\"')\]]\s*$", s)
        else:
            keep.append(p)

    if not notes:
        return text
    block = "\n".join(notes)
    if existing.strip():
        block = existing.rstrip() + "\n" + block
    new_body = "\n\n".join(keep).strip()
    return f"{new_body}\n\n{FN_HEADER}\n{block}".strip()
