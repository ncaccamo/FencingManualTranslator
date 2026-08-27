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

PROMPT_FILE = "/app/translation_prompt.txt"
TRANSLATION_MODEL = os.environ.get("TRANSLATION_MODEL_PATH", "/models/Qwen2.5-7B-Instruct-Q6_K.gguf")
LLAMA_API_URL = "http://localhost:11434/v1/chat/completions"

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
    (libllama-server-impl.so) at runtime from /usr/local/lib/llama-<backend>/,
    a dir the entrypoint adds for the servers IT launches but that we don't
    get here -- so a server we spawn dies immediately with
    "cannot open shared object file". Restore it, matching the entrypoint's
    backend-resolution (fall back to rocm if the requested build is absent).
    """
    backend = os.environ.get("LLAMA_BACKEND", "rocm")
    if not os.access(f"/usr/local/bin/llama-server-{backend}", os.X_OK):
        backend = "rocm"
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = f"/usr/local/lib/llama-{backend}:" + env.get("LD_LIBRARY_PATH", "")
    return env


def start_translation_server():
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
            resp = requests.get("http://localhost:11434/health", timeout=2)
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
                headers={"Content-Type": "application/json"},
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

                translated_pages.append(candidate)
                success = True
                print(" ✓")
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
