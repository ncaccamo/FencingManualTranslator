#!/usr/bin/env python3
"""Translation with VLM output cleaning"""

import glob
import os
import time
import re
import requests
import subprocess
import sys
from pathlib import Path

import backend_config as bc
import _pageio

PROMPT_FILE = "/app/translation_prompt.txt"
TRANSLATION_MODEL = os.environ.get("TRANSLATION_MODEL_PATH", "/models/Qwen2.5-14B-Instruct-Q4_K_M.gguf")
# Request "model" label. Ignored by a single-model llama-server (local /
# simple remote); the routing key under llama-swap. Kept in sync with the
# actual model via env, falling back to the model file's own name.
TRANSLATION_MODEL_NAME = (os.environ.get("TRANSLATION_MODEL_NAME")
                          or os.environ.get("MODEL_NAME")
                          or Path(TRANSLATION_MODEL).stem)
LLAMA_API_URL = f"{bc.translate_base_url()}/v1/chat/completions"

# Returned by start_translation_server() when BACKEND_MODE=remote.
REMOTE_SERVER = object()

if not os.path.exists(PROMPT_FILE):
    print(f"\n✗ ERROR: Missing {PROMPT_FILE}\n")
    exit(1)

with open(PROMPT_FILE, "r", encoding="utf-8") as f:
    PROMPT_TEMPLATE = f.read().strip()

FRENCH_WORDS = {
    "le", "la", "les", "un", "une", "des", "du", "de", "d", "et", "est", "sont", "était", "étaient",
    "dans", "sur", "sous", "par", "pour", "avec", "sans", "vers", "chez", "entre", "depuis",
    "au", "aux", "en", "il", "ils", "elle", "elles", "on", "je", "tu", "nous", "vous",
    "ne", "n", "pas", "plus", "point", "rien", "jamais", "mais", "ou", "où", "si", "donc",
    "que", "qu", "qui", "quoi", "dont", "quel", "quelle", "quels", "quelles",
    "ce", "cet", "cette", "ces", "cela", "ceci", "celui", "celle", "ceux",
    "son", "sa", "ses", "leur", "leurs", "mon", "ma", "mes", "ton", "ta", "tes", "notre", "votre",
    "à", "se", "s", "lui", "moi", "toi", "soi", "l", "c", "j", "m", "t",
    "être", "avoir", "fait", "faire", "faites", "était", "être", "sera", "ont", "avait", "avaient",
    "tout", "tous", "toute", "toutes", "bien", "très", "aussi", "ainsi", "alors", "comme",
    "quand", "lorsque", "puisque", "parce", "afin", "selon", "malgré", "cependant", "toutefois",
    "encore", "déjà", "toujours", "peut", "doit", "peu", "trop", "assez",
}
ITALIAN_WORDS = {"il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "del", "della", "di", "e", "è", "sono", "in", "su", "per", "con", "da", "a", "che", "questo", "questa", "quello", "quella", "mio", "tuo", "suo"}
GERMAN_WORDS = {"der", "die", "das", "ein", "eine", "und", "ist", "sind", "in", "im", "auf", "zu", "von", "mit", "den", "dem", "des", "als", "wie", "dass", "aus", "nach", "bei", "an", "vor", "hinter", "über"}
SPANISH_WORDS = {"el", "la", "los", "las", "un", "una", "y", "es", "son", "en", "de", "del", "por", "para", "con", "al", "que", "este", "esta", "ese", "esa", "mi", "tu", "su", "nuestro", "vuestro"}
LATIN_WORDS = {"et", "in", "ad", "ab", "cum", "ex", "per", "pro", "sub", "super", "non", "est", "sunt", "ut", "si", "aut", "vel", "sed", "atque", "neque", "quod", "qui", "quo", "quam", "ubi", "unde"}

NON_ENGLISH_INDICATORS = FRENCH_WORDS | ITALIAN_WORDS | GERMAN_WORDS | SPANISH_WORDS | LATIN_WORDS

def clean_vlm_extraction(text: str) -> str:
    """Extract only the body text from VLM structured output"""
    # Try to extract just the BODY_TEXT section
    body_match = re.search(r'BODY_TEXT:\s*(.+)', text, re.IGNORECASE | re.DOTALL)
    if body_match:
        return body_match.group(1).strip()
    
    # If no BODY_TEXT marker, try to remove common VLM labels
    cleaned = re.sub(r'PAGE_NUMBER:\s*\S+', '', text, flags=re.IGNORECASE)
    cleaned = re.sub(r'HEADER:\s*[^\n]+', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'BODY_TEXT:', '', cleaned, flags=re.IGNORECASE)
    
    return cleaned.strip()

FN_MARK = "†"          # dagger, used as the footnote reference mark
FN_HEADER = "___FOOTNOTES___"
FN_MAX_PER_PAGE = 4


def _strip_accents(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


# Category-3 terms (see translation_prompt.txt): translate to the English
# rendering, and on the FIRST occurrence in a book mark it as
# "English<fn>Fr. <term></fn>" -- a bare source reference, never an
# explanatory gloss. The model does this from the prompt;
# enforce_cat3_footnotes() is the backstop for when it leaves the term in
# French or forgets the note. Plural before singular so the longer pattern
# wins.
_CAT3 = [
    ("prise de fer",   "taking the blade"),
    ("rassemblements", "gatherings of the feet"), ("rassemblement", "gathering of the feet"),
    ("rassemblant",    "gathering the feet"),     ("rassembler",   "to gather the feet"),
    ("dérobements",    "evasions of the blade"),   ("dérobement",   "evasion of the blade"),
    ("dégagements",    "disengagements"),          ("dégagement",   "disengagement"),
    ("liements",       "binds"),                   ("liement",      "bind"),
    ("froissements",   "grazes"),                  ("froissement",  "graze"),
    ("coup de temps",  "time-hit"),
    ("coupés",         "cut-overs"),               ("coupé",        "cut-over"),
    ("coulés",         "glides"), ("coulé", "glide"), ("déroulé", "glide"),
    ("à-propos",       "sense of timing"),
    ("appels",         "stamps of the foot"),      ("appel",        "stamp of the foot"),
    ("caver",          "to drop the point"),
]

# canonical singular French for the "Fr. <term>" note, so a plural form
# ("appels") doesn't produce a second note distinct from "Fr. appel".
_CAT3_NOTE = {
    "rassemblements": "rassemblement", "rassemblant": "rassemblement",
    "rassembler": "rassemblement", "dérobements": "dérobement",
    "dégagements": "dégagement", "liements": "liement",
    "froissements": "froissement", "coupés": "coupé",
    "coulés": "coulé", "déroulé": "coulé", "appels": "appel",
}


def enforce_cat3_footnotes(text: str, seen: set, footnote: bool = True) -> str:
    """Backstop for Category-3 terms. Translate EVERY occurrence the model
    left in French (bare or italicised) to its English rendering, and on
    the FIRST one in a book append "<fn>Fr. <term></fn>". Case of the
    surrounding token is preserved. `<fn>...</fn>` spans already in the
    text (the model's own source refs) are masked so a French term inside
    one is never re-wrapped."""
    if not text:
        return text
    masked = []

    def _mask(m):
        masked.append(m.group(0))
        return f"\x00{len(masked) - 1}\x00"

    text = re.sub(r"<fn>.*?</fn>", _mask, text, flags=re.DOTALL | re.IGNORECASE)

    for fr, en in _CAT3:
        pat = re.compile(r"(?:<i>\s*)?(?<![\w-])" + re.escape(fr)
                         + r"(?![\w-])(?:\s*</i>)?", re.I)
        first = [True]
        note_fr = _CAT3_NOTE.get(fr, fr)

        def _sub(m, en=en, note_fr=note_fr, first=first):
            g = m.group(0)
            r = (en.upper() if g.isupper()
                 else en.capitalize() if g[:1].isupper() else en)
            if footnote and first[0] and note_fr not in seen:
                # mask the tag we add too, so a later _CAT3 pass can't
                # match the French term sitting inside it
                masked.append(f"<fn>Fr. {note_fr}</fn>")
                r += f"\x00{len(masked) - 1}\x00"
                seen.add(note_fr)
            first[0] = False
            return r

        text = pat.sub(_sub, text)

    text = re.sub(r"\x00(\d+)\x00", lambda m: masked[int(m.group(1))], text)
    return text


def pull_footnotes(text: str, source_text: str = "", seen: set = None):
    """Turn the model's inline `phrase<fn>note</fn>` tags into `†N` markers
    plus a trailing ___FOOTNOTES___ block (the header can't collide with
    the page separator). A note whose text was already used earlier in the
    book -- tracked in `seen` -- is dropped to its bare phrase, so a
    Category-3 source reference like "Fr. liement" appears once per book.
    `source_text` is unused; kept for call-site compatibility.
    Returns (text, n_notes)."""
    if seen is None:
        seen = set()
    numbered = []

    _cat3_fr = {t for t, _ in _CAT3}

    def take(m):
        note = re.sub(r"</?fn>", "", re.sub(r"\s+", " ", m.group(1))).strip()
        # accept only a bare "Fr. <cat-3 French term>" -- the 14B sometimes
        # footnotes a Cat-2 term or puts the English rendering in the note.
        ref = re.sub(r"^Fr\.\s*", "", note, flags=re.I).lower().rstrip(".")
        if not note or ref not in _cat3_fr:
            return ""                       # drop the tag, keep the phrase
        key = _strip_accents(note.lower())
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


# Rule-3 "always translate" terms the 7B drops inconsistently (leaves
# "an arm called a fleuret" in the body, "ENTRAINEMENT" as a header).
# Only unambiguous ones -- these have no other meaning in this corpus.
# Longest first so "fleurettiste" wins before "fleuret".
_ALWAYS_TRANSLATE = [
    (r"fleurettistes", "foil fencers"), (r"fleurettiste", "foil fencer"),
    (r"fleurets", "foils"), (r"fleuret", "foil"),
    # "contre-" as a compound prefix = "counter-", not "against-"
    (r"contre-dégagements", "counter-disengagements"),
    (r"contre-dégagement", "counter-disengagement"),
    (r"contre-dégagez", "counter-disengage"), (r"contre-dégager", "counter-disengage"),
    # equipment / material vocab the model hedges on and leaves in French
    (r"crispins", "cuffs"), (r"crispin", "cuff"),
    (r"cuissards", "thigh guards"), (r"cuissard", "thigh guard"),
    (r"schémas", "diagrams"), (r"schéma", "diagram"),
    (r"entra[iî]nements", "training sessions"), (r"entra[iî]nement", "training"),
    (r"escrimes", "fencing"), (r"escrime", "fencing"),
    (r"sociétés", "societies"), (r"société", "society"),
    (r"divers", "miscellaneous"),
    (r"demi-fentes", "half-lunges"), (r"demi-fente", "half-lunge"),
    (r"fentes", "lunges"), (r"fente", "lunge"),
    # "leçon d'assaut" = a free-play lesson; the 7B collapses it to just
    # "the bout" (drops "leçon"). Bare "assaut" -> "bout" is left to the
    # prompt (rule 3) -- "assaut" alone is sometimes "assault" in a duel.
    (r"leçons d['’]assaut", "bout lessons"), (r"leçon d['’]assaut", "bout lesson"),
    # "fer" (the opponent's steel) -> "the blade" (Category 1). NOT bare
    # "fer": leave "prise de fer" / "sentiment du fer" for their own
    # handlers (Cat 3 / Cat 2).
    (r"attaques au fer", "attacks on the blade"), (r"attaque au fer", "attack on the blade"),
    (r"sur le fer", "on the blade"), (r"au fer", "on the blade"),
    (r"le fer", "the blade"),
    # "l'escrime au fleuret" -> "foil fencing"; the 7B renders it
    # "épée fencing with the fleuret" / "épée fencing with the foil".
    (r"escrime au fleuret", "foil fencing"), (r"au fleuret", "with the foil"),
    (r"escrime à l['’]épée", "épée fencing"), (r"à l['’]épée", "with the épée"),
    # "au bout d'épée" -- idiom for a bout fenced with the épée, not a
    # literal "at the point of the sword"; the 7B renders it word-for-word.
    (r"au bout d['’]épée", "an épée bout"),
    # "un assaut d'épée" is just an épée bout -- there is no "leçon" in it,
    # but the 7B pulls "lesson" in from the leçon-d'assaut rule and writes
    # "a bout lesson with the épée". Repair the output.
    (r"a bout lesson with the épée", "an épée bout"),
    (r"bout lesson with the épée", "épée bout"),
]


def apply_always_translate(text: str) -> str:
    if not text:
        return text
    for pat, repl in _ALWAYS_TRANSLATE:
        def _sub(m, r=repl):
            g = m.group(0)
            return r.upper() if g.isupper() else r.capitalize() if g[:1].isupper() else r
        text = re.sub(r"\b" + pat + r"\b", _sub, text, flags=re.IGNORECASE)
    return _phrase_repairs(text)


# Free-standing repairs for known 7B phrasings that _ALWAYS_TRANSLATE's
# \b-anchored substitution can't reach (e.g. a phrase split by an <i> tag).
def _phrase_repairs(text: str) -> str:
    if not text:
        return text
    # "épée fencing with the foil/fleuret" (épée possibly tagged) -> foil fencing
    text = re.sub(r"(?:<i>)?épée(?:</i>)?\s+fencing with the (?:foil|fleuret)",
                  "foil fencing", text, flags=re.IGNORECASE)
    # "Nᵉ temps" -> "Nth tempo"  (leaves "coup de temps", "temps d'arrêt" alone)
    text = re.sub(
        r"\b(\d{1,2}(?:er|re|nd|e|ème)?\.?)\s+temps\b",
        lambda m: m.group(1) + (" TEMPO" if m.group(0).isupper() else " tempo"),
        text, flags=re.IGNORECASE)
    # model already wrote "against-disengage..." for "contre-dégage..."
    text = re.sub(r"\b([Aa])gainst-(?=(?:dis)?engag|dégag)",
                  lambda m: ("C" if m.group(1).isupper() else "c") + "ounter-", text)
    # "glacé fabric" / "neither varnished nor glacé" -> glazed
    text = re.sub(r"\bglac[ée]s?\b",
                  lambda m: "Glazed" if m.group(0)[:1].isupper() else "glazed", text)
    return text


# Category-2 "keep in the source language, italic" terms that the 7B
# drops the <i>...</i> off or mistranslates -- plate captions especially.
# Only the tight retained set; riposte / remise / reprise / the parry
# numbers the model handles on its own. Each entry: term -> English
# mistranslations to snap back to it.
_KEEP_TERMS = [
    ("corps à corps", [r"body[- ]to[- ]body", r"body contact"]),
    ("sentiment du fer", [r"feeling of (?:the )?(?:blade|iron|steel)"]),
    ("croisé", []),
    ("volte", []),
]

# A source-language token the 7B reliably garbles. rassemblant is now a
# Category-3 (translated) term, so snap the garble back to the BARE French
# word -- enforce_cat3_footnotes() then translates it and adds the note.
_MANGLE_PRONE = [(r"rassembl", "rassemblant")]


def repair_mangled_terms(dst: str, src: str) -> str:
    """When the SOURCE has a term the 7B is known to mangle and the output
    has lost it, swap the first quoted non-English token back to the term."""
    if not dst or not src:
        return dst
    for needle, term in _MANGLE_PRONE:
        if re.search(needle, src, re.I) and not re.search(needle, dst, re.I):
            dst = re.sub(
                r'([«"“(])\s*[A-Za-zÀ-ÿ]{5,}\s*([»"”)])',
                lambda m: f'{m.group(1)}{term}{m.group(2)}', dst, count=1)
    return dst


def enforce_kept_terms(text: str, src: str = "") -> str:
    """Make sure a Category-2 term is present and italicised: repair a
    known mistranslation, then wrap any bare occurrence in <i>...</i>
    (skipping what is already inside a tag). The term is lower-cased -- a
    technical term of art is set italic-lower even inside an all-caps
    caption."""
    if not text:
        return text
    text = repair_mangled_terms(text, src)
    for term, wrongs in _KEEP_TERMS:
        for w in wrongs:
            text = re.sub(r"(?<!<i>)\b" + w + r"\b(?!</i>)",
                          f"<i>{term}</i>", text, flags=re.IGNORECASE)
        # wrap a bare occurrence not already tagged
        text = re.sub(
            r"(?<![\w>])" + term.replace(" ", r"\s+") + r"(?![\w<])",
            lambda m: f"<i>{term}</i>", text, flags=re.IGNORECASE)
        # collapse any accidental double-wrap
        text = re.sub(f"<i>\\s*<i>{re.escape(term)}</i>\\s*</i>",
                      f"<i>{term}</i>", text, flags=re.IGNORECASE)
    return text


_TITLE_STOPWORDS = {
    "a", "an", "the", "and", "or", "nor", "but", "of", "to", "in", "on", "at",
    "by", "for", "with", "from", "as", "per", "via",
    # French particles, so "Parade de Sixte." still reads as Title Case
    "de", "du", "des", "la", "le", "les", "à", "au", "aux", "et", "ou",
    "d", "l", "en", "sur", "par", "dans",
}


def match_case(src: str, dst: str) -> str:
    """Make a translated heading / caption follow the CASE STYLE of its
    source line, faithfully -- a book that sets a running head in capitals
    gets an all-caps English head, a sentence-case caption gets a
    sentence-case English caption (even if the model shouted it).
      source ALL CAPS   -> dst.upper()  (incl. kept <i> terms)
      source Title Case -> Title Case (particles stay lower)
      anything else     -> sentence case: first letter up, the rest down
                           (acronyms present in the source are preserved)
    Words the model kept in the source language (<i>..</i>) keep their own
    case, EXCEPT under ALL CAPS where the whole line goes up together."""
    if not dst:
        return dst
    letters = [c for c in src if c.isalpha()]
    if not letters:
        return dst
    words = [w for w in re.findall(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", src)
             if any(c.isalpha() for c in w)]
    upper_ratio = sum(c.isupper() for c in letters) / len(letters)
    src_acronyms = {w.upper() for w in words if w.isupper() and len(w) >= 2}

    if upper_ratio >= 0.8 and len(letters) >= 2:
        # uppercase everything, then restore the lowercase markup tags
        return re.sub(r"</?(?:i|fn)>", lambda m: m.group(0).lower(),
                      dst.upper(), flags=re.I)

    sig = [w for w in words if w.lower() not in _TITLE_STOPWORDS]
    if sig and all(w[0].isupper() for w in sig):
        def _title(m, first=[True]):
            w = m.group(0)
            lead = first[0]
            first[0] = False
            if len(w) == 1:
                return w.upper()                       # figure label ("Schéma A")
            if w.upper() in src_acronyms:
                return w.upper()
            if not lead and w.lower() in _TITLE_STOPWORDS:
                return w.lower()
            return w[:1].upper() + w[1:].lower()
        return _recase(dst, lambda s: re.sub(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", _title, s))

    # sentence case: lower the model's over-capitalisation, keep source
    # acronyms, then upper the very first letter.
    def _sent(s):
        s = re.sub(r"[^\W\d_]+",
                   lambda m: m.group(0) if (m.group(0).upper() in src_acronyms
                                            and len(m.group(0)) >= 2)
                   else m.group(0).lower(), s)
        return re.sub(r"[A-Za-zÀ-ÿ]", lambda m: m.group(0).upper(), s, count=1)
    return _recase(dst, _sent)


def _recase(text: str, fn) -> str:
    """Apply fn to the parts of `text` that are NOT inside <i>...</i>
    (kept-in-source terms keep their own casing)."""
    out, i = [], 0
    for m in re.finditer(r"<i>.*?</i>", text, re.DOTALL | re.IGNORECASE):
        out.append(fn(text[i:m.start()]))
        out.append(m.group(0))
        i = m.end()
    out.append(fn(text[i:]))
    return "".join(out)


# ---------------------------------------------------------------------------
# Glossary-mode (Rule: a "termes techniques" / definitions section is a
# lexicon of the SOURCE language -- keep every headword in the original,
# italic; translate only the definition. See docs/model-quirks.md.
# ---------------------------------------------------------------------------
_GLOSS_MARKERS = re.compile(
    r"TERMES\s+TECHNIQUES|PARTICULARIT[ÉE]S|EXPRESSIONS?\s+ET\s+TERMES"
    r"|TERMES\s+ET\s+EXPRESSIONS", re.I)
_GLOSS_SRC_LINE = re.compile(
    r"(?m)^[ \t]*([A-ZÀ-Þ][^\n—–]{1,45}?)[ \t]*\.?[ \t]*[—–][ \t]+\S")
_TOC_LINE = re.compile(r"(?m)(?:\.{3,}|…)\s*\d+\s*$")
_GLOSS_DST_LINE = re.compile(
    r"^([ \t]*)([A-ZÀ-Þ][^\n—–]{0,60}?)[ \t]*\.?[ \t]*([—–])[ \t]+(.*)$")


def apply_glossary_mode(dst: str, src: str) -> str:
    """In a detected glossary section, put each entry's headword back in the
    original language, italicised; keep the model's translated definition.
    Aligns dst dash-lines to src dash-lines by position; bails if the
    counts diverge (the model merged/split entries)."""
    if not dst or not src:
        return dst
    src_lines = _GLOSS_SRC_LINE.findall(src)
    if len(_TOC_LINE.findall(src)) >= 3:
        return dst                                    # table of contents
    if len(src_lines) < 3 and not (_GLOSS_MARKERS.search(src) and src_lines):
        return dst
    heads = [re.sub(r"\s+", " ", h).strip().strip(".").strip() for h in src_lines]
    dst_lines = dst.split("\n")
    n_dash = sum(1 for ln in dst_lines if _GLOSS_DST_LINE.match(ln))
    if abs(n_dash - len(heads)) > 1 or n_dash < 3:
        return dst
    it = iter(heads)
    out = []
    for ln in dst_lines:
        m = _GLOSS_DST_LINE.match(ln)
        if not m:
            out.append(ln)
            continue
        h = next(it, None)
        if h is None:
            out.append(ln)
            continue
        out.append(f"{m.group(1)}<i>{h}</i> {m.group(3)} {m.group(4)}".rstrip())
    return "\n".join(out)


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

    if (state.get("open") and paras and paras[0][:1].islower()
            and not re.match(r"\(?\d{1,2}\)?[ .]", paras[0].strip())):
        notes.append("… " + re.sub(r"\s+", " ", paras.pop(0)).strip())
        state["open"] = False

    for p in paras:
        s = re.sub(r"\s+", " ", p).strip()
        if re.match(r"\(\d{1,2}\)[ . ]", s):
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


def label_margin_notes(text: str) -> str:
    """A printed shoulder-note captured by the VLM as a trailing standalone
    short paragraph -> move it to the note strip labelled '[margin note]'
    so it is clearly not body text."""
    parts = text.split("\n" + FN_HEADER + "\n", 1)
    body = parts[0]
    existing = parts[1] if len(parts) > 1 else ""
    paras = [p for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]
    if len(paras) < 2:
        return text
    last = paras[-1].strip()
    letters = [c for c in last if c.isalpha()]
    if ("\n" in last or len(last.split()) > 20 or last[:1] in "([{«"
            or re.match(r"\d", last) or re.match(r"[A-Za-zÀ-ÿ]?\d+[°ºo]", last)
            or (letters and sum(c.isupper() for c in letters) > 0.7 * len(letters))):
        return text
    note = "[margin note] " + re.sub(r"\s+", " ", last)
    block = (existing.rstrip() + "\n" if existing.strip() else "") + note
    rest = "\n\n".join(paras[:-1])
    return f"{rest}\n\n{FN_HEADER}\n{block}"


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
    cap = re.sub(r"(?i)\bcorrect(?:ing|ed)?\b[^.:]*\btranslation\b[^.:]*[.:]", "", cap)
    cap = re.sub(r"(?i)\bbased on the (?:given|critical) rules?\b[^.]*\.?", "", cap)
    cap = re.sub(r"^\s*[—–\-]\s*", "", cap.strip())
    cap = re.sub(r"\s{2,}", " ", cap).strip(" —–-")
    if _CAPTION_JUNK.search(cap):
        return ""
    return cap.strip()


_PARRY_WRONG = {
    "first": "prime", "second": "seconde", "third": "tierce", "fourth": "quarte",
    "fifth": "quinte", "sixth": "sixte", "seventh": "septime", "eighth": "octave",
}


def fix_parry_names(en: str, src: str) -> str:
    """Numbered parries (prime..octave) are Category-2 -- kept, not
    translated. When the source names one and the model turned it into an
    English ordinal, snap it back."""
    if not en or not re.search(
            r"parade\s+d|de\s+(?:prime|seconde|tierce|quarte|quinte|sixte|septime|octave)",
            src, re.I):
        return en
    for wrong, right in _PARRY_WRONG.items():
        en = re.sub(r"\b(of\s+|parry\s+of\s+)" + wrong + r"\b",
                    lambda m, r=right: m.group(1) + r, en, flags=re.I)
    return en


def _french_token_count(line: str) -> int:
    toks = re.findall(r"\b[\wàâäéèêëïîôùûüç]+\b", line.lower())
    n = sum(1 for w in toks if w in FRENCH_WORDS)
    n += len(re.findall(r"\b[cdjlmnqst]['’][aeiouyhàâäéèêëïîôùûü]", line, re.I))
    return n


def retranslate_stray_french_headings(text: str) -> str:
    """A short all-caps display heading on an otherwise-good page can come
    back half-translated ("COMMENT ON DOIT HOLD THE SABER") -- the per-page
    French ratio is too diluted by the good body to trip the quality gate.
    Re-translate just those lines through the full prompt (as translate_headers
    does), and keep the result only if it is less French than what we had."""
    out = []
    for line in text.split("\n"):
        s = line.strip()
        letters = [c for c in s if c.isalpha()]
        is_heading = (
            0 < len(s.split()) <= 9
            and len(letters) >= 4
            and sum(c.isupper() for c in letters) >= 0.6 * len(letters)
        )
        if not is_heading or _french_token_count(re.sub(r"</?i>", "", s)) == 0:
            out.append(line)
            continue
        en = translate_with_llama(f"{PROMPT_TEMPLATE}\n\nText to translate:\n{s}")
        en = strip_model_preamble((en or "").strip()).strip()
        en = re.sub(r"(?m)^\s*#{1,6}\s+", "", en)
        en = re.sub(r"</?i>", "", en).splitlines()[0].strip() if en else ""
        en = apply_always_translate(en)
        if en and _french_token_count(en) < _french_token_count(s):
            out.append(match_case(s, en))
        else:
            out.append(line)
    return "\n".join(out)


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
    if s and s[-1] not in _SENT_END and not re.search(r'\b(?:etc|cf|ibid|op)\.$', s):
        d2 = re.sub(r'[\s.…—–-]+$', '', d)
        if d2:
            d = d2
    dst = d + tail
    s0 = src.lstrip()
    if s0[:1].islower():
        dst = re.sub(r'^(\s*)([A-Z])(?=[a-zà-ÿ])',
                     lambda m: m.group(1) + m.group(2).lower(), dst, count=1)
    return dst


def _shout_ratio(text: str) -> float:
    """Fraction of letters that are uppercase. Only meaningful with enough
    letters to average over -- returns 0 for short strings (a plate caption
    or a heading is legitimately all-caps)."""
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 25:
        return 0.0
    return sum(c.isupper() for c in letters) / len(letters)


def deshout(text: str, source: str) -> str:
    """Qwen sometimes latches onto a source paragraph that opens with a
    small-caps word ('L'ESCRIME au fleuret ...') and renders the WHOLE page
    in capitals. Last-resort repair: lowercase a shouting line, re-capitalise
    sentence starts, and keep any token that is genuinely all-caps in the
    source line (acronyms, a section title)."""
    keep = {w for w in re.findall(r"\b[A-ZÀ-Þ]{2,}\b", source)}
    out = []
    for line in text.split("\n"):
        if _shout_ratio(line) <= 0.85:
            out.append(line)
            continue
        lowered = re.sub(r"[A-Za-zÀ-ÿ]+",
                         lambda m: m.group(0) if m.group(0) in keep
                         else m.group(0).capitalize() if m.group(0).istitle()
                         else m.group(0).lower(),
                         line)
        lowered = re.sub(r"(^\s*[\"«(]?\s*[a-zà-ÿ])",
                         lambda m: m.group(1).upper(), lowered)
        lowered = re.sub(r"([.!?]\s+[\"«(]?)([a-zà-ÿ])",
                         lambda m: m.group(1) + m.group(2).upper(), lowered)
        lowered = re.sub(r"\bi\b", "I", lowered)
        out.append(lowered)
    return "\n".join(out)


def has_wrong_script(text: str) -> bool:
    """Sources are all Latin-script (French/Italian/German/Spanish/Latin).
    CJK / Cyrillic / Hebrew / Arabic characters in the output mean the model
    glitched -- e.g. it wrote a note to itself in Chinese mid-sentence.
    (Greek is deliberately allowed: classical treatises do quote it.)
    """
    return bool(re.search(
        r"[Ѐ-ӿ֐-׿؀-ۿ　-ヿ㐀-鿿가-힯]",
        text))


def contains_too_much_non_english(text: str) -> bool:
    # Technical terms the prompt instructs the model to KEEP in the source
    # language are wrapped in <i>...</i> -- those are correct output, not a
    # translation failure, so drop them before measuring.
    text = re.sub(r"<i>.*?</i>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    words = re.findall(r"\b\w+\b", text.lower())
    if not words:
        return True
    non_english_count = sum(1 for w in words if w in NON_ENGLISH_INDICATORS)
    if non_english_count / len(words) > 0.35:
        return True
    # Backstop for a page that stayed mostly French but is short on the
    # stop words above: elision (consonant + apostrophe + vowel, "d'une" /
    # "qu'il" -- English contractions put a consonant after the apostrophe)
    # and endings that need French-specific letters.
    fr = len(re.findall(r"\b[cdjlmnqst]['’][aeiouyhàâäéèêëïîôùûü]", text, re.I)) \
        + len(re.findall(r"\w(?:ité|eaux|aient|âtre|euse|eux|oué)\b", text, re.I))
    return fr / len(words) > 0.045


# An optional leading politeness token the model likes to prepend to both
# preambles and refusals ("Certainly. Here is...", "Sure, please provide...").
_LEAD_POLITE = r"(?:(?:sure|certainly|of course|okay|ok|yes|alright|understood)[,.!]?\s+)?"

# Prose preambles the model emits despite the prompt's "output ONLY the
# translation" rule. Matched only at the very start of the output, one per
# line, case-insensitively. The Markdown sanitizers already handle '#'/'**'.
#
# Every alternative must reference the ACT of translating or be a known
# fixed filler phrase -- loose patterns like "any line ending in a colon"
# strip real page-initial sentences ("In this case, the appreciation of
# the thrust ... Example:" is genuine fencing prose).
_PREAMBLE_RE = re.compile(
    r"^\s*" + _LEAD_POLITE + r"(?:"
    r"here(?:'s| is)\b[^\n]*\b(?:translat\w+|rendering|english version)\b[^\n]*|"
    r"[^\n]*\bhere(?:'s| is) the translation\b[^\n]*|"      # "... Here is the translation:"
    r"below is\b[^\n]*\btranslat\w+[^\n]*|"
    r"the (?:following is|translation (?:is|follows))\b[^\n]*|"
    r"i(?:'ll| will| can)\b[^\n]*\btranslat\w+[^\n]*|"
    r"(?:sure|certainly|of course|okay|ok)[,.!]?|"          # bare filler token
    r"note:\s*[^\n]*|"
    r"following the (?:critical )?rules[^\n]*:|"
    r"this (?:one )?may take a step back[^\n]*"
    r")\s*$",
    re.IGNORECASE)

# Whole-output shapes that mean "the model had nothing to translate" or
# refused -- never a usable page, not even as a low-confidence fallback.
_REFUSAL_RE = re.compile(
    r"^\s*" + _LEAD_POLITE + r"(?:"
    r"please provide (?:the|some) (?:text|content)\b|"
    r"(?:the\s+)?(?:text|content)\s+(?:you\s+)?provided\s+(?:does\s+not|doesn't|is\s+not)\b|"
    r"there (?:is|was) no (?:text|content)\b|"
    r"it (?:seems|appears) (?:that )?(?:there is|no) \b|"
    r"this text does not appear to (?:be|contain)\b|"
    r"no (?:text|content) (?:was )?provided\b"
    r")",
    re.IGNORECASE)


def strip_model_preamble(text: str) -> str:
    """Drop leading 'Here is the translation:' / 'Sure:' / 'Note: ...' lines
    the model prepends despite the prompt. Stops at the first real line."""
    lines = text.split("\n")
    while lines and (not lines[0].strip() or _PREAMBLE_RE.match(lines[0])):
        lines.pop(0)
    return "\n".join(lines).strip()


def looks_like_refusal(text: str) -> bool:
    """True when the whole output is a 'give me something to translate' /
    'this isn't fencing text' message rather than a translation."""
    return bool(_REFUSAL_RE.match(text.strip()))


def is_near_blank(text: str) -> bool:
    """A page whose OCR body is just a stray mark / page number / one word --
    not worth a model call (the model answers 'Please provide the text...').
    Kept conservative so real terse content (a plate caption like
    'Planche IV') still goes through."""
    letters = re.findall(r"[^\W\d_]", text, flags=re.UNICODE)
    return len(text.split()) < 2 or len(letters) < 8

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


def start_translation_server():
    if bc.is_remote():
        base = bc.translate_base_url()
        print(f"\n[remote] using translation endpoint {base}")
        print("[remote] make sure the TEXT model is loaded on that host.")
        try:
            r = requests.get(f"{base}/health", headers=bc.auth_headers(), timeout=5)
            if r.status_code == 200:
                print("[remote] /health OK\n")
                return REMOTE_SERVER
            print(f"[remote] /health returned HTTP {r.status_code}")
        except Exception as e:
            print(f"[remote] cannot reach {base}: {e}")
        return None

    print("\n" + "="*60)
    print("LOADING TRANSLATION MODEL")
    print("="*60 + "\n")
    print(f"Model: {Path(TRANSLATION_MODEL).name}")
    print("Port:  11434 | GPU: Full offload\n")

    if not os.path.exists(TRANSLATION_MODEL):
        print(f"✗ Model not found: {TRANSLATION_MODEL}\n")
        return None
    
    model_size = os.path.getsize(TRANSLATION_MODEL) / (1024**3)
    print(f"✓ Model verified ({model_size:.2f} GB)\n")
    
    print("Checking for existing servers...")
    subprocess.run(["pkill", "-f", "llama-server.*11434"], stderr=subprocess.DEVNULL)
    time.sleep(5)
    print("✓ Port cleared\n")

    cmd = [
        "llama-server",
        "--model", TRANSLATION_MODEL,
        "--host", "0.0.0.0",
        "--port", "11434",
        # 8192, not 4096: PROMPT_TEMPLATE (term policy + EXAMPLES) is ~1.5k
        # tokens and prepended to every call, so a long source page + a
        # full-length response overran a 4096 window ("Context size has
        # been exceeded" -> empty response -> "kept original text").
        "--ctx-size", "8192",
        "--n-gpu-layers", "999",
        "--threads", str(os.cpu_count())
    ]

    print("Starting server (loads ~7GB VRAM; ~60s warm, up to ~4min on a "
          "cleared GPU shader cache)...\n")

    log_file = open("/tmp/translation_server.log", "w")
    proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT,
                            env=_llama_server_env())
    
    dots = 0
    for attempt in range(120):   # 240s: cold RADV shader compile can take ~4min
        time.sleep(2)
        dots = (dots + 1) % 4
        elapsed = (attempt+1)*2
        sys.stdout.write(f"\r  Loading{'.' * dots}{' ' * (3-dots)} {elapsed}s")
        sys.stdout.flush()
        
        if proc.poll() is not None:
            print("\n\n✗ Server process died!")
            print("Check logs: docker exec booktranslator-translator-1 cat /tmp/translation_server.log\n")
            log_file.close()
            return None
        
        try:
            resp = requests.get(f"{bc.translate_base_url()}/health", timeout=2)
            if resp.status_code == 200:
                print(f"\n\n✓ Translation server ready! (took {elapsed}s)")
                print("="*60 + "\n")
                log_file.close()
                return proc
        except requests.exceptions.ConnectionError:
            pass
        except Exception:
            pass
    
    print(f"\n\n✗ Server timeout after 240s")
    print("Check logs: docker exec booktranslator-translator-1 cat /tmp/translation_server.log\n")
    log_file.close()
    proc.terminate()
    return None

def translate_with_llama(prompt: str, temperature: float = 0.3) -> str:
    for attempt in range(3):
        try:
            response = requests.post(
                LLAMA_API_URL,
                headers={"Content-Type": "application/json", **bc.auth_headers()},
                json={
                    "model": TRANSLATION_MODEL_NAME,
                    "messages": [
                        {"role": "system", "content": "You are an expert translator of historical fencing texts."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": temperature,
                    # a translated page is never longer than its source; the
                    # >2500-word oversized guard handles the rare monster
                    # page. Leaves room in the 8192 window for prompt+source.
                    "max_tokens": 3000
                },
                timeout=1800
            )
            
            if response.status_code == 200:
                result = response.json()
                if "choices" in result and len(result["choices"]) > 0:
                    return result["choices"][0]["message"]["content"].strip()
            
            if attempt < 2:
                time.sleep(5)
        except Exception:
            if attempt < 2:
                time.sleep(10)
    
    return None


def translate_figure_captions(folder: str, force: bool = False) -> int:
    """Translate the source captions in figures.json (written by step 1) in
    place, adding a 'caption_en' to each. Uses the full translation prompt
    -- plate captions are headings ("PARADE DE SIXTE" etc.) and need the
    same term policy as body text. Skips captions already done unless
    `force`. Returns the number translated."""
    import json as _json
    fig_path = f"{folder}/figures.json"
    if not os.path.exists(fig_path):
        return 0
    with open(fig_path, encoding="utf-8") as fh:
        fig_map = _json.load(fh)
    n = 0
    for figs in fig_map.values():
        for fig in figs:
            cap = _clean_caption((fig.get("caption") or "").strip())
            if cap != (fig.get("caption") or "").strip():
                fig["caption"] = cap            # persist the cleaned source caption
            if not cap:
                fig.pop("caption_en", None)
                continue
            if fig.get("caption_en") and not force:
                continue
            one = ""
            for attempt in range(3):
                en = translate_with_llama(
                    f"{PROMPT_TEMPLATE}\n\nText to translate:\n{cap}",
                    temperature=0.3 + 0.25 * attempt)
                en = strip_model_preamble((en or "").strip()).strip()
                en = re.sub(r'(?m)^\s*#{1,6}\s+', '', en)
                if not en:
                    continue
                # a caption is one line -- join a multi-line rendering
                cand = " ".join(l.strip() for l in en.splitlines() if l.strip())
                # reject a caption the model just echoed back in French
                if contains_too_much_non_english(cand):
                    one = one or cand
                    continue
                one = cand
                break
            if one:
                one = _clean_caption(one)
                one = enforce_kept_terms(apply_always_translate(one), cap)
                one = fix_parry_names(one, cap)
                # captions don't render a footnote block: translate the
                # Category-3 terms but drop any <fn> marker (backstop's and
                # a stray one from the model).
                one = re.sub(r"<fn>.*?</fn>|</?fn>", "", one,
                             flags=re.DOTALL | re.IGNORECASE)
                one = enforce_cat3_footnotes(one, set(), footnote=False)
                fig["caption_en"] = match_case(cap, one) or cap
            else:
                fig["caption_en"] = cap
            n += 1
    with open(fig_path, "w", encoding="utf-8") as fh:
        _json.dump(fig_map, fh, ensure_ascii=False, indent=1)
    if n:
        print(f"  translated {n} figure caption(s)")
    return n


def translate_headers(folder: str, force: bool = False) -> int:
    """Translate each distinct running header from extracted.txt into a
    headers.json { raw -> english } map, used by the PDF steps. Headers
    are section/chapter titles ("LES PARADES", "SOCIÉTÉS") -- an English
    reader needs to know what section they're in. Translated once per
    distinct string, through the full prompt (rule 4: headings)."""
    import json as _json
    ex_path = f"{folder}/extracted.txt"
    if not os.path.exists(ex_path):
        return 0
    hp = f"{folder}/headers.json"
    hmap = {}
    if os.path.exists(hp) and not force:
        with open(hp, encoding="utf-8") as fh:
            hmap = _json.load(fh)
    raw = set()
    for pg in _pageio.split_pages(open(ex_path, encoding="utf-8").read()):
        m = re.search(r'(?im)^[ \t]*HEADER[ \t]*:[ \t]*(.+?)[ \t]*$', pg)
        if m:
            h = m.group(1).strip()
            if h and h.lower() not in ("none", "n/a", "-"):
                raw.add(h)
    n = 0
    for h in sorted(raw):
        if h in hmap and not force:
            continue
        en = translate_with_llama(f"{PROMPT_TEMPLATE}\n\nText to translate:\n{h}")
        en = strip_model_preamble((en or "").strip()).strip()
        en = re.sub(r'(?m)^\s*#{1,6}\s+', '', en)
        en = re.sub(r'</?i>', '', en)          # a header is plain text (rule 4)
        en = enforce_cat3_footnotes(apply_always_translate(en), set(), footnote=False)
        hmap[h] = match_case(h, en.splitlines()[0].strip()) if en else h
        n += 1
    with open(hp, "w", encoding="utf-8") as fh:
        _json.dump(hmap, fh, ensure_ascii=False, indent=1)
    if n:
        print(f"  translated {n} running header(s)")
    return n


print("\n" + "="*60)
print("STEP 2: AI TRANSLATION")
print("="*60)

translation_proc = start_translation_server()

if translation_proc is None:
    print("✗ Cannot start translation server\n")
    exit(1)

# `2_translate.py --captions` re-runs only the figure captions AND running
# headers (all books), without touching the page bodies -- for iterating.
if len(sys.argv) > 1 and sys.argv[1] == "--captions":
    try:
        for folder in glob.glob("/data/processed/*_translated"):
            print(f"captions/headers: {Path(folder).name}")
            translate_figure_captions(folder, force=True)
            translate_headers(folder, force=True)
    finally:
        if translation_proc is not REMOTE_SERVER and translation_proc:
            translation_proc.terminate()
    print("\n✓ Caption pass complete\n")
    sys.exit(0)

try:
    for folder in glob.glob("/data/processed/*_translated"):
        src = f"{folder}/extracted.txt"
        dst = f"{folder}/translation.txt"
        
        if not os.path.exists(src):
            continue
        
        if os.path.exists(dst) and os.path.getmtime(dst) > os.path.getmtime(src):
            print(f"⊘ Skip (up to date): {Path(folder).name}\n")
            continue

        with open(src, "r", encoding="utf-8") as f:
            pages = [p.strip() for p in _pageio.split_pages(f.read()) if p.strip()]

        print(f"Translating: {Path(folder).name}")
        print(f"Pages: {len(pages)}\n")
        
        translated_pages = []
        fn_seen = set()     # dedupe <fn> note text across the book
        cat3_seen = set()   # footnote each Category-3 term once per book
        fn_state = {}       # cross-page state for the book's own footnotes

        for i, page_text in enumerate(pages, 1):
            if not page_text.strip() or "VLM Error" in page_text:
                print(f"  Page {i}/{len(pages)} — empty / OCR error, passed through")
                translated_pages.append(page_text)
                continue

            # Clean VLM structured output before translation
            clean_text = clean_vlm_extraction(page_text)

            if not clean_text.strip():
                print(f"  Page {i}/{len(pages)} — no body text, skipped")
                translated_pages.append("[Empty page]")
                continue

            # A step-1 marker page ("[handwritten inscription — see original]")
            # -- pass it through verbatim, never translate it.
            if re.fullmatch(r"\[[^\[\]\n]+\]", clean_text.strip()):
                translated_pages.append(clean_text.strip())
                print(f"  Page {i}/{len(pages)} — marker page, passthrough")
                continue

            # A scoring / poule table: replace each <table>..</table> with a
            # [[TABLE]] sentinel; steps 3 & 4 embed the scan for it. A page
            # that is nothing but table(s) skips the model entirely.
            if re.search(r"<table\b", clean_text, re.I):
                clean_text = re.sub(r"<table\b.*?</table\s*>", "\n[[TABLE]]\n",
                                    clean_text, flags=re.I | re.S)
                clean_text = re.sub(r"\n{3,}", "\n\n", clean_text).strip()
                if re.fullmatch(r"(?:\s*\[\[TABLE\]\]\s*)+", clean_text):
                    translated_pages.append("[[TABLE]]")
                    print(f"  Page {i}/{len(pages)} — table-only page, passthrough")
                    continue

            # A near-blank page (stray mark, bare page number, single word)
            # isn't worth a model call -- the model just replies "Please
            # provide the text you would like to translate", which then
            # leaks into translation.txt as the page's content.
            if is_near_blank(clean_text):
                print(f"  Page {i}/{len(pages)} — near-blank ({clean_text.split()[:4]}), skipped")
                translated_pages.append("[Empty page]")
                continue

            src_words = len(clean_text.split())

            # A page whose OCR ran away (dot-leader / ornament repetition
            # loop) overflows the translation model's context and burns 3
            # dead API calls. Cap it: translate the first ~2500 words, keep
            # the rest verbatim, flag it.
            if src_words > 2500:
                head = " ".join(clean_text.split()[:2500])
                cand = translate_with_llama(
                    f"{PROMPT_TEMPLATE}\n\nText to translate:\n{head}")
                cand = cand or "[translation unavailable]"
                print(f"  Page {i}/{len(pages)} — oversized OCR ({src_words} w), "
                      f"translated head only")
                translated_pages.append(
                    f"[oversized OCR page — likely a repetition artifact; "
                    f"first part translated, remainder omitted]\n\n{cand}"[:6000])
                continue
            success = False
            best_candidate = None   # best rejected attempt, for the fallback
            for attempt in range(3):
                print(f"  Page {i}/{len(pages)} (try {attempt + 1})", end="", flush=True)

                # After a first failure, raise the temperature and add a
                # blunt instruction -- the 7B sometimes gets stuck echoing
                # the French source verbatim, and a hotter sample + the
                # extra line breaks it out.
                if attempt == 0:
                    full_prompt = f"{PROMPT_TEMPLATE}\n\nText to translate:\n{clean_text}"
                    temp = 0.3
                else:
                    full_prompt = (f"{PROMPT_TEMPLATE}\n\nTranslate the following into "
                                   f"fluent English. Do NOT repeat any of it in the "
                                   f"original language. Match the capitalisation of the "
                                   f"source; do NOT write the text in all capitals."
                                   f"\n\nText to translate:\n{clean_text}")
                    temp = 0.3 + 0.25 * attempt
                candidate = translate_with_llama(full_prompt, temperature=temp)

                if not candidate:
                    print(" → failed")
                    continue

                # The model sometimes emits Markdown despite the prompt. A
                # lone "---" thematic break used to collide with the page
                # separator used everywhere downstream (178 pages -> 179,
                # every later page misaligned). Strip all of it; <i>...</i>
                # is the only markup the pipeline expects.
                candidate = re.sub(r'(?m)^\s*-{3,}\s*$', '* * *', candidate)
                candidate = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', candidate)
                candidate = re.sub(r'\*\*([^*\n]+)\*\*', r'\1', candidate)   # **bold**
                candidate = re.sub(r'(?m)^\s{0,3}#{1,6}\s+', '', candidate)  # # heading
                candidate = re.sub(r"(?im)^\s*\[+\s*TABLE[^\]\n]*\]+\s*$",
                                   "[[TABLE]]", candidate)      # normalise the sentinel
                candidate = apply_always_translate(candidate)
                candidate = enforce_kept_terms(candidate, clean_text)
                candidate = enforce_cat3_footnotes(candidate, cat3_seen)
                candidate = apply_glossary_mode(candidate, clean_text)
                candidate = retranslate_stray_french_headings(candidate)

                # "This isn't fencing text" / "Please provide the text..." --
                # the model had nothing real to translate. Not a fallback
                # candidate; try again, then fall through to [Empty page].
                if looks_like_refusal(candidate):
                    print(" → refusal / echo")
                    time.sleep(4)
                    continue

                # "Here is the translation:" / "Sure:" / "Note: ..." preambles.
                candidate = strip_model_preamble(candidate)
                if not candidate:
                    print(" → preamble only")
                    time.sleep(4)
                    continue

                if has_wrong_script(candidate):
                    # Never keep this one, even as a fallback -- CJK/Cyrillic
                    # in the output is the model breaking down.
                    print(" → garbled (non-Latin script)")
                    time.sleep(8)
                    continue

                best_candidate = best_candidate or candidate

                if contains_too_much_non_english(candidate):
                    print(" → not English")
                    time.sleep(8)
                    continue

                # Qwen rendered the whole page in capitals (source wasn't).
                # Retry for a clean one; deshout() is the fallback repair.
                if _shout_ratio(candidate) > 0.85 and _shout_ratio(clean_text) < 0.6:
                    print(" → all caps")
                    time.sleep(6)
                    continue

                # Length sanity check -- but only for pages with enough source
                # text for the ratio to mean anything. Plate captions, drill
                # lists and dimension callouts are legitimately terse and were
                # being rejected as "too short".
                if src_words >= 25 and len(candidate.split()) < 0.5 * src_words:
                    print(" → too short")
                    time.sleep(8)
                    continue

                candidate = match_boundary_punctuation(candidate, clean_text)
                candidate, n_fn = pull_footnotes(candidate, clean_text, fn_seen)
                candidate = extract_source_footnotes(candidate, fn_state)
                candidate = label_margin_notes(candidate)
                translated_pages.append(candidate)
                success = True
                print(f" ✓{'  (%d fn)' % n_fn if n_fn else ''}")
                break

            if not success:
                # Never drop a page. Keep the best non-garbled attempt
                # (flagged for review), or fall back to the original text so
                # the page still appears in the output for a human to fix.
                if best_candidate and _shout_ratio(best_candidate) > 0.85 \
                        and _shout_ratio(clean_text) < 0.6 \
                        and not contains_too_much_non_english(best_candidate) \
                        and not (src_words >= 25
                                 and len(best_candidate.split()) < 0.5 * src_words):
                    # The only thing wrong was the all-caps shout: repair the
                    # case and keep it as a normal page, no review banner.
                    fixed, n_fn = pull_footnotes(deshout(best_candidate, clean_text),
                                                 clean_text, fn_seen)
                    translated_pages.append(fixed)
                    print(" ✓ (deshouted)")
                elif best_candidate:
                    print(" ⚠ kept (low confidence)")
                    body = deshout(best_candidate, clean_text) \
                        if _shout_ratio(best_candidate) > 0.85 else best_candidate
                    translated_pages.append(f"[low-confidence translation — review]\n{body}")
                elif src_words < 15:
                    # Every attempt was a refusal/preamble and there was
                    # barely any source text -- it's an effectively blank
                    # page, not a translation failure worth surfacing.
                    print(" ✗ refusals only on a near-blank page — [Empty page]")
                    translated_pages.append("[Empty page]")
                else:
                    print(" ✗ no usable output — kept original text")
                    translated_pages.append(f"[translation unavailable — original text follows]\n{clean_text}")

        with open(dst, "w", encoding="utf-8") as f:
            f.write(_pageio.join_pages(translated_pages))

        translate_figure_captions(folder)
        translate_headers(folder)

        print(f"\n✓ Translation complete: {Path(folder).name}\n")

finally:
    if translation_proc is REMOTE_SERVER:
        print("[remote] leaving the remote translation server as-is\n")
    else:
        print("="*60)
        print("UNLOADING TRANSLATION MODEL")
        print("="*60 + "\n")
        print("Shutting down translation server...")
        translation_proc.terminate()
        try:
            translation_proc.wait(timeout=10)
            print("✓ Translation server stopped (VRAM freed)\n")
        except:
            translation_proc.kill()
            print("✓ Translation server killed (VRAM freed)\n")

print("="*60)
print("✓ TRANSLATION COMPLETE")
print("="*60 + "\n")
