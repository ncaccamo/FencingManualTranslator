#!/usr/bin/env python3
import fitz
import os
from pathlib import Path
import glob
import re
from datetime import datetime

print("\n" + "="*60)
print("STEP 4: SIDE-BY-SIDE PDF GENERATION")
print("="*60 + "\n")

def parse_vlm_output(text: str) -> dict:
    """Parse VLM structured output into components"""
    result = {"page_num": "", "header": "", "body": ""}
    
    page_match = re.search(r'PAGE_NUMBER:\s*(.+?)(?:\n|$)', text, re.IGNORECASE)
    if page_match:
        result["page_num"] = page_match.group(1).strip()
    
    header_match = re.search(r'HEADER:\s*(.+?)(?:\n|BODY_TEXT:)', text, re.IGNORECASE | re.DOTALL)
    if header_match:
        header_text = header_match.group(1).strip()
        if header_text.lower() != "none":
            result["header"] = header_text
    
    body_match = re.search(r'BODY_TEXT:\s*(.+)', text, re.IGNORECASE | re.DOTALL)
    if body_match:
        result["body"] = body_match.group(1).strip()
    
    return result

created_count = 0

for folder in [f for f in glob.glob("/data/processed/*_translated") if os.path.isdir(f)]:
    book = Path(folder).name.replace("_translated", "")
    
    if not os.path.exists(f"{folder}/translation.txt"):
        print(f"⊘ Skip (no translation): {book}")
        continue

    ocr_pdf = None
    for file in os.listdir(folder):
        if file.endswith("_OCR.pdf") or ("OCR" in file and file.endswith(".pdf")):
            ocr_pdf = os.path.join(folder, file)
            break
    
    if not ocr_pdf or not os.path.exists(ocr_pdf):
        print(f"⊘ Skip (no OCR PDF): {book}")
        continue

    print(f"Creating: {book}")

    out = f"{folder}/{book} — Side by Side (Landscape).pdf"
    src = fitz.open(ocr_pdf)
    
    with open(f"{folder}/translation.txt", "r", encoding="utf-8") as f:
        translations = [p.strip() for p in f.read().split("\n---\n") if p.strip()]
    
    with open(f"{folder}/extracted.txt", "r", encoding="utf-8") as f:
        extractions = [p.strip() for p in f.read().split("\n---\n") if p.strip()]

    new = fitz.open()
    
    # Cover page
    cover = new.new_page(width=792, height=612)
    cover.insert_text((72, 100), "Side-by-Side Translation", fontsize=16, fontname="times-roman")
    cover.insert_text((72, 130), f"Date: {datetime.now().strftime('%B %d, %Y')}", fontsize=12, fontname="times-roman")
    cover.insert_text((72, 150), "Left: Original | Right: English Translation", fontsize=12, fontname="times-roman")
    cover.insert_text((72, 180), f"Book: {book}", fontsize=11, fontname="times-roman")
    
    for i in range(min(len(src), len(translations))):
        p = new.new_page(width=792, height=612)
        
        # Left side: Original page
        left_rect = fitz.Rect(10, 10, 386, 602)
        p.show_pdf_page(left_rect, src, i)
        
        # Right side: Translation
        structure = parse_vlm_output(extractions[i]) if i < len(extractions) else {}
        translation_text = translations[i]
        
        right_x0 = 406
        right_x1 = 782
        cursor_y = 30
        
        # Page number
        if structure.get("page_num"):
            p.insert_text((right_x0, cursor_y), f"Page: {structure['page_num']}", 
                         fontsize=9, fontname="times-bold")
            cursor_y += 15
        
        # Header
        if structure.get("header"):
            header_text = structure['header'].replace('\n', ' ')
            p.insert_text((right_x0, cursor_y), f"Header: {header_text}", 
                         fontsize=8, fontname="times-italic")
            cursor_y += 12
        
        # Separator
        p.draw_line((right_x0, cursor_y), (right_x1, cursor_y), width=0.5)
        cursor_y += 10
        
        # Translation text area
        text_area = fitz.Rect(right_x0, cursor_y, right_x1, 582)
        
        # Clean text
        clean_text = translation_text.replace('\n', ' ')
        clean_text = re.sub(r'\s+', ' ', clean_text)
        plain_text = clean_text.replace('<i>', '').replace('</i>', '')
        
        p.insert_textbox(text_area, plain_text, fontsize=9, fontname="times-roman",
                        align=fitz.TEXT_ALIGN_LEFT)
        
        # Vertical divider
        p.draw_line((396, 10), (396, 602), width=1)

    new.save(out, garbage=4, deflate=True)
    new.close()
    src.close()
    print(f"✓ Created: {Path(out).name}\n")
    created_count += 1

if created_count == 0:
    print("✗ No PDFs created (translations missing or failed)\n")
else:
    print(f"✓ Created {created_count} side-by-side PDF(s)\n")

print("="*60)
print("SIDE-BY-SIDE PDF GENERATION COMPLETE")
print("="*60 + "\n")
