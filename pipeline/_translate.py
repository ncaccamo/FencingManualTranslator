# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Step 2, translation: the part that talks to the model -- the prompt, the
server and its client, translate_page() with its retries, the caption /
header / table passes, the overrides and the resume state. The rules applied
to the model's English are in _tx_terms (terms), _tx_case (letter case),
_tx_notes (footnotes), _tx_checks (checks on a try) and _tx_structure (page
structure). Importable without starting a server; the command is main()."""

import glob
import json
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
import _overrides
import _pagekind
import _progress
from _tx_case import (
    _shout_ratio, caps_kept_terms, deshout, match_case, match_dash_caps, source_caps_lines,
    source_case_kept_terms,
)
from _tx_checks import (
    _french_token_count, _line_name_counts, contains_too_much_non_english, has_wrong_script,
    is_near_blank, is_toc_page, line_names_swapped, looks_like_refusal, lost_negations,
    note_bodies, quotation_left_untranslated, strip_model_preamble, untranslated_paragraph,
)
from _tx_common import _LANG_REF, _strip_accents
from _tx_notes import extract_source_footnotes, pull_footnotes
from _tx_structure import (
    _capitalize_definition_entries, _clean_caption, fix_subheading_spacing,
    match_boundary_punctuation, restore_dividers, restore_dropped_part_title,
    restore_table_markers,
)
from _tx_terms import (
    apply_always_translate, enforce_footnoted_terms, enforce_kept_terms, enforce_maestro,
    fix_known_garbles, fix_parry_names, house_italics, restore_blade_phrases, restore_rapier,
    restore_reprise, restore_straight_thrust,
)

PROMPT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "translation_prompt.txt")
TRANSLATION_MODEL = os.environ.get("TRANSLATION_MODEL_PATH", "/models/Qwen3.8-27B.i1-Q4_K_S.gguf")
# Qwen3.8-27B 4-bit is ~15.8 GB: it doesn't fit whole in 16 GB of VRAM, so a
# fixed number of its 64 layers go on the GPU and the rest on the CPU. 46
# fits a dedicated 16 GB card (local mode); in remote mode the GPU system's
# llama-swap entry sets its own (fewer: other programs share that card;
# docs/remote-gpu.md). A smaller model can take 999 (= all).
TRANSLATION_GPU_LAYERS = os.environ.get("TRANSLATION_GPU_LAYERS", "46")
# Request "model" label. Ignored by a single-model llama-server (local /
# simple remote); the routing key under llama-swap. Kept in sync with the
# actual model via env, falling back to the model file's own name.
TRANSLATION_MODEL_NAME = (os.environ.get("TRANSLATION_MODEL_NAME")
                          or os.environ.get("MODEL_NAME")
                          or Path(TRANSLATION_MODEL).stem)
LLAMA_API_URL = f"{bc.translate_base_url()}/v1/chat/completions"

# First try at temperature 0 (2026-10-10, was 0.3 since the 7B days): the
# model's most likely translation, so a retranslated page keeps its wording
# (35 of 36 test pages repeat word for word) and terms don't drift by chance.
# Scored against published translations (evals/gold_passages.py, 60
# passages): 46.49 chrF++ at 0 vs 46.31 / 46.22 for two runs at 0.3 -- no
# loss, a small consistent gain. Retries stay hotter (0.55, 0.8) to break a
# failed answer; the 3-sample majority votes for headers and captions keep
# their own temperatures (they need samples that can differ).
FIRST_TRY_TEMPERATURE = 0.0

# Deterministic mode for the eval harness: temp 0 + a fixed seed so a
# re-run of an unchanged book reproduces byte-for-byte and `evals/check.py`
# page-diffs mean something. Off by default (production: first try at temp 0, see above, with
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
BASE_PROMPT = PROMPT_TEMPLATE   # + each book's [terms] from its overrides file
PROMPT_FILE_IT = os.path.join(os.path.dirname(PROMPT_FILE), "translation_prompt_it.txt")


def prompt_for_language(lang: str) -> str:
    """The base prompt for a source language. translation_prompt.txt is the
    French prompt; for an Italian book its language-specific parts -- rules
    2-3b (kept / translated / footnoted terms) and the examples -- are
    replaced by translation_prompt_it.txt. Everything else is shared."""
    if lang != "Italian" or not os.path.exists(PROMPT_FILE_IT):
        return BASE_PROMPT
    it = open(PROMPT_FILE_IT, encoding="utf-8").read().strip()
    rules, _, examples = it.partition("=== EXAMPLES ===")
    p = BASE_PROMPT
    a, b = p.index("2. KEEP"), p.index("4. Headings")
    p = p[:a] + rules.strip() + "\n\n" + p[b:]
    c, d = p.index("EXAMPLES\n"), p.index("Text to translate:")
    return p[:c] + "EXAMPLES\n\n" + examples.strip() + "\n\n" + p[d:]


def book_language(stem: str) -> str:
    """The book's source language: books/<stem>.meta.json "language" (an
    evaluation copy's meta file sits next to it in evals/gold/), else a
    guess from the extracted text's function words."""
    for base in (os.environ.get("BOOKS_DIR", "/data/books"), "/data/books", "/data/books/_skip"):
        mp = os.path.join(base, f"{stem}.meta.json")
        if os.path.exists(mp):
            try:
                lang = json.load(open(mp, encoding="utf-8")).get("language", "")
                if lang:
                    return lang
            except (OSError, ValueError):
                pass
    for mp in glob.glob(f"/data/evals/gold/*/{stem}.meta.json"):
        try:
            return json.load(open(mp, encoding="utf-8")).get("language", "French") or "French"
        except (OSError, ValueError):
            pass
    ep = f"/data/processed/{stem}_translated/intermediate/extracted.txt"
    if os.path.exists(ep):
        words = re.findall(r"[a-zàèéìòù]+", open(ep, encoding="utf-8").read(200000).lower())
        it = sum(w in {"il", "della", "di", "che", "si", "nel", "sono", "gli", "colpo", "sciabola"} for w in words)
        fr = sum(w in {"le", "les", "des", "du", "que", "est", "une", "dans", "coup", "épée"} for w in words)
        if it > fr:
            return "Italian"
    return "French"


def prompt_for_book(stem: str) -> str:
    """Set BOOK_LANG for this book and return its full prompt: the base
    prompt for its language + its own [terms] (overrides file)."""
    global BOOK_LANG
    BOOK_LANG = book_language(stem)
    return prompt_for_language(BOOK_LANG) + _overrides.terms_addendum(_overrides.load(stem))

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

# The book being translated's source language (set per book with its prompt,
# see prompt_for_book). The language rules take it as their `lang` argument
# (footnoted-term list, "Fr."/"It." reference, kept-term backstop ...); only
# step 2's own passes read it here.
BOOK_LANG = "French"


def retranslate_stray_french_headings(text: str) -> str:
    """A short all-caps display heading on an otherwise-good page can come
    back half-translated ("COMMENT ON DOIT HOLD THE SABER") -- the per-page
    French ratio is too diluted by the good body to trip the quality gate.
    Re-translate just those lines through the full prompt (as translate_headers
    does), and keep the result only if it is less French than what we had."""
    out = []
    for line in text.split("\n"):
        s = line.strip()
        # a pipeline marker ("[[TABLE]]", "[Empty page]", a margin note) is
        # not a heading: sent to the model it came back as "TABLE" or nothing,
        # and the table was lost (Masiello 1887, 9 pages, 2026-10-12)
        if re.fullmatch(r"\[\[?[^\[\]\n]*\]\]?|⟦[^⟧]*⟧", s):
            out.append(line)
            continue
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
        # this second answer skips the page's repairs -- run the known-
        # garble ones ("RIPPOSTES TAC-AU-TAC.", Gomard scan 242: the kept
        # term's "AU" made the heading look French and sent it back here)
        en = fix_known_garbles(en, s)
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

def _llama_server_env() -> dict:
    """A `docker exec` of this script inherits only the image-level
    LD_LIBRARY_PATH; llama-server links its backend impl .so
    (libllama-server-impl.so) at runtime from /usr/local/lib/llama-<suffix>/,
    a dir set only in the entrypoint's own environment, which a `docker
    exec` doesn't get -- so a server we spawn dies immediately with
    "cannot open shared object file". Set it for the configured backend
    (validated; rocm714 errors clearly if it isn't built). This path, not
    the /usr/local/bin/llama-server link (fixed at build time, vulkan), is
    what picks the backend: every llama-server-* loads the impl it finds
    here (checked 2026-10-19: with the rocm dir, libggml-hip loads)."""
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
    print(f"Port:  11434 | GPU layers: {TRANSLATION_GPU_LAYERS}\n")

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
        # Not 4096: PROMPT_TEMPLATE (term policy + EXAMPLES) is ~1.5k
        # tokens and prepended to every call, so a long source page + a
        # full-length response overran a 4096 window ("Context size has
        # been exceeded" -> empty response -> "kept original text"). 8192,
        # not 6144, since 2026-10-12: the French prompt grew to ~4.9k tokens
        # and long pages were cut mid-sentence (see LAST_CUT).
        "--ctx-size", "8192",
        "--n-gpu-layers", TRANSLATION_GPU_LAYERS,
        # llama.cpp now defaults to 4 slots = 4x the context memory
        "--parallel", "1",
        # the model's own chat template, thinking off (Qwen3.x reasons by
        # default, which only slows translation)
        "--jinja", "--reasoning-budget", "0",
        "--threads", str(os.cpu_count())
    ]

    print("Starting server (~60s warm, up to ~4min on a cleared GPU shader "
          "cache)...\n")

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
            print("Check logs: docker exec fmt-translator cat /tmp/translation_server.log\n")
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
    print("Check logs: docker exec fmt-translator cat /tmp/translation_server.log\n")
    log_file.close()
    proc.terminate()
    return None

LAST_MODEL = ""   # model file of the last reply (set by translate_with_llama)
MODEL_CALLS = 0   # replies so far -- tells a page that used the model from one skipped as blank
LAST_CUT = False  # the last reply stopped because it ran out of room (finish_reason "length")


def translate_with_llama(prompt: str, temperature: float = FIRST_TRY_TEMPERATURE) -> str:
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
    attempt = 0
    while attempt < 3:
        try:
            response = requests.post(
                LLAMA_API_URL,
                headers={"Content-Type": "application/json", **bc.auth_headers()},
                json=body,
                timeout=1800
            )
            if response.status_code in _SERVER_DOWN_STATUS and wait_for_remote_server():
                continue                    # the server came back: same request again
            if response.status_code == 200:
                result = response.json()
                if "choices" in result and len(result["choices"]) > 0:
                    # the model file the server actually ran (llama-server
                    # reports its path) -- what the cover page credits
                    global LAST_MODEL, MODEL_CALLS, LAST_CUT
                    MODEL_CALLS += 1
                    # out of room: the context window (prompt + source + reply)
                    # filled and the reply stops mid-sentence. 2026-10-12: the
                    # French prompt reached 4,891 tokens of a 6,144 window and
                    # long pages were cut at ~330 words (Kirchhoffer scans 26,
                    # 35, 52, 70), only one caught by the too-short check.
                    LAST_CUT = result["choices"][0].get("finish_reason") == "length"
                    m = str(result.get("model") or "")
                    LAST_MODEL = re.sub(r"\.gguf$", "", re.split(r"[\\/]", m)[-1]) or LAST_MODEL
                    # A reasoning model (Qwen3.x, Gemma 4) run with thinking
                    # left on puts its reasoning in a separate field (ignored
                    # here) or inline as <think>...</think> -- never let that
                    # reach a translation.
                    text = result["choices"][0]["message"].get("content") or ""
                    text = re.sub(r"(?is)<think>.*?</think>", "", text)
                    text = re.sub(r"(?is)^.*?</think>", "", text)   # opening tag cut off
                    return text.strip()
            
            if attempt < 2:
                time.sleep(5)
        except (requests.ConnectionError, requests.exceptions.ConnectTimeout):
            if wait_for_remote_server():
                continue                    # the server came back: same request again
            if attempt < 2:
                time.sleep(10)
        except Exception:
            if attempt < 2:
                time.sleep(10)
        attempt += 1

    return None


# The remote GPU system unreachable (power cut, reboot, llama-swap restart)
# is not a translation failure: wait for it and send the same request again,
# instead of using up the page's attempts and keeping the original text --
# which on 2026-10-02 marked 33 pages of a book "[translation unavailable]"
# in a few minutes. Local mode doesn't wait: the pipeline owns that server,
# and it won't come back by itself.
_SERVER_DOWN_STATUS = (502, 503, 504)
SERVER_WAIT_HOURS = float(os.environ.get("SERVER_WAIT_HOURS", "24"))


def wait_for_remote_server(poll: int = 60) -> bool:
    """Remote mode, server down: block until it answers /health again (True),
    or give up after SERVER_WAIT_HOURS (False). Server up -- the error was
    something else -- or local mode: False at once, so the caller counts it
    as a failed attempt (no endless retry against a server that answers)."""
    if not bc.is_remote():
        return False
    base = bc.translate_base_url()
    def up():
        try:
            return requests.get(f"{base}/health", headers=bc.auth_headers(),
                                timeout=10).status_code == 200
        except requests.RequestException:
            return False
    if up():
        return False
    print(f"\n  [{time.strftime('%H:%M')}] GPU server not answering -- waiting "
          f"(up to {SERVER_WAIT_HOURS:g} h)", flush=True)
    t0 = time.time()
    _progress.waiting(True)
    deadline = t0 + SERVER_WAIT_HOURS * 3600
    while time.time() < deadline:
        time.sleep(poll)
        if up():
            print(f"  [{time.strftime('%H:%M')}] GPU server back -- continuing", flush=True)
            _progress.waiting(False, since=t0)
            return True
    return False


def translate_figure_captions(folder: str, force: bool = False) -> int:
    """Translate the source captions in figures.json (written by step 1) in
    place, adding a 'caption_en' to each. Uses the full translation prompt
    -- plate captions are headings ("PARADE DE SIXTE" etc.) and need the
    same term policy as body text. Skips captions already done unless
    `force`. Returns the number translated."""
    fig_path = f"{folder}/intermediate/figures.json"
    if not os.path.exists(fig_path):
        return 0
    with open(fig_path, encoding="utf-8") as fh:
        fig_map = json.load(fh)
    # the reviewer's caption decisions first, so a corrected caption is
    # translated in this same pass
    _overrides.apply_captions(fig_map, _overrides.load(Path(folder).name.replace("_translated", "")))
    n = 0
    for figs in fig_map.values():
        for fig in figs:
            raw = (fig.get("caption") or "").strip()
            cap = raw if fig.get("decided") else _clean_caption(raw)
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
                cand = enforce_kept_terms(apply_always_translate(cand), cap, lang=BOOK_LANG)
                cand = fix_parry_names(cand, cap)
                # captions don't render a footnote block: translate the
                # footnoted terms but drop any <fn> marker (backstop's and
                # a stray one from the model).
                cand = re.sub(r"<fn>.*?</fn>|</?fn>", "", cand,
                              flags=re.DOTALL | re.IGNORECASE)
                cand = enforce_footnoted_terms(cand, set(), footnote=False, lang=BOOK_LANG)
                if cand:
                    candidates.append(cand)
            if candidates:
                counts = Counter(c.lower() for c in candidates)
                best_norm, _ = counts.most_common(1)[0]
                one = next(c for c in candidates if c.lower() == best_norm)
                fig["caption_en"] = caps_kept_terms(source_case_kept_terms(match_case(cap, one) or cap, cap), cap)
            elif fallback:
                fig["caption_en"] = caps_kept_terms(source_case_kept_terms(match_case(cap, fallback) or cap, cap), cap)
            else:
                fig["caption_en"] = cap
            n += 1
    with open(fig_path, "w", encoding="utf-8") as fh:
        json.dump(fig_map, fh, ensure_ascii=False, indent=1)
    if n:
        print(f"  translated {n} figure caption(s)")
    return n


def _cell_text(cell_html: str) -> str:
    import html as _html
    return _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell_html))).strip()


BR = " <br> "


def _cell_lines(cell_html: str) -> str:
    """A cell's text with its printed line breaks kept as " <br> " -- they
    part the items of a list cell (Masiello's ripostes «filo, o cavazione al
    fianco<br>botta dritta, ...»), which run together without them."""
    return BR.join(t for t in (_cell_text(x) for x in re.split(r"<br\s*/?>", cell_html, flags=re.I)) if t)


def _cell_post(cell: str, src: str) -> str:
    """The rules every translated cell gets, line by line against its source line."""
    out = []
    for c, t in zip(cell.split(BR.strip()), src.split(BR.strip())):
        c, t = c.strip(), t.strip()
        c = enforce_kept_terms(apply_always_translate(c), t, lang=BOOK_LANG)
        c = fix_parry_names(c, t)
        c = enforce_footnoted_terms(c, set(), footnote=False, lang=BOOK_LANG)
        out.append(caps_kept_terms(source_caps_lines(source_case_kept_terms(c, t), t), t))
    return BR.join(out)


def number_cells_or_and(table_html: str) -> str:
    """A table cell of numbers joined by Italian "o" / "e" (Bruno: "3. o 1.
    e 2.") has no word for the model, so it is never sent; its or / and are
    put in English here (2026-10-18)."""
    return re.sub(r"(<t[hd]\b[^>]*>)([\d.,\s]*\d[\d.,\s]*(?:\s[oe]\s[\d.,\s]+)+)(</t[hd]>)",
                  lambda m: m.group(1) + re.sub(r"\s([oe])\s", lambda w: " or " if w.group(1) == "o"
                                                else " and ", m.group(2)) + m.group(3), table_html)


def translate_tables(folder: str, force: bool = False) -> int:
    """Translate the tables of the book (reviewer, 2026-10-18: the side-by-side
    showed only "[table — see facing page]"; Masiello 1887 has 135 table
    pages). The OCR gives each table as HTML; each distinct cell text is
    translated once, as a numbered list with the book's full prompt, then
    given the caption treatment (footnote markers dropped -- a cell has no
    room for a note and the term is footnoted in the running text -- kept
    terms, the book's case). Written to intermediate/tables.json as
    {scan index: {"src": [table html], "en": [english html or null]}}; a
    page whose tables are unchanged is skipped unless `force`. A table the
    OCR read as headings only (no body cell has text, at most 3 rows --
    Kirchhoffer's bout order) is stored as null: the PDFs keep the pointer
    to the scan. Returns the number of pages translated."""
    ex_path, path = f"{folder}/intermediate/extracted.txt", f"{folder}/intermediate/tables.json"
    if not os.path.exists(ex_path):
        return 0
    pages = _pageio.split_pages(open(ex_path, encoding="utf-8").read())
    done = json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}
    n = 0
    for i, page in enumerate(pages):
        src = clean_vlm_extraction(page)
        tables = re.findall(r"<table\b.*?</table\s*>", src, re.S | re.I)
        tail = re.search(r"<table\b(?:(?!</table).)*\Z", src, re.S | re.I)   # a grid the page cut off
        if tail:
            tables.append(tail.group(0) + "</table>")
        if not tables:
            done.pop(str(i), None)
            continue
        prev = done.get(str(i), {})
        if not force and prev.get("src") == tables and (prev.get("br") or not any(
                re.search(r"<br", t, re.I) for t in tables)):
            continue                                # (translated before cells kept their breaks: again)
        en = []
        for tb in tables:
            cells = re.findall(r"<t([hd])\b[^>]*>(.*?)</t[hd]>", tb, re.S | re.I)
            if BOOK_LANG == "Italian":
                tb = number_cells_or_and(tb)
            body = [_cell_text(c) for kind, c in cells if kind.lower() == "d"]
            rows = len(re.findall(r"<tr\b", tb, re.I))
            if not any(re.search(r"\w", b) for b in body) and rows <= 3:
                en.append(None)
                continue
            texts = list(dict.fromkeys(t for t in (_cell_lines(c) for _, c in cells)
                                       if re.search(r"[^\W\d_]{2,}", t)))
            tr = {}

            def _ask(chunk, brs):
                listing = "\n".join(f"{j}. {t}" for j, t in enumerate(chunk, 1))
                keep = (" A <br> in a line marks a line break in the printed cell: keep every <br> where "
                        "it falls, the same number in each line.") if brs else ""
                reply = translate_with_llama(
                    f"{PROMPT_TEMPLATE}\n\nThe text below is the cells of a TABLE, one per numbered line. "
                    f"Translate each line on its own, keeping its number; answer with exactly {len(chunk)} "
                    f"numbered lines and nothing else.{keep}\n\nText to translate:\n{listing}")
                got = dict(re.findall(r"(?m)^\s*(\d+)\.\s*(.*\S)", strip_model_preamble(reply or "")))
                return [re.sub(r"\s*<br\s*/?>\s*", BR,
                               re.sub(r"<fn>.*?</fn>|</?fn>", "", got.get(str(j), ""), flags=re.S | re.I)).strip()
                        for j in range(1, len(chunk) + 1)]
            redo = []                               # cells whose breaks the model moved or lost
            for k in range(0, len(texts), 60):
                chunk = texts[k:k + 60]
                for t, cell in zip(chunk, _ask(chunk, any(BR in t for t in chunk))):
                    if cell and cell.count(BR.strip()) != t.count(BR.strip()):
                        redo.append(t)
                        continue
                    if not cell or contains_too_much_non_english(cell) and len(cell.split()) > 3:
                        tr[t] = t                   # kept as printed: better than a wrong guess
                        continue
                    tr[t] = _cell_post(cell, t)
            # each line of such a cell on its own: the breaks stay where printed
            lines = list(dict.fromkeys(x for t in redo for x in t.split(BR)
                                       if re.search(r"[^\W\d_]{2,}", x)))
            one = {}
            for k in range(0, len(lines), 60):
                chunk = lines[k:k + 60]
                one.update(zip(chunk, _ask(chunk, False)))
            for t in redo:
                parts = t.split(BR)
                got = [one.get(x) or x for x in parts]
                tr[t] = _cell_post(BR.join(got), t) if all(one.get(x) or not re.search(r"[^\W\d_]{2,}", x)
                                                           for x in parts) else t

            def _sub(m, tr=tr):
                raw = _cell_lines(m.group(3))
                return f"<{m.group(1)}{m.group(2)}>{tr.get(raw, raw).replace(BR, '<br>')}</{m.group(1)}>"
            en.append(re.sub(r"\s+", " ", re.sub(r"<(t[hd])\b([^>]*)>(.*?)</t[hd]>", _sub, tb,
                                                 flags=re.S | re.I)))
        done[str(i)] = {"src": tables, "en": en, "br": True}
        n += 1
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(done, fh, ensure_ascii=False, indent=1)
    if n:
        print(f"  translated the tables of {n} page(s)")
    return n


def translate_headers(folder: str, force: bool = False) -> int:
    """Translate each distinct running header from extracted.txt into a
    headers.json { raw -> english } map, used by the PDF steps. Headers
    are section/chapter titles ("LES PARADES", "SOCIÉTÉS") -- an English
    reader needs to know what section they're in. Translated once per
    distinct string, through the full prompt (rule 4: headings)."""
    ex_path = f"{folder}/intermediate/extracted.txt"
    if not os.path.exists(ex_path):
        return 0
    hp = f"{folder}/intermediate/headers.json"
    hmap = {}
    if os.path.exists(hp) and not force:
        with open(hp, encoding="utf-8") as fh:
            hmap = json.load(fh)
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
            en = enforce_kept_terms(apply_always_translate(en), h, lang=BOOK_LANG)   # snap "RAPIER RAPIER" -> riposte
            en = enforce_footnoted_terms(en, set(), footnote=False, lang=BOOK_LANG)
            en = re.sub(r'</?i>', '', en)          # drop the <i> enforce_kept_terms added
            en = re.sub(r'\s*<fn>.*?</fn>|</?fn>|†\d*', '', en)   # a running head carries no note (Spinnewyn "BIND<fn>…")
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
        json.dump(hmap, fh, ensure_ascii=False, indent=1)
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
    I = f"{folder}/intermediate"
    ex_p, tr_p, hp = f"{I}/extracted.txt", f"{I}/translation.txt", f"{I}/headers.json"
    if not all(os.path.exists(x) for x in (ex_p, tr_p, hp)):
        return 0
    ex = _pageio.split_pages(open(ex_p, encoding="utf-8").read())
    tr = _pageio.split_pages(open(tr_p, encoding="utf-8").read())
    hmap = json.load(open(hp, encoding="utf-8"))
    n = 0
    for i in range(min(len(ex), len(tr))):
        m = re.search(r"(?im)^[ \t]*HEADER[ \t]*:[ \t]*(.+?)[ \t]*$", ex[i])
        h = m.group(1).strip() if m else ""
        if not h or "\t" in h or h.lower() == "none" or not hmap.get(h):
            continue
        body = clean_vlm_extraction(ex[i])
        src_title = next((l.strip() for l in body.split("\n")
                          if l.strip() and not l.strip().startswith("⟦MARGIN:")), "")
        if _norm_title(src_title) != _norm_title(h):
            continue
        t = tr[i]
        if t.strip().startswith("[") or not t.strip():     # empty / flagged page
            continue
        en = enforce_kept_terms(hmap[h], h, lang=BOOK_LANG)
        lines = t.split("\n")
        k = next((j for j, l in enumerate(lines)
                  if l.strip() and not l.strip().startswith("⟦MARGIN:")), None)
        if k is None:
            continue
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


def _house_fixes(ov: dict, french: bool) -> dict:
    """ov with each [[fix]]'s text under the italics rules, so a fix written
    before a rule changed still finds the text and leaves it rule-true."""
    out = []
    for f in ov["fix"]:
        g = {k: (house_italics(v, french, quotes=False) if k in ("find", "replace", "prepend") else v)
             for k, v in f.items()}
        out.append(f if "find" in g and g["find"] in g["replace"] else g)
    return dict(ov, fix=out)


def move_note_continuations(folder: str) -> int:
    """A book's footnote paragraph the English has as text -- a note's
    second paragraph, or its part carried over to the next page -- moved
    into the page's notes (general rule 1, _note_continuations.py: the
    scan's smaller note type tells them apart). No model call; the scan's
    text layer is read from the OCR copy. Returns the pages changed."""
    I = f"{folder}/intermediate"
    tp, ep = f"{I}/translation.txt", f"{I}/extracted.txt"
    pdf = next((os.path.join(I, f) for f in os.listdir(I) if f.endswith("OCR.pdf")), None) \
        if os.path.isdir(I) else None
    if not (pdf and os.path.exists(tp) and os.path.exists(ep)):
        return 0
    import fitz
    import _note_continuations
    tr = _pageio.split_pages(open(tp, encoding="utf-8").read())
    ex = _pageio.split_pages(open(ep, encoding="utf-8").read())
    if len(tr) != len(ex):
        return 0
    doc, changed = fitz.open(pdf), []
    for i in range(min(len(tr), len(doc))):
        new = _note_continuations.move_into_notes(
            tr[i], ex[i].split("BODY_TEXT:", 1)[-1], doc[i], tr[i - 1] if i else "")
        if new is not None and new != tr[i]:
            tr[i] = new
            changed.append(i + 1)
    doc.close()
    if changed:
        with open(tp, "w", encoding="utf-8") as fh:
            fh.write(_pageio.join_pages(tr))
        print(f"  note paragraphs moved out of the text on scan page(s) {changed}")
    return len(changed)


def apply_book_overrides(folder: str, fixes: bool = True) -> None:
    """Re-apply the reviewer's decisions (books/<stem>.overrides.toml) to
    headers.json and translation.txt. Runs after every translation pass,
    so regenerating either file can never lose a decision. Called twice:
    fixes=False (headers only) BEFORE harmonize_titles_with_headers, so body
    titles are matched to the reviewer's header wording, not the model's
    (Prévost scan 47: body "BREAK GROUND." under the decided "BREAKING
    GROUND."); then in full after it."""
    stem = Path(folder).name.replace("_translated", "")
    ov = _overrides.load(stem)
    I = f"{folder}/intermediate"
    hp, tp = f"{I}/headers.json", f"{I}/translation.txt"
    if ov["headers"] and os.path.exists(hp):
        hmap = json.load(open(hp, encoding="utf-8"))
        if _overrides.apply_headers(hmap, ov):
            with open(hp, "w", encoding="utf-8") as fh:
                json.dump(hmap, fh, ensure_ascii=False, indent=1)
    french = book_language(stem) != "Italian"
    if fixes and os.path.exists(tp):
        # the italics rules first, so text written before a rule changed
        # matches the [[fix]] finds, which follow the rules (and again after)
        t = open(tp, encoding="utf-8").read()
        if house_italics(t, french) != t:
            with open(tp, "w", encoding="utf-8") as fh:
                fh.write(house_italics(t, french))
    if fixes and os.path.exists(tp):
        move_note_continuations(folder)
    if fixes and (ov["fix"] or ov.get("book_fix")) and os.path.exists(tp):
        tr = _pageio.split_pages(open(tp, encoding="utf-8").read())
        applied, missing = _overrides.apply_fixes(tr, _house_fixes(ov, french))
        tr2 = [house_italics(pg, french) for pg in tr]
        if applied or tr2 != tr:
            tr = tr2
            with open(tp, "w", encoding="utf-8") as fh:
                fh.write(_pageio.join_pages(tr))
        missing = [m for m in missing if m not in _overrides.load_edits(stem)]   # page edited by hand
        if missing:
            print(f"  ⚠ overrides: [[fix]] text not found on scan page(s) {missing} (0: a [[book_fix]]) "
                  f"-- obsolete after a retranslation? Review {_overrides.path_for(stem)}")
    # hand edits of whole pages (books/<stem>.edits/) win over everything above
    edits = _overrides.load_edits(stem)
    if fixes and edits and os.path.exists(tp):
        tr = _pageio.split_pages(open(tp, encoding="utf-8").read())
        if _overrides.apply_edits(tr, edits):
            with open(tp, "w", encoding="utf-8") as fh:
                fh.write(_pageio.join_pages(tr))
    tbp = f"{I}/tables.json"
    if fixes and ov["table_fix"] and os.path.exists(tbp):
        tmap = json.load(open(tbp, encoding="utf-8"))
        applied, missing = _overrides.apply_table_fixes(tmap, ov)
        if applied:
            with open(tbp, "w", encoding="utf-8") as fh:
                json.dump(tmap, fh, ensure_ascii=False, indent=1)
        if missing:
            print(f"  ⚠ overrides: [[table_fix]] text not found on scan page(s) {missing} "
                  f"-- obsolete after a retranslation? Review {_overrides.path_for(stem)}")
    gp = f"{I}/figures.json"
    if fixes and os.path.exists(gp):
        fmap = json.load(open(gp, encoding="utf-8"))
        changed, need = _overrides.apply_captions(fmap, ov)
        for figs in fmap.values():
            for fg in figs:
                if fg.get("caption_en"):
                    fixed = match_dash_caps(fg.get("caption", ""), fg["caption_en"])
                    fixed = house_italics(fixed, french)
                    if fixed != fg["caption_en"]:
                        fg["caption_en"] = fixed
                        changed += 1
        if changed:
            with open(gp, "w", encoding="utf-8") as fh:
                json.dump(fmap, fh, ensure_ascii=False, indent=1)
        if need:
            print(f"  ⚠ overrides: caption(s) on scan page(s) {need} have no English yet -- "
                  f"run 2_translate.py --pages {stem} {','.join(map(str, need))}")
    if fixes and (ov["headers"] or ov["fix"]):
        print(f"  overrides applied: {len(ov['headers'])} header(s), {len(ov['fix'])} fix(es)")

# Per-page staleness. translated_from.json holds, for each page, a
# fingerprint of the source text its translation was made from -- body
# text only (headers and page numbers are translated separately). A run on
# an already-translated book retranslates just the pages whose fingerprint
# no longer matches, instead of the old "extracted.txt is newer than
# translation.txt -> redo the whole book" rule, which cost a full GPU run
# after any reprocess and needed a manual `touch` to avoid.
def page_fingerprint(page_text: str) -> str:
    import hashlib
    body = re.sub(r"\s+", " ", clean_vlm_extraction(page_text) if page_text.strip() else "").strip()
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]


def load_fingerprints(folder: str):
    p = f"{folder}/intermediate/translated_from.json"
    return json.load(open(p, encoding="utf-8"))["pages"] if os.path.exists(p) else None


_RUN_STAMP = None


def run_stamp() -> list:
    """[pipeline revision, ISO date] for pages translated by this run --
    taken once per run, so a run crossing midnight stamps one date."""
    global _RUN_STAMP
    if _RUN_STAMP is None:
        import _version
        from datetime import date
        _RUN_STAMP = [_version.pipeline_version(), date.today().isoformat()]
    return list(_RUN_STAMP)


def save_fingerprints(folder: str, fps: list, translated: list = None,
                      page_models: list = None, page_stamps: list = None) -> None:
    """Also records which translation model produced each page (`models`:
    the model file the server reported, else the name sent to it), and the
    pipeline revision + date it was translated on (`stamps`: [rev, date]).
    Pages listed in `translated` (1-based) get the current model and stamp;
    the rest keep what was recorded before. `page_models` / `page_stamps`
    (per page, from a resumed run) override both. The cover page
    summarises them."""
    p = f"{folder}/intermediate/translated_from.json"
    old = json.load(open(p, encoding="utf-8")) if os.path.exists(p) else {}
    # pages translated before the model was recorded: unknown ("") -- the
    # cover shows them as the configured default model, never as whatever
    # model happens to be running now. Same for the stamp (None): the cover
    # says "not recorded".
    models = old.get("models") or [""] * len(fps)
    models = (models + [""] * len(fps))[:len(fps)]
    stamps = old.get("stamps") or [None] * len(fps)
    stamps = (stamps + [None] * len(fps))[:len(fps)]
    for k in (translated if translated is not None else range(1, len(fps) + 1)):
        models[k - 1] = LAST_MODEL or TRANSLATION_MODEL_NAME
        stamps[k - 1] = run_stamp()
    for k, m in enumerate(page_models or []):
        models[k] = m or models[k]
    for k, t in enumerate(page_stamps or []):
        stamps[k] = t or stamps[k]
    with open(p, "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "pages": fps, "models": models, "stamps": stamps},
                   fh, indent=0)


def _partial_path(folder: str) -> str:
    return f"{folder}/intermediate/translation.partial.json"


def save_partial(folder: str, fps: list, done: list, models: list, st: dict,
                 stamps: list = None) -> None:
    """After each page of a whole-book run: the pages translated so far, the
    source fingerprints they came from, the model and stamp per page, and the
    book-wide footnote state at this point -- so a crashed or cancelled run
    resumes where it stopped instead of starting over. Written to a temp
    file and renamed, so a crash mid-write never leaves a broken file."""
    p = _partial_path(folder)
    with open(p + ".tmp", "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "fingerprints": fps, "pages": done, "models": models,
                    "stamps": stamps or [None] * len(done),
                    "state": {"fn_seen": sorted(st["fn_seen"]),
                              "footnoted_seen": sorted(st["footnoted_seen"]),
                              "fn_state": st["fn_state"]}}, fh, ensure_ascii=False)
    os.replace(p + ".tmp", p)


def load_partial(folder: str, fps: list):
    """(pages done, models, state, stamps) from an interrupted whole-book run, or
    None. Used only if the book still has the same page count and the done
    pages' source text is unchanged (the saved state is only valid for
    exactly those pages); pages after them may have changed freely."""
    p = _partial_path(folder)
    try:
        d = json.load(open(p, encoding="utf-8"))
        done, old_fps = d["pages"], d["fingerprints"]
        if len(old_fps) != len(fps) or old_fps[:len(done)] != fps[:len(done)]:
            return None
        s = d["state"]
        # "cat3_seen": the key's name before 2026-09-30 (saved runs still resume)
        st = {"fn_seen": set(s["fn_seen"]),
              "footnoted_seen": set(s.get("footnoted_seen", s.get("cat3_seen", []))),
              "fn_state": dict(s["fn_state"])}
        return (done, list(d.get("models") or [""] * len(done)), st,
                list(d.get("stamps") or [None] * len(done)))
    except (OSError, ValueError, KeyError, TypeError):
        return None


# Page context (experimental, off unless PAGE_CONTEXT=1): a sentence split
# across a page break is otherwise translated as two unrelated fragments
# (Kirchhoffer scan 12-13 "se mé-" / "fier" -> "smell of"; Spinnewyn
# 101-102 "Bien" / "fermer..." -> "Well / then close"). The model is shown
# the end of the previous page and the start of the next, marked as
# context it must not translate. Callers put them in st["context"].
PAGE_CONTEXT = os.environ.get("PAGE_CONTEXT", "").lower() in ("1", "true", "yes", "on")
_CONTEXT_WORDS = 40


def neighbour_context(pages: list, k: int) -> dict:
    """{"prev": last words of page k-1, "next": first words of page k+1}
    (k is 0-based), from the pages' body text without footnotes or margin
    notes; empty strings at the book's ends or on blank pages."""
    def body(j):
        if not 0 <= j < len(pages) or not pages[j].strip():
            return ""
        t = clean_vlm_extraction(pages[j])
        t = re.sub(r"⟦MARGIN:[^⟧]*⟧", " ", t)
        t = re.split(r"\n\s*\n\s*(?:\(\d+\)|\*)\s", t)[0]       # author's footnotes
        return " ".join(t.split())
    prev, nxt = body(k - 1).split(), body(k + 1).split()
    return {"prev": " ".join(prev[-_CONTEXT_WORDS:]), "next": " ".join(nxt[:_CONTEXT_WORDS])}


def page_context_block(st: dict) -> str:
    c = st.get("context") if PAGE_CONTEXT else None
    if not c or not (c.get("prev") or c.get("next")):
        return ""
    lines = ["CONTEXT ONLY -- do NOT translate or repeat this; it is here so a sentence "
             "split across a page break reads correctly. Translate only the text after "
             "\"Text to translate:\", starting and ending exactly where it does."]
    if c.get("prev"):
        lines.append(f"The previous page ends: \u00ab\u2026{c['prev']}\u00bb")
    if c.get("next"):
        lines.append(f"The next page begins: \u00ab{c['next']}\u2026\u00bb")
    return "\n".join(lines) + "\n\n"


def _mark_tables(clean_text: str) -> str:
    """A scoring / poule table: replace each <table>..</table> with a
    [[TABLE]] sentinel; steps 3 & 4 embed the scan for it."""
    clean_text = re.sub(r"<table\b.*?</table\s*>", "\n[[TABLE]]\n",
                        clean_text, flags=re.I | re.S)
    # a table the OCR truncated (page cut off mid-grid) has no
    # closing tag -- collapse the dangling <table ...> to end.
    clean_text = re.sub(r"<table\b.*\Z", "\n[[TABLE]]\n",
                        clean_text, flags=re.I | re.S)
    clean_text = re.sub(r"(?:\[\[TABLE\]\]\s*){2,}", "[[TABLE]]\n",
                        clean_text)
    return re.sub(r"\n{3,}", "\n\n", clean_text).strip()


def _translate_without_retries(clean_text: str, i: int, n: int, page_had_table: bool):
    """The pages that skip translate_page()'s retry loop: a page that is
    nothing but table(s), a near-blank page, a runaway-OCR page. Returns
    the page's entry, or None for a normal page."""
    if page_had_table:
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
            print(f"  Page {i}/{n} — table page, grid skipped")
            return out

    # A near-blank page (stray mark, bare page number, single word)
    # isn't worth a model call -- the model just replies "Please
    # provide the text you would like to translate", which then
    # leaks into translation.txt as the page's content.
    if is_near_blank(clean_text):
        print(f"  Page {i}/{n} — near-blank ({clean_text.split()[:4]}), skipped")
        return "[Empty page]"

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
        print(f"  Page {i}/{n} — oversized OCR ({src_words} w), "
              f"translated head only")
        return (f"[oversized OCR page — likely a repetition artifact; "
                f"first part translated, remainder omitted]\n\n{cand}"[:6000])
    return None


def _apply_page_rules(candidate: str, clean_text: str, kind, page_had_table: bool,
                      page_is_toc: bool, footnoted_seen: set) -> str:
    """The code rules every model reply goes through, before it is checked."""
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
    # ... and the book's own divider goes back where the source has it
    candidate = restore_dividers(candidate, clean_text)
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
    # before the term passes: a headword kept in the original is guarded there
    heads = []
    if kind == "glossary":
        candidate = _pagekind.complete_glossary(candidate, clean_text)
        candidate, heads = _pagekind.mask_headwords(candidate)
    elif kind == "bibliography":
        candidate = _pagekind.complete_bibliography(candidate, clean_text)
    candidate = apply_always_translate(candidate)
    candidate = enforce_kept_terms(candidate, clean_text, lang=BOOK_LANG)
    candidate = enforce_maestro(candidate, clean_text, lang=BOOK_LANG)
    candidate = restore_rapier(candidate, clean_text)
    candidate = restore_reprise(candidate, clean_text)
    candidate = restore_blade_phrases(candidate, clean_text, lang=BOOK_LANG)
    candidate = restore_straight_thrust(candidate, clean_text, lang=BOOK_LANG)
    # on a ToC page: translate the Cat-3 terms but emit NO
    # footnote marker (a contents line must not carry one, and
    # it would burn the once-per-book note), and skip the
    # heading re-translate pass (every entry looks like a
    # heading) to keep the "entry … NN" structure intact.
    candidate = enforce_footnoted_terms(
        candidate, footnoted_seen, footnote=not page_is_toc, lang=BOOK_LANG)
    candidate = _capitalize_definition_entries(candidate)
    if not page_is_toc:
        candidate = retranslate_stray_french_headings(candidate)
        candidate = restore_dropped_part_title(candidate, clean_text)
        candidate = fix_subheading_spacing(candidate)
    return _pagekind.unmask_headwords(candidate, heads)


def _check_reply(candidate: str, clean_text: str, attempt: int, src_words: int):
    """The checks a usable reply must pass. Returns (candidate, problem):
    problem is None when the reply is accepted, else (message, pause in
    seconds, hint) -- hint is None or ("para" | "line", text for the
    retry prompt). A missing table marker is put back, not retried."""
    # the reply ran out of room and stops mid-sentence. Retried; if every
    # try is cut, the best one is kept with the low-confidence banner.
    if LAST_CUT:
        return candidate, ("cut off (out of room)", 0, None)

    # every margin note (⟦MARGIN: …⟧, from _margins.py) must survive
    if candidate.count("⟦MARGIN:") < clean_text.count("⟦MARGIN:"):
        return candidate, ("margin note lost", 2, None)

    # every table marker must survive: steps 3 & 4 put the scan of the
    # table there (Masiello 1887 scans 376-377: the model left it out).
    # A retry rarely helps (2026-10-13: 12 of 12 tries on four pages
    # dropped it), so it is put back where the source has it.
    if candidate.count("[[TABLE]]") < clean_text.count("[[TABLE]]"):
        candidate = restore_table_markers(candidate, clean_text)
        print(" (table marker put back)", end="")

    if contains_too_much_non_english(candidate):
        return candidate, ("not English", 8, None)

    # a paragraph still in French / Italian / Latin: retried with the
    # paragraph named; on the last try it is kept as it is (a list of
    # names or titles can look the same, and the review report shows it)
    left = untranslated_paragraph(candidate)
    if left and attempt < 2:
        start, lang = left
        para_hint = (f" The paragraph beginning «{start}» is still in {lang}: translate it into English too"
                     + ("." if lang == BOOK_LANG else
                        f", and right after it write <fn>{lang} in the original</fn>."))
        return candidate, (f"paragraph still in {lang}", 2, ("para", para_hint))

    # one of the author's footnotes dropped (Marchionni 1847 pdf 34: the
    # retry for its Latin notes left all three out; Florio pdf 44 lost two).
    # Retried; on the last try kept as it is (a table of "(2)" cells looks
    # the same), and the review report flags the page.
    if attempt < 2 and note_bodies(candidate) < note_bodies(clean_text):
        return candidate, ("footnote lost", 2, None)

    # a "not" lost turns the meaning round (Kirchhoffer pdf 87 «nous ne
    # cesserons» -> "we shall cease"); two or more negations short ->
    # retried, the last try kept and the review report lists the page
    if attempt < 2 and lost_negations(clean_text, candidate, BOOK_LANG == "Italian") >= 2:
        return candidate, ("negation lost", 2, None)

    # a quotation noted "French in the original" but left in French
    # (Masiello 1887 pdf 73: "Sans le bouter d'estocq ou hachier.")
    if quotation_left_untranslated(candidate):
        return candidate, ("quotation not translated", 2, None)

    # Qwen rendered the whole page in capitals (source wasn't).
    # Retry for a clean one; deshout() is the fallback repair.
    if _shout_ratio(candidate) > 0.85 and _shout_ratio(clean_text) < 0.6:
        return candidate, ("all caps", 6, None)

    # Length sanity check -- but only for pages with enough source
    # text for the ratio to mean anything. Plate captions, drill
    # lists and dimension callouts are legitimately terse and were
    # being rejected as "too short".
    if src_words >= 25 and len(candidate.split()) < 0.5 * src_words:
        return candidate, ("too short", 8, None)

    # a line / parry named wrongly -- quarte became tierce (Kirchhoffer
    # scan 52). Retried hotter; if every try has it, the best one is kept
    # with the low-confidence banner for a person to check.
    swap = line_names_swapped(candidate, clean_text, lang=BOOK_LANG)
    if swap:
        # a hotter try alone often repeats it (Prévost-Jollivet scan 57, all
        # three tries) -- Kirchhoffer scan 52 was a
        # near-tie, tierce 34% / quarte 33%, then self-consistent -- so the
        # retry is told which lines the source names, and how often
        named = {w: c for w, c in _line_name_counts(clean_text, source=True, lang=BOOK_LANG).items() if c}
        line_hint = (" The source names these lines / parries, and no others: "
                     + ", ".join(f"{w} ({c}x)" for w, c in named.items())
                     + ". Write each one exactly where the source has it; never put one line in place of another.")
        return candidate, (f"line names changed ({swap})", 2, ("line", line_hint))
    return candidate, None


def _fallback(best_candidate, clean_text: str, src_words: int, fn_seen: set) -> str:
    """Every attempt failed. Never drop a page: keep the best non-garbled
    attempt (flagged for review), or fall back to the original text so
    the page still appears in the output for a human to fix."""
    if best_candidate and _shout_ratio(best_candidate) > 0.85 \
            and _shout_ratio(clean_text) < 0.6 \
            and not contains_too_much_non_english(best_candidate) \
            and not (src_words >= 25
                     and len(best_candidate.split()) < 0.5 * src_words):
        # The only thing wrong was the all-caps shout: repair the
        # case and keep it as a normal page, no review banner.
        fixed, n_fn = pull_footnotes(deshout(best_candidate, clean_text),
                                     clean_text, fn_seen, lang=BOOK_LANG)
        print(" ✓ (deshouted)")
        return fixed
    elif best_candidate:
        print(" ⚠ kept (low confidence)")
        body = deshout(best_candidate, clean_text) \
            if _shout_ratio(best_candidate) > 0.85 else best_candidate
        if body.count("[[TABLE]]") < clean_text.count("[[TABLE]]"):
            body = restore_table_markers(body, clean_text)
        return f"[low-confidence translation — review]\n{body}"
    elif src_words < 15:
        # Every attempt was a refusal/preamble and there was
        # barely any source text -- it's an effectively blank
        # page, not a translation failure worth surfacing.
        print(" ✗ refusals only on a near-blank page — [Empty page]")
        return "[Empty page]"
    else:
        print(" ✗ no usable output — kept original text")
        return f"[translation unavailable — original text follows]\n{clean_text}"


def translate_page(page_text: str, i: int, n: int, st: dict) -> str:
    """Translate one extracted.txt page record -> its translation.txt entry.
    Never raises and never returns nothing: a failed page comes back as a
    flagged fallback. `i` / `n` are 1-based position / page count (progress
    output only). `st` is the book-wide state that has to persist across
    pages: fn_seen (dedupe <fn> notes), footnoted_seen (footnoted terms already
    footnoted), fn_state (the book's own footnote continuing onto the next
    page). Used by the whole-book loop and by `--pages` retranslation.

    Up to three tries: each reply goes through _apply_page_rules(), then
    _check_reply(); a failed check retries hotter (with a hint for some),
    and when every try fails _fallback() picks what to keep."""
    fn_seen, footnoted_seen, fn_state = st["fn_seen"], st["footnoted_seen"], st["fn_state"]
    if not page_text.strip() or "VLM Error" in page_text:
        print(f"  Page {i}/{n} — empty / OCR error, passed through")
        return page_text

    # Clean VLM structured output before translation
    clean_text = clean_vlm_extraction(page_text)

    if not clean_text.strip():
        print(f"  Page {i}/{n} — no body text, skipped")
        return "[Empty page]"

    # A step-1 marker page ("[handwritten inscription — see original]")
    # -- pass it through verbatim, never translate it.
    if re.fullmatch(r"\[[^\[\]\n]+\]", clean_text.strip()):
        print(f"  Page {i}/{n} — marker page, passthrough")
        return clean_text.strip()

    page_is_toc = is_toc_page(clean_text)

    # A page that is nothing but table(s) (+ maybe a heading) skips the
    # model's attempt to linearise the grid.
    page_had_table = bool(re.search(r"<table\b", clean_text, re.I))
    if page_had_table:
        clean_text = _mark_tables(clean_text)
    done = _translate_without_retries(clean_text, i, n, page_had_table)
    if done is not None:
        return done

    src_words = len(clean_text.split())
    best_candidate = None   # best rejected attempt, for the fallback
    line_hint = ""          # set when a try swapped a line name (_check_reply)
    para_hint = ""          # set when a try left a paragraph untranslated (_check_reply)
    # the book's glossary / bibliography (_pagekind): the model is told what
    # kind of page it has, and code completes what it still leaves out
    kind = st.get("kind")
    kind_note = f"{_pagekind.INSTRUCTION[kind]}\n\n" if kind else ""
    for attempt in range(3):
        print(f"  Page {i}/{n} (try {attempt + 1})", end="", flush=True)

        # After a first failure, raise the temperature and add a
        # blunt instruction -- the old 7B model sometimes gets stuck echoing
        # the French source verbatim, and a hotter sample + the
        # extra line breaks it out.
        ctx = page_context_block(st) + kind_note
        if attempt == 0:
            full_prompt = f"{PROMPT_TEMPLATE}\n\n{ctx}Text to translate:\n{clean_text}"
            temp = FIRST_TRY_TEMPERATURE
        else:
            full_prompt = (f"{PROMPT_TEMPLATE}\n\nTranslate the following into "
                           f"fluent English. Do NOT repeat any of it in the "
                           f"original language. Match the capitalisation of the "
                           f"source; do NOT write the text in all capitals."
                           f"{line_hint}{para_hint}"
                           f"\n\n{ctx}Text to translate:\n{clean_text}")
            temp = 0.3 + 0.25 * attempt
        candidate = translate_with_llama(full_prompt, temperature=temp)

        if not candidate:
            print(" → failed")
            continue

        candidate = _apply_page_rules(candidate, clean_text, kind, page_had_table,
                                      page_is_toc, footnoted_seen)

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

        candidate, problem = _check_reply(candidate, clean_text, attempt, src_words)
        if problem:
            message, pause, hint = problem
            print(f" → {message}")
            if pause:
                time.sleep(pause)
            if hint and hint[0] == "para":
                para_hint = hint[1]
            elif hint:
                line_hint = hint[1]
            continue

        candidate = match_boundary_punctuation(candidate, clean_text)
        candidate, n_fn = pull_footnotes(candidate, clean_text, fn_seen, lang=BOOK_LANG)
        candidate = extract_source_footnotes(candidate, fn_state)
        print(f" ✓{'  (%d fn)' % n_fn if n_fn else ''}")
        return candidate

    return _fallback(best_candidate, clean_text, src_words, fn_seen)


_USAGE = """usage: python3 /app/2_translate.py <stem> [<stem> ...] | --all
         python3 /app/2_translate.py --pages <stem> <scan pages>   e.g. 12,40-42
         python3 /app/2_translate.py --flagged <stem> <words> [--list]   e.g. risposta, or kind:split
         python3 /app/2_translate.py --captions <stem> [<stem> ...] | --all
         python3 /app/2_translate.py --tables <stem> [<stem> ...] | --all
  translates new books, and the pages whose source changed since translation;
  --flagged retranslates the pages whose review-report checks mention <words>
  (or are of that kind) -- after a prompt or [terms] fix for that mistake"""


def _flagged_pages(stem: str, words: str) -> str:
    """Scan pages whose review-report CHECKs match: "kind:<kind>", or words
    found in the item's text (case ignored) -- e.g. "risposta" for the
    "'answer' against the glossary (risposta → riposte)" items."""
    p = f"/data/processed/{stem}_translated/intermediate/quality_report.json"
    if not os.path.exists(p):
        sys.exit(f"✗ {stem}: no review report yet -- run quality_report.py {stem}")
    items = [x for x in json.load(open(p, encoding="utf-8")) if x.get("level") == "CHECK"]
    if words.startswith("kind:"):
        hit = [x for x in items if x["kind"] == words[5:]]
    else:
        hit = [x for x in items if words.lower() in (x["kind"] + " " + x["detail"]).lower()]
    pages = sorted({x["scan"] for x in hit if x["scan"] > 0})
    if not pages:
        sys.exit(f"⊘ {stem}: no review-report check matches {words!r}")
    print(f"{stem}: {len(pages)} page(s) flagged for {words!r}: {','.join(map(str, pages))}")
    return ",".join(map(str, pages))


def finish_book(folder: str) -> None:
    """After a book's pages are translated (all, or some): the captions,
    running heads and tables not yet translated (only those, or the pages
    whose tables changed), then the overrides file -- its header wording
    first, so body titles are harmonised with the heads, then its fixes."""
    translate_figure_captions(folder)
    translate_headers(folder)
    translate_tables(folder)
    apply_book_overrides(folder, fixes=False)
    harmonize_titles_with_headers(folder)
    apply_book_overrides(folder)


def notes_already_given(tr: list, targets: list, lang: str) -> dict:
    """Book-wide footnote state from every translated page NOT being redone,
    so a redone page neither repeats a note nor loses its own first-use one.
    Notes are read in the book's language ("†1 Italian: legamento"); reading
    only "French:" let an Italian book repeat a note on a redone page."""
    st = {"fn_seen": set(), "footnoted_seen": set(), "fn_state": {}}
    label = _LANG_REF.get(lang, _LANG_REF["French"])[1]
    for k, t in enumerate(tr, 1):
        if k in targets:
            continue
        for ref in re.findall(rf"(?m)^†\d+ {label}: (.+?)\s*$", t):
            st["footnoted_seen"].add(ref.strip().lower())
            st["fn_seen"].add(_strip_accents(ref.strip().lower()))
    return st


def retranslate_pages(stem: str, spec: str = "") -> None:
    """`2_translate.py --pages <stem> [12,40-42]`: retranslate just those
    SCAN pages of an already-translated book, in place. With no page list,
    takes the pages marked `retranslate = true` in the book's overrides
    file -- each only once per version of its source text (a fingerprint
    is kept in intermediate/retranslated.json), so rerunning costs nothing
    until that page's extracted text changes. The rest of translation.txt
    is untouched; header/title harmonization and the overrides file are
    re-applied afterwards, as after a full run."""
    import hashlib
    global PROMPT_TEMPLATE
    folder = f"/data/processed/{stem}_translated"
    I = f"{folder}/intermediate"
    ex = _pageio.split_pages(open(f"{I}/extracted.txt", encoding="utf-8").read())
    tp = f"{I}/translation.txt"
    tr = _pageio.split_pages(open(tp, encoding="utf-8").read())
    if len(ex) != len(tr):
        sys.exit(f"✗ {stem}: extracted.txt has {len(ex)} pages, translation.txt "
                 f"{len(tr)} -- out of step; a full translation is needed")
    ov = _overrides.load(stem)
    PROMPT_TEMPLATE = prompt_for_book(stem)
    done_p = f"{I}/retranslated.json"
    done = json.load(open(done_p)) if os.path.exists(done_p) else {}
    fp = lambda t: hashlib.sha1(t.strip().encode("utf-8")).hexdigest()[:16]
    if spec:
        targets = _overrides.parse_page_spec(spec)
    else:
        targets = [s for s in _overrides.retranslate_pages(ov)
                   if done.get(str(s)) != fp(ex[s - 1])]
    bad = [s for s in targets if not 1 <= s <= len(ex)]
    if bad:
        sys.exit(f"✗ {stem}: scan page(s) {bad} outside 1..{len(ex)}")
    if not targets:
        print(f"⊘ {stem}: nothing to retranslate")
        return
    st = notes_already_given(tr, targets, BOOK_LANG)
    print(f"Retranslating {stem}: scan page(s) {targets}")
    # Saved after every page, and resumable: an interrupted run of the same
    # page list skips the pages it already finished (the progress record is
    # dropped at the end). A long list (Gomard: 226 pages, ~3 h) must not be
    # all-or-nothing.
    prog_p = f"{I}/retranslate.partial.json"
    try:
        prog = json.load(open(prog_p, encoding="utf-8"))
        finished = set(prog["done"]) if prog.get("targets") == targets else set()
    except (OSError, ValueError, KeyError):
        finished = set()
    if finished:
        print(f"Resuming: {len(finished)} of {len(targets)} page(s) already done")
    fps = load_fingerprints(folder) or [page_fingerprint(p) for p in ex]
    kinds = _pagekind.book_page_kinds([clean_vlm_extraction(p) for p in ex])
    _progress.start(stem, "translation", len([t for t in targets if t not in finished]))
    for s_ in targets:
        if s_ in finished:
            continue
        st["context"] = neighbour_context(ex, s_ - 1) if PAGE_CONTEXT else None
        st["kind"] = kinds.get(s_ - 1)
        tr[s_ - 1] = translate_page(ex[s_ - 1].strip(), s_, len(ex), st)
        done[str(s_)] = fp(ex[s_ - 1])
        fps[s_ - 1] = page_fingerprint(ex[s_ - 1])
        finished.add(s_)
        with open(tp, "w", encoding="utf-8") as fh:
            fh.write(_pageio.join_pages(tr))
        with open(done_p, "w", encoding="utf-8") as fh:
            json.dump(done, fh, indent=1, sort_keys=True)
        save_fingerprints(folder, fps, translated=[s_])
        with open(prog_p + ".tmp", "w", encoding="utf-8") as fh:
            json.dump({"targets": targets, "done": sorted(finished)}, fh)
        os.replace(prog_p + ".tmp", prog_p)
        _progress.page(s_)
    _progress.finish()
    if os.path.exists(prog_p):
        os.remove(prog_p)
    finish_book(folder)
    print(f"\n✓ {len(targets)} page(s) retranslated -- rebuild the PDFs:\n"
          f"  python3 /app/4_side_by_side_pdf.py {stem}")


def main():
    """The `2_translate.py` command: argument handling, the translation
    server, and the per-book run."""
    global PROMPT_TEMPLATE
    print("\n" + "="*60)
    print("STEP 2: AI TRANSLATION")
    print("="*60)

    import _books

    # which books, before any server starts (_books.py: names or --all; a
    # book marked finished is left alone without --unlock)
    if len(sys.argv) > 1 and sys.argv[1] == "--flagged":
        if len(sys.argv) < 4:
            sys.exit(_USAGE)
        _fl = _flagged_pages(sys.argv[2], sys.argv[3])
        if "--list" in sys.argv:              # show the pages only, translate nothing
            sys.exit(0)
        sys.argv = [sys.argv[0], "--pages", sys.argv[2], _fl] + sys.argv[4:]
    if len(sys.argv) > 1 and sys.argv[1] == "--pages":
        if len(sys.argv) < 3:
            sys.exit(_USAGE)
        _books.check_unlocked(sys.argv[2], sys.argv)
        _BOOK_FOLDERS = []
    elif len(sys.argv) > 1 and sys.argv[1] in ("--captions", "--tables"):
        _BOOK_FOLDERS = [_books.folder_of(n) for n in _books.select(sys.argv[2:], _USAGE)]
    else:
        _BOOK_FOLDERS = [_books.folder_of(n) for n in _books.select(sys.argv[1:], _USAGE)]

    translation_proc = start_translation_server()

    if translation_proc is None:
        print("✗ Cannot start translation server\n")
        exit(1)

    # `2_translate.py --captions` re-runs only the figure captions AND running
    # headers of the named books, without touching the page bodies -- for iterating.
    if len(sys.argv) > 1 and sys.argv[1] == "--captions":
        try:
            for folder in _BOOK_FOLDERS:
                print(f"captions/headers: {Path(folder).name}")
                PROMPT_TEMPLATE = prompt_for_book(Path(folder).name.replace("_translated", ""))
                translate_figure_captions(folder, force=True)
                translate_headers(folder, force=True)
                apply_book_overrides(folder, fixes=False)
                harmonize_titles_with_headers(folder)
                apply_book_overrides(folder)
        finally:
            if translation_proc is not REMOTE_SERVER and translation_proc:
                translation_proc.terminate()
        print("\n✓ Caption pass complete\n")
        sys.exit(0)


    # `2_translate.py --tables` translates only the books' tables (tables.json),
    # without touching the page bodies -- each run translates the pages whose
    # tables changed, so a run that stops resumes where it was.
    if len(sys.argv) > 1 and sys.argv[1] == "--tables":
        try:
            for folder in _BOOK_FOLDERS:
                print(f"tables: {Path(folder).name}")
                PROMPT_TEMPLATE = prompt_for_book(Path(folder).name.replace("_translated", ""))
                translate_tables(folder)
                apply_book_overrides(folder)    # [[table_fix]] decisions win over the new English
        finally:
            if translation_proc is not REMOTE_SERVER and translation_proc:
                translation_proc.terminate()
        print("\n✓ Table pass complete\n")
        sys.exit(0)


    if len(sys.argv) > 2 and sys.argv[1] == "--pages":
        try:
            retranslate_pages(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "")
        finally:
            if translation_proc is not REMOTE_SERVER and translation_proc:
                translation_proc.terminate()
        sys.exit(0)

    try:
        for folder in _BOOK_FOLDERS:
            src = f"{folder}/intermediate/extracted.txt"
            dst = f"{folder}/intermediate/translation.txt"
        
            if not os.path.exists(src):
                continue
        
            with open(src, "r", encoding="utf-8") as f:
                pages = [p.strip() for p in _pageio.split_pages(f.read())]
            cur_fp = [page_fingerprint(p) for p in pages]

            if os.path.exists(dst):
                n_tr = len(_pageio.split_pages(open(dst, encoding="utf-8").read()))
                stored = load_fingerprints(folder)
                if stored is None and n_tr == len(pages):
                    # translated before fingerprints existed: take the current
                    # source as the baseline (the translation is assumed to match it)
                    save_fingerprints(folder, cur_fp, translated=[])
                    print(f"⊘ {Path(folder).name}: recorded source fingerprints (baseline)\n")
                    continue
                if stored is not None and len(stored) == len(pages) == n_tr:
                    changed = [k + 1 for k, (a, b) in enumerate(zip(stored, cur_fp)) if a != b]
                    if not changed:
                        print(f"⊘ Skip (up to date): {Path(folder).name}\n")
                        continue
                    print(f"{Path(folder).name}: {len(changed)} page(s) changed since translation")
                    retranslate_pages(Path(folder).name[:-len("_translated")],
                                      ",".join(map(str, changed)))
                    continue
                print(f"{Path(folder).name}: page count changed ({n_tr} translated, "
                      f"{len(pages)} extracted) -- translating the whole book")
            PROMPT_TEMPLATE = prompt_for_book(Path(folder).name.replace("_translated", ""))
            print(f"Source language: {BOOK_LANG}")

            print(f"Translating: {Path(folder).name}")
            print(f"Pages: {len(pages)}\n")
        
            # book-wide state: fn_seen dedupes <fn> note text, footnoted_seen
            # footnotes each footnoted term once, fn_state carries the book's
            # own footnote across a page break
            resumed = load_partial(folder, cur_fp)
            if resumed:
                translated_pages, page_models, _st, page_stamps = resumed
                print(f"Resuming an interrupted run: {len(translated_pages)} page(s) already "
                      f"done, continuing at page {len(translated_pages) + 1}\n"
                      f"  (delete intermediate/translation.partial.json to start over)\n")
            else:
                if os.path.exists(_partial_path(folder)):
                    print("  (saved partial run is for different source text -- starting over)\n")
                translated_pages, page_models, page_stamps = [], [], []
                _st = {"fn_seen": set(), "footnoted_seen": set(), "fn_state": {}}

            kinds = _pagekind.book_page_kinds([clean_vlm_extraction(p) for p in pages])
            _progress.start(Path(folder).name[:-len("_translated")], "translation",
                            len(pages) - len(translated_pages))
            for i in range(len(translated_pages) + 1, len(pages) + 1):
                calls = MODEL_CALLS
                _st["context"] = neighbour_context(pages, i - 1) if PAGE_CONTEXT else None
                _st["kind"] = kinds.get(i - 1)
                translated_pages.append(translate_page(pages[i - 1], i, len(pages), _st))
                # "-": no model reply (blank / marker page) -- the cover skips it
                page_models.append((LAST_MODEL or TRANSLATION_MODEL_NAME)
                                   if MODEL_CALLS != calls else "-")
                page_stamps.append(run_stamp())
                save_partial(folder, cur_fp, translated_pages, page_models, _st, page_stamps)
                _progress.page(i)
            _progress.finish()

            with open(dst, "w", encoding="utf-8") as f:
                f.write(_pageio.join_pages(translated_pages))
            save_fingerprints(folder, cur_fp, page_models=page_models,
                              page_stamps=page_stamps)
            os.remove(_partial_path(folder))

            finish_book(folder)

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
            except Exception:
                translation_proc.kill()
                print("✓ Translation server killed (VRAM freed)\n")

    print("="*60)
    print("✓ TRANSLATION COMPLETE")
    print("="*60 + "\n")
