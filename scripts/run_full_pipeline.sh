#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
# Runs steps 1-4 and the review report on the books you name:
#   scripts/run_full_pipeline.sh <stem> [<stem> ...]     (books/<stem>.pdf)
#   scripts/run_full_pipeline.sh --all                   (every book; a book
#                                    marked finished is left alone)
WORKDIR=~/FencingManualTranslator
cd "$WORKDIR" || exit 1
if [ $# -eq 0 ]; then
    echo "usage: $0 <stem> [<stem> ...] | --all"
    echo "  books available:"; ls "$WORKDIR/books"/*.pdf 2>/dev/null | xargs -n1 basename | sed 's/\.pdf$//; s/^/    /'
    exit 2
fi

# Stamp the cover page with the pipeline revision. `docker exec` does not
# inherit host env, so pass it explicitly on the steps that build PDFs.
# Manual `docker exec` invocations just omit the line (see pipeline/_cover.py).
PIPELINE_VERSION=$(git -C "$WORKDIR" rev-parse --short HEAD 2>/dev/null)
export PIPELINE_VERSION

echo ""
echo "╔═══════════════════════════════════════════════════════╗"
echo "║  HISTORICAL FENCING TRANSLATOR – VLM PIPELINE         ║"
echo "╚═══════════════════════════════════════════════════════╝"
echo ""

# Ensure processed folder exists
if [ ! -d "$WORKDIR/processed" ]; then
    mkdir -p "$WORKDIR/processed"
fi

# Fix ownership
OWNER=$(stat -c '%U' "$WORKDIR/processed" 2>/dev/null || echo "")
if [ "$OWNER" = "root" ]; then
    echo "[SETUP] Fixing processed folder ownership..."
    sudo chown -R $(id -u):$(id -g) "$WORKDIR/processed"
fi

echo "[1/4] Starting container..."
echo "────────────────────────────────────────────────────────"
# No explicit -f: passing one disables Compose's auto-merge of
# docker-compose.override.yml, which a GPU-less remote host needs to strip
# the /dev/kfd device mount (see docs/remote-gpu.md). cd'd to $WORKDIR above.
docker compose down --remove-orphans
docker compose up -d
echo ""

echo "[2/4] VLM OCR + Structure Extraction..."
echo "────────────────────────────────────────────────────────"
echo "  → This step loads olmOCR-2-7B-1025 (vision model)"
echo "  → Extracts text and structure from PDF pages"
echo "  → Time: ~5-10 seconds per page"
echo ""
docker exec fmt-translator python3 /app/1_ocr_and_extract_vlm.py "$@"
OCR_EXIT=$?
if [ $OCR_EXIT -ne 0 ]; then
    echo ""
    echo "[ERROR] VLM OCR failed (exit code: $OCR_EXIT)"
    echo ""
    read -p "Press Enter to exit..."
    exit 1
fi
echo ""

echo "[3/4] AI Translation..."
echo "────────────────────────────────────────────────────────"
echo "  → This step loads the translation model"
echo "  → Translates extracted text to English"
echo "  → Preserves technical fencing terms"
echo "  → Time: ~10-20 seconds per page"
echo ""
docker exec fmt-translator python3 /app/2_translate.py "$@"
echo ""

echo "[4/4] PDF Generation..."
echo "────────────────────────────────────────────────────────"
docker exec -e PIPELINE_VERSION fmt-translator python3 /app/3_english_only_pdf.py "$@"
docker exec -e PIPELINE_VERSION fmt-translator python3 /app/4_side_by_side_pdf.py "$@"
echo ""

echo "Review reports (pages worth checking, by pdf page)..."
echo "────────────────────────────────────────────────────────"
docker exec fmt-translator python3 /app/quality_report.py "$@"
echo ""

echo "╔═══════════════════════════════════════════════════════╗"
echo "║                  PIPELINE COMPLETE                    ║"
echo "╚═══════════════════════════════════════════════════════╝"
echo ""
echo "Your PDFs and review reports are in: ~/FencingManualTranslator/processed/"
echo ""

if ls "$WORKDIR/processed"/*_translated 2>/dev/null | grep -q .; then
    echo "Processed books:"
    ls -1 "$WORKDIR/processed" | grep "_translated" | sed 's/_translated$//' | sed 's/^/  • /'
else
    echo "No books processed yet"
fi
echo ""

read -p "Press Enter to close..."
