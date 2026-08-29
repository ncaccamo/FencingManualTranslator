#!/usr/bin/env python3
"""VLM OCR using olmOCR-2-7B-1025 (Allen AI, fine-tuned from Qwen2.5-VL-7B
specifically for document/OCR extraction -- not the general-purpose
Qwen2-VL-7B-Instruct chat model this pipeline started with).

Changes from the Qwen2-VL version:
  - Model/mmproj paths point at olmOCR-2 by default, still env-overridable
    (VLM_MODEL_PATH, VLM_MMPROJ_PATH).
  - Image resize target is now a fixed 1288px longest dimension -- this is
    a documented requirement of olmOCR-2 (not a quality/speed tunable like
    Qwen2-VL's was), so the old two-tier "normal vs aggressive compression"
    pre-screening logic is gone; every page is prepared the same way.
  - Prompt (vlm_ocr_prompt.txt) now explicitly instructs the model to
    ignore facing-page/binding/margin clutter rather than relying on a
    physical .crop sidecar -- this is the actual advantage a VLM has over
    pixel-segmentation OCR (see chat log: it correctly read a line
    Tesseract garbled next to handwritten marginalia). A .crop file still
    works if you have one and want to use it too; it's just no longer
    required for this path.
  - Retained: server load/unload, retry-with-restart-on-failure. This is
    still a 7B VLM and can still fail/hang on a bad page.

Output contract (added after the hard-page eval, see chat log):
  - raw_ocr.txt: the untouched model responses, written before any parsing
    -- the ground-truth fallback, matching the Tesseract path.
  - extracted.txt: a CANONICAL PAGE_NUMBER/HEADER/BODY_TEXT scaffold rebuilt
    from the parsed fields (downstream -- 2_translate.py, the PDF
    generators -- still parses this exact scaffold; it's just now
    well-formed and the page number is trustworthy).
  - page_numbers.txt: one reconciled page number per line, '[n]' where
    inferred. olmOCR-2 reads a real printed folio fine but will invent one
    from an engraving plate number or a library stamp, or convert 'VIII' to
    '8'; reconcile_page_numbers() keeps a reported number only when it
    agrees with the book's dominant page-index -> folio offset.
"""

import glob
import os
import re
import json
from collections import Counter
import fitz
import base64
import requests
import subprocess
import time
import shutil
from pathlib import Path
from PIL import Image, ImageOps
import io
import sys

import backend_config as bc
import _pageio

VLM_MODEL_PATH = os.environ.get("VLM_MODEL_PATH", "/models/allenai_olmOCR-2-7B-1025-Q6_K.gguf")
VLM_MMPROJ_PATH = os.environ.get("VLM_MMPROJ_PATH", "")


def _find_matching_mmproj(model_path_str: str) -> str:
    """
    Pick the mmproj file that actually belongs to VLM_MODEL_PATH, when
    multiple mmproj-*.gguf files for DIFFERENT models sit in the same
    directory (e.g. one per vision model you've tried). Confirmed the hard
    way that naive alphabetical-first selection is wrong here: filenames
    starting with an uppercase letter sort before ones starting with a
    lowercase letter in plain ASCII sort, which has nothing to do with
    which file matches the loaded model.

    Matches by counting shared '-'-separated tokens between the model's
    filename and each candidate mmproj's filename (after stripping the
    'mmproj-' prefix and '-f16'/'-bf16' suffix). If nothing scores above
    zero, refuses to guess and raises instead of silently loading a
    mismatched projector.
    """
    model_path = Path(model_path_str)
    model_dir = model_path.parent
    candidates = sorted(model_dir.glob("mmproj*.gguf"))
    if not candidates:
        raise FileNotFoundError(f"No mmproj*.gguf files found in {model_dir}")

    model_tokens = set(re.split(r"[-_.]", model_path.stem.lower()))
    scored = []
    for c in candidates:
        core = re.sub(r"-(f16|bf16|fp16)$", "", c.stem.lower())
        core = re.sub(r"^mmproj-", "", core)
        core_tokens = set(re.split(r"[-_.]", core))
        overlap = len(model_tokens & core_tokens)
        scored.append((overlap, c))
    scored.sort(key=lambda x: (-x[0], "bf16" in x[1].stem.lower(), str(x[1])))
    best_overlap, best = scored[0]

    if best_overlap == 0:
        names = ", ".join(c.name for c in candidates)
        raise FileNotFoundError(
            f"Found mmproj files ({names}) but none share a recognizable "
            f"name with the model file ({model_path.name}). Set "
            f"VLM_MMPROJ_PATH explicitly rather than risk loading the "
            f"wrong projector."
        )
    return str(best)


if not VLM_MMPROJ_PATH:
    try:
        VLM_MMPROJ_PATH = _find_matching_mmproj(VLM_MODEL_PATH)
    except FileNotFoundError as e:
        print(f"\n✗ ERROR: {e}\n")
        exit(1)
VLM_PROMPT_FILE = "/app/vlm_ocr_prompt.txt"
VLM_PREPROCESS = os.environ.get("VLM_PREPROCESS", "1") != "0"
# Documented olmOCR-2 requirement, not a tunable -- see model card.
VLM_MAX_DIMENSION = int(os.environ.get("VLM_MAX_DIMENSION", "1288"))

if not os.path.exists(VLM_PROMPT_FILE):
    print(f"\n✗ ERROR: Missing VLM prompt file: {VLM_PROMPT_FILE}\n")
    exit(1)

with open(VLM_PROMPT_FILE, "r", encoding="utf-8") as f:
    VLM_PROMPT_TEMPLATE = f.read().strip()


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
    hd = re.search(r'^[ \t]*HEADER[ \t]*:[ \t]*(.+?)[ \t]*$', raw, re.I | re.M)
    bd = re.search(r'^[ \t]*BODY_TEXT[ \t]*:[ \t]*\n?(.*)\Z', raw, re.I | re.S | re.M)

    def _clean(v: str) -> str:
        v = (v or "").strip().strip("[]").strip()
        if v.lower() in ("", "none", "n/a", "-", "not visible", "no page number"):
            return ""
        return v

    page_raw = _clean(pn.group(1) if pn else "")
    header = _clean(hd.group(1) if hd else "")
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


def strip_leaked_headmatter(body: str, known_headers: set) -> str:
    """Peel bookbinding apparatus off BODY_TEXT.

    TOP: the VLM sometimes drops the running header and/or the printed
    folio into the body, so the page opens with a stray "25" / "THE GUARD".

    BOTTOM: printer's signature marks -- a lone small number, or the title
    catchword (a short line matching one of the book's header words, e.g.
    "ESCRIME.") -- sit at the foot of the first page of each gathering.

    A line is apparatus when it is a bare roman/arabic number (<=4 digits,
    optional trailing '.', optional leading superscript letter) or its
    normalised form matches a header seen elsewhere in this book."""
    def _is_apparatus(s):
        return bool(re.fullmatch(r"[a-z]?[\dIVXLCDM]{1,4}\.?", s, re.I)) \
            or _norm_head(s) in known_headers

    lines = body.split("\n")
    while lines and (not lines[0].strip() or _is_apparatus(lines[0].strip())):
        lines.pop(0)
    # Tail: a signature is at most catchword + number, so strip at most 2
    # apparatus lines, and never strip the last line if it is the only
    # content left (a one-line page could be a legitimate short entry).
    for _ in range(2):
        while lines and not lines[-1].strip():
            lines.pop()
        if len(lines) > 1 and _is_apparatus(lines[-1].strip()):
            lines.pop()
        else:
            break
    return "\n".join(lines).strip()


def parse_figures_block(raw: str) -> list:
    """Parse the FIGURES: section of a VLM response into a list of
        {"kind": "plate"|"inset", "caption": str}
    'plate' = whole-page illustration, 'inset' = illustration sharing the
    page with text. Geometry is left to 3_english_only_pdf.py (the density
    profiler) -- the VLM only reliably gives presence + caption. Tolerant
    of a missing block, "none", and stray bullets/quotes."""
    m = re.search(r'(?im)^[ \t]*FIGURES[ \t]*:[ \t]*\n?(.*)\Z', raw, re.S)
    if not m:
        return []
    figs = []
    for line in m.group(1).splitlines():
        line = line.strip().lstrip("-*•[] ").strip().strip('"').strip()
        low = line.lower()
        if not low or low in ("none", "figures: none", "no figures", "* * *"):
            continue
        if line.startswith("!["):          # leaked Markdown image ref
            continue
        parts = [p.strip() for p in line.split("|")]
        kind = parts[0].lower()
        if kind.startswith("plate") or "full" in kind or "whole" in kind:
            kind = "plate"
        elif kind.startswith("inset") or "inline" in kind or "text" in kind:
            kind = "inset"
        elif figs and re.match(r"[0-9a-zà-ÿ]", line):
            # A follow-on line starting with a digit or lowercase -- a
            # caption's second line ("2e TEMPS : le dérobement ..."), not
            # a fresh title ("FIG. 5. —", "PRISE DE FER ..."). Append it.
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


def _roman_to_int(s: str):
    s = s.strip().upper()
    if not s or not re.fullmatch(r'[IVXLCDM]+', s):
        return None
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = prev = 0
    for ch in reversed(s):
        v = vals[ch]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total or None


def _page_to_int(s: str):
    """Best-effort numeric value of a reported page-number string, accepting
    plain digits ('83'), digits embedded in noise, or a roman numeral
    ('VIII')."""
    if not s:
        return None
    m = re.search(r'\d+', s)
    if m:
        return int(m.group())
    return _roman_to_int(s)


def reconcile_page_numbers(page_raws: list) -> list:
    """olmOCR-2 reliably reads a real printed folio, but it will also invent
    a plausible-looking number from a stray mark -- an engraver's plate
    number inside an illustration, a library accession stamp -- or silently
    convert 'VIII' to '8'.

    A reported folio is TRUSTED when a neighbouring sheet's folio continues
    the printed sequence (i-1 -> v-1, or i+1 -> v+1): a real run like
    "20, 21, 22" corroborates itself even if a later section of the book
    runs on a different index->folio offset (inserted plates shift it).
    Everything else is interpolated from the nearest trusted anchor and
    bracketed '[n]'; with no anchor at all, the plain sheet ordinal.
    Never emits <= 0.
    """
    ints = [_page_to_int(p) for p in page_raws]
    n = len(ints)

    trusted = [False] * n
    for i, v in enumerate(ints):
        if v is None or v <= 0:
            continue
        if (i > 0 and ints[i - 1] == v - 1) or (i + 1 < n and ints[i + 1] == v + 1):
            trusted[i] = True

    anchors = [i for i in range(n) if trusted[i]]
    if not anchors:
        return [f"[{i + 1}]" for i in range(n)]

    out = []
    for i in range(n):
        if trusted[i]:
            out.append(page_raws[i])                       # printed folio, corroborated
            continue
        j = min(anchors, key=lambda a: abs(a - i))          # nearest trusted sheet
        guess = ints[j] + (i - j)                           # 1 sheet == 1 folio
        out.append(f"[{guess}]" if guess >= 1 else f"[{i + 1}]")
    return out


def preprocess_for_ocr(img: Image.Image) -> Image.Image:
    """Light contrast normalization before OCR. Historical scans are
    frequently faded/yellowed/low-contrast; autocontrast on the luminance
    channel tends to make text edges cleaner for the vision model."""
    if not VLM_PREPROCESS:
        return img
    gray = ImageOps.autocontrast(img.convert("L"), cutoff=1)
    return gray.convert("RGB")


# Returned by start_vlm_server() when BACKEND_MODE=remote -- there is no
# local process to manage, just an endpoint to talk to.
REMOTE_SERVER = object()


def start_vlm_server(restart=False):
    if bc.is_remote():
        base = bc.ocr_base_url()
        print(f"\n[remote] using VLM/OCR endpoint {base}")
        print("[remote] make sure the VISION model is loaded on that host.")
        try:
            r = requests.get(f"{base}/health", headers=bc.auth_headers(), timeout=5)
            if r.status_code == 200:
                print("[remote] /health OK\n")
                return REMOTE_SERVER
            print(f"[remote] /health returned HTTP {r.status_code}")
        except Exception as e:
            print(f"[remote] cannot reach {base}: {e}")
        return None

    if restart:
        print("\n🔄 RESTARTING VLM SERVER (clearing stuck state)")
    else:
        print("\n" + "="*60)
        print("LOADING VISION MODEL (olmOCR-2-7B-1025)")
        print("="*60 + "\n")
    # Claim port 11435: kill any llama-server already on it (a stale one from
    # a previous run, or start_servers.sh) so our Popen below is the server
    # this script actually manages, at the ctx-size it wants. Safe now that
    # the entrypoint no longer runs a server under `wait -n`.
    subprocess.run(["pkill", "-f", "llama-server.*11435"], stderr=subprocess.DEVNULL)
    time.sleep(5)

    print(f"Model:  {VLM_MODEL_PATH}")
    print(f"Mmproj: {VLM_MMPROJ_PATH}")
    print("Port:   11435 | GPU: Full offload\n")

    if not os.path.exists(VLM_MODEL_PATH):
        print(f"✗ Model not found: {VLM_MODEL_PATH}\n")
        return None
    if not os.path.exists(VLM_MMPROJ_PATH):
        print(f"✗ Projector not found: {VLM_MMPROJ_PATH}\n")
        return None

    model_size = os.path.getsize(VLM_MODEL_PATH) / (1024**3)
    mmproj_size = os.path.getsize(VLM_MMPROJ_PATH) / (1024**3)
    print(f"✓ Model verified ({model_size:.2f} GB)")
    print(f"✓ Projector verified ({mmproj_size:.2f} GB)\n")

    cmd = [
        "llama-server",
        "--model", VLM_MODEL_PATH,
        "--mmproj", VLM_MMPROJ_PATH,
        "--host", "0.0.0.0",
        "--port", "11435",
        "--ctx-size", "8192",
        "--n-gpu-layers", "999",
        "--threads", str(os.cpu_count()),
        "--parallel", "1"
    ]

    print("Starting server...\n")

    log_file = open("/tmp/vlm_server.log", "w")
    proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT,
                            env=_llama_server_env())

    dots = 0
    # 240s cold ceiling: a first llama-server load on a cleared shader cache
    # (RADV compiles every pipeline, ~2-3 min) used to blow the old 120s.
    # Warm (docker-compose volume persists the cache) it's ~60s. `restart`
    # only fires mid-run, cache already warm.
    max_wait = 60 if restart else 240
    for attempt in range(max_wait // 2):
        time.sleep(2)
        dots = (dots + 1) % 4
        elapsed = (attempt+1)*2
        sys.stdout.write(f"\r  Loading{'.' * dots}{' ' * (3-dots)} {elapsed}s")
        sys.stdout.flush()

        if proc.poll() is not None:
            print(f"\n\n✗ Server process exited (code {proc.returncode}) after {elapsed}s")
            print("--- tail of /tmp/vlm_server.log ---")
            try:
                print("".join(open("/tmp/vlm_server.log").readlines()[-15:]))
            except Exception:
                pass
            return None

        try:
            resp = requests.get(f"{bc.ocr_base_url()}/health", timeout=2)
            if resp.status_code == 200:
                print(f"\n\n✓ Vision server ready! (took {elapsed}s)")
                if not restart:
                    print("="*60)
                print()
                return proc
        except:
            pass

    print("\n\n✗ Server timeout")
    print("Check logs: docker exec booktranslator-translator-1 cat /tmp/vlm_server.log\n")
    proc.terminate()
    return None


def prepare_image_for_vlm(img: Image.Image, max_dimension: int = VLM_MAX_DIMENSION, quality: int = 92) -> tuple:
    """Resize to olmOCR-2's documented longest-dimension spec and encode.
    Quality can stay high since the image itself is intentionally small --
    this isn't a size/speed compromise the way Qwen2-VL's escalating
    compression was."""
    if img.width > max_dimension or img.height > max_dimension:
        ratio = min(max_dimension / img.width, max_dimension / img.height)
        new_size = (int(img.width * ratio), int(img.height * ratio))
        img = img.resize(new_size, Image.Resampling.LANCZOS)

    buffered = io.BytesIO()
    img.save(buffered, format="JPEG", quality=quality, optimize=True)
    img_bytes = buffered.getvalue()
    size_kb = len(img_bytes) / 1024
    img_b64 = base64.b64encode(img_bytes).decode()
    return img_b64, size_kb


def resolve_crop(src_path: str):
    """Optional, no longer the primary defense against margin clutter (the
    prompt now handles that directly -- see module docstring), but still
    honored if present in case a page needs it anyway."""
    crop_file = Path(src_path).with_suffix(".crop")
    if crop_file.exists():
        try:
            parts = [float(x.strip()) for x in crop_file.read_text(encoding="utf-8").strip().split(",")]
            if len(parts) == 4:
                return tuple(parts)
        except ValueError:
            pass
    return (0.0, 0.0, 0.0, 0.0)


def apply_crop_to_image(img: Image.Image, crop: tuple) -> Image.Image:
    left, top, right, bottom = crop
    w, h = img.size
    return img.crop((int(w * left), int(h * top), int(w * (1 - right)), int(h * (1 - bottom))))


def extract_page_with_vlm(image_b64: str, size_kb: float, page_num: int, total_pages: int) -> dict:
    """Extract text from page using VLM"""
    timeout = 300

    try:
        response = requests.post(
            f"{bc.ocr_base_url()}/v1/chat/completions",
            headers={"Content-Type": "application/json", **bc.auth_headers()},
            json={
                "model": "olmocr2",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": VLM_PROMPT_TEMPLATE},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}
                    ]
                }],
                "temperature": 0.1,
                "max_tokens": 4096,
                # Mild -- breaks the dot-leader / "* * *" repetition loops
                # olmOCR-2 falls into on some table-of-contents and ornament
                # pages without hurting legitimately repeated words.
                "repeat_penalty": 1.12
            },
            timeout=timeout
        )

        if response.status_code == 200:
            result = response.json()
            content = collapse_runs(result["choices"][0]["message"]["content"])
            print(f"  Page {page_num+1}/{total_pages} ✓ ({size_kb:.0f}KB)")
            return {"success": True, "content": content}

        elif response.status_code == 500:
            try:
                error_json = response.json()
                error_msg = error_json.get('error', {}).get('message', 'Unknown')
            except:
                error_msg = "Unknown"

            print(f"  Page {page_num+1}/{total_pages} ✗ HTTP 500: {error_msg}")
            return {"success": False, "error": error_msg, "retry": True}

        else:
            print(f"  Page {page_num+1}/{total_pages} ✗ HTTP {response.status_code}")
            return {"success": False, "error": f"HTTP {response.status_code}"}

    except requests.exceptions.Timeout:
        print(f"  Page {page_num+1}/{total_pages} ✗ Timeout after {timeout}s")
        return {"success": False, "error": "Timeout", "retry": True}
    except Exception as e:
        print(f"  Page {page_num+1}/{total_pages} ✗ {type(e).__name__}")
        return {"success": False, "error": str(e)}

print("\n" + "="*60)
print("STEP 1: VLM OCR EXTRACTION (olmOCR-2-7B-1025)")
print("="*60)
print(f"Preprocessing (grayscale+autocontrast): {'ON' if VLM_PREPROCESS else 'OFF'}")
print(f"Resize target (longest dimension): {VLM_MAX_DIMENSION}px")

vlm_proc = None

try:
    input_pdfs = [
        f for f in glob.glob("/data/books/*.pdf")
        if not os.path.exists(f"/data/processed/{Path(f).stem}_translated")
    ]

    if not input_pdfs:
        print("\n✓ No new books to process\n")
        exit(0)

    vlm_proc = start_vlm_server()

    if vlm_proc is None:
        print("✗ Cannot start VLM server\n")
        exit(1)

    for src_path in input_pdfs:
        stem = Path(src_path).stem
        work_dir = f"/data/processed/{stem}_translated"
        os.makedirs(work_dir, exist_ok=True)

        print(f"Processing: {stem}")

        ocr_pdf_path = f"{work_dir}/{stem}_OCR.pdf"
        shutil.copy2(src_path, ocr_pdf_path)
        print(f"✓ Original PDF copied as OCR PDF")

        crop = resolve_crop(src_path)
        if any(crop):
            print(f"  Using .crop override (left,top,right,bottom): {crop}")

        doc = fitz.open(src_path)
        total_pages = len(doc)
        print(f"Total pages: {total_pages}\n")

        extracted_pages = []
        error_count = 0

        for i in range(total_pages):
            page = doc[i]
            mat = fitz.Matrix(300/72, 300/72)
            pix = page.get_pixmap(matrix=mat)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            img = preprocess_for_ocr(img)
            if any(crop):
                img = apply_crop_to_image(img, crop)

            img_b64, size_kb = prepare_image_for_vlm(img)
            result = extract_page_with_vlm(img_b64, size_kb, i, total_pages)

            if not result["success"] and vlm_proc and vlm_proc is not REMOTE_SERVER:
                print(f"  → Restarting server and retrying page {i+1}...")
                vlm_proc.terminate()
                try:
                    vlm_proc.wait(timeout=5)
                except:
                    vlm_proc.kill()
                time.sleep(3)

                vlm_proc = start_vlm_server(restart=True)

                if vlm_proc:
                    # Last-resort retry at a smaller size in case the first
                    # attempt failed due to a load/VRAM hiccup rather than
                    # the page itself.
                    img_b64_small, size_kb_small = prepare_image_for_vlm(img, max_dimension=900, quality=80)
                    result = extract_page_with_vlm(img_b64_small, size_kb_small, i, total_pages)

            if result and result["success"]:
                extracted_pages.append(result["content"])
            else:
                error_count += 1
                error_msg = result.get("error", "Unknown") if result else "No response"
                extracted_pages.append(f"[VLM Error: {error_msg}]")

            time.sleep(2)

        doc.close()
        print()

        # Preserve the TRUE raw model responses permanently, before any
        # parsing -- same contract as the Tesseract path's raw_ocr.txt: the
        # ground truth to fall back on if the parsing/reconciliation below
        # turns out to have dropped or altered something.
        with open(f"{work_dir}/raw_ocr.txt", "w", encoding="utf-8") as f:
            f.write(_pageio.join_pages(extracted_pages))

        # Split olmOCR-2's PAGE_NUMBER/HEADER/BODY_TEXT scaffold into fields
        # and sequence-check the page number. Previously the raw scaffold
        # went straight downstream, so a hallucinated "PAGE_NUMBER: 57"
        # (read off an engraving plate) became a line handed to the
        # translator and printed on the side-by-side PDF.
        parsed = [parse_vlm_response(p) for p in extracted_pages]
        _known_heads = {_norm_head(p["header"]) for p in parsed if p["header"]}
        for p in parsed:
            p["body"] = strip_leaked_headmatter(p["body"], _known_heads)
        reconciled_nums = reconcile_page_numbers([p["page_raw"] for p in parsed])

        canonical_pages = []
        for raw, fields, pnum in zip(extracted_pages, parsed, reconciled_nums):
            if raw.lstrip().startswith("[VLM Error"):
                canonical_pages.append(raw)          # keep the error marker intact
                continue
            canonical_pages.append(
                f"PAGE_NUMBER: {pnum}\n"
                f"HEADER: {fields['header'] or 'none'}\n"
                f"BODY_TEXT:\n{fields['body']}"
            )

        with open(f"{work_dir}/extracted.txt", "w", encoding="utf-8") as f:
            f.write(_pageio.join_pages(canonical_pages))

        # Per-page figure metadata for the English-only PDF (kind / vertical
        # extent / caption, as reported by the VLM). Captions are translated
        # into this file by 2_translate.py. Keyed by 0-based page index.
        figures_map = {str(i): fields["figures"]
                       for i, fields in enumerate(parsed) if fields.get("figures")}
        with open(f"{work_dir}/figures.json", "w", encoding="utf-8") as f:
            json.dump(figures_map, f, ensure_ascii=False, indent=1)
        n_fig = sum(len(v) for v in figures_map.values())
        print(f"  {n_fig} figure(s) flagged across {len(figures_map)} page(s)")

        # Parity with the Tesseract path: one reconciled page number per
        # line, '[n]' where it had to be inferred rather than read.
        with open(f"{work_dir}/page_numbers.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(reconciled_nums))

        if error_count > 0:
            print(f"⚠ Extraction complete with {error_count}/{total_pages} errors: {stem}\n")
        else:
            print(f"✓ Extraction complete (all {total_pages} pages successful): {stem}\n")

finally:
    if vlm_proc is REMOTE_SERVER:
        print("[remote] leaving the remote VLM/OCR server as-is\n")
    elif vlm_proc:
        print("="*60)
        print("UNLOADING VISION MODEL")
        print("="*60 + "\n")
        print("Shutting down vision server...")
        vlm_proc.terminate()
        try:
            vlm_proc.wait(timeout=10)
            print("✓ Vision server stopped (VRAM freed)\n")
        except:
            vlm_proc.kill()
            print("✓ Vision server killed (VRAM freed)\n")

print("="*60)
print("✓ VLM OCR COMPLETE")
print("="*60 + "\n")
