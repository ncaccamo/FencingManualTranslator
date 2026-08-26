#!/usr/bin/env python3
import fitz
import os
from pathlib import Path
import glob
import re
from datetime import datetime

print("\n" + "="*60)
print("STEP 3: ENGLISH-ONLY PDF GENERATION")
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

def insert_text_with_italics_proper(page, rect, text, fontsize=10):
    """Insert text with proper spacing using textbox"""
    # Split into parts with and without italic tags
    parts = re.split(r'(<i>.*?</i>)', text)
    
    # For now, just use textbox with plain text (italics will be preserved via tags later)
    # This is simpler and gives better spacing
    clean_text = text.replace('<i>', '').replace('</i>', '')
    
    # Use insert_textbox which handles wrapping properly
    page.insert_textbox(rect, clean_text, fontsize=fontsize, fontname="times-roman", 
                       align=fitz.TEXT_ALIGN_LEFT)

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

    out = f"{folder}/{book} — English Only (Portrait).pdf"
    src = fitz.open(ocr_pdf)
    
    with open(f"{folder}/translation.txt", "r", encoding="utf-8") as f:
        translations = [p.strip() for p in f.read().split("\n---\n") if p.strip()]
    
    with open(f"{folder}/extracted.txt", "r", encoding="utf-8") as f:
        extractions = [p.strip() for p in f.read().split("\n---\n") if p.strip()]

    new = fitz.open()
    
    # Create cover page
    cover = new.new_page(width=612, height=792)
    cover.insert_text((72, 100), "Translated by BookTranslator", fontsize=16, fontname="times-roman")
    cover.insert_text((72, 130), f"Date: {datetime.now().strftime('%B %d, %Y')}", fontsize=12, fontname="times-roman")
    cover.insert_text((72, 150), "Translation Model: Qwen2.5-7B-Instruct", fontsize=10, fontname="times-roman")
    cover.insert_text((72, 165), "Vision Model: Qwen2-VL-7B-Instruct", fontsize=10, fontname="times-roman")
    cover.insert_text((72, 200), f"Original: {book}", fontsize=12, fontname="times-roman")
    cover.insert_text((72, 230), "This is an AI-generated translation for research purposes.", fontsize=9, fontname="times-italic")
    
    # Process pages - 2 per printed page
    i = 0
    while i < len(translations):
        page = new.new_page(width=612, height=792)
        
        for slot in range(2):
            if i >= len(translations):
                break
            
            structure = parse_vlm_output(extractions[i]) if i < len(extractions) else {}
            translation_text = translations[i]
            
            y_offset = 40 if slot == 0 else 416
            slot_rect = fitz.Rect(36, y_offset, 576, y_offset + 356)
            
            cursor_y = slot_rect.y0
            
            # Page number
            if structure.get("page_num"):
                page.insert_text((slot_rect.x0, cursor_y), f"Page: {structure['page_num']}", 
                               fontsize=9, fontname="times-bold")
                cursor_y += 15
            
            # Header
            if structure.get("header"):
                header_text = structure['header'].replace('\n', ' ')
                page.insert_text((slot_rect.x0, cursor_y), f"Header: {header_text}", 
                               fontsize=8, fontname="times-italic")
                cursor_y += 12
            
            # Separator line
            page.draw_line((slot_rect.x0, cursor_y), (slot_rect.x1, cursor_y), width=0.5)
            cursor_y += 8
            
            # Body text area
            text_area = fitz.Rect(slot_rect.x0, cursor_y, slot_rect.x1, slot_rect.y1 - 10)
            
            # Clean and format text
            clean_text = translation_text.replace('\n', ' ')
            clean_text = re.sub(r'\s+', ' ', clean_text)
            
            # For italics, we need to handle them specially
            # For now, render without italic support but with proper spacing
            plain_text = clean_text.replace('<i>', '').replace('</i>', '')
            
            page.insert_textbox(text_area, plain_text, fontsize=10, fontname="times-roman",
                              align=fitz.TEXT_ALIGN_LEFT)
            
            # Draw border around slot
            page.draw_rect(slot_rect, width=0.3)
            
            i += 1

    new.save(out, garbage=4, deflate=True, clean=True)
    new.close()
    src.close()
    print(f"✓ Created: {Path(out).name}\n")
    created_count += 1

if created_count == 0:
    print("✗ No PDFs created (translations missing or failed)\n")
else:
    print(f"✓ Created {created_count} English-only PDF(s)\n")

print("="*60)
print("ENGLISH-ONLY PDF GENERATION COMPLETE")
print("="*60 + "\n")
