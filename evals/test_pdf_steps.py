#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Steps 3 and 4 run for real on a three-page book made up on the spot: no
model, no GPU, none of the operator's books. In the container:

    docker exec fmt-translator python3 /data/evals/test_pdf_steps.py

test_rules.py holds the rules to their past fixes; this is the check that
the two PDF steps still start, read a book's files and draw every page --
after a change to the Dockerfile's package versions, to _render.py, or to
anything both steps share. The book is written to a temporary folder and
given to the steps as a folder, so nothing under processed/ or books/ is
touched.

What it asserts: both steps finish without an error, each writes its PDF,
the English-only PDF holds every page's text, the side-by-side PDF has a
sheet for every page beside the cover, a kept term is in italics, a
footnote is drawn, and the source's own words are not in the English-only
copy.
"""
import os
import shutil
import subprocess
import sys
import tempfile

APP = "/app" if os.path.isdir("/app") else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pipeline")
sys.path.insert(0, APP)

STEM = "tinybook_1900_essai"
SOURCE = [
    ("I", "PREMIÈRE PARTIE.\n\nDE LA GARDE.\n\nLa garde est la position la plus favorable pour l'attaque et pour la "
          "défense. Le tireur se place de profil, les pieds en équerre, le bras droit à demi tendu."),
    ("2", "DE LA RIPOSTE.\n\nLa riposte est le coup porté après la parade. Elle se fait par un liement ou par un "
          "coup droit, selon que l'adversaire reste ou non sur le fer."),
    ("3", "Le maître doit exiger que l'élève revienne en garde après chaque attaque, sans précipitation, et qu'il "
          "garde la mesure."),
]
ENGLISH = [
    "FIRST PART.\n\nOF THE GUARD.\n\nThe guard is the position most favorable for the attack and for the defense. "
    "The fencer places himself in profile, the feet at right angles, the right arm half extended.",
    "OF THE <i>RIPOSTE</i>.\n\nThe <i>riposte</i> is the attack delivered after the parry. It is made by a bind†1 or "
    "by a straight thrust, according to whether the opponent stays on the blade or not.\n\n"
    "___FOOTNOTES___\n†1 French: liement",
    "The fencing master must require that the student return to guard after each attack, without haste, and that "
    "he keep the measure.",
]


def make_book(root: str) -> str:
    import fitz
    import _pageio
    folder = os.path.join(root, f"{STEM}_translated")
    inter = os.path.join(folder, "intermediate")
    os.makedirs(inter)
    doc = fitz.open()
    for folio, text in SOURCE:
        page = doc.new_page(width=420, height=595)
        page.insert_textbox(fitz.Rect(50, 60, 370, 520), text, fontsize=11, fontname="tiro")
        page.insert_text((205, 560), folio, fontsize=10, fontname="tiro")
    doc.save(os.path.join(inter, f"{STEM}_OCR.pdf"))
    doc.close()
    rec = lambda folio, text: f"\nPAGE_NUMBER: {folio}\nHEADER: none\nBODY_TEXT:\n{text}\n"
    with open(os.path.join(inter, "extracted.txt"), "w", encoding="utf-8") as fh:
        fh.write(_pageio.join_pages([rec(f, t) for f, t in SOURCE]))
    with open(os.path.join(inter, "raw_ocr.txt"), "w", encoding="utf-8") as fh:
        fh.write(_pageio.join_pages([rec(f, t) for f, t in SOURCE]))
    with open(os.path.join(inter, "translation.txt"), "w", encoding="utf-8") as fh:
        fh.write(_pageio.join_pages(["\n" + t + "\n" for t in ENGLISH]))
    with open(os.path.join(inter, "page_numbers.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(f for f, _ in SOURCE) + "\n")
    return folder


def run_step(script: str, folder: str) -> str:
    r = subprocess.run([sys.executable, os.path.join(APP, script), folder], capture_output=True, text=True, timeout=600)
    out = r.stdout + r.stderr
    assert r.returncode == 0, f"{script} exited {r.returncode}:\n{out[-2000:]}"
    assert "Traceback" not in out, f"{script}:\n{out[-2000:]}"
    return out


def pdf_text(path: str) -> tuple:
    import fitz
    doc = fitz.open(path)
    pages = [p.get_text() for p in doc]
    italic = {s["text"].strip() for p in doc for b in p.get_text("dict")["blocks"] for l in b.get("lines", [])
              for s in l["spans"] if s["flags"] & 2 or "Italic" in s["font"] or "Oblique" in s["font"]}
    doc.close()
    return pages, italic


def main() -> int:
    try:
        import fitz  # noqa: F401
    except ImportError:
        print("test_pdf_steps.py needs the container (PyMuPDF): docker exec fmt-translator python3 /data/evals/test_pdf_steps.py")
        return 2
    root = tempfile.mkdtemp(prefix="fmt_pdf_steps_")
    try:
        folder = make_book(root)
        for script, name in (("3_english_only_pdf.py", "English Only (Portrait)"),
                             ("4_side_by_side_pdf.py", "Side by Side (Landscape)")):
            out = run_step(script, folder)
            path = os.path.join(folder, f"{STEM} — {name}.pdf")
            assert os.path.exists(path) and os.path.getsize(path) > 2000, f"{script}: no PDF written\n{out[-800:]}"
            pages, italic = pdf_text(path)
            whole = " ".join(" ".join(pages).split())
            for want in ("OF THE GUARD", "The guard is the position most favorable", "delivered after the parry",
                         "The fencing master must require", "keep the measure", "French: liement"):
                assert want in whole, f"{name}: {want!r} is not in the PDF"
            assert any("riposte" in t.lower() for t in italic), f"{name}: the kept term is not in italics ({sorted(italic)[:8]})"
            assert "___FOOTNOTES___" not in whole and "<i>" not in whole and "†1 French" not in whole.replace("†1 French: liement", ""), \
                f"{name}: markup drawn as text"
            if name.startswith("English"):
                assert "La garde est la position" not in whole, "English-only PDF holds the source text"
            else:
                assert len(pages) >= len(SOURCE) + 1, f"side-by-side: {len(pages)} sheets for {len(SOURCE)} pages and a cover"
            print(f"  ok   {script}: {len(pages)} sheet(s)")
        print("\n2/2 passed")
        return 0
    except AssertionError as e:
        print(f"  FAIL {e}")
        return 1
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
