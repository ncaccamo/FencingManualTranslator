#!/usr/bin/env python3
"""Translation with VLM output cleaning"""

import glob
import os
import time
import re
import requests
import subprocess
import sys
from collections import Counter
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

# Deterministic mode for the eval harness: temp 0 + a fixed seed so a
# re-run of an unchanged book reproduces byte-for-byte and `evals/check.py`
# page-diffs mean something. Off by default (production uses temp 0.3 with
# the 3-try retry ladder).
EVAL_DETERMINISTIC = os.environ.get("EVAL_DETERMINISTIC", "").lower() in (
    "1", "true", "yes", "on")
EVAL_SEED = int(os.environ.get("EVAL_SEED", "1234"))

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

    if re.search(r'(?im)^BODY_TEXT:\s*$', text):
        # The label is there but nothing follows -- a genuinely empty body
        # (a plate leaf with no prose), not a malformed record. Don't fall
        # through to the label-stripping heuristic below: it used to strip
        # "PAGE_NUMBER:\s*\S+" greedily enough to eat the "HEADER:" label
        # along with it (\S+ doesn't stop at a colon), leaving the header's
        # own text stranded and unlabeled -- mistaken for body text, sent
        # through the translator, and rendered in place of the real plate
        # caption (already captured correctly and separately in
        # figures.json).
        return ""

    # No BODY_TEXT marker at all (malformed record) -- strip the known
    # labels line by line, not with a run-on \s* that can cross into the
    # next label.
    cleaned = re.sub(r'(?im)^PAGE_NUMBER:.*$', '', text)
    cleaned = re.sub(r'(?im)^HEADER:.*$', '', cleaned)
    cleaned = re.sub(r'(?im)^BODY_TEXT:.*$', '', cleaned)

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
                masked.append(f"<fn>French: {note_fr}</fn>")
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
        # accept only a bare source reference ("Fr. liement" / "French:
        # liement") for a cat-3 term -- the 14B sometimes footnotes a
        # Cat-2 term or puts the English rendering in the note.
        ref = re.sub(r"^(?:fr\.|french:)\s*", "", note, flags=re.I).lower().rstrip(".")
        if not note or ref not in _cat3_fr:
            return ""                       # drop the tag, keep the phrase
        note = f"French: {ref}"             # canonical rendered form
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


# Rule-3 "always translate" terms the 7B drops inconsistently (leaves
# "an arm called a fleuret" in the body, "ENTRAINEMENT" as a header).
# Only unambiguous ones -- these have no other meaning in this corpus.
# Longest first so "fleurettiste" wins before "fleuret".
_ALWAYS_TRANSLATE = [
    # "LISTE DES OUVRAGES" -> "LISTE OF WORKS" (Gomard bibliography section
    # header): "DES OUVRAGES" translates fine but bare "LISTE" survives
    # untranslated on this specific isolated header string, even though
    # the SAME word translates correctly as part of the two-part header
    # "LISTE DES OUVRAGES\tCONSULTÉS." -- a one-off model quirk on the
    # shorter isolated string, consistent enough across sampling that
    # majority-vote alone doesn't catch it.
    (r"listes", "lists"), (r"liste", "list"),
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
    # a title-page byline: French "PAR X ET Y" -> "By X and Y", not
    # "Translated by ..." (the model reads "par" as the translation credit).
    text = re.sub(
        r"(?m)^([ \t]*)Translated by(?=[ \t]+[\"“'’A-ZÀ-Þ][^\n]{0,60}$)",
        r"\1By", text)
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
    # riposte / remise / reprise: normally kept by the model, but a bare
    # heading ("DE LA RIPOSTE.") gets mangled -- "OF THE RAPIER RAPIER".
    # Isolated-heading translation (no body context, no retry ladder) is
    # where this shows up -- "PARADES ET RIPOSTES" -> "PARRIES AND
    # RAPISES" is the same failure with a different garbled spelling.
    ("riposte", [r"rapier(?:[- ]?(?:rapier|response|riposte|reply))?",
                 r"riposte response", r"rapises?"]),
    ("remise", []),
    ("reprise", []),
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


def _match_term_case(term: str, sample: str) -> str:
    # Same convention as apply_always_translate's own case-preserving
    # substitution: match the case of whatever text prompted this
    # insertion, rather than always forcing one fixed case. A technical
    # term of art normally sits lowercase-italic mid-sentence, but when
    # it lands inside an all-caps body-level title line (Prévost "DE LA
    # RIPOSTE" opening Part Four) it should read as part of that title,
    # not switch to lowercase against the rest of the caps -- there was
    # never an intent to force lowercase in EVERY context, only to fix
    # the garbled-word substitution this originally guarded.
    return term.upper() if sample.isupper() \
        else term.capitalize() if sample[:1].isupper() else term


def fix_known_garbles(text: str, src: str = "") -> str:
    """Deterministic repairs for REPEATABLE model mistakes -- ones that
    survive the 3-sample majority vote because the model makes them the
    same way every time (Gomard, batch-2 pass 8):
      * "THUST" for "thrust" -- in headers AND body, 13 lines + 4 headers.
        Also "SIMPLE THROUGHS" (pdf 174 retranslation). Neither is an
        English word, so safe to fix anywhere.
      * "THRUST/HIT" -- the prompt glossary's old "botte -> thrust/hit"
        copied verbatim. botte -> hit (coup -> thrust; Gomard pdf 158
        defines the two as distinct).
      * "plank(s)" for planche(s) -- the carpentry sense; in these books a
        planche is always an illustration plate. Only when the source
        actually has "planche", so a genuine plank elsewhere survives."""
    if not text:
        return text

    def _case(m, word):
        g = m.group(0)
        plural = g.lower().endswith("s")
        w = word + ("s" if plural else "")
        return w.upper() if g.isupper() else w.capitalize() if g[:1].isupper() else w
    text = re.sub(r"\bthusts?\b", lambda m: _case(m, "thrust"), text, flags=re.I)
    text = re.sub(r"\bthroughs\b", lambda m: _case(m, "thrust"), text, flags=re.I)
    text = re.sub(r"\bthrusts?\s*/\s*hits?\b",
                  lambda m: _case(re.search(r"hits?$", m.group(0), re.I), "hit"),
                  text, flags=re.I)
    if src and re.search(r"\bplanches?\b", src, re.I):
        text = re.sub(r"\bplanks?\b", lambda m: _case(m, "plate"), text, flags=re.I)
    return text


def enforce_kept_terms(text: str, src: str = "") -> str:
    """Make sure a Category-2 term is present and italicised: repair a
    known mistranslation, then wrap any bare occurrence in <i>...</i>
    (skipping what is already inside a tag), matching the case of
    whatever prompted the wrap."""
    if not text:
        return text
    text = repair_mangled_terms(text, src)
    text = fix_known_garbles(text, src)
    for term, wrongs in _KEEP_TERMS:
        for w in wrongs:
            def _sub_term(m, term=term):
                # preserve plurality of the garbled match ("RAPISES" ->
                # "ripostes", not the always-singular "riposte")
                g = m.group(0)
                plural = g.rstrip().lower().endswith("s") and not term.endswith("s")
                disp = term + ("s" if plural else "")
                return f"<i>{_match_term_case(disp, g)}</i>"
            text = re.sub(r"(?<!<i>)\b" + w + r"\b(?!</i>)",
                          _sub_term, text, flags=re.IGNORECASE)
        # wrap a bare occurrence not already tagged
        text = re.sub(
            r"(?<![\w>])" + term.replace(" ", r"\s+") + r"(?![\w<])",
            lambda m: f"<i>{_match_term_case(term, m.group(0))}</i>",
            text, flags=re.IGNORECASE)
        # collapse any accidental double-wrap, keeping whichever case is
        # already inside it rather than reinserting the lower-cased term
        text = re.sub(
            rf"<i>\s*<i>({re.escape(term)})</i>\s*</i>",
            lambda m: f"<i>{m.group(1)}</i>", text, flags=re.IGNORECASE)
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

    # A caption can combine a main title with a trailing parenthetical
    # annotation set in a DIFFERENT case register (Kirchhoffer: "LE
    # CROISÉ AU FLANC." all-caps, then "(La main de l'attaquant reste
    # haute.)" in ordinary sentence case -- confirmed against the real
    # scan). Measuring case over the whole combined string dilutes an
    # all-caps main title's upper_ratio below the 0.8 threshold, so the
    # real all-caps title fell through to a broken middle case instead.
    # Split off a trailing "(...)" and case-match each side separately,
    # only when BOTH src and dst end in one -- otherwise fall through to
    # the single-mode behaviour below unchanged.
    _PAREN_TAIL = re.compile(r"^(.*?)([ \t]*\([^()]*\))\s*$", re.S)
    sm = _PAREN_TAIL.match(src)
    dm = _PAREN_TAIL.match(dst)
    if sm and dm and sm.group(1).strip() and dm.group(1).strip():
        main = match_case(sm.group(1), dm.group(1))
        paren = match_case(sm.group(2).strip(), dm.group(2).strip())
        return f"{main.rstrip()} {paren}"

    # Same combined-case-register problem, different separator: a main
    # title followed by a trailing "-- <clause>" annotation ALSO set in
    # ordinary lower case in print (Kirchhoffer "PRISE DE FER SUR LE
    # BRAS TENDU. -- 1er TEMPS : feinte de liement de seconde." -- the
    # title is genuinely all-caps, the "1er TEMPS : ..." clause genuinely
    # lower case, confirmed by the same pattern repeating identically
    # across 3 plates in one run). Only split at the LAST em-dash when
    # the trailing segment actually STARTS lower-case (letter or digit,
    # for ordinals like "1er"/"3rd") -- a caption that only uses "--" to
    # separate two SAME-case segments ("FIG. 4. -- LE CROISÉ AU FLANC.",
    # both all-caps) must stay whole. Recurses, so a caption with more
    # than one such trailing clause peels them off one at a time.
    #
    # *2026-09-20:* "1er" doesn't have as natural a digit-form as "2e"/
    # "3e" (no bare "1st" the model reaches for as readily), so on this
    # exact caption's FIRST plate the model spelled it "FIRST TEMPO"
    # instead of "1st Tempo" -- still visually part of the caps run, so
    # the tail no longer starts lower-case/digit and the split silently
    # stopped firing (Fig. 6/7 in the SAME plate run use "2nd"/"3rd" and
    # split fine; only Fig. 5's "1er" hit this). Also accept a spelled-
    # out ordinal word as a tail-start, case-insensitively -- it's the
    # same "this is really the lower-case-register clause" signal as a
    # bare digit, just spelled differently.
    _ORDINAL_WORD = r"(?i:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)\b"
    _DASH_TAIL = re.compile(
        rf"^(.*\S)[ \t]*[—-][ \t]+([a-zà-ÿ0-9].*|{_ORDINAL_WORD}.*)$", re.S)
    sm2 = _DASH_TAIL.match(src)
    dm2 = _DASH_TAIL.match(dst)
    if sm2 and dm2:
        main = match_case(sm2.group(1), dm2.group(1))
        tail = match_case(sm2.group(2), dm2.group(2))
        return f"{main.rstrip()} — {tail}"

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
        # Capitalise the first GENUINE word-initial letter, not just the
        # first letter found -- a clause starting with an ordinal ("3rd
        # tempo...", "3e TEMPS...") has its first letter mid-token ("r"
        # in "3rd"), and force-capitalising that produced a broken "3Rd".
        return re.sub(r"(?<![\dA-Za-zÀ-ÿ])[A-Za-zÀ-ÿ]",
                      lambda m: m.group(0).upper(), s, count=1)
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


_FIG_TITLE_RE = re.compile(
    r"(?i)^\s*(?:parry|parade|thrust|coup|lunge|figure|fig\.|plate|pl\.|planche"
    r"|salut|guard|on guard|en garde|développement|\w+\s+(?:position|movement))\b")


def label_margin_notes(text: str, has_fig: bool = False) -> str:
    """A printed shoulder-note captured by the VLM as a trailing standalone
    short paragraph -> move it to the note strip labelled '[margin note]'
    so it is clearly not body text. Skipped on a figure page (the trailing
    short line there is a caption title, not a shoulder note) and on a page
    that isn't prose-dominant."""
    if has_fig:
        return text
    parts = text.split("\n" + FN_HEADER + "\n", 1)
    body = parts[0]
    existing = parts[1] if len(parts) > 1 else ""
    paras = [p for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]
    if len(paras) < 3:                       # need a prose-dominant page
        return text

    def _note_shaped(p):
        s = p.strip()
        letters = [c for c in s if c.isalpha()]
        # A genuine printed shoulder-note is a complete remark -- it ends
        # the sentence. A paragraph the page boundary cut off (continues on
        # the next page) usually doesn't, and not just on a comma
        # (P-review D) -- a corpus scan after that fix still found 40/67
        # tagged notes with no terminal punctuation at all, nearly all
        # clearly mid-sentence ("One breaks the distance to disengage from
        # an enemy...", cut off). Require the sentence to actually end,
        # instead of blacklisting a few specific "clearly continues" chars.
        ends_sentence = bool(re.search(r'[.!?][)\]"»]*$', s))
        return not ("\n" in s or len(s.split()) > 20 or s[:1] in "([{«"
                    or re.match(r"\d", s) or re.match(r"[A-Za-zÀ-ÿ]?\d+[°ºo]", s)
                    or _FIG_TITLE_RE.match(s)   # a figure caption title, not a note
                    or (letters and sum(c.isupper() for c in letters) > 0.7 * len(letters))
                    or not ends_sentence)

    # A shoulder-note doesn't have to be the page's LAST paragraph -- real
    # body prose can continue after it (Gomard src 32: the genuine note,
    # "He recognizes only three ways of striking.", sat one paragraph
    # before the end, immediately followed by a real closing paragraph
    # that used to get wrongly tagged as the note instead). The two are
    # shape-IDENTICAL (both short, both end a sentence, neither uppercase-
    # heavy) -- position alone can't tell them apart. What can: a genuine
    # printed shoulder-note is a summary that INTRODUCES what follows it
    # in the main text (it's a marginal "argument", not a conclusion), so
    # it is essentially never the page's actual last paragraph -- real
    # prose almost always continues after it on the same page. Check the
    # second-to-last paragraph FIRST for exactly that reason, and only
    # fall back to the true last paragraph if that one isn't note-shaped.
    # Kept narrow (not "anywhere on the page") on purpose: a 3-paragraph
    # page's own opening paragraph can incidentally satisfy the same
    # shape checks (short-ish, ends a sentence) without being anywhere
    # near a margin note, so this must never reach back to paragraph 1.
    idx = None
    if len(paras) >= 3 and _note_shaped(paras[-2]):
        idx = len(paras) - 2
    elif _note_shaped(paras[-1]):
        idx = len(paras) - 1
    if idx is None:
        return text
    # Detect and remove it from body -- but do NOT render it anywhere.
    # Review feedback (2026-09-18): a page can carry more than one
    # margin-note-shaped paragraph (Gomard src 32 originally had two);
    # this function only ever finds the single best candidate, so
    # showing just one out of an unknown true count leaves the reader
    # unable to tell which printed note it corresponds to. Simplest
    # honest fix: keep it out of the body (still clearly not body prose)
    # without claiming to faithfully reproduce it.
    rest = "\n\n".join(paras[:idx] + paras[idx + 1:])
    if existing.strip():
        return f"{rest}\n\n{FN_HEADER}\n{existing.strip()}"
    return rest


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
    (docs/STATUS.md open item 9) -- that will space headings from a real
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
    source ("Riposte. — ...", "Coup d'arrêt. — ..."). A Category-2 term
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
        plain = re.sub(r"</?i>", "", s)
        # The stop-word count below only has a signal for FUNCTION words
        # (le, de, et...) -- a heading that's a single content word has
        # none to detect and was never even attempted (Prévost src 23:
        # "ROMPRE" standing alone as its own body-level title line stayed
        # untranslated, even though the same word translates correctly
        # mid-paragraph on the very same page, where sentence context
        # carries stop words around it). A short (<=2 word) all-caps
        # heading is checked regardless of the stop-word count.
        looks_french = (_french_token_count(plain) > 0
                        or (is_heading and len(s.split()) <= 2))
        if not is_heading or not looks_french:
            out.append(line)
            continue
        en = translate_with_llama(f"{PROMPT_TEMPLATE}\n\nText to translate:\n{s}")
        en = strip_model_preamble((en or "").strip()).strip()
        en = re.sub(r"(?m)^\s*#{1,6}\s+", "", en)
        en = re.sub(r"</?i>", "", en).splitlines()[0].strip() if en else ""
        en = apply_always_translate(en)
        # For the single-word case the old "strictly less French" gate is
        # blind (0 < 0 is never true) -- accept a genuinely different,
        # non-French result instead of requiring the stop-word count to
        # have dropped.
        if en and not contains_too_much_non_english(en) \
                and en.strip().lower() != re.sub(r"</?i>", "", s).strip().lower() \
                and (_french_token_count(en) < _french_token_count(s) or len(s.split()) <= 2):
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
    words_cased = re.findall(r"\b\w+\b", text)
    if not words_cased:
        return True
    words = [w.lower() for w in words_cased]
    # A French article/preposition sitting directly in front of a
    # Capitalized word is very likely a correctly-untranslated proper name
    # ("Cte J. de Reverseaux", "Parc des Princes"), not failed prose --
    # a page that's mostly a list of duel participants' names legitimately
    # stays almost entirely unchanged and shouldn't count against it
    # (Kirchhoffer src 38: "DUEL ANDRÉ GAUCHER... TOURNOI DE MONTE-CARLO").
    non_english_count = sum(
        1 for i, w in enumerate(words)
        if w in NON_ENGLISH_INDICATORS
        and not (i + 1 < len(words_cased) and words_cased[i + 1][:1].isupper()))
    if non_english_count / len(words) > 0.35:
        return True
    # Backstop for a page that stayed mostly French but is short on the
    # stop words above: elision (consonant + apostrophe + vowel, "d'une" /
    # "qu'il" -- English contractions put a consonant after the apostrophe)
    # and endings that need French-specific letters. An elision straight
    # into a Capitalized letter ("d'Hurcourt") is the same proper-name
    # case as above, not genuine untranslated prose.
    fr = sum(1 for m in re.finditer(
                r"\b[cdjlmnqst]['’]([aeiouyhàâäéèêëïîôùûü])", text, re.I)
             if not m.group(1).isupper()) \
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


_TOC_LINE_RE = re.compile(r"^.{1,70}?[.·•…\s]{4,}\s*\d{1,4}\s*$")


def is_toc_page(text: str) -> bool:
    """A table-of-contents / index page: most lines are
    `entry text … dot-leader … page number`. Such a page is neither prose
    nor a clean heading list -- the model applies the term policy
    unevenly and (worse) staples a once-per-book `<fn>` footnote onto a
    contents line. Detected pages translate with footnotes suppressed."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) < 4:
        return False
    hits = sum(1 for l in lines if _TOC_LINE_RE.match(l))
    return hits >= 0.5 * len(lines)

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
    body = {
        "model": TRANSLATION_MODEL_NAME,
        "messages": [
            {"role": "system", "content": "You are an expert translator of historical fencing texts."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.0 if EVAL_DETERMINISTIC else temperature,
        # a translated page is never longer than its source; the >2500-word
        # oversized guard handles the rare monster page. Leaves room in the
        # 8192 window for prompt+source.
        "max_tokens": 3000,
    }
    if EVAL_DETERMINISTIC:
        body["seed"] = EVAL_SEED
    for attempt in range(3):
        try:
            response = requests.post(
                LLAMA_API_URL,
                headers={"Content-Type": "application/json", **bc.auth_headers()},
                json=body,
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
    fig_path = f"{folder}/intermediate/figures.json"
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
            # Like translate_headers(), a caption is translated in total
            # isolation with no other quality gate -- a one-off garbled
            # word ("THRUST" -> "THUST", a duplicated gloss like "GATHERING
            # THE FEET" OF THE FEET") is unlikely to repeat identically
            # across independent samples, so collect all 3 and majority-
            # vote instead of stopping at the first that merely passes the
            # non-English check.
            candidates = []
            fallback = ""
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
                    fallback = fallback or cand
                    continue
                cand = _clean_caption(cand)
                cand = enforce_kept_terms(apply_always_translate(cand), cap)
                cand = fix_parry_names(cand, cap)
                # captions don't render a footnote block: translate the
                # Category-3 terms but drop any <fn> marker (backstop's and
                # a stray one from the model).
                cand = re.sub(r"<fn>.*?</fn>|</?fn>", "", cand,
                              flags=re.DOTALL | re.IGNORECASE)
                cand = enforce_cat3_footnotes(cand, set(), footnote=False)
                if cand:
                    candidates.append(cand)
            if candidates:
                counts = Counter(c.lower() for c in candidates)
                best_norm, _ = counts.most_common(1)[0]
                one = next(c for c in candidates if c.lower() == best_norm)
                fig["caption_en"] = match_case(cap, one) or cap
            elif fallback:
                fig["caption_en"] = match_case(cap, fallback) or cap
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
    ex_path = f"{folder}/intermediate/extracted.txt"
    if not os.path.exists(ex_path):
        return 0
    hp = f"{folder}/intermediate/headers.json"
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
    def _one(h):
        # A header/caption gets translated in complete isolation -- no
        # surrounding page for context -- and, unlike body text, a SINGLE
        # model call with no quality check at all. A small quantized model
        # occasionally garbles a short isolated phrase (ROMPRE -> ROMPRER,
        # "PARADES ET RIPOSTES" -> "PARRIES AND RAPISES") -- a one-off
        # sampling fluke that's very unlikely to repeat identically across
        # independent temperature-varied samples. Take 3 and majority-vote
        # instead of trusting the first.
        candidates = []
        for attempt in range(3):
            en = translate_with_llama(f"{PROMPT_TEMPLATE}\n\nText to translate:\n{h}",
                                       temperature=0.2 + 0.3 * attempt)
            en = strip_model_preamble((en or "").strip()).strip()
            en = re.sub(r'(?m)^\s*#{1,6}\s+', '', en)
            en = re.sub(r'</?i>', '', en)          # a header is plain text (rule 4)
            en = enforce_kept_terms(apply_always_translate(en), h)   # snap "RAPIER RAPIER" -> riposte
            en = enforce_cat3_footnotes(en, set(), footnote=False)
            en = re.sub(r'</?i>', '', en)          # drop the <i> enforce_kept_terms added
            en = en.splitlines()[0].strip() if en else ""
            en = re.sub(r'\s*:\s*$', '', en)       # "CONTRES:" -> "CONTRES" (P8)
            if en:
                candidates.append(en)
        if not candidates:
            return h
        counts = Counter(c.lower() for c in candidates)
        best_norm, _ = counts.most_common(1)[0]
        best = next(c for c in candidates if c.lower() == best_norm)
        return match_case(h, best)

    n = 0
    for h in sorted(raw):
        if h in hmap and not force:
            continue
        # "left\tright" == a two-part running head (F13); translate the
        # halves separately so the model doesn't collapse the gap.
        if "\t" in h:
            l, r = h.split("\t", 1)
            hmap[h] = _one(l) + "\t" + _one(r)
        else:
            hmap[h] = _one(h)
        n += 1
    with open(hp, "w", encoding="utf-8") as fh:
        _json.dump(hmap, fh, ensure_ascii=False, indent=1)
    if n:
        print(f"  translated {n} running header(s)")
    return n



def _norm_title(s: str) -> str:
    return re.sub(r"\s+", " ", _strip_accents(s).lower()).strip(" .-:")


def _is_title_line(line: str, src_title: str) -> bool:
    """Is a translation's first line the rendering of the source's title
    (vs. the first prose sentence, when the title never got translated)?"""
    t = re.sub(r"</?i>", "", line).strip()
    return bool(t) and len(t) < 120 \
        and len(t.split()) <= 2 * len(src_title.split()) + 4 \
        and not re.search(r"[.!?;:]\s+\S", t)


def harmonize_titles_with_headers(folder: str) -> int:
    """When a page prints the same text as its running head AND as a body
    title (Gomard/Prévost chapter openers), make the body title read
    exactly like the header's translation. The two were translated
    independently and diverge ("OF THE COUNTER-RIPOSTE" in the bar, "ON
    THE COUNTER-RIPOSTE" in the body -- Prévost src 21 and others). If
    the translation has no title line at all (the body title was restored
    to extracted.txt after the page was translated), insert it. No model
    call. Returns the number of pages changed."""
    import json as _json
    I = f"{folder}/intermediate"
    ex_p, tr_p, hp = f"{I}/extracted.txt", f"{I}/translation.txt", f"{I}/headers.json"
    if not all(os.path.exists(x) for x in (ex_p, tr_p, hp)):
        return 0
    ex = _pageio.split_pages(open(ex_p, encoding="utf-8").read())
    tr = _pageio.split_pages(open(tr_p, encoding="utf-8").read())
    hmap = _json.load(open(hp, encoding="utf-8"))
    n = 0
    for i in range(min(len(ex), len(tr))):
        m = re.search(r"(?im)^[ \t]*HEADER[ \t]*:[ \t]*(.+?)[ \t]*$", ex[i])
        h = m.group(1).strip() if m else ""
        if not h or "\t" in h or h.lower() == "none" or not hmap.get(h):
            continue
        body = clean_vlm_extraction(ex[i])
        src_title = next((l.strip() for l in body.split("\n") if l.strip()), "")
        if _norm_title(src_title) != _norm_title(h):
            continue
        t = tr[i]
        if t.strip().startswith("[") or not t.strip():     # empty / flagged page
            continue
        en = enforce_kept_terms(hmap[h], h)
        lines = t.split("\n")
        k = next(j for j, l in enumerate(lines) if l.strip())
        if lines[k].strip() == en:
            continue
        if _is_title_line(lines[k], src_title):
            lines[k] = en
        else:
            lines[k:k] = [en, ""]
        tr[i] = "\n".join(lines)
        n += 1
    if n:
        with open(tr_p, "w", encoding="utf-8") as fh:
            fh.write(_pageio.join_pages(tr))
        print(f"  harmonized {n} body title(s) with their running head")
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
            harmonize_titles_with_headers(folder)
    finally:
        if translation_proc is not REMOTE_SERVER and translation_proc:
            translation_proc.terminate()
    print("\n✓ Caption pass complete\n")
    sys.exit(0)

try:
    for folder in glob.glob("/data/processed/*_translated"):
        src = f"{folder}/intermediate/extracted.txt"
        dst = f"{folder}/intermediate/translation.txt"
        
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
            page_has_fig = (bool(re.search(r"(?im)^\s*FIGURES\s*:", page_text))
                            and not re.search(r"(?im)^\s*FIGURES\s*:\s*none\s*$",
                                              page_text))

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

            page_is_toc = is_toc_page(clean_text)

            # A scoring / poule table: replace each <table>..</table> with a
            # [[TABLE]] sentinel; steps 3 & 4 embed the scan for it. A page
            # that is nothing but table(s) (+ maybe a heading) skips the
            # model's attempt to linearise the grid.
            page_had_table = bool(re.search(r"<table\b", clean_text, re.I))
            if page_had_table:
                clean_text = re.sub(r"<table\b.*?</table\s*>", "\n[[TABLE]]\n",
                                    clean_text, flags=re.I | re.S)
                # a table the OCR truncated (page cut off mid-grid) has no
                # closing tag -- collapse the dangling <table ...> to end.
                clean_text = re.sub(r"<table\b.*\Z", "\n[[TABLE]]\n",
                                    clean_text, flags=re.I | re.S)
                clean_text = re.sub(r"(?:\[\[TABLE\]\]\s*){2,}", "[[TABLE]]\n",
                                    clean_text)
                clean_text = re.sub(r"\n{3,}", "\n\n", clean_text).strip()
                residue = re.sub(r"\[\[TABLE\]\]", "", clean_text).strip()
                res_paras = [p for p in re.split(r"\n\s*\n", residue) if p.strip()]
                if not any(len(p.split()) > 12 for p in res_paras):
                    # table(s) + at most a short heading/caption: translate
                    # just the heading text, keep one sentinel, drop the grid.
                    cap = ""
                    if residue:
                        raw = translate_with_llama(
                            f"{PROMPT_TEMPLATE}\n\nText to translate:\n{residue}")
                        cap = re.sub(r'(?m)^\s*#{1,6}\s+', '',
                                     strip_model_preamble((raw or residue).strip())).strip()
                        cap = apply_always_translate(cap)
                    out = f"{cap}\n\n[[TABLE]]" if cap else "[[TABLE]]"
                    translated_pages.append(out)
                    print(f"  Page {i}/{len(pages)} — table page, grid skipped")
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
                # is the only markup the pipeline expects. A decorative
                # section-break ornament (dinkus / row of asterisks) is
                # dropped to a blank line -- rendering it as "* * *" was
                # hit-or-miss and looked wrong (Gomard p255).
                candidate = re.sub(r'(?m)^[ \t]*(?:-{3,}|\*(?:[ \t]*\*)+|•(?:[ \t]*•)+)[ \t]*$',
                                   '', candidate)
                candidate = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', candidate)
                candidate = re.sub(r'\*\*([^*\n]+)\*\*', r'\1', candidate)   # **bold**
                candidate = re.sub(r'(?m)^\s{0,3}#{1,6}\s+', '', candidate)  # # heading
                # a whole body line that is a hallucinated figure
                # description ("Image of a hand holding a sword ...")
                candidate = re.sub(
                    r"(?im)^[ \t]*(?:an?\s+)?(?:image|photo(?:graph)?|drawing|"
                    r"illustration|picture|sketch|depiction)\s+(?:of|showing|"
                    r"depicting)\b[^\n]*$", "", candidate)
                # normalise the table sentinel: the model turns "[[TABLE]]"
                # into "[ TABLE ]", "[TABLE]" or a bare "TABLE" line.
                if page_had_table:
                    candidate = re.sub(r"(?im)^[ \t]*\[*[ \t]*TABLE[ \t]*\]*[ \t]*$",
                                       "[[TABLE]]", candidate)
                    candidate = re.sub(r"(?:\[\[TABLE\]\][ \t]*\n?){2,}",
                                       "[[TABLE]]\n", candidate)
                else:
                    candidate = re.sub(r"(?im)^\s*\[+\s*TABLE[^\]\n]*\]+\s*$",
                                       "[[TABLE]]", candidate)
                candidate = apply_always_translate(candidate)
                candidate = enforce_kept_terms(candidate, clean_text)
                # on a ToC page: translate the Cat-3 terms but emit NO
                # footnote marker (a contents line must not carry one, and
                # it would burn the once-per-book note), and skip the
                # heading re-translate pass (every entry looks like a
                # heading) to keep the "entry … NN" structure intact.
                candidate = enforce_cat3_footnotes(
                    candidate, cat3_seen, footnote=not page_is_toc)
                candidate = _capitalize_definition_entries(candidate)
                if not page_is_toc:
                    candidate = retranslate_stray_french_headings(candidate)
                    candidate = restore_dropped_part_title(candidate, clean_text)
                    candidate = fix_subheading_spacing(candidate)

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
                candidate = label_margin_notes(candidate, has_fig=page_has_fig)
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
        harmonize_titles_with_headers(folder)

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
