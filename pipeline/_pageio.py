# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Page separator for the multi-page intermediate files.

`raw_ocr.txt`, `extracted.txt` and `translation.txt` are one record per
source page, joined by a separator. The old separator was `\n---\n` --
content-fragile: the translation model emits Markdown, and a lone `---`
thematic-break line inside one page split 178 pages into 179, misaligning
every later page with its source scan (Spinnewyn run, see
docs/model-quirks.md).

The separator is now U+001E (INFORMATION SEPARATOR TWO / "record
separator") -- a non-printing control character that cannot occur in OCR
text or model output, so nothing the models produce can split a page.

`split_pages` still reads a legacy `\n---\n` file (written before the
switch) so old `processed/<book>_translated/` folders keep working
without a re-run. A file is one format or the other, never mixed.
"""

PAGE_SEP = "\n\x1e\n"
_LEGACY_SEP = "\n---\n"


def split_pages(text: str) -> list:
    """Split a multi-page intermediate file into per-page records. Accepts
    the current U+001E separator or a legacy `\\n---\\n` file."""
    if "\x1e" in text:
        return text.split("\x1e")
    return text.split(_LEGACY_SEP)


def join_pages(pages) -> str:
    """Join per-page records with the current separator."""
    return PAGE_SEP.join(p.strip("\n") for p in pages)
