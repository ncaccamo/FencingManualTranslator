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

FRENCH_WORDS = {"le", "la", "les", "un", "une", "des", "du", "de", "et", "est", "sont", "dans", "sur", "par", "pour", "avec", "au", "aux", "en", "il", "elle", "on", "ne", "pas", "mais", "ou", "si", "que", "qui", "quoi", "ce", "cet", "cette", "ces", "son", "sa", "ses", "leur", "mon", "ma", "ton", "ta", "notre", "votre"}
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
    """Attach notes to a translated page and append a delimited block (the
    header can't collide with the "\\n---\\n" page separator).

    Two sources:
      1. explicit `phrase<fn>note</fn>` the model emitted (numbered, with an
         inline † marker) -- rare with a 7B model.
      2. footnote_glossary.txt terms present in THIS page's SOURCE text and
         not yet glossed earlier in the book -- each glossed once, on first
         occurrence, as a bare "term — gloss" line (the 7B model won't keep
         terms in French, so there is nothing in the translation to anchor
         an inline marker to). Pass a per-book `seen` set to dedupe.
    Returns (text, n_notes)."""
    if seen is None:
        seen = set()
    numbered = []

    def take(note):
        numbered.append(re.sub(r"\s+", " ", note).strip())
        return f"{FN_MARK}{len(numbered)}"

    body = re.sub(r"<fn>(.*?)</fn>", lambda m: take(m.group(1)),
                  text, flags=re.DOTALL | re.IGNORECASE).strip()

    src = _strip_accents(source_text.lower())
    gloss_lines = []
    for key, (term, gloss) in GLOSSARY.items():
        pos = src.find(key)
        if pos != -1 and key not in seen and len(gloss_lines) < FN_MAX_PER_PAGE:
            seen.add(key)
            gloss_lines.append((pos, f"{term} — {gloss}"))
    gloss_lines = [g for _, g in sorted(gloss_lines)]

    lines = [f"{FN_MARK}{i} {n}" for i, n in enumerate(numbered, 1)] + gloss_lines
    if not lines:
        return body, 0
    return f"{body}\n\n{FN_HEADER}\n" + "\n".join(lines), len(lines)


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
    return (non_english_count / len(words)) > 0.35

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
        "--ctx-size", "4096",
        "--n-gpu-layers", "999",
        "--threads", str(os.cpu_count())
    ]

    print("Starting server (loads ~7GB VRAM, may take 30-120s)...\n")

    log_file = open("/tmp/translation_server.log", "w")
    proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT,
                            env=_llama_server_env())
    
    dots = 0
    for attempt in range(90):
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
    
    print(f"\n\n✗ Server timeout after 180s")
    print("Check logs: docker exec booktranslator-translator-1 cat /tmp/translation_server.log\n")
    log_file.close()
    proc.terminate()
    return None

def translate_with_llama(prompt: str) -> str:
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
                    "temperature": 0.3,
                    "max_tokens": 4096
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

print("\n" + "="*60)
print("STEP 2: AI TRANSLATION")
print("="*60)

translation_proc = start_translation_server()

if translation_proc is None:
    print("✗ Cannot start translation server\n")
    exit(1)

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
            pages = [p.strip() for p in f.read().split("\n---\n") if p.strip()]

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

            src_words = len(clean_text.split())
            success = False
            best_candidate = None   # best rejected attempt, for the fallback
            for attempt in range(3):
                print(f"  Page {i}/{len(pages)} (try {attempt + 1})", end="", flush=True)

                full_prompt = f"{PROMPT_TEMPLATE}\n\nText to translate:\n{clean_text}"
                candidate = translate_with_llama(full_prompt)

                if not candidate:
                    print(" → failed")
                    continue

                # The model sometimes emits Markdown despite the prompt. A
                # lone "---" thematic break collides with the "\n---\n" page
                # separator used everywhere downstream (178 pages -> 179,
                # every later page misaligned). Strip all of it; <i>...</i>
                # is the only markup the pipeline expects.
                candidate = re.sub(r'(?m)^\s*-{3,}\s*$', '* * *', candidate)
                candidate = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', candidate)
                candidate = re.sub(r'\*\*([^*\n]+)\*\*', r'\1', candidate)   # **bold**
                candidate = re.sub(r'(?m)^\s{0,3}#{1,6}\s+', '', candidate)  # # heading

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
                if best_candidate:
                    print(" ⚠ kept (low confidence)")
                    translated_pages.append(f"[low-confidence translation — review]\n{best_candidate}")
                else:
                    print(" ✗ no usable output — kept original text")
                    translated_pages.append(f"[translation unavailable — original text follows]\n{clean_text}")

        with open(dst, "w", encoding="utf-8") as f:
            f.write("\n---\n".join(translated_pages))

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
