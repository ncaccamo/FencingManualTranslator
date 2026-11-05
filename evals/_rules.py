# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""The pipeline's step 1 / step 2 code (_ocr.py, _translate.py) for the
regression tests, with stubs for heavy dependencies the tests never touch.
`TX` / `OCR` are the modules' namespaces: a test that sets TX["BOOK_LANG"]
changes the module itself, so it puts the value back afterwards.
"""
from pathlib import Path
import importlib
import sys
import types

PIPELINE = Path(__file__).resolve().parent.parent / "pipeline"
if not PIPELINE.is_dir():                  # in the image: evals at /data/evals, pipeline at /app
    PIPELINE = Path("/app")
sys.path.insert(0, str(PIPELINE))


def _stub_missing(*names: str) -> None:
    """The pipeline scripts import heavy deps (fitz, PIL, numpy) at module
    top that the pure helpers here never touch. Inject inert stubs for any
    that aren't installed so the modules import."""
    for name in names:
        try:
            importlib.import_module(name.split(".")[0])
        except ImportError:
            mod = types.ModuleType(name)
            # .Image: _ocr.py's type hints (img: Image.Image) need the name
            mod.__getattr__ = lambda _n: types.SimpleNamespace(Image=object)  # type: ignore
            sys.modules.setdefault(name, mod)
            if "." in name:
                sys.modules.setdefault(name.split(".")[0], mod)


_stub_missing("fitz", "PIL", "PIL.Image", "PIL.ImageOps", "numpy")


import _ocr  # noqa: E402
import _translate  # noqa: E402

TX = vars(_translate)
OCR = vars(_ocr)

# --- translation-side pure helpers ---
apply_always_translate = TX["apply_always_translate"]
match_case = TX["match_case"]
enforce_kept_terms = TX["enforce_kept_terms"]
plain_line_names = TX["plain_line_names"]
house_italics = TX["house_italics"]
fix_known_garbles = TX["fix_known_garbles"]
match_dash_caps = TX["match_dash_caps"]
save_partial = TX["save_partial"]
load_partial = TX["load_partial"]
harmonize_titles_with_headers = TX["harmonize_titles_with_headers"]
enforce_footnoted_terms = TX["enforce_footnoted_terms"]
pull_footnotes = TX["pull_footnotes"]
repair_mangled_terms = TX["repair_mangled_terms"]
contains_too_much_non_english = TX["contains_too_much_non_english"]
has_wrong_script = TX["has_wrong_script"]
strip_model_preamble = TX["strip_model_preamble"]
looks_like_refusal = TX["looks_like_refusal"]
match_boundary_punctuation = TX["match_boundary_punctuation"]
deshout = TX["deshout"]
_shout_ratio = TX["_shout_ratio"]
_phrase_repairs = TX["_phrase_repairs"]
clean_vlm_extraction = TX["clean_vlm_extraction"]
extract_source_footnotes = TX["extract_source_footnotes"]
fix_subheading_spacing = TX["fix_subheading_spacing"]
is_toc_page = TX["is_toc_page"]
_clean_caption = TX["_clean_caption"]
_capitalize_definition_entries = TX["_capitalize_definition_entries"]
fix_parry_names = TX["fix_parry_names"]
restore_dropped_part_title = TX["restore_dropped_part_title"]

# --- ocr-side pure helpers ---
parse_vlm_response = OCR["parse_vlm_response"]
save_ocr_partial = OCR["save_ocr_partial"]
load_ocr_partial = OCR["load_ocr_partial"]
parse_figures_block = OCR["parse_figures_block"]
reconcile_page_numbers = OCR["reconcile_page_numbers"]
strip_leaked_headmatter = OCR["strip_leaked_headmatter"]
strip_fieldless_folio = OCR["strip_fieldless_folio"]
dehyphenate_across_pages = OCR["dehyphenate_across_pages"]
tail_catchwords = OCR["tail_catchwords"]
leaked_top_header = OCR["leaked_top_header"]
is_plate_leaf = OCR["is_plate_leaf"]
_fold_caption_only_body = OCR["_fold_caption_only_body"]
_norm_head = OCR["_norm_head"]
salvage_leaked_folio_header = OCR["salvage_leaked_folio_header"]
dehyphenate_across_pages = OCR["dehyphenate_across_pages"]
join_split_sentences = OCR["join_split_sentences"]
apply_folio_witness = OCR["apply_folio_witness"]
plain_math = OCR["plain_math"]
drop_google_boilerplate = OCR["drop_google_boilerplate"]
mark_handwritten_inscriptions = OCR["mark_handwritten_inscriptions"]
drop_scan_apparatus = OCR["drop_scan_apparatus"]
fold_caption_header_into_figure = OCR["fold_caption_header_into_figure"]
reconcile_headers = OCR["reconcile_headers"]
apply_read_heads = OCR["apply_read_heads"]
recover_plate_captions = OCR["recover_plate_captions"]
drop_image_descriptions = OCR["drop_image_descriptions"]
repair_source_ocr_misreads = OCR["repair_source_ocr_misreads"]
