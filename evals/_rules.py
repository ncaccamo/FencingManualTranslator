"""Load the *pure* helper functions out of the pipeline scripts without
running their `main` bodies (which start llama-servers and read /app paths).

`2_translate.py` and `1_ocr_and_extract_vlm.py` are plain scripts, not
importable modules -- everything above the "STEP N" banner is definitions,
everything below is the run. We exec the top slice in a throwaway namespace.

If either split marker or an /app path in those files changes, the exec
here breaks loudly and `test_rules.py` fails -- that's intentional, it
keeps the harness honest about drift.
"""
from pathlib import Path
import importlib
import sys
import types

PIPELINE = Path(__file__).resolve().parent.parent / "pipeline"
sys.path.insert(0, str(PIPELINE))


def _stub_missing(*names: str) -> None:
    """The pipeline scripts import heavy deps (fitz, PIL, numpy) at module
    top that the pure helpers here never touch. Inject inert stubs for any
    that aren't installed so the exec of the definitions slice succeeds."""
    for name in names:
        try:
            importlib.import_module(name.split(".")[0])
        except ImportError:
            mod = types.ModuleType(name)
            mod.__getattr__ = lambda _n: types.SimpleNamespace()  # type: ignore
            sys.modules.setdefault(name, mod)
            if "." in name:
                sys.modules.setdefault(name.split(".")[0], mod)


_stub_missing("fitz", "PIL", "PIL.Image", "PIL.ImageOps", "numpy")


def _load_prefix(filename: str, split_marker: str) -> dict:
    src = (PIPELINE / filename).read_text(encoding="utf-8")
    if split_marker not in src:
        raise RuntimeError(
            f"{filename}: split marker {split_marker!r} not found -- the "
            f"file's structure changed; update evals/_rules.py")
    src = src.split(split_marker)[0]
    # the scripts hard-code container paths and do exit(1) on missing model
    # files; point the prompt/glossary reads at the repo copies and skip the
    # model-file checks (the pure helpers don't need a model).
    for cp, real in {
        "/app/translation_prompt.txt": PIPELINE / "translation_prompt.txt",
        "/app/footnote_glossary.txt": PIPELINE / "footnote_glossary.txt",
        "/app/vlm_ocr_prompt.txt": PIPELINE / "vlm_ocr_prompt.txt",
    }.items():
        src = src.replace(f'"{cp}"', repr(str(real)))
        src = src.replace(f'path="{cp}"', f"path={str(real)!r}")
    src = src.replace("VLM_MMPROJ_PATH = _find_matching_mmproj(VLM_MODEL_PATH)",
                      'VLM_MMPROJ_PATH = "unused-in-eval"')
    ns: dict = {"__name__": f"_rules::{filename}"}
    exec(compile(src, f"{filename}:prefix", "exec"), ns)
    return ns


TX = _load_prefix("2_translate.py", '\nprint("\\n" + "="*60)')
OCR = _load_prefix("1_ocr_and_extract_vlm.py", "\ndef preprocess_for_ocr")

# --- translation-side pure helpers ---
apply_always_translate = TX["apply_always_translate"]
match_case = TX["match_case"]
enforce_kept_terms = TX["enforce_kept_terms"]
enforce_cat3_footnotes = TX["enforce_cat3_footnotes"]
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
label_margin_notes = TX["label_margin_notes"]
_clean_caption = TX["_clean_caption"]
fix_parry_names = TX["fix_parry_names"]

# --- ocr-side pure helpers ---
parse_vlm_response = OCR["parse_vlm_response"]
parse_figures_block = OCR["parse_figures_block"]
reconcile_page_numbers = OCR["reconcile_page_numbers"]
strip_leaked_headmatter = OCR["strip_leaked_headmatter"]
_fold_caption_only_body = OCR["_fold_caption_only_body"]
_norm_head = OCR["_norm_head"]
salvage_leaked_folio_header = OCR["salvage_leaked_folio_header"]
dehyphenate_across_pages = OCR["dehyphenate_across_pages"]
drop_google_boilerplate = OCR["drop_google_boilerplate"]
mark_handwritten_inscriptions = OCR["mark_handwritten_inscriptions"]
drop_scan_apparatus = OCR["drop_scan_apparatus"]
fold_caption_header_into_figure = OCR["fold_caption_header_into_figure"]
reconcile_headers = OCR["reconcile_headers"]
recover_plate_captions = OCR["recover_plate_captions"]
