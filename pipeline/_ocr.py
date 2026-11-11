# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Step 1, OCR and extraction: the part that talks to the vision model --
page images, the OCR server and its client, the per-page post-processing
order (postprocess_parsed_pages), the resume state, and main(). The rest is
in _ocr_parse (reading one reply), _ocr_cleanup (one page's clean-ups),
_ocr_heads (running heads), _ocr_folios (page numbers) and _ocr_text (text
across page breaks). Importable without starting a server.

The model is olmOCR-2-7B-1025 (Allen AI, Qwen2.5-VL-7B fine-tuned for
document OCR); paths are env-overridable (VLM_MODEL_PATH, VLM_MMPROJ_PATH).
Every page is resized to 1288 px on its longest side, a documented olmOCR-2
requirement, not a tunable. The prompt (vlm_ocr_prompt.txt) tells the model
to ignore facing-page, binding and margin clutter; a book's .crop file is
still applied when it has one. A failed page is retried, restarting the
server.

Outputs (intermediate/):
  - raw_ocr.txt: the model's untouched replies, written before any parsing,
    so the parse can be redone without the model (reprocess_extracted.py).
  - extracted.txt: a canonical PAGE_NUMBER / HEADER / BODY_TEXT record per
    page, rebuilt from the parsed fields; step 2 and the PDF steps read it.
  - page_numbers.txt: one reconciled page number per line, '[n]' where
    inferred. olmOCR-2 reads a printed number well but invents one from an
    engraving plate number or a library stamp and turns 'VIII' into '8';
    reconcile_page_numbers() shows a number only when the sequence
    corroborates it.
"""
import os
import re
import json
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
import _overrides
from _ocr_cleanup import (
    _fold_caption_only_body, drop_google_boilerplate, drop_image_descriptions,
    drop_scan_apparatus, fold_caption_header_into_figure, is_plate_leaf,
    mark_handwritten_inscriptions, plain_math, recover_plate_captions,
    repair_source_ocr_misreads,
)
from _ocr_folios import (
    apply_folio_witness, reconcile_page_numbers, strip_fieldless_folio, text_layer_folio_bands,
)
from _ocr_heads import (
    apply_read_heads, known_headmatter, leaked_top_header, reconcile_headers,
    salvage_leaked_folio_header, settle_disputed_heads, strip_leaked_headmatter,
    tail_catchwords,
)
from _ocr_parse import _norm_head, collapse_runs, has_page_number_field, parse_vlm_response
from _ocr_text import dehyphenate_across_pages, join_split_sentences

VLM_MODEL_PATH = os.environ.get("VLM_MODEL_PATH", "/models/allenai_olmOCR-2-7B-1025-Q6_K.gguf")
VLM_MMPROJ_PATH = os.environ.get("VLM_MMPROJ_PATH", "")
# Request "model" label -- ignored by a single-model llama-server, the
# routing key under llama-swap. Env-overridable, else the model file name.
VLM_MODEL_NAME = os.environ.get("VLM_MODEL_NAME") or Path(VLM_MODEL_PATH).stem


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


def _resolve_mmproj() -> None:
    """Only needed to launch a local llama-server, so done when one starts,
    not at import. In BACKEND_MODE=remote the GPU system owns the model +
    projector and /models here is empty (see scripts/setup.sh --remote)."""
    global VLM_MMPROJ_PATH
    if not VLM_MMPROJ_PATH and not bc.is_remote():
        try:
            VLM_MMPROJ_PATH = _find_matching_mmproj(VLM_MODEL_PATH)
        except FileNotFoundError as e:
            print(f"\n✗ ERROR: {e}\n")
            exit(1)


VLM_PROMPT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vlm_ocr_prompt.txt")
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


def postprocess_parsed_pages(parsed: list) -> None:
    """Run every Group-A structural salvage in order. Called once, right
    after parse_vlm_response() over the whole book. reconcile_headers() and
    fold_caption_header_into_figure() run separately, AFTER
    _fold_caption_only_body()."""
    repair_source_ocr_misreads(parsed)
    kh = {_norm_head(p["header"]) for p in parsed if p.get("header")}
    salvage_leaked_folio_header(parsed, kh)
    dehyphenate_across_pages(parsed)
    drop_google_boilerplate(parsed)
    drop_scan_apparatus(parsed)
    drop_image_descriptions(parsed)
    plain_math(parsed)
    mark_handwritten_inscriptions(parsed)
    recover_plate_captions(parsed)


def save_ocr_partial(int_dir: str, total: int, crop, pages: list) -> None:
    """After each page: the raw model responses so far, so a crashed or
    cancelled OCR run resumes instead of starting over. Temp file + rename,
    so a crash mid-write never leaves a broken file."""
    p = f"{int_dir}/raw_ocr.partial.json"
    with open(p + ".tmp", "w", encoding="utf-8") as fh:
        json.dump({"version": 1, "total": total, "crop": list(crop), "pages": pages},
                  fh, ensure_ascii=False)
    os.replace(p + ".tmp", p)


def load_ocr_partial(int_dir: str, total: int, crop) -> list:
    """Raw responses saved by an interrupted OCR run, or [] -- only if the
    scan still has the same page count and crop. A page that failed
    ("[VLM Error: …]") is returned as None, to be read again."""
    try:
        d = json.load(open(f"{int_dir}/raw_ocr.partial.json", encoding="utf-8"))
        if d["total"] != total or d["crop"] != list(crop) or len(d["pages"]) > total:
            return []
        return [None if p.startswith("[VLM Error") else p for p in d["pages"]]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []


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
    _resolve_mmproj()
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
        except Exception:
            pass

    print("\n\n✗ Server timeout")
    print("Check logs: docker exec fmt-translator cat /tmp/vlm_server.log\n")
    proc.terminate()
    return None


def prepare_image_for_vlm(img: Image.Image, max_dimension: int = VLM_MAX_DIMENSION, quality: int = 92) -> tuple:
    """Resize to olmOCR-2's documented longest-dimension spec and encode.
    Quality can stay high: the image itself is small, so it costs little."""
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
                "model": VLM_MODEL_NAME,
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
            except Exception:
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


def main():
    """The `1_ocr_and_extract_vlm.py` command: the OCR server and the
    per-book run."""
    print("\n" + "="*60)
    print("STEP 1: VLM OCR EXTRACTION (olmOCR-2-7B-1025)")
    print("="*60)
    print(f"Preprocessing (grayscale+autocontrast): {'ON' if VLM_PREPROCESS else 'OFF'}")
    print(f"Resize target (longest dimension): {VLM_MAX_DIMENSION}px")

    vlm_proc = None

    try:
        # new books, plus any whose OCR run was interrupted (the folder exists,
        # the saved partial run does, raw_ocr.txt doesn't yet)
        # the books named on the command line, or --all (_books.py)
        # BOOK_PDF=/path/to/<stem>.pdf reads that one file instead -- e.g. a
        # temporary evaluation copy kept in evals/gold/, never in books/
        if os.environ.get("BOOK_PDF"):
            input_pdfs = [os.environ["BOOK_PDF"]]
        else:
            import _books
            input_pdfs = [f"/data/books/{_books.stem_of(n)}.pdf" for n in _books.select(
                sys.argv[1:], "usage: python3 /app/1_ocr_and_extract_vlm.py <stem> [<stem> ...] | --all\n"
                "  OCR + extraction of books/<stem>.pdf (a book already done is skipped)",
                source="pdf")]
        input_pdfs = [
            f for f in input_pdfs
            if not os.path.exists(f"/data/processed/{Path(f).stem}_translated")
            or (os.path.exists(f"/data/processed/{Path(f).stem}_translated/intermediate/raw_ocr.partial.json")
                and not os.path.exists(f"/data/processed/{Path(f).stem}_translated/intermediate/raw_ocr.txt"))
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
            int_dir = f"{work_dir}/intermediate"
            os.makedirs(int_dir, exist_ok=True)

            print(f"Processing: {stem}")

            ocr_pdf_path = f"{int_dir}/{stem}_OCR.pdf"
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
            saved = load_ocr_partial(int_dir, total_pages, crop)
            if saved:
                redo = sum(p is None for p in saved)
                print(f"Resuming an interrupted OCR run: {len(saved) - redo} page(s) already "
                      f"read" + (f", {redo} failed page(s) to read again" if redo else "") +
                      f"\n  (delete intermediate/raw_ocr.partial.json to start over)\n")
            else:
                # marks the book as in progress from the start, so even a crash
                # on page 1 is picked up again next run
                save_ocr_partial(int_dir, total_pages, crop, [])

            import _progress
            _progress.start(stem, "OCR", total_pages - sum(p is not None for p in (saved or [])))
            for i in range(total_pages):
                if i < len(saved) and saved[i] is not None:
                    extracted_pages.append(saved[i])
                    continue
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
                    except Exception:
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
                save_ocr_partial(int_dir, total_pages, crop, extracted_pages)
                _progress.page(i + 1)

                time.sleep(2)

            _progress.finish()
            doc.close()
            print()

            # Preserve the TRUE raw model responses permanently, before any
            # parsing -- same contract as the Tesseract path's raw_ocr.txt: the
            # ground truth to fall back on if the parsing/reconciliation below
            # turns out to have dropped or altered something.
            with open(f"{int_dir}/raw_ocr.txt", "w", encoding="utf-8") as f:
                f.write(_pageio.join_pages(extracted_pages))
            if os.path.exists(f"{int_dir}/raw_ocr.partial.json"):
                os.remove(f"{int_dir}/raw_ocr.partial.json")

            # Page layout (CPU) and margin notes, before parsing: the running-head
            # checks in the review report use the layout, and margin notes are
            # read by the OCR model while it's still loaded, then inserted into
            # the page text as ⟦MARGIN: …⟧ lines (_margins.py). Never allowed to
            # fail the OCR run -- layout_scan.py can fill them in later.
            try:
                import _layout
                import _margins
                print("Layout detection (CPU)...")
                _layout.scan_book(work_dir, ocr_pdf_path)
                _margins.read_book(work_dir, ocr_pdf_path)
            except Exception as e:
                print(f"⚠ layout / margin notes skipped ({type(e).__name__}: {e}) -- "
                      f"run layout_scan.py {stem} later\n")

            # Split olmOCR-2's PAGE_NUMBER/HEADER/BODY_TEXT scaffold into fields
            # and sequence-check the page number. Previously the raw scaffold
            # went straight downstream, so a hallucinated "PAGE_NUMBER: 57"
            # (read off an engraving plate) became a line handed to the
            # translator and printed on the side-by-side PDF.
            # the reviewer's corrections of OCR misreadings ([[ocr_fix]]), after
            # raw_ocr.txt is written (it always keeps the model's own reply)
            _n, _miss = _overrides.apply_ocr_fixes(extracted_pages, _overrides.load(stem))
            if _miss:
                print(f"  ⚠ overrides: [[ocr_fix]] text not found on scan page(s) {_miss} -- "
                      f"read right by this OCR? Review {_overrides.path_for(stem)}")
            parsed = [parse_vlm_response(p) for p in extracted_pages]
            postprocess_parsed_pages(parsed)
            # running heads read from the page where the OCR reply left the field out
            try:
                import _margins
                _read = _margins.read_heads(work_dir, ocr_pdf_path)
                apply_read_heads(parsed, extracted_pages, _read)
            except Exception as e:
                print(f"⚠ running heads not read ({type(e).__name__}: {e})")
            _known_heads = known_headmatter(parsed)
            _catch = tail_catchwords(parsed)
            # pages the layout detector is sure print no running head: no body
            # line is promoted to (or stripped as) a running head on them
            try:
                import _layout
                _no_head = _layout.no_head_pages(work_dir, [p.get("header") for p in parsed])
                _disputed = _layout.disputed_heads(work_dir, [p.get("header") for p in parsed])
            except Exception as e:
                print(f"⚠ layout not used for running heads ({type(e).__name__}: {e})")
                _no_head, _disputed = set(), set()
            for i, p in enumerate(parsed):
                if i in _no_head:
                    p["body"] = strip_fieldless_folio(p["body"], extracted_pages[i])
                    continue
                own = (p.get("header") or "").strip()   # the VLM's own, pre-promotion
                if not (p.get("header") or "").strip():
                    promoted = leaked_top_header(p["body"], _known_heads)
                    if promoted:
                        p["header"] = promoted
                p["body"] = strip_leaked_headmatter(p["body"], _known_heads, own, _catch)
            _fold_caption_only_body(parsed)
            fold_caption_header_into_figure(parsed)
            _no_head |= settle_disputed_heads(parsed, _disputed)
            reconcile_headers(parsed, _no_head)
            try:
                import _margins
                _margins.apply_to_book(work_dir, parsed)
            except Exception as e:
                print(f"⚠ margin notes not inserted ({type(e).__name__}: {e})")
            join_split_sentences(parsed)
            _is_plate = [any(f.get("kind") == "plate" for f in (p.get("figures") or []))
                         or is_plate_leaf(p.get("body"))
                         for p in parsed]
            reconciled_nums = reconcile_page_numbers(
                [p["page_raw"] for p in parsed], _is_plate, [p.get("body") for p in parsed],
                text_layer_folio_bands(ocr_pdf_path),
                [has_page_number_field(r) for r in extracted_pages])
            # a second reading of the page's margins confirms a hidden number
            _fs = f"{int_dir}/folio_strips.json"
            if os.path.exists(_fs):
                reconciled_nums = apply_folio_witness(reconciled_nums, json.load(open(_fs, encoding="utf-8")))
            # the reviewer's per-book decisions (books/<stem>.overrides.toml)
            # always win over the automatic reconciliation above
            reconciled_nums = _overrides.apply_extraction(
                parsed, reconciled_nums, _overrides.load(stem))

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

            with open(f"{int_dir}/extracted.txt", "w", encoding="utf-8") as f:
                f.write(_pageio.join_pages(canonical_pages))

            # Per-page figure metadata for the English-only PDF (kind / vertical
            # extent / caption, as reported by the VLM). Captions are translated
            # into this file by 2_translate.py. Keyed by 0-based page index.
            figures_map = {str(i): fields["figures"]
                           for i, fields in enumerate(parsed) if fields.get("figures")}
            with open(f"{int_dir}/figures.json", "w", encoding="utf-8") as f:
                json.dump(figures_map, f, ensure_ascii=False, indent=1)
            n_fig = sum(len(v) for v in figures_map.values())
            print(f"  {n_fig} figure(s) flagged across {len(figures_map)} page(s)")

            # Parity with the Tesseract path: one reconciled page number per
            # line, '[n]' where it had to be inferred rather than read.
            with open(f"{int_dir}/page_numbers.txt", "w", encoding="utf-8") as f:
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
            except Exception:
                vlm_proc.kill()
                print("✓ Vision server killed (VRAM freed)\n")

    print("="*60)
    print("✓ VLM OCR COMPLETE")
    print("="*60 + "\n")
