# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Small pieces shared by step 2's rule modules (_tx_*.py): the footnote
block markers, the language labels, accent folding."""


FN_HEADER = "___FOOTNOTES___"


def _strip_accents(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


_LANG_REF = {"French": ("fr", "French"), "Italian": ("it", "Italian")}
