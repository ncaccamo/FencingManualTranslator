#!/usr/bin/env python3
"""
Test VLM OCR on a single page to verify it's working -- matches the real
pipeline's image prep (1288px, JPEG, optional preprocessing/crop) and
prompt (vlm_ocr_prompt.txt) so what you see here is what the full run will
actually produce, not a different code path.

Fixed from the original version:
  - Port was 11434 (that's the TRANSLATION model's port); the VLM server
    runs on 11435. This would have failed against a running VLM server and
    silently talked to nothing (or the translation server) instead.
  - Now loads the same vlm_ocr_prompt.txt the real pipeline uses, instead
    of a different inline prompt -- a single-page test using a different
    prompt than production doesn't actually tell you what production will
    do.
  - Now resizes to olmOCR-2's documented 1288px longest-dimension spec as
    JPEG, instead of sending a full-resolution PNG.
"""

import sys
import os
import fitz
from PIL import Image, ImageOps
import base64
import io
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backend_config as bc

if len(sys.argv) < 2:
    print("Usage: python3 test_vlm_single_page.py <pdf_file> [page_number]")
    print("Example: python3 test_vlm_single_page.py /data/books/mybook.pdf 0")
    sys.exit(1)

pdf_path = sys.argv[1]
page_num = int(sys.argv[2]) if len(sys.argv) > 2 else 0

PROMPT_FILE = os.environ.get("VLM_PROMPT_FILE", "/app/vlm_ocr_prompt.txt")
MAX_DIMENSION = int(os.environ.get("VLM_MAX_DIMENSISN", "1288"))
PREPROCESS = os.environ.get("VLM_PREPROCESS", "1") != "0"

if not os.path.exists(PROMPT_FILE):
    print(f"✗ Prompt file not found: {PROMPT_FILE}")
    sys.exit(1)
with open(PROMPT_FILE, "r", encoding="utf-8") as f:
    prompt = f.read().strip()

print(f"\nTesting VLM OCR on: {pdf_path}")
print(f"Page: {page_num + 1}")
print(f"Prompt file: {PROMPT_FILE}")
print(f"Resize target: {MAX_DIMENSION}px | Preprocess: {'ON' if PREPROCESS else 'OFF'}\n")

# Convert page to image, matching the real pipeline
doc = fitz.open(pdf_path)
page = doc[page_num]
mat = fitz.Matrix(300/72, 300/72)
pix = page.get_pixmap(matrix=mat)
img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
doc.close()

if PREPROCESS:
    img = ImageOps.autocontrast(img.convert("L"), cutoff=1).convert("RGB")

crop_file = os.path.splitext(pdf_path)[0] + ".crop"
if os.path.exists(crop_file):
    left, top, right, bottom = [float(x.strip()) for x in open(crop_file).read().strip().split(",")]
    w, h = img.size
    img = img.crop((int(w * left), int(h * top), int(w * (1 - right)), int(h * (1 - bottom))))
    print(f"Applied crop from {crop_file}: {left},{top},{right},{bottom}")

if img.width > MAX_DIMENSION or img.height > MAX_DIMENSION:
    ratio = min(MAX_DIMENSION / img.width, MAX_DIMENSION / img.height)
    img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.Resampling.LANCZOS)

buffered = io.BytesIO()
img.save(buffered, format="JPEG", quality=92, optimize=True)
img_b64 = base64.b64encode(buffered.getvalue()).decode()
print(f"Image prepared: {img.width}x{img.height}, {len(buffered.getvalue())/1024:.0f}KB")

print(f"Sending to VLM ({bc.ocr_base_url()})...")

response = requests.post(
    f"{bc.ocr_base_url()}/v1/chat/completions",
    headers={"Content-Type": "application/json", **bc.auth_headers()},
    json={
        "model": "olmocr2",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
                ]
            }
        ],
        "temperature": 0.1,
        "max_tokens": 4096
    },
    timeout=180
)

if response.status_code == 200:
    result = response.json()
    content = result["choices"][0]["message"]["content"]

    print("\n" + "="*60)
    print("VLM RESPONSE:")
    print("="*60)
    print(content)
    print("="*60 + "\n")
else:
    print(f"Error: {response.status_code}")
    print(response.text)
