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
TRANSLATION_MODEL = os.environ.get("TRANSLATION_MODEL_PATH", "/models/Qwen2.5-7B-Instruct-Q6_K.gguf")
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


def _load_glossary(path="/app/footnote_glossary.txt") -> dict:
    g = {}
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "|" not in line:
                continue
            term, gloss = line.split("|", 1)
            g[_strip_accents(term.strip().lower())] = (term.strip(), gloss.strip())
    except FileNotFoundError:
        pass
    return g


GLOSSARY = _load_glossary()


def pull_footnotes(text: str, source_text: str = "", seen: set = None):
    """Attach numbered notes to a translated page and append a delimited
    block (the header can't collide with the page separator).

    Two sources, both anchored to a real spot in the TRANSLATION with a
    `†N` marker:
      1. explicit `phrase<fn>note</fn>` the model emitted -- rare with a
         7B model.
      2. a `footnote_glossary.txt` term the translation kept as
         `<i>term</i>` and that hasn't been glossed earlier in the book --
         the first such occurrence gets a `†N` right after `</i>` and a
         "term — gloss" line. Matching is on the italicised text only
         (accents ignored, trailing plural -s allowed), NOT the French
         source -- a substring scan of the source glossed the verb
         *appeler* as the term *appel*. Pass a per-book `seen` to dedupe.
    `source_text` is unused now; kept for call-site compatibility.
    Returns (text, n_notes)."""
    if seen is None:
        seen = set()
    numbered = []

    def take(note):
        numbered.append(re.sub(r"\s+", " ", note).strip())
        return f"{FN_MARK}{len(numbered)}"

    body = re.sub(r"<fn>(.*?)</fn>", lambda m: take(m.group(1)),
                  text, flags=re.DOTALL | re.IGNORECASE).strip()

    def gloss_italic(m):
        if len(numbered) >= FN_MAX_PER_PAGE:
            return m.group(0)
        inner = _strip_accents(m.group(1).strip().lower())
        for key, (term, gloss) in GLOSSARY.items():
            if key in seen:
                continue
            if inner == key or inner == key + "s" or \
               re.search(r"\b" + re.escape(key) + r"s?\b", inner):
                seen.add(key)
                return m.group(0) + take(f"{term} — {gloss}")
        return m.group(0)

    body = re.sub(r"<i>(.*?)</i>", gloss_italic, body,
                  flags=re.DOTALL | re.IGNORECASE)

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
    # "fer" = the (opponent's) steel/blade. Safe phrases only -- NOT bare
    # "fer", which would break the kept term "prise de fer" (rule 2) and
    # the glossary term "sentiment du fer".
    (r"attaques au fer", "attacks on the blade"), (r"attaque au fer", "attack on the blade"),
    (r"sur le fer", "on the blade"), (r"au fer", "on the blade"),
    (r"le fer", "the blade"),
    # "l'escrime au fleuret" -> "foil fencing"; the 7B renders it
    # "épée fencing with the fleuret" (hallucinates épée, keeps fleuret).
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
    return text


_TITLE_STOPWORDS = {
    "a", "an", "the", "and", "or", "nor", "but", "of", "to", "in", "on", "at",
    "by", "for", "with", "from", "as", "per", "via",
}


def match_case(src: str, dst: str) -> str:
    """Make a translated heading / caption follow the CASE STYLE of its
    source, so a book that sets every running head in capitals gets an
    all-caps English head too (not "épée" one page and "Épée" the next).
      source ALL CAPS  -> dst.upper()
      source Title Case -> Title Case (articles/prepositions stay lower)
      anything else     -> capitalise the first letter only
    Words the model kept in the source language (already inside <i>..</i>)
    keep their own case."""
    if not dst:
        return dst
    letters = [c for c in src if c.isalpha()]
    if not letters:
        return dst
    words = [w for w in re.findall(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", src) if any(c.isalpha() for c in w)]
    upper_ratio = sum(c.isupper() for c in letters) / len(letters)

    if upper_ratio >= 0.8 and len(letters) >= 2:
        return _recase(dst, str.upper)
    if words and all(w[0].isupper() for w in words):
        def _title(m, first=[True]):
            w = m.group(0)
            lead = first[0]
            first[0] = False
            if not lead and w.lower() in _TITLE_STOPWORDS:
                return w.lower()
            return w[:1].upper() + w[1:].lower()
        return _recase(dst, lambda s: re.sub(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", _title, s))
    # sentence case: first letter up, leave the rest as the model wrote it
    return _recase(dst, lambda s: re.sub(r"[A-Za-zÀ-ÿ]", lambda m: m.group(0).upper(), s, count=1))


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
    print("LOADING TRANSLATION MODEL (Qwen2.5-7B)")
    print("="*60 + "\n")
    print("Model: Qwen2.5-7B-Instruct-Q6_K.gguf (~6.0GB)")
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
                    "model": "qwen2.5-7b-instruct",
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
            cap = (fig.get("caption") or "").strip()
            if not cap:
                fig.pop("caption_en", None)
                continue
            if fig.get("caption_en") and not force:
                continue
            en = translate_with_llama(
                f"{PROMPT_TEMPLATE}\n\nText to translate:\n{cap}")
            en = strip_model_preamble((en or "").strip()).strip()
            en = re.sub(r'(?m)^\s*#{1,6}\s+', '', en)
            fig["caption_en"] = match_case(
                cap, apply_always_translate(en.splitlines()[0].strip())) if en else cap
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
        en = apply_always_translate(en)
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
        glossed = set()   # footnote each glossary term once per book

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
                candidate = apply_always_translate(candidate)
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

                candidate, n_fn = pull_footnotes(candidate, clean_text, glossed)
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
                                                 clean_text, glossed)
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
