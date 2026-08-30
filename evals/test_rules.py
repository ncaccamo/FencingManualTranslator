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
)


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


def test_match_case_leaves_italic_alone():
    out = match_case("CROISÉ SUR LE BRAS", "<i>croisé</i> on the arm")
    assert "<i>croisé</i>" in out and "ON THE ARM" in out, out


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
    assert out == ("a taking the blade<fn>Fr. prise de fer</fn>, then a "
                   "bind<fn>Fr. liement</fn>, then another bind"), out
    assert seen == {"prise de fer", "liement"}


def test_enforce_cat3_caption_mode_no_footnote():
    assert enforce_cat3_footnotes("PARADE DU DÉGAGEMENT", set(), footnote=False) \
        == "PARADE DU DISENGAGEMENT"


def test_enforce_cat3_leaves_english_alone():
    assert enforce_cat3_footnotes("a bind and a disengagement", set()) \
        == "a bind and a disengagement"


def test_pull_footnotes_dedupes_across_book():
    seen = set()
    body, n = pull_footnotes("a bind<fn>Fr. liement</fn> then a bind<fn>Fr. liement</fn>",
                             "", seen)
    assert n == 1 and body.count("†") == 2  # one marker in text, one in block
    assert body.endswith("†1 Fr. liement")


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
    assert match_boundary_punctuation("was lacking.", "manquait") == "was lacking …"


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
    # 20-21-22 is a trusted run; 12 is a misread plate folio; 24 is read
    # correctly and matches the run's projection -> promoted, not bracketed
    assert reconcile_page_numbers(["20", "21", "22", "12", "24"]) \
        == ["20", "21", "22", "[23]", "24"]


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
