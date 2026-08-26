# BookTranslator — Handoff / Offramp Document
Updated after a session that: (1) fixed real OCR-quality bugs in the
Tesseract path, validated against a real 7-page sample, (2) swapped the
VLM OCR path from Qwen2-VL-7B to olmOCR-2-7B-1025, validated against a
real page with a perfect word-for-word transcription match, (3) set up
git + GitHub, (4) moved tooling from claude.ai chat to Claude Code in
VS Code.

---

## 1. CURRENT STATUS

**Infrastructure: DONE and confirmed working**, unchanged from before —
ROCm 7.2.4 + Vulkan dual backend, both llama-server ports (11434
translation, 11435 vision) load correctly. See prior HANDOFF content in
git history for full infra details if needed.

**Pipeline: substantially improved this session.**

### `1_ocr_and_extract.py` (Tesseract path) — rewritten, validated on a real 7-page sample
Confirmed via direct testing (not just code review):
- Raw OCR is now preserved permanently as `raw_ocr.txt` before any
  cleanup runs (previously NOT saved at all — a real gap against the
  project's own "keep raw OCR" requirement).
- Per-book OCR language via optional `<stem>.lang` sidecar file next to
  the source PDF (default `fra+lat+eng`) instead of always OCRing
  against 6 languages at once, which measurably hurts accuracy.
- Per-book crop via optional `<stem>.crop` sidecar (`left,top,right,bottom`
  fractions) — confirmed this fixes garbage tokens from facing-page/
  binding bleed in photographed scans.
- Forces Tesseract `--tesseract-pagesegmode 3` — confirmed dramatically
  cleaner than leaving PSM unset or forcing PSM 6.
- Removed `--oversample` by default — confirmed it was blowing up images
  5-6x for zero quality gain on scans where the PDF's page size is set
  from pixel count instead of true DPI (common in library digitizations).
- **Found and fixed a real corruption bug**: two "aggressive cleanup"
  regexes were silently destroying correctly-OCR'd French text —
  `"vous ébranler"` → `"vousébranler"`, systematically, on any word
  starting with é/è/ê/à following another word. Deleted outright, not
  just gated. `AGGRESSIVE_CLEANUP` now defaults off.
- Real ligature-replacement bug fixed (old code's fi/fl/ff replacements
  were accidental no-ops — source and target strings were identical).
- Validated end-to-end on a real page (from "Le jeu de l'épée") against
  visually-confirmed ground truth: near-perfect output.

### VLM OCR path — model swapped Qwen2-VL-7B-Instruct → olmOCR-2-7B-1025
- olmOCR-2 (Allen AI, fine-tuned from Qwen2.5-VL-7B specifically for
  document OCR, not general vision chat) is a strict upgrade for this
  task at the same VRAM class. GGUF via `bartowski/allenai_olmOCR-2-7B-1025-GGUF`.
- `1_ocr_and_extract_vlm.py` rewritten: fixed 1288px resize (documented
  olmOCR-2 requirement, replaces the old two-tier compression logic),
  `_find_matching_mmproj()` picks the correct mmproj by filename-token
  overlap rather than naive alphabetical-first (confirmed alphabetical
  sort was silently wrong: `mmproj-Qwen2-VL...` sorts before
  `mmproj-allenai_olmOCR...` in plain ASCII, nothing to do with which
  model is actually loaded).
- `vlm_ocr_prompt.txt` rewritten with explicit anti-hallucination
  instructions (no paraphrase/modernize/silent-skip) and an instruction
  to ignore facing-page/binding/margin clutter directly, rather than
  relying only on a physical crop.
- `test_vlm_single_page.py` fixed — was hardcoded to port 11434 (the
  TRANSLATION model's port, not the VLM's 11435) and used a different
  ad-hoc prompt than production. Now matches the real pipeline's image
  prep and prompt exactly.
- **`docker-compose.yml` fixed**: `VLM_MODEL_PATH`/`VLM_MMPROJ_PATH` were
  hardcoded to the old Qwen2-VL paths in the `environment:` block, which
  `entrypoint.sh` reads to auto-start the VLM server on container boot.
  This is now pointed at the olmOCR-2 files, so a normal
  `docker compose up -d` brings up the correct model with no manual
  intervention.
- **Validated on a real page**: ran against an actual library page (page
  124 of a different book than the 7-page sample), compared byte-for-byte
  against the visible source image — perfect match, 118/118 words
  identical, including correctly reassembling hyphenated line-wrapped
  words (`troi-\nsième` → `troisième`) and correctly ignoring bleed-through
  text from the reverse side of the thin paper without being told about
  this specific artifact.

### Not yet tested
- olmOCR-2 on a genuinely hard page (illustration, footnote, physical
  damage) — only tested on two clean prose pages so far.
- Direct head-to-head: same real page through both the fixed Tesseract
  path and olmOCR-2, to know which to default to per-page rather than
  assuming.
- Full-length book (still only validated on individual sample pages).

### Still not done (unchanged from before)
- Italic rendering for `<i>...</i>` spans in both PDF generators (still
  stripped, not rendered).
- Footnotes for hard-to-translate terms — not implemented; needs a
  `translation_prompt.txt` update to flag difficult terms plus footnote
  layout logic in both PDF generators.
- Image/figure embedding in `3_english_only_pdf.py` (side-by-side has
  this via `show_pdf_page`, english-only doesn't).
- Cover page consistency between the two PDF generators.
- `insert_textbox` overflow checking in both PDF generators — still
  unguarded, could silently drop text on long pages.
- English-only PDF still uses a fixed "2 slots per page" layout instead
  of flowing as many translated pages as fit per the original requirement.
- User Manual Sections I (Overview) and III (How to use) — Section II
  (Host Prerequisites) is the only one drafted so far.
- Open WebUI standalone setup script exists but was never connected /
  brought up.

---

## 2. TOOLING CHANGE THIS SESSION

Moved from claude.ai chat (with copy-paste terminal commands) to
Claude Code running directly in VS Code on this machine
(`nicholas-Ubuntu-AI`), specifically because most of the prior session's
time was lost to terminal paste corruption (heredocs getting mangled
mid-transit) and manual state-verification round-trips (`wc -l`/`grep -c`/
`diff` after every change to confirm a file actually landed correctly).
Claude Code has direct file and shell access, which removes both problems.

Project is now a git repo, pushed to GitHub (private). `.gitignore`
excludes `books/`, `processed/`, `*.gguf`, `.env`, `__pycache__/`.

---

## 3. PROJECT STRUCTURE

(unchanged — see MANIFEST.md and prior HANDOFF content in git history)

---

## 4. IMMEDIATE NEXT STEPS, IN ORDER

1. Test olmOCR-2 on a hard page (illustration/footnote/damage) and
   compare against the fixed Tesseract path on the same page.
2. Decide default OCR strategy: Tesseract-first with VLM fallback on
   suspect pages, or per-book choice, based on step 1's result.
3. Full-length book test through the whole pipeline.
4. Footnote implementation for hard-to-translate terms.
5. PDF generator fixes: italics rendering, image embedding, dynamic
   pagination/overflow handling, unified cover pages.
6. User Manual Sections I and III.