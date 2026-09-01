"""Frozen regression cases for the pipeline's deterministic helpers.

Every entry here is a bug that was found by manual review and fixed. If a
later prompt/rule/regex change breaks one, that is the cheap early warning
before you spend an evening re-reading a book.

Runs with or without pytest:
    pytest evals/test_rules.py -q      # if pytest is installed
    python3 evals/test_rules.py        # plain, no deps
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _rules import (
    apply_always_translate, match_case, enforce_kept_terms,
    enforce_cat3_footnotes, pull_footnotes,
    repair_mangled_terms, contains_too_much_non_english,
    match_boundary_punctuation, deshout, _phrase_repairs,
    parse_figures_block, reconcile_page_numbers, _fold_caption_only_body,
    strip_leaked_headmatter,
    extract_source_footnotes, label_margin_notes,
    _clean_caption, fix_parry_names,
    salvage_leaked_folio_header, dehyphenate_across_pages,
    drop_google_boilerplate, mark_handwritten_inscriptions,
    drop_scan_apparatus, fold_caption_header_into_figure, reconcile_headers,
    parse_vlm_response,
)


def test_salvage_leaked_folio_header():
    p = [{"page_raw": "", "header": "", "figures": [],
          "body": "246 DES ATTAQUES SUR PRÉPARATIONS.\n\ntaque, une différence."}]
    salvage_leaked_folio_header(p, {"des attaques sur preparations"})
    assert p[0]["page_raw"] == "246"
    assert p[0]["header"] == "DES ATTAQUES SUR PRÉPARATIONS"
    assert p[0]["body"] == "taque, une différence."


def test_salvage_leaves_numbered_drill_alone():
    p = [{"page_raw": "", "header": "", "figures": [],
          "body": "1° Le coup droit de quarte.\nDans cette riposte."}]
    salvage_leaked_folio_header(p, set())
    assert p[0]["page_raw"] == "" and p[0]["body"].startswith("1°")


def test_dehyphenate_across_pages():
    p = [{"body": "les traités déjà pu-"}, {"body": "bliés, on pourrait"}]
    dehyphenate_across_pages(p)
    assert p[0]["body"].endswith("déjà publiés,")
    assert p[1]["body"] == "on pourrait"


def test_dehyphenate_skips_capital_continuation():
    p = [{"body": "composé par Henry de Sainct-"}, {"body": "Didier, gentilhomme"}]
    dehyphenate_across_pages(p)
    assert p[0]["body"].endswith("Sainct-") and p[1]["body"].startswith("Didier")


def test_reconcile_tight_gap_promotes():
    assert reconcile_page_numbers(["14", "", "16"]) == ["14", "15", "16"]


def test_reconcile_plate_run_stays_bracketed():
    # 16 .. (3 unnumbered leaves) .. 17 : the two folio reads are a valid
    # monotonic pair and now show; the unnumbered middle stays bracketed
    # (suppressed by the PDF steps).
    out = reconcile_page_numbers(["16", "", "", "", "17"])
    assert out[0] == "16" and out[-1] == "17"
    assert all(x.startswith("[") for x in out[1:-1])


def test_reconcile_single_plate_leaf_bracketed():
    out = reconcile_page_numbers(["16", "", "17"])
    assert out[1].startswith("[")


def test_extract_source_footnotes():
    state = {}
    txt = ("The riposte can be made immediately (1), or at a lost tempo.\n\n"
           "(1) The origin of the expression is not easy to explain.")
    out = extract_source_footnotes(txt, state)
    assert "___FOOTNOTES___" in out
    assert out.split("___FOOTNOTES___")[1].strip().startswith("(1)")
    assert "(1) The origin" not in out.split("___FOOTNOTES___")[0]


def test_label_margin_notes():
    txt = ("The main body paragraph goes on for a while about the left hand "
           "and its uses in a serious affair.\n\n"
           "A second full paragraph of ordinary prose so the page reads as "
           "prose-dominant and not just a caption with a stray line.\n\n"
           "The lesson must teach in what cases one can use the left hand.")
    out = label_margin_notes(txt)
    assert "[margin note] The lesson must teach" in out
    assert "___FOOTNOTES___" in out


def test_label_margin_notes_skipped_on_figure_page():
    txt = ("A first paragraph.\n\nA second paragraph.\n\nParry of septime.")
    assert label_margin_notes(txt, has_fig=True) == txt
    # even without has_fig, a caption-shaped trailing line is not a note
    assert "[margin note]" not in label_margin_notes(txt)


def test_extract_source_footnotes_bare_number_tail():
    state = {}
    txt = ("Body paragraph one about the guard position and how it is held.\n\n"
           "Body paragraph two, continuing on for a good while longer here.\n\n"
           "1. Redaction of the Academy of Arms of Paris.\n\n"
           "2. The name of the wall was first given to a similar exercise.")
    out = extract_source_footnotes(txt, state)
    block = out.split("___FOOTNOTES___")[1]
    assert "1. Redaction" in block and "2. The name" in block
    assert "1. Redaction" not in out.split("___FOOTNOTES___")[0]


def test_extract_source_footnotes_ignores_numbered_drill():
    state = {}
    txt = ("1. Advance the front foot.\n\n2. Lunge to the target.\n\n"
           "3. Recover to guard.")
    assert "___FOOTNOTES___" not in extract_source_footnotes(txt, state)


def test_extract_source_footnotes_continuation_before_own_notes():
    # page N: a note runs off the bottom (no terminal punctuation)
    state = {}
    extract_source_footnotes(
        "Body (1).\n\n(1) The origin of this expression is not easy to", state)
    assert state["open"] is True
    # page N+1: continuation, then N+1's own (1) and (2)
    out = extract_source_footnotes(
        "explain, since the early masters disagree.\n\n"
        "More body referring to (1) and (2).\n\n"
        "(1) A first footnote of page N+1.\n\n"
        "(2) A second footnote of page N+1.", state)
    block = [l for l in out.split("___FOOTNOTES___")[1].strip().splitlines() if l]
    assert block[0].startswith("… explain, since")     # continuation first
    assert block[1].startswith("(1)") and block[2].startswith("(2)")
    assert state["open"] is False


def test_clean_caption_drops_stamps_and_markdown():
    assert _clean_caption("BRITISH MUSEUM JUN 27 1947") == ""
    assert _clean_caption("ALPH. ROQUES, — 51, High Holborn, — LONDRES.") == ""
    assert _clean_caption(
        "![Photograph of a man](page_10.png) — DEUXIÈME MOUVEMENT.") \
        == "DEUXIÈME MOUVEMENT."


def test_fix_parry_names():
    assert fix_parry_names("Parry of second.", "Parade de seconde.") \
        == "Parry of seconde."
    assert fix_parry_names("Parry of second.", "Some other caption") \
        == "Parry of second."


def test_always_translate():
    for src, want in [
        ("DIVERS", "MISCELLANEOUS"),
        ("Rompre — Divers", "Rompre — Miscellaneous"),
        ("le fer", "the blade"),
        ("attaque au fer", "attack on the blade"),
        ("SOCIÉTÉS", "SOCIETIES"),
        ("Entraînement", "Training"),
        ("leçon d'assaut", "bout lesson"),
        ("la fente", "la lunge"),          # sub does the term, not the article
        ("FENTE", "LUNGE"),
    ]:
        assert apply_always_translate(src) == want, (src, apply_always_translate(src))


def test_always_translate_leaves_prise_de_fer_for_cat3():
    # apply_always_translate must NOT touch "prise de fer" -- it's Cat 3,
    # handled (with the footnote) by enforce_cat3_footnotes.
    assert apply_always_translate("la prise de fer") == "la prise de fer"


def test_phrase_epee_fencing_with_the_foil():
    assert _phrase_repairs("that <i>épée</i> fencing with the foil was lacking") \
        == "that foil fencing was lacking"


def test_phrase_temps():
    for src, want in [
        ("1er TEMPS : feinte", "1er tempo : feinte"),
        ("2e TEMPS : coup droit", "2e tempo : coup droit"),
        ("1ER TEMPS : X", "1ER TEMPO : X"),
        ("in the same tempo", "in the same tempo"),
        ("coup de temps here", "coup de temps here"),
    ]:
        assert _phrase_repairs(src) == want, (src, _phrase_repairs(src))


def test_match_case():
    for src, dst, want in [
        ("ÉPÉE", "épée fencing", "ÉPÉE FENCING"),
        ("SOCIÉTÉS", "societies", "SOCIETIES"),
        ("La Garde", "the guard", "The Guard"),
        ("Coup d'arrêt", "stop hit", "Stop hit"),
        ("Schéma A", "Schéma A", "Schéma A"),
        ("Schéma A", "schéma a", "Schéma A"),
    ]:
        assert match_case(src, dst) == want, (src, dst, match_case(src, dst))


def test_match_case_italic_follows_all_caps():
    # ALL-CAPS source line: the kept <i> term goes up with the rest
    # (tags stay lowercase), so it doesn't sit lowercase mid-caption.
    out = match_case("UN COMMENCEMENT DE CORPS A CORPS.",
                     "beginning of <i>corps à corps</i>.")
    assert out == "BEGINNING OF <i>CORPS À CORPS</i>.", out


def test_match_case_sentence_lowers_shout_keeps_italic():
    # sentence-case source: lower the model's over-capitalisation but leave
    # a kept <i> term's own casing intact
    out = match_case("Parade de seconde.", "PARRY OF <i>seconde</i>")
    assert out == "Parry of <i>seconde</i>", out


def test_enforce_kept_terms():  # Cat-2 only now
    for dst, src, needle in [
        ("a body-to-body was forbidden", "un corps à corps", "<i>corps à corps</i>"),
        ("he made a croisé", "il fit un croisé", "<i>croisé</i>"),
        ("a feeling of the blade", "le sentiment du fer", "<i>sentiment du fer</i>"),
    ]:
        assert needle in enforce_kept_terms(dst, src), (dst, enforce_kept_terms(dst, src))


def test_enforce_cat3_translates_and_footnotes_once():
    seen = set()
    out = enforce_cat3_footnotes(
        "a prise de fer, then a <i>liement</i>, then another liement", seen)
    assert out == ("a taking the blade<fn>French: prise de fer</fn>, then a "
                   "bind<fn>French: liement</fn>, then another bind"), out
    assert seen == {"prise de fer", "liement"}


def test_enforce_cat3_caption_mode_no_footnote():
    assert enforce_cat3_footnotes("PARADE DU DÉGAGEMENT", set(), footnote=False) \
        == "PARADE DU DISENGAGEMENT"


def test_enforce_cat3_leaves_english_alone():
    assert enforce_cat3_footnotes("a bind and a disengagement", set()) \
        == "a bind and a disengagement"


def test_pull_footnotes_dedupes_across_book():
    seen = set()
    body, n = pull_footnotes("a bind<fn>Fr. liement</fn> then a bind<fn>French: liement</fn>",
                             "", seen)
    assert n == 1 and body.count("†") == 2  # one marker in text, one in block
    assert body.endswith("†1 French: liement")


def test_repair_mangled_rassemblant_to_bare_french():
    # rassemblant is Cat 3 now -> mangle repair yields the BARE French word
    out = repair_mangled_terms('IN "RAASSEMBLANT".', "EN « RASSEMBLANT ».")
    assert 'rassemblant' in out and '<i>' not in out, out


def test_repair_mangled_leaves_clean_quote_alone():
    assert repair_mangled_terms('a "QUOTED" english word', "une phrase") \
        == 'a "QUOTED" english word'


def test_french_page_flagged():
    fr = ("Il faut que le tireur se place en garde et que la main soit tenue "
          "de telle sorte que la pointe menace le corps de l'adversaire sans "
          "que le bras soit tendu.")
    assert contains_too_much_non_english(fr)


def test_english_page_not_flagged():
    en = ("The fencer must come on guard with the hand held so that the point "
          "threatens the opponent's body without the arm being straight.")
    assert not contains_too_much_non_english(en)


def test_boundary_strips_added_full_stop():
    # source ends mid-sentence -> strip the model's added terminal
    # punctuation, add nothing; the page just continues on the next.
    assert match_boundary_punctuation("was lacking.", "manquait") == "was lacking"
    assert match_boundary_punctuation("the blade —", "la lame") == "the blade"


def test_boundary_lowercases_continuation():
    assert match_boundary_punctuation("Decidedly practical.", "décidément pratique.") \
        .startswith("decidedly")


def test_boundary_leaves_complete_sentence():
    assert match_boundary_punctuation("A full sentence.", "Une phrase complète.") \
        == "A full sentence."


def test_deshout_body_lower_source_caps():
    src = "L'ESCRIME au fleuret est un exercice."
    dst = "FENCING WITH THE FOIL IS AN EXERCISE THAT DEMANDS PATIENCE AND CARE."
    out = deshout(dst, src)
    assert out[0].isupper() and out[1:5].islower(), out


def test_figures_block_cuts_leaked_body():
    raw = ("HEADER: x\nFIGURES:\nplate | FIG. 3\nno caption\n\n"
           "BODY_TEXT:\nFIG. 4 leaked\n2e TEMPS : x")
    assert parse_figures_block(raw) == [{"kind": "plate", "caption": "FIG. 3"}]


def test_figures_block_recovers_markdown_image():
    raw = "BODY_TEXT:\ntext ![Schéma A.](http://x/y.png)\nSchéma A.\nmore text"
    assert any("Schéma A" in f["caption"] for f in parse_figures_block(raw))


def test_reconcile_trusts_run():
    assert reconcile_page_numbers(["20", "21", "22"]) == ["20", "21", "22"]


def test_reconcile_promotes_matching_read():
    # 24 matches the run's projection -> promoted. The misread "12" sits in
    # a tight EXACT single-slot gap (22 .. 24) -> forced to the real folio.
    assert reconcile_page_numbers(["20", "21", "22", "12", "24"]) \
        == ["20", "21", "22", "23", "24"]
    # ...but if that slot is a PLATE leaf it carries no folio -> bracketed.
    assert reconcile_page_numbers(["20", "21", "22", "12", "24"],
                                  is_plate=[0, 0, 0, 1, 0]) \
        == ["20", "21", "22", "[23]", "24"]


def test_reconcile_monotonic_chain_across_plates():
    # text p.160, four unnumbered leaves, text p.161, then p.162 -- sheet !=
    # folio so the strict check can't fire; the monotonic chain trusts them.
    out = reconcile_page_numbers(
        ["160", "", "", "", "", "161", "", "162"],
        is_plate=[0, 1, 0, 0, 0, 0, 0, 0])
    assert out[0] == "160" and out[5] == "161" and out[7] == "162"


def test_reconcile_forces_single_slot_misread():
    # Gomard: ...301, 302, [903 misread], 304, 305... -> 302 and 304 are
    # trusted off their neighbours, so the exact single-slot gap forces 303.
    assert reconcile_page_numbers(
        ["301", "302", "903", "304", "305"]) == ["301", "302", "303", "304", "305"]


def test_reconcile_brackets_uncorroborated():
    out = reconcile_page_numbers(["20", "21", "none", "99"])
    assert out[2].startswith("[") and out[3].startswith("[")


def test_fold_synthesises_plate():
    parsed = [{"header": "", "page_raw": "", "figures": [],
               "body": "FIG. 3. — ARRÊT EN LIGNE HAUTE, EN « RASSEMBLANT »."}]
    _fold_caption_only_body(parsed)
    assert parsed[0]["figures"] == [
        {"kind": "plate",
         "caption": "FIG. 3. — ARRÊT EN LIGNE HAUTE, EN « RASSEMBLANT »."}]
    assert parsed[0]["body"] == ""


def test_fold_no_doubled_caption():
    parsed = [{"header": "", "page_raw": "",
               "figures": [{"kind": "plate",
                            "caption": "Fig. 5. - Prise de fer sur le bras tendu."}],
               "body": ("FIG. 5. — PRISE DE FER SUR LE BRAS TENDU.\n"
                        "1er TEMPS : feinte de liement de seconde.")}]
    _fold_caption_only_body(parsed)
    cap = parsed[0]["figures"][0]["caption"]
    assert cap.count("PRISE DE FER SUR LE BRAS TENDU") == 1, cap
    assert "1er TEMPS" in cap and parsed[0]["body"] == ""


def test_fold_leaves_text_page_alone():
    parsed = [{"header": "", "page_raw": "",
               "figures": [{"kind": "inset", "caption": "Schéma A"}],
               "body": ("Je préconise comme parades sur les attaques au corps le "
                        "contre de sixte, la seconde, la septime haute, la quinte, "
                        "et même la parade de quarte, selon la ligne pointillée.")}]
    _fold_caption_only_body(parsed)
    assert parsed[0]["body"] != ""


def test_strip_leaked_folio_top():
    assert strip_leaked_headmatter("25\nReal body text here.", {"escrime"}) \
        == "Real body text here."


def test_strip_tail_capped_at_two():
    assert strip_leaked_headmatter("Body.\nESCRIME\nESCRIME\n5", {"escrime"}) \
        == "Body.\nESCRIME"


def test_reconcile_headers_one_off_to_body():
    parsed = [
        {"header": "DES PARADES", "body": "a", "figures": []},
        {"header": "DES PARADES", "body": "b", "figures": []},
        {"header": "QUATRIÈME PARTIE", "body": "c", "figures": []},
    ]
    reconcile_headers(parsed)
    assert parsed[2]["header"] == "" and parsed[2]["body"].startswith("QUATRIÈME PARTIE")
    assert parsed[0]["header"] == "DES PARADES"      # repeated -> kept


def test_reconcile_headers_lifts_confirmed_from_body():
    parsed = [
        {"header": "DES CONTRES", "body": "x", "figures": []},
        {"header": "DES CONTRES", "body": "y", "figures": []},
        {"header": "", "body": "DES CONTRES\n\nLe contre se fait ...", "figures": []},
    ]
    reconcile_headers(parsed)
    assert parsed[2]["header"] == "DES CONTRES"
    assert not parsed[2]["body"].startswith("DES CONTRES")


def test_fold_caption_header_into_figure():
    parsed = [{"header": "PARADE DE TIERCE.", "body": "",
               "figures": [{"kind": "plate", "caption": ""}]}]
    fold_caption_header_into_figure(parsed)
    assert parsed[0]["header"] == ""
    assert parsed[0]["figures"][0]["caption"] == "PARADE DE TIERCE"


def test_fold_caption_header_keeps_real_running_head():
    parsed = [{"header": "DES PARADES", "body": "long body " * 40,
               "figures": [{"kind": "inset", "caption": "Fig. 3"}]}]
    fold_caption_header_into_figure(parsed)
    assert parsed[0]["header"] == "DES PARADES"


def test_drop_scan_apparatus():
    parsed = [{"header": "Insufficient contrast", "body": "", "figures": []},
              {"header": "Bibliothèque Larousse 15720", "body": "", "figures": []}]
    drop_scan_apparatus(parsed)
    assert parsed[0]["header"] == "" and parsed[1]["header"] == ""


def test_parse_two_part_header():
    raw = "PAGE_NUMBER: 53\nHEADER: SABRE     ATTACK AND PARRY\nBODY_TEXT:\ntext"
    assert parse_vlm_response(raw)["header"] == "SABRE\tATTACK AND PARRY"


def test_phrase_repairs_translated_by():
    assert _phrase_repairs("Translated by Prévost and Jollivet") \
        == "By Prévost and Jollivet"
    # a real sentence is untouched
    assert _phrase_repairs("Translated by hand, the manuscript survives.") \
        == "Translated by hand, the manuscript survives."


def test_boundary_strips_trailing_hyphen():
    assert match_boundary_punctuation("the move was per-", "le mouvement fut par-") \
        == "the move was per"


def _run_plain() -> int:
    fns = sorted((n, f) for n, f in globals().items()
                 if n.startswith("test_") and callable(f))
    fails = 0
    for name, fn in fns:
        try:
            fn()
            print(f"  ok   {name}")
        except AssertionError as e:
            fails += 1
            print(f"  FAIL {name}: {e}")
        except Exception as e:  # noqa: BLE001
            fails += 1
            print(f"  ERR  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(fns) - fails}/{len(fns)} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(_run_plain())
