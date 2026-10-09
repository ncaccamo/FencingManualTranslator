# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
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

from _rules import PIPELINE as _PIPELINE
from _rules import match_dash_caps, apply_read_heads, match_case
from _rules import (
    apply_always_translate, match_case, enforce_kept_terms,
    enforce_footnoted_terms, pull_footnotes, fix_subheading_spacing, is_toc_page,
    repair_mangled_terms, contains_too_much_non_english,
    match_boundary_punctuation, deshout, _phrase_repairs,
    parse_figures_block, reconcile_page_numbers, _fold_caption_only_body,
    strip_leaked_headmatter, strip_fieldless_folio, tail_catchwords, dehyphenate_across_pages,
    leaked_top_header, is_plate_leaf,
    extract_source_footnotes,
    _clean_caption, _capitalize_definition_entries, fix_parry_names,
    salvage_leaked_folio_header, dehyphenate_across_pages, join_split_sentences, apply_folio_witness, plain_math,
    drop_google_boilerplate, mark_handwritten_inscriptions,
    drop_scan_apparatus, fold_caption_header_into_figure, reconcile_headers,
    recover_plate_captions, parse_vlm_response, clean_vlm_extraction,
    repair_source_ocr_misreads, restore_dropped_part_title,
    fix_known_garbles, harmonize_titles_with_headers,
    save_partial, load_partial, save_ocr_partial, load_ocr_partial,
)


def test_clean_vlm_extraction_empty_body_not_leaked():
    # (P-review A) a plate leaf with a genuinely empty BODY_TEXT and a
    # multi-word header must come back empty, not the header's own text --
    # the old fallback's PAGE_NUMBER:\s*\S+ ate the "HEADER:" label along
    # with it (\S+ doesn't stop at a colon), stranding "FLEURET LES
    # PARADES" unlabeled and indistinguishable from real body text (it
    # then got machine-translated and rendered in place of the real plate
    # caption already sitting in figures.json).
    page = "PAGE_NUMBER: \nHEADER: FLEURET\tLES PARADES\nBODY_TEXT:"
    assert clean_vlm_extraction(page) == ""


def test_clean_vlm_extraction_normal_body_unaffected():
    page = ("PAGE_NUMBER: 13\nHEADER: none\nBODY_TEXT:\n"
             "PARTICULARITÉS\n\nBattement. — Frapper le fer ennemi.")
    assert clean_vlm_extraction(page) == (
        "PARTICULARITÉS\n\nBattement. — Frapper le fer ennemi.")


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
    # the gap's value is known (15) but nothing was read there: kept for
    # sequencing, not shown (operator rule 2026-09-28: only printed numbers)
    assert reconcile_page_numbers(["14", "", "16"]) == ["14", "[15]", "16"]
    # ...unless the scan's text layer shows it
    assert reconcile_page_numbers(["14", "", "16"], printed=["", "  15 ", ""]) == ["14", "15", "16"]


def test_reconcile_plate_run_stays_bracketed():
    # 16 .. (3 unnumbered leaves) .. 17 : the two folio reads are a valid
    # monotonic pair and now show. The middle run has more leaves (3) than
    # the folio gap spans (1) -> unnumbered leaves -> emit blank, don't
    # seesaw a fabricated folio ([17][18][16]) off the nearest anchor.
    out = reconcile_page_numbers(["16", "", "", "", "17"])
    assert out[0] == "16" and out[-1] == "17"
    assert all(x == "" for x in out[1:-1])


def test_reconcile_single_unread_leaf_blanked():
    # 16 and 17 are consecutive folios; the unread leaf between them is an
    # inserted unnumbered plate/blank -> no folio, not a fabricated "[17]".
    out = reconcile_page_numbers(["16", "", "17"])
    assert out == ["16", "", "17"]


def test_extract_source_footnotes():
    state = {}
    txt = ("The riposte can be made immediately (1), or at a lost tempo.\n\n"
           "(1) The origin of the expression is not easy to explain.")
    out = extract_source_footnotes(txt, state)
    assert "___FOOTNOTES___" in out
    assert out.split("___FOOTNOTES___")[1].strip().startswith("(1)")
    assert "(1) The origin" not in out.split("___FOOTNOTES___")[0]


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


def test_is_toc_page():
    toc = ("TABLE DES MATIÈRES\n"
           "Nomenclature de la lame . . . . . . . . 5\n"
           "Notions de distance . . . . . . . . . . 12\n"
           "Les parades simples . . . . . . . . . . 28\n"
           "Les ripostes . . . . . . . . . . . . . . 41")
    assert is_toc_page(toc)
    prose = ("La riposte se fait immédiatement ou à temps perdu.\n\n"
             "On distingue trois sortes de parades, selon la ligne.\n\n"
             "Le contre est une parade circulaire.")
    assert not is_toc_page(prose)


def test_fix_subheading_spacing():
    # gap after the subheading, none before -> move it before
    src = "End of the previous paragraph.\nLES GRANDS ESCRIMEURS\n\nThe great fencers of the era."
    out = fix_subheading_spacing(src)
    assert out == ("End of the previous paragraph.\n\nLES GRANDS ESCRIMEURS\n"
                   "The great fencers of the era.")
    # a normal sentence is never touched
    keep = "He advanced.\nThe attack was parried.\n\nThen the riposte landed."
    assert fix_subheading_spacing(keep) == keep


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


def test_extract_source_footnotes_deferred_last_paragraph():
    # (P-review, Gomard src 220/221) the continuation isn't always the
    # very next thing OCR reads -- it can be deferred to the FOOT of the
    # next page, after that page's own unrelated new body text. The real
    # continuation opened on a kept italic term ("<i>riposte</i>, but…"),
    # which a bare [:1].islower() check misses ("<" isn't a letter) --
    # this first attempt at the fix passed with plain-text fixtures but
    # silently failed on the real translated data.
    state = {}
    extract_source_footnotes(
        "La riposte se fait du tac au tac (1), ou à temps perdu.\n\n"
        "(1) L'origine de l'expression n'est pas très facile à "
        "expliquer. Par le premier mot tac on a voulu peindre le choc de la",
        state)
    assert state["open"] is True

    out = extract_source_footnotes(
        "La riposte du tac au tac est celle qui suit immédiatement la "
        "parade et doit s'exécuter avec la plus grande rapidité.\n\n"
        "La riposte à temps perdu peut avoir lieu par un coup simple ou "
        "par un coup composé.\n\n"
        "<i>riposte</i>, but by the second word \"tac,\" what has been "
        "intended to depict? It is more probable that it signifies the "
        "button's strike.",
        state)
    assert state["open"] is False
    body, notes = out.split("___FOOTNOTES___")
    assert "second word" not in body             # not left as body text
    assert "… <i>riposte</i>, but by the second word" in notes


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
        # Gomard "LISTE OF WORKS" bug: bare "LISTE" survived untranslated
        # on this isolated header even though it translates fine as part
        # of the two-part header "LISTE DES OUVRAGES\tCONSULTÉS."
        ("LISTE OF WORKS", "LIST OF WORKS"),
        ("Liste des ouvrages", "List des ouvrages"),
    ]:
        assert apply_always_translate(src) == want, (src, apply_always_translate(src))


def test_always_translate_leaves_prise_de_fer_for_footnoted():
    # apply_always_translate must NOT touch "prise de fer" -- it's Cat 3,
    # handled (with the footnote) by enforce_footnoted_terms.
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


def test_match_case_splits_parenthetical_case_register():
    # Real Kirchhoffer example (pdf 47): a caption combining an ALL-CAPS
    # main title with a trailing parenthetical set in ordinary sentence
    # case in the actual book ("LE CROISÉ AU FLANC." / "(La main de
    # l'attaquant reste haute.)"). Measuring case over the whole combined
    # string dilutes the all-caps title's upper_ratio below the 0.8
    # threshold, so the real all-caps title fell through to a broken
    # mixed case instead. Each side must be case-matched independently.
    src = "Fig. 4. — LE CROISÉ AU FLANC. (La main de l'attaquant reste haute.)"
    dst = "fig. 4. the croisé to the side. (the attacker's hand remains high.)"
    out = match_case(src, dst)
    assert "THE CROISÉ TO THE SIDE" in out.upper()
    assert out.split("(")[0].isupper() or out.split("(")[0].strip().isupper()
    assert "(The attacker" in out   # parenthetical: own sentence case, capitalized


def test_match_case_splits_dash_tail_case_register():
    # Real Kirchhoffer example (pdf 58): the same combined-case-register
    # problem as the parenthetical case, but with a trailing "-- <clause>"
    # annotation instead of parens -- "PRISE DE FER SUR LE BRAS TENDU."
    # (all-caps title) then "-- 3e TEMPS : coup droit au corps." (genuine
    # lower case in print, confirmed by the same pattern repeating
    # identically across 3 plates in the same run: Fig. 5/6/7).
    src = "FIG. 7. — PRISE DE FER SUR LE BRAS TENDU. — 3e TEMPS : coup droit au corps."
    dst = "fig. 7. — taking the blade on the extended arm. — 3rd tempo: straight thrust to the body."
    out = match_case(src, dst)
    assert "TAKING THE BLADE ON THE EXTENDED ARM" in out.upper()
    assert "TAKING THE BLADE ON THE EXTENDED ARM." in out   # main title: all-caps
    assert "3rd" in out and "3RD" not in out.upper().replace("3RD TEMPO".upper(), "")  # not "3Rd"/mid-token cap
    assert "straight thrust to the body" in out.lower()   # tail: own lower case


def test_match_case_dash_tail_accepts_spelled_out_ordinal():
    # Real Kirchhoffer bug: Fig. 5 in the SAME plate run as the test
    # above translates "1er" as the spelled-out "FIRST" (still visually
    # part of the caps run) instead of a digit form like "2nd"/"3rd" --
    # the dash-tail split's lowercase-or-digit requirement silently
    # stopped firing on this ONE plate, leaving the whole caption
    # (wrongly) diluted to lower case throughout.
    src = "FIG. 5. — PRISE DE FER SUR LE BRAS TENDU. — 1er TEMPS : feinte de liement de seconde."
    dst = "FIG. 5. — TAKING THE BLADE ON THE EXTENDED ARM. — FIRST TEMPO: feinted bind to seconde."
    out = match_case(src, dst)
    assert "TAKING THE BLADE ON THE EXTENDED ARM." in out   # main title: all-caps
    assert "feinted bind to seconde" in out.lower()          # tail: own lower case
    assert "FEINTED BIND" not in out                         # not dragged into caps


def test_match_case_dash_tail_requires_lowercase_start():
    # A caption that only uses "--" to separate two SAME-case segments
    # must stay whole -- must NOT split just because there's a dash.
    src = "FIG. 4. — LE CROISÉ AU FLANC."
    dst = "fig. 4. the croisé to the side."
    out = match_case(src, dst)
    assert out == out.upper()   # whole thing uppercased together, no split


def test_match_case_sentence_skips_ordinal_mid_token_capital():
    # A sentence-case clause starting with an ordinal ("3rd tempo...")
    # must not capitalise the first LETTER found (mid-token, "r" in
    # "3rd", producing a broken "3Rd") -- only a genuine word-initial
    # letter.
    out = match_case("3e temps.", "3rd tempo.")
    assert "3Rd" not in out
    assert out.startswith("3rd")


def test_restore_dropped_part_title():
    # Real Prévost example: "PREMIÈRE PARTIE" / "DEUXIÈME PARTIE" / etc
    # as the body's own first line gets silently dropped by the
    # translation model on EVERY occurrence in the book (5/5 confirmed:
    # Première, Deuxième, Quatrième, Cinquième, Sixième), while the very
    # next title line on the same page translates fine.
    source = "PREMIÈRE PARTIE\n\nDE LA MANIÈRE\nDE TENIR LE FLEURET OU L'ÉPÉE"
    candidate = "ON THE WAY\nTO HOLD THE FOIL OR THE ÉPÉE"
    out = restore_dropped_part_title(candidate, source)
    assert out.startswith("FIRST PART\n\n") or out.startswith("First Part\n\n")
    assert "ON THE WAY" in out


def test_restore_dropped_part_title_noop_when_already_present():
    # Must not double up when the candidate already has the part title.
    source = "DEUXIÈME PARTIE\n\nLIGNES"
    candidate = "SECOND PART\n\nLINES"
    out = restore_dropped_part_title(candidate, source)
    assert out == candidate


def test_restore_dropped_part_title_noop_on_other_pages():
    # Must not fire when the source's first line isn't a part title at all.
    source = "Le doigté est la faculté de conduire sa pointe."
    candidate = "Finger control is the ability to direct one's point."
    out = restore_dropped_part_title(candidate, source)
    assert out == candidate


def test_enforce_kept_terms():  # Cat-2 only now
    for dst, src, needle in [
        ("a body-to-body was forbidden", "un corps à corps", "<i>corps à corps</i>"),
        ("he made a croisé", "il fit un croisé", "<i>croisé</i>"),
        ("a feeling of the blade", "le sentiment du fer", "<i>sentiment du fer</i>"),
    ]:
        assert needle in enforce_kept_terms(dst, src), (dst, enforce_kept_terms(dst, src))


def test_enforce_kept_terms_catches_rapises_garble():
    # Real Kirchhoffer example: "PARADES ET RIPOSTES" -> "PARRIES AND
    # RAPISES" in the isolated header-translation path (no retry ladder
    # caught it, since the model garbles it the same way every attempt --
    # not a one-off sampling fluke). enforce_kept_terms() already had a
    # known-garble list for "riposte" (comment: "OF THE RAPIER RAPIER")
    # but didn't include this spelling, and always replaced with the
    # singular "riposte" regardless of the garbled match's plurality.
    out = enforce_kept_terms("PARRIES AND RAPISES", "PARADES ET RIPOSTES")
    assert "<i>ripostes</i>" in out.lower() or "ripostes" in out.lower()
    assert "rapise" not in out.lower()


def test_enforce_kept_terms_matches_surrounding_case():
    # Real Prévost bug: "DE LA RIPOSTE" (Part Four's own body-level,
    # all-caps title line) translated to "OF THE riposte" -- the kept
    # term was ALWAYS forced lowercase, even inside an otherwise all-caps
    # title, which was never the intent (that forcing was only meant to
    # fix the garbled-word substitution, not override the term's actual
    # case everywhere). Match the case of whatever prompted the wrap,
    # same convention as apply_always_translate.
    assert enforce_kept_terms("OF THE RIPOSTE") == "OF THE <i>RIPOSTE</i>"
    # Ordinary mid-sentence usage is unaffected -- still lowercase.
    assert enforce_kept_terms("a riposte follows") == "a <i>riposte</i> follows"
    # The garbled-word repair path also now preserves case.
    out = enforce_kept_terms("PARRIES AND RAPISES", "PARADES ET RIPOSTES")
    assert "<i>RIPOSTES</i>" in out


def test_enforce_footnoted_translates_and_footnotes_once():
    seen = set()
    out = enforce_footnoted_terms(
        "a prise de fer, then a <i>liement</i>, then another liement", seen)
    assert out == ("a taking the blade<fn>French: prise de fer</fn>, then a "
                   "bind<fn>French: liement</fn>, then another bind"), out
    assert seen == {"prise de fer", "liement"}


def test_enforce_footnoted_caption_mode_no_footnote():
    assert enforce_footnoted_terms("PARADE DU DÉGAGEMENT", set(), footnote=False) \
        == "PARADE DU DISENGAGEMENT"


def test_enforce_footnoted_leaves_english_alone():
    assert enforce_footnoted_terms("a bind and a disengagement", set()) \
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


def test_figures_block_prefers_unlabeled_caption_repeat_case():
    # olmOCR-2 sometimes skips BODY_TEXT: on a plate-only page and just
    # repeats the caption a second time, in the case actually printed on
    # the plate -- more reliable than its own FIGURES: line. Real example:
    # Kirchhoffer src 27 ("Parade de seconde." in FIGURES:, "PARADE DE
    # SECONDE." repeated after it with no label -- the book prints it caps).
    raw = "HEADER: x\nFIGURES:\nplate | Parade de seconde.\n\nPARADE DE SECONDE."
    assert parse_figures_block(raw) == [
        {"kind": "plate", "caption": "PARADE DE SECONDE."}]


def test_figures_block_unlabeled_repeat_must_match_caption():
    # A trailing all-caps line that is NOT the same caption (a genuine
    # second figure or unrelated leaked text) must not be swallowed as a
    # case-correction -- only an exact normalized match qualifies.
    raw = "HEADER: x\nFIGURES:\nplate | Parade de seconde.\n\nAUTRE TITRE."
    figs = parse_figures_block(raw)
    assert figs[0]["caption"] == "Parade de seconde."


def test_figures_block_keeps_parenthetical_continuation():
    # Real example: Kirchhoffer pdf 47, "FIG. 4. — LE CROISÉ AU FLANC."
    # followed by "(La main de l'attaquant reste haute.)" on its own line
    # -- a parenthetical continuation, not a fresh figure. It starts with
    # "(" rather than a digit/lowercase letter, which the old continuation
    # check didn't recognise, silently dropping the whole second line.
    raw = ("HEADER: x\nFIGURES:\nplate | Fig. 4. — LE CROISÉ AU FLANC.\n"
           "(La main de l'attaquant reste haute.)")
    figs = parse_figures_block(raw)
    assert len(figs) == 1
    assert "reste haute" in figs[0]["caption"]


def test_reconcile_trusts_run():
    assert reconcile_page_numbers(["20", "21", "22"]) == ["20", "21", "22"]


def test_reconcile_promotes_matching_read():
    # 24 matches the run's projection -> promoted. "12" in the 22 .. 24 slot
    # is what the OCR invents on unnumbered pages: value 23, not shown.
    assert reconcile_page_numbers(["20", "21", "22", "12", "24"]) \
        == ["20", "21", "22", "[23]", "24"]
    # ...but if that slot is a PLATE leaf it carries no folio -> blank.
    assert reconcile_page_numbers(["20", "21", "22", "12", "24"],
                                  is_plate=[0, 0, 0, 1, 0]) \
        == ["20", "21", "22", "", "24"]


def test_reconcile_monotonic_chain_across_plates():
    # text p.160, four unnumbered leaves, text p.161, then p.162 -- sheet !=
    # folio so the strict check can't fire; the monotonic chain trusts them.
    out = reconcile_page_numbers(
        ["160", "", "", "", "", "161", "", "162"],
        is_plate=[0, 1, 0, 0, 0, 0, 0, 0])
    assert out[0] == "160" and out[5] == "161" and out[7] == "162"


def test_reconcile_plate_own_read_never_trusted():
    # Real example: Gomard pdf 375 -- a whole-page plate's own printed
    # "Planche XVII." reads as folio 17, which happened to be exactly
    # what the surrounding sequence's arithmetic predicted, so it got
    # trusted as if it were a genuine folio ("Page XVII" rendered). A
    # plate leaf's own number is an engraver's plate number, never a
    # folio, and must not be trusted for ITSELF even when it lines up --
    # it must fall through to the existing "inferred plate leaf -> no
    # folio" path (blank), not render the bogus value.
    out = reconcile_page_numbers(["16", "17", "18"], is_plate=[0, 1, 0])
    assert out == ["16", "", "18"]


def test_reconcile_chain_rejects_bracketed_outlier():
    # Real Kirchhoffer bug (src idx 11-19, pdf 18): p.8, an "8*" plate the
    # VLM didn't flag, two flagged plates around an unnumbered INSET plate
    # it read as "10", then a real p.9. The monotonic chain paired 8 -> 10
    # across one plate leaf and trusted both, rendering "Page 10" on a
    # leaf with no folio. 8 -> 9 around it brackets the 10 out as bogus.
    out = reconcile_page_numbers(["6", "7", "8", "8", "12", "10", "12", "9", "10"],
                                 is_plate=[0, 0, 0, 0, 1, 0, 1, 0, 0])
    assert out == ["6", "7", "8", "", "", "", "", "9", "10"]


def test_reconcile_rejects_mutually_corroborating_misreads():
    # Three real cases where two adjacent pages' reads differ by exactly 1,
    # so each vouched for the other, while the pages outside them agree
    # with each other and contradict the pair:
    #   Gomard scans 314-315: 298/299 read "208/209" (9->0, blurred print)
    #   Prévost scans 262-263: 198/199 in old-style figures read "108/109"
    #   Spinnewyn scans 170-171: a blank + a title page, no folio printed,
    #   the VLM invented "10/11"
    assert reconcile_page_numbers(["295", "296", "297", "208", "209", "300", "301"]) \
        == ["295", "296", "297", "298", "299", "300", "301"]
    assert reconcile_page_numbers(["196", "197", "108", "109", "200", "201"]) \
        == ["196", "197", "198", "199", "200", "201"]
    # (invented numbers: values known, not shown)
    assert reconcile_page_numbers(["138", "139", "10", "11", "142", "143"]) \
        == ["138", "139", "[140]", "[141]", "142", "143"]


def test_reconcile_repeated_folio():
    # Kirchhoffer scans 64-66: p. 38, an unflagged plate the VLM also read
    # "38", a flagged plate, then p. 39. A folio can't repeat -> the plate's
    # copy is dropped. But Jacob scans 201-202 are ONE leaf scanned twice
    # (same text, both p. 155): that repeat is faithful and stays.
    out = reconcile_page_numbers(["36", "37", "38", "38", "", "39", "40"],
                                 is_plate=[0, 0, 0, 0, 1, 0, 0],
                                 bodies=["a", "b", "text of p38", "plate", "", "c", "d"])
    assert out == ["36", "37", "38", "", "", "39", "40"]
    out = reconcile_page_numbers(["153", "154", "155", "155", "156", "157"],
                                 bodies=["a", "b", "terrain et plus d'élan", "terrain et plus d'élan", "c", "d"])
    assert out == ["153", "154", "155", "155", "156", "157"]


def test_reconcile_keeps_genuine_renumbering():
    # A real change of numbering (roman front matter -> arabic 1, a second
    # volume restarting) must survive: its outer pages don't agree across
    # the gap, so nothing is "exposed".
    assert reconcile_page_numbers(["xii", "xiii", "", "1", "2", "3"]) \
        == ["xii", "xiii", "", "1", "2", "3"]
    assert reconcile_page_numbers(["98", "99", "100", "1", "2", "3", "4"]) \
        == ["98", "99", "100", "1", "2", "3", "4"]


def test_is_plate_leaf_detects_unflagged_planche_body():
    # Real Gomard bug: "Planche XIV."/"Planche XV." plates where the VLM
    # never emitted a FIGURES: block at all (no figure kind == "plate" to
    # signal it), so the figures-only is_plate computation missed them --
    # the plate's own roman numeral ("XV") then got trusted as a genuine
    # folio and rendered as "Page XV". Body-content detection catches
    # what figures.json alone can't.
    assert is_plate_leaf("Planche XV.\n\nCoup touché en botte de septime renversée.")
    assert is_plate_leaf("PLANCHE 7.\nSome caption.")


def test_is_plate_leaf_ignores_ordinary_body_caption():
    # Narrower than _CAPTION_SHAPE_RE on purpose -- "Parade de sixte..."
    # legitimately opens an ordinary text page's body too; only a leaf
    # that IS a numbered plate ("Planche N.") should be excluded from
    # folio trust.
    assert not is_plate_leaf("Parade de sixte sur une attaque de quarte.")
    assert not is_plate_leaf("")


def test_reconcile_plate_leaf_unflagged_by_figures_still_excluded():
    # End-to-end: the exact Gomard idx366/368 shape -- two consecutive
    # plate leaves whose own PAGE_NUMBER reads their plate numeral (14,
    # 15) and whose figures list is empty (VLM never flagged them), each
    # separated by a genuinely blank/unnumbered leaf. Without the body-
    # content fallback, is_plate stays False for both and 14/15 get
    # trusted as real folios.
    parsed = [
        {"page_raw": "", "body": "", "figures": []},
        {"page_raw": "", "body": "Planche XIII.\nCaption.",
         "figures": [{"kind": "plate", "caption": "Planche XIII"}]},
        {"page_raw": "", "body": "", "figures": []},
        {"page_raw": "XIV", "body": "Planche XIV.\nCoup touché.", "figures": []},
        {"page_raw": "", "body": "", "figures": []},
        {"page_raw": "XV", "body": "Planche XV.\nCoup touché.", "figures": []},
    ]
    is_plate = [any(f.get("kind") == "plate" for f in (p.get("figures") or []))
                or is_plate_leaf(p.get("body")) for p in parsed]
    assert is_plate == [False, True, False, True, False, True]
    out = reconcile_page_numbers([p["page_raw"] for p in parsed], is_plate)
    # whatever it renders, it must never be the plate's own bogus roman
    # numeral read as if it were a genuine folio ("Page XIV" / "Page XV")
    assert out[3] not in ("XIV", "14") and out[5] not in ("XV", "15")


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


def test_strip_top_keeps_compound_title_block():
    # Real Gomard bug: HEADER natively "DE L'EMPLOI DE LA MAIN GAUCHE.",
    # body opens with the SAME line followed by "COMME MOYEN DE PARADE ET
    # D'OPOSITION." -- together they're ONE two-line printed title ("On
    # the Use of the Left Hand as a Means of Parry and Opposition"), not
    # a stray duplicate. Stripping line 1 as "known headmatter" silently
    # dropped "on the use of the left hand" from the only place the full
    # title was legible (the header bar renders separately, on its own).
    body = "DE L'EMPLOI DE LA MAIN GAUCHE\nCOMME MOYEN DE PARADE ET D'OPOSITION.\n\nL'emploi de la main gauche remonte aux premiers temps."
    out = strip_leaked_headmatter(body, {"de l'emploi de la main gauche"})
    assert out.startswith("DE L'EMPLOI DE LA MAIN GAUCHE\nCOMME MOYEN DE PARADE ET D'OPOSITION.")


def test_strip_keeps_title_vlm_also_reported_as_header():
    # Real Gomard bug (pdf 158 / 164, ~30 more chapter openers): the page
    # prints a small running head AND a large centred title with the same
    # text (scan-confirmed). The VLM reported both; the body copy was
    # stripped as a "leaked header". When the page's OWN HEADER: matches,
    # the body line is the printed title -- keep it.
    body = "DU COUP ET DE LA BOTTE.\n\nLe coup est l'ensemble des mouvements."
    kh = {"du coup et de la botte"}
    assert strip_leaked_headmatter(body, kh, "DU COUP ET DE LA BOTTE.") == body
    # no own header (a real leak into body) -> still stripped
    assert strip_leaked_headmatter(body, kh).startswith("Le coup")


def test_strip_top_still_strips_plain_duplicate():
    # The ordinary case must still work: a known-header duplicate NOT
    # followed by a second title-shaped line is still noise and strips.
    out = strip_leaked_headmatter(
        "INTRODUCTION.\n\nOrdinary prose follows, not a title.", {"introduction"})
    assert out == "Ordinary prose follows, not a title."


def test_strip_tail_capped_at_two():
    assert strip_leaked_headmatter("Body.\nESCRIME\nESCRIME\n5", {"escrime"}) \
        == "Body.\nESCRIME"


def test_strip_tail_catchword_not_a_known_page_header():
    # Real Kirchhoffer example: "ESCRIME." is the BOOK's own overall
    # title (from the title page), never any specific page's running
    # head, so it's never in known_headers and used to slip through --
    # the docstring's own example case, previously not actually caught.
    # It recurs at the foot of each gathering, which is what marks it
    # (tail_catchwords); a one-off short capitals line is content.
    cw = tail_catchwords([{"body": "Texte.\nESCRIME."}, {"body": "Suite.\nESCRIME."},
                          {"body": "Vers.\nPAUL BOURGET"}])
    assert cw == {"escrime"}
    out = strip_leaked_headmatter("Body text continues for a while.\n\nESCRIME.", set(), "", cw)
    assert out == "Body text continues for a while."


def test_strip_tail_keeps_signatures_and_one_off_lines():
    # The 2026-09-18 widening stripped every short capitals tail line: 16
    # real lines over six books. Signatures, title-page lines, a half-title's
    # second line (Gomard scan 9 "DE L'ESCRIME."), "FIN." must survive.
    cw = {"escrime"}
    for tail in ("PAUL BOURGET", "G. JOLLIVET.", "A. SPINNEWYN.", "DE L’ESCRIME.", "FIN.", "PAR"):
        body = "D'une plume qui vaut votre savant fleuret.\n\n" + tail
        assert strip_leaked_headmatter(body, set(), "", cw) == body, tail


def test_strip_tail_catchword_keeps_only_line():
    # Never strip the only line left, even if it looks catchword-shaped --
    # a one-line page could be a legitimate short entry.
    out = strip_leaked_headmatter("ESCRIME.", set())
    assert out == "ESCRIME."


def test_strip_tail_catchword_does_not_eat_real_short_ending():
    # Guard against being too eager: a normal short sentence at the tail
    # (lowercase, more than 2 words) must survive.
    out = strip_leaked_headmatter("Body text continues for a while.\n\nThe end.", set())
    assert out == "Body text continues for a while.\n\nThe end."


def test_leaked_top_header_returns_known_match():
    # Gomard "TABLE DES MATIÈRES." index: a malformed VLM response with no
    # HEADER: field at all puts the running head as body's own first line.
    # Caller must catch this BEFORE strip_leaked_headmatter deletes it --
    # once gone, reconcile_headers()'s later _MAX_GAP-capped gap-fill can
    # miss a page that's one hop past the nearest confirmed anchor (src
    # 334, distance 5 from the nearest confirmed src 329).
    body = "TABLE DES MATIÈRES.\n\nQuelle doit être sa véritable dénomination...... 296"
    assert leaked_top_header(body, {"table des matieres"}) == "TABLE DES MATIÈRES."


def test_leaked_top_header_ignores_bare_number():
    # A leaked folio number must never be promoted into HEADER -- only
    # strip_leaked_headmatter's genuine-known-header branch, never its
    # bare-apparatus-number branch.
    assert leaked_top_header("25\nReal body text.", {"escrime"}) == ""


def test_leaked_top_header_ignores_unconfirmed_line():
    # A first line that isn't in known_headers (not a confirmed running
    # head) must not be promoted -- that's reconcile_headers()'s own,
    # more cautious "looks like a section title" job, not this one's.
    body = "SOME RANDOM CAPS LINE\n\nOrdinary prose follows."
    assert leaked_top_header(body, {"table des matieres"}) == ""


def test_reconcile_headers_one_off_is_trusted():
    # (P-review B) a header seen only once must NOT be demoted -- visual
    # check against the scan (K-review-2 "PARTICULARITÉS" -- src 13) found
    # it sitting in the exact folio+title+rule position of every other
    # running head; it's a real one-page-long section's own running head,
    # not OCR noise. A repeat-count threshold can't distinguish that from
    # noise, so stop demoting on count alone -- trust whatever HEADER:
    # olmOCR-2 reported.
    parsed = [
        {"header": "DES PARADES", "body": "a", "figures": []},
        {"header": "DES PARADES", "body": "b", "figures": []},
        {"header": "PARTICULARITÉS", "body": "c", "figures": []},
    ]
    reconcile_headers(parsed)
    assert parsed[2]["header"] == "PARTICULARITÉS" and parsed[2]["body"] == "c"
    assert parsed[0]["header"] == "DES PARADES"      # repeated -> kept


def test_reconcile_headers_no_fill_on_compound_title_opener():
    # Real Prévost bug (pdf 132): a part-opener leaf ("QUATRIÈME PARTIE" /
    # "DE LA RIPOSTE") prints no running head -- scan-confirmed. The lift
    # step already recognised the two-line title block and left it in
    # body, but the gap-fill then copied the neighbours' running head onto
    # it. Same on Gomard pdf 339 ("TABLEAU EXPLICATIF" / "DES PLANCHES.").
    H = "THÉORIE PRATIQUE DE L'ESCRIME."
    parsed = [
        {"header": H, "body": "a", "figures": []},
        {"header": H, "body": "b", "figures": []},
        {"header": "", "body": "QUATRIÈME PARTIE\n\nDE LA RIPOSTE\n\nOn nomme riposte le coup.",
         "figures": []},
        {"header": H, "body": "c", "figures": []},
    ]
    reconcile_headers(parsed)
    assert parsed[2]["header"] == ""
    assert parsed[2]["body"].startswith("QUATRIÈME PARTIE")


def test_reconcile_headers_no_fill_on_demoted_title_leaf():
    # Real Prévost bug (pdf 14): the half-title "L'Escrime et le Duel" is
    # correctly demoted from HEADER into body, then the gap-fill gave the
    # leaf a neighbouring half-title's "BIBLIOTHEQUE DU SPORT" header.
    B = "BIBLIOTHEQUE DU SPORT"
    parsed = [
        {"header": B, "body": "x", "figures": []},
        {"header": B, "body": "y", "figures": []},
        {"header": "L'Escrime et le Duel", "body": "", "figures": []},
    ]
    reconcile_headers(parsed)
    assert parsed[2]["header"] == "" and parsed[2]["body"] == "L'Escrime et le Duel"


def test_reconcile_headers_lifts_confirmed_from_body():
    parsed = [
        {"header": "DES CONTRES", "body": "x", "figures": []},
        {"header": "DES CONTRES", "body": "y", "figures": []},
        {"header": "", "body": "DES CONTRES\n\nLe contre se fait ...", "figures": []},
    ]
    reconcile_headers(parsed)
    assert parsed[2]["header"] == "DES CONTRES"
    assert not parsed[2]["body"].startswith("DES CONTRES")


def test_reconcile_headers_promotes_unconfirmed_section_opener():
    # (P-review-3) TERMES TECHNIQUES / PARADES ET RIPOSTES / RÈGLEMENTS
    # style: the model reads HEADER: none on EVERY page of the section
    # (never confirms it as a header anywhere), but the body still opens
    # with it as a standalone, short, all-caps line every time. Promote
    # anyway -- confirmed against the actual scan for two of these.
    parsed = [
        {"header": "FLEURET", "body": "x", "figures": []},
        {"header": "FLEURET", "body": "y", "figures": []},
        {"header": "",
         "body": "TERMES TECHNIQUES\n\nCoup droit. — Attaque simple.",
         "figures": []},
    ]
    reconcile_headers(parsed)
    assert parsed[2]["header"] == "TERMES TECHNIQUES"


def test_reconcile_headers_demotes_known_compound_title_block():
    # Real Gomard example, one of the few instances in the narrow, named
    # exception list (_KNOWN_COMPOUND_TITLE_BLOCKS) -- NOT a general
    # shape rule, after that general version wrongly demoted a genuine
    # book-wide running head on >=20 Prévost pages (see the test below).
    # The model reads "HEADER: LISTE DES OUVRAGES" NATIVELY on the
    # bibliography section's OPENING page, whose body opens with a
    # second title-shaped line, "CONSULTÉS PAR L'AUTEUR." Visual check
    # against the scan: this is one big two-line title block printed
    # within the body area, not a running head -- the section's genuine
    # small running head (just "LISTE DES OUVRAGES", confirmed by
    # repetition) only starts on the SECOND page, where the body opens
    # with ordinary prose instead of a second title line.
    parsed = [
        {"header": "LISTE DES OUVRAGES",
         "body": "CONSULTÉS PAR L'AUTEUR.\n\nDi Antonio Manciolino, Bolognese.",
         "figures": []},
        {"header": "LISTE DES OUVRAGES",
         "body": "Didier, gentilhomme provençal, dédié à la majesté du Roy.",
         "figures": []},
    ]
    reconcile_headers(parsed)
    assert parsed[0]["header"] == ""


def test_reconcile_headers_does_not_demote_unlisted_running_head():
    # Real Prévost regression: a BOOK-WIDE running head ("THÉORIE
    # PRATIQUE DE L'ESCRIME.", confirmed 69x across the whole book) is
    # structurally IDENTICAL by text shape to the genuine Gomard bug
    # above -- native header, followed by a different title-shaped body
    # line -- every time a new chapter opens under it. The general
    # version of the demotion (shape alone, no exception list) wrongly
    # stripped this real running head on >20 pages. Only a name on the
    # narrow list may be demoted; everything else, no matter how well it
    # matches the shape, must be left alone.
    parsed = [
        {"header": "THÉORIE PRATIQUE DE L'ESCRIME.",
         "body": "DE LA MANIÈRE DE SE REPLACER\nDANS LA PREMIÈRE POSITION\n\n"
                  "Jadis on appelait ces mouvements autrement.",
         "figures": []},
    ]
    reconcile_headers(parsed)
    assert parsed[0]["header"] == "THÉORIE PRATIQUE DE L'ESCRIME."


def test_reconcile_headers_keeps_header_when_body_echoes_it():
    # Real Prévost src 21 example -- must NOT be caught by the demotion
    # above. The header carries the title and the body repeats it
    # verbatim before its own subtitle ("DE LA MANIÈRE DE SE RELEVER." /
    # body: "DE LA MANIÈRE DE SE RELEVER" then "OU DE SE REPLACER EN
    # GARDE"). Already confirmed as a deliberately-fine book convention
    # ("header and a body title both is fine if that's what the book
    # does") -- the Gomard demotion is specifically for a header plus a
    # DIFFERENT second title line, not an echo of the header itself.
    parsed = [
        {"header": "DE LA MANIÈRE DE SE RELEVER.",
         "body": "DE LA MANIÈRE DE SE RELEVER\n\nOU DE SE REPLACER EN GARDE\n\n"
                  "Replier le bras droit, relever la main gauche.",
         "figures": []},
    ]
    reconcile_headers(parsed)
    assert parsed[0]["header"] == "DE LA MANIÈRE DE SE RELEVER."


def test_reconcile_headers_demotes_part_title_from_header():
    # Real Prévost example: "TROISIÈME PARTIE" is the ONE part-division
    # title (out of six in the book) the model natively classified into
    # HEADER: instead of BODY_TEXT: like every other occurrence
    # (PREMIÈRE/DEUXIÈME/QUATRIÈME/CINQUIÈME/SIXIÈME PARTIE, all five
    # natively in body). A part title is body text in this book,
    # regardless of what follows it -- unconditional demotion, unlike
    # the compound-title-block rule above (which requires a second
    # title-shaped body line).
    parsed = [
        {"header": "TROISIÈME PARTIE",
         "body": "DES DIFFÉRENTES MANIÈRES\nDE TROMPER LES PARADES SIMPLES\n\n"
                  "Étant engagé en quarte.",
         "figures": []},
    ]
    reconcile_headers(parsed)
    assert parsed[0]["header"] == ""
    assert parsed[0]["body"].startswith("TROISIÈME PARTIE\n\nDES DIFFÉRENTES")


def test_reconcile_headers_strips_leaked_folio_glued_to_header():
    # Real Gomard bug: a malformed-VLM-response page (no scaffold at all)
    # whose body opens with "26: INTRODUCTION." -- the leaked printed
    # folio glued straight onto the header text with a colon, no space.
    # The whole line is title-shaped (letters-only uppercase ratio
    # ignores the digits/colon) so it gets lifted as one unit, folio
    # prefix and all. Only strips when what's left is itself confirmed.
    parsed = (
        [{"header": "INTRODUCTION.", "body": "p" + str(i), "figures": []}
         for i in range(3)]
        + [{"header": "26: INTRODUCTION.", "body": "prose.", "figures": []}]
    )
    reconcile_headers(parsed)
    assert parsed[3]["header"] == "INTRODUCTION."


def test_reconcile_headers_leaked_folio_strip_requires_confirmed_remainder():
    # Must not fire on an ordinary heading that happens to start with a
    # number for some unrelated reason -- only when what's left IS a
    # confirmed header.
    parsed = [{"header": "3 MUSKETEERS RETURN", "body": "prose.", "figures": []}]
    reconcile_headers(parsed)
    assert parsed[0]["header"] == "3 MUSKETEERS RETURN"


def test_reconcile_headers_does_not_promote_glossary_headword():
    # A mixed-case "Headword. — definition" opening line -- even if it's
    # the first thing on the page -- is NOT a section title, so the
    # promotion path must never turn the glossary text ITSELF into a
    # header value (would misfile every glossary entry that happens to
    # start a page as a running head). It may still legitimately inherit
    # the surrounding confirmed "FLEURET" via the separate nearest-
    # confirmed-header gap fill -- that's a different, correct mechanism
    # (real book pages don't go headerless just because a glossary entry
    # happens to open them).
    parsed = [
        {"header": "FLEURET", "body": "x", "figures": []},
        {"header": "FLEURET", "body": "y", "figures": []},
        {"header": "",
         "body": "Croisé. — Parade qui consiste à s'emparer du fer.",
         "figures": []},
    ]
    reconcile_headers(parsed)
    assert parsed[2]["header"] == "FLEURET"


def test_reconcile_headers_reorders_swapped_two_part():
    # Kirchhoffer "FLEURET / LA MISE EN GARDE" plate run: some leaves read
    # the two-part head order-swapped and without the tab
    # ("LA MISE EN GARDE FLEURET" instead of "FLEURET\tLA MISE EN
    # GARDE"). FLEURET is confirmed (repeats dozens of times) elsewhere
    # in the book. 2026-10-07 review, scans 16/17: the book PRINTS the
    # halves on alternate sides (left-hand pages "LA MISE EN GARDE ...
    # FLEURET"), so the head is split at the root but keeps its order.
    parsed = (
        [{"header": "FLEURET", "body": "p" + str(i), "figures": []} for i in range(4)]
        + [{"header": "FLEURET\tLA MISE EN GARDE", "body": "", "figures": []},
           {"header": "LA MISE EN GARDE FLEURET", "body": "", "figures": []}]
    )
    reconcile_headers(parsed)
    assert parsed[4]["header"] == "FLEURET\tLA MISE EN GARDE"
    assert parsed[5]["header"] == "LA MISE EN GARDE\tFLEURET"


def test_reconcile_headers_reorders_even_when_swap_repeats_identically():
    # The actual Kirchhoffer bug: "LA MISE EN GARDE FLEURET" is read the
    # SAME (wrong) way on >=2 leaves, so it satisfies its OWN count>=2
    # confirmation threshold as an accidental standalone root -- which
    # used to skip the reorder check entirely (it looked "confirmed").
    parsed = (
        [{"header": "FLEURET", "body": "p" + str(i), "figures": []} for i in range(6)]
        + [{"header": "LA MISE EN GARDE FLEURET", "body": "", "figures": []},
           {"header": "LA MISE EN GARDE FLEURET", "body": "", "figures": []}]
    )
    reconcile_headers(parsed)
    # split at the root, in the printed order (see the test above)
    assert parsed[6]["header"] == "LA MISE EN GARDE\tFLEURET"
    assert parsed[7]["header"] == "LA MISE EN GARDE\tFLEURET"


def test_reconcile_headers_swap_reorder_skips_bare_title_leaf():
    # Real Prévost bug: a half-title page's own body-less HEADER is the
    # book's OWN title, "L'Escrime et le Duel" -- coincidentally ending
    # in "le Duel", which the book's UNRELATED later duel chapter uses as
    # its own confirmed running head ("LE DUEL."). The old reorder check
    # had no way to tell that apart from a genuine swapped running head
    # (Kirchhoffer FLEURET, above) and produced a nonsense reversed split
    # ("LE DUEL." / "L'Escrime et"). The leftover chunk here isn't
    # header-shaped (lowercase "et", apostrophe-elided "L'Escrime") --
    # reject the reorder, and since there's no body under it either and
    # the whole phrase isn't itself confirmed, it's a title-only leaf:
    # route it to body untouched instead of leaving it as a header. The
    # confirmed "LE DUEL." run sits far away (a later chapter, well
    # outside _MAX_GAP) -- as it does in the real book -- so the later
    # gap-fill pass can't re-inherit it back onto the title leaf.
    parsed = (
        [{"header": "L'Escrime et le Duel", "body": "", "figures": []}]
        + [{"header": "", "body": "front matter " + str(i), "figures": []}
           for i in range(10)]
        + [{"header": "LE DUEL.", "body": "p" + str(i), "figures": []} for i in range(3)]
    )
    reconcile_headers(parsed)
    assert parsed[0]["header"] == ""
    assert parsed[0]["body"] == "L'Escrime et le Duel"


def test_reconcile_headers_clears_gap_filled_header_on_split_title_page():
    # Real Prévost bug: the book's own confirmed running head appears
    # split across the top TWO lines of body on its opening page ("page
    # [1]" -- native HEADER: none), followed by the chapter's own title
    # on a third line. Gap-fill (from the confirmed neighbour just after
    # it) fills the header anyway, producing a small running-head bar
    # duplicating the book's own grand opening title. Only the two split
    # lines TOGETHER reconstruct the confirmed root -- clear the header.
    # Body uses a curly apostrophe, the confirmed header a straight one
    # (both real, page-to-page OCR variance) -- must still match.
    parsed = (
        [{"header": "", "body": "THÉORIE PRATIQUE\nDE L’ESCRIME\n\nL'HISTOIRE DE L'ESCRIME\n\nprose.",
          "figures": []}]
        + [{"header": "THÉORIE PRATIQUE DE L'ESCRIME.", "body": "p" + str(i), "figures": []}
           for i in range(3)]
    )
    reconcile_headers(parsed)
    assert parsed[0]["header"] == ""
    assert parsed[0]["body"].startswith("THÉORIE PRATIQUE\nDE L’ESCRIME")


def test_reconcile_headers_split_title_guard_ignores_ordinary_chapter_opener():
    # Must NOT fire on the extremely common ordinary case: a real running
    # head plus a genuinely different, unrelated two-line chapter title
    # -- only an EXACT reconstruction of the confirmed root triggers it.
    parsed = (
        [{"header": "THÉORIE PRATIQUE DE L'ESCRIME.",
          "body": "DE LA RIPOSTE\nET DE LA CONTRE-RIPOSTE\n\nprose.", "figures": []}]
        + [{"header": "THÉORIE PRATIQUE DE L'ESCRIME.", "body": "p" + str(i), "figures": []}
           for i in range(3)]
    )
    reconcile_headers(parsed)
    assert parsed[0]["header"] == "THÉORIE PRATIQUE DE L'ESCRIME."


def test_reconcile_headers_sandwich_interpolation():
    # A single page with no header at all, and nothing in its body to
    # lift, sandwiched between two pages that both already carry the
    # same confirmed header -> inherits it (Kirchhoffer ÉPÉE src 27/30,
    # Gomard PRÉFACE. src 13 -- an isolated single-page OCR miss inside
    # an otherwise heavily-confirmed run).
    parsed = [
        {"header": "ÉPÉE", "body": "a", "figures": []},
        {"header": "ÉPÉE", "body": "b", "figures": []},
        {"header": "", "body": "continues mid-sentence, no title here", "figures": []},
        {"header": "ÉPÉE", "body": "d", "figures": []},
    ]
    reconcile_headers(parsed)
    assert parsed[2]["header"] == "ÉPÉE"


def test_reconcile_headers_sandwich_interpolation_needs_agreement():
    # Neighbours disagree (one of them is itself a one-off subsection
    # opener, not the parent section's running head) -> the strict
    # sandwich rule (both neighbours must already agree) correctly
    # declines. This is the real Kirchhoffer src 30 shape: sandwiched
    # between "PARADES ET RIPOSTES" and "CONTRE LE BRAS TENDU", each a
    # one-page interlude read natively on exactly one page, neither
    # independently confirmed. Visual check against the scan showed src
    # 30's real header reverts to the parent "ÉPÉE" -- caught by a
    # separate, later mechanism (nearest-CONFIRMED-header search, not
    # this strict immediate-neighbour rule), which is why the page ends
    # up with a header at all despite this rule declining.
    parsed = [
        {"header": "ÉPÉE", "body": "a0", "figures": []},
        {"header": "ÉPÉE", "body": "a1", "figures": []},   # ÉPÉE confirmed
        {"header": "PARADES ET RIPOSTES", "body": "b", "figures": []},
        {"header": "", "body": "continues mid-sentence, no title here", "figures": []},
        {"header": "CONTRE LE BRAS TENDU", "body": "d", "figures": []},
    ]
    reconcile_headers(parsed)
    assert parsed[3]["header"] == "ÉPÉE"   # nearest-confirmed fallback, not the strict sandwich rule


def test_reconcile_headers_gap_fill_prefers_nearer_confirmed():
    # When the nearest-confirmed search could reach a confirmed header in
    # either direction, it takes whichever is CLOSER -- here the backward
    # confirmed root ("FLEURET") is 2 pages off (behind a one-off), while
    # the forward confirmed root ("ÉPÉE") is only 1 page off.
    parsed = [
        {"header": "FLEURET", "body": "a0", "figures": []},
        {"header": "FLEURET", "body": "a1", "figures": []},   # FLEURET confirmed, 2 away
        {"header": "ONE-OFF INTERLUDE", "body": "b", "figures": []},
        {"header": "", "body": "gap", "figures": []},
        {"header": "ÉPÉE", "body": "c0", "figures": []},
        {"header": "ÉPÉE", "body": "c1", "figures": []},   # ÉPÉE confirmed, 1 away
    ]
    reconcile_headers(parsed)
    assert parsed[3]["header"] == "ÉPÉE"


def test_reconcile_headers_gap_fill_does_not_cascade():
    # Real Gomard bug: a genuine confirmed header ("TABLE DES MATIÈRES.")
    # near a long run of otherwise-blank plate leaves propagated across
    # the ENTIRE ~54-page run, far past the intended gap cap, because the
    # search used to look at LIVE (already gap-filled) neighbours -- once
    # page N got wrongly filled, page N+4 could reach page N as if it
    # were a genuine anchor, chaining indefinitely. The fix searches from
    # a fixed snapshot of the ORIGINAL confirmed headers only, so a fill
    # never feeds another fill. Build a run of 10 blank pages after one
    # confirmed header -- only the first _MAX_GAP (4) should fill.
    parsed = (
        [{"header": "TABLE DES MATIÈRES.", "body": "a0", "figures": []},
         {"header": "TABLE DES MATIÈRES.", "body": "a1", "figures": []}]  # confirmed
        + [{"header": "", "body": f"blank{i}", "figures": []} for i in range(10)]
    )
    reconcile_headers(parsed)
    filled = [i for i, p in enumerate(parsed) if (p.get("header") or "").strip()]
    # indices 0,1 are the originals; only within _MAX_GAP (4) of index 1
    # should fill (indices 2-5), never marching further down the run
    assert filled == [0, 1, 2, 3, 4, 5], filled
    for i in range(6, 12):
        assert parsed[i]["header"] == "", (i, parsed[i]["header"])


def test_reconcile_headers_skips_genuinely_blank_leaf():
    # Real Prévost example: several leaves before "PREMIÈRE PARTIE"
    # begins are genuinely blank in the scan -- no header, no body text,
    # no figure, just faint digitisation specks (confirmed by rendering
    # the actual page). Unlike a plate page (no body text, but IS a real
    # printed page that typically still carries the running head), a
    # truly blank leaf has nothing printed on it at all -- neither
    # sandwich interpolation nor the nearest-confirmed gap fill should
    # ever assign it a header.
    parsed = [
        {"header": "ÉPÉE", "body": "a0", "figures": []},
        {"header": "ÉPÉE", "body": "a1", "figures": []},   # confirmed
        {"header": "", "body": "", "figures": []},          # genuinely blank leaf
        {"header": "ÉPÉE", "body": "c0", "figures": []},
        {"header": "ÉPÉE", "body": "c1", "figures": []},
    ]
    reconcile_headers(parsed)
    assert parsed[2]["header"] == ""


def test_reconcile_headers_skips_blank_leaf_with_literal_none_body():
    # The real Prévost bug: body isn't an empty string at this stage, it's
    # the literal raw VLM sentinel "none" (from "BODY_TEXT: none"),
    # un-cleaned until later in the pipeline -- a blank check looking
    # only for an empty string missed it entirely, and the page still
    # got a header assigned.
    parsed = [
        {"header": "ÉPÉE", "body": "a0", "figures": []},
        {"header": "ÉPÉE", "body": "a1", "figures": []},   # confirmed
        {"header": "", "body": "none", "figures": []},      # genuinely blank leaf
        {"header": "ÉPÉE", "body": "c0", "figures": []},
        {"header": "ÉPÉE", "body": "c1", "figures": []},
    ]
    reconcile_headers(parsed)
    assert parsed[2]["header"] == ""


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


def test_fold_caption_header_into_body_when_no_figure_flagged():
    # Real Gomard example: the VLM missed flagging the illustration
    # entirely (no FIGURES: block at all), so the existing fold-into-
    # caption path (which requires a figure already present) never ran,
    # leaving "Planche V." stuck as a running head. A plate-number header
    # reads as a caption either way -- review feedback: put it in body,
    # not the header bar, regardless of whether a figure got flagged.
    parsed = [{"header": "Planche V.",
               "body": "Coup tiré en botte de prime paré par la prime.",
               "figures": []}]
    fold_caption_header_into_figure(parsed)
    assert parsed[0]["header"] == ""
    assert parsed[0]["body"].startswith("Planche V.\n\nCoup tiré")


def test_drop_scan_apparatus():
    parsed = [{"header": "Insufficient contrast", "body": "", "figures": []},
              {"header": "Bibliothèque Larousse 15720", "body": "", "figures": []}]
    drop_scan_apparatus(parsed)
    assert parsed[0]["header"] == "" and parsed[1]["header"] == ""


def test_parse_two_part_header():
    raw = "PAGE_NUMBER: 53\nHEADER: SABRE     ATTACK AND PARRY\nBODY_TEXT:\ntext"
    assert parse_vlm_response(raw)["header"] == "SABRE\tATTACK AND PARRY"


def test_reconcile_headers_two_part_root_survives():
    # SABRE is a running head on many pages; each per-page right half is
    # unique. Keying the count on the whole "SABRE\tX" string demoted every
    # plate-page header (batch-2 regression). Key on the LEFT root instead.
    parsed = (
        [{"header": "SABRE", "body": "p" + str(i), "figures": []} for i in range(4)]
        + [{"header": "SABRE\tCORPS A CORPS", "body": "", "figures": []},
           {"header": "SABRE\tCOUP D'ARRÊT", "body": "", "figures": []},
           {"header": "DES CONTRES", "body": "one-off", "figures": []}]
    )
    reconcile_headers(parsed)
    assert parsed[4]["header"] == "SABRE\tCORPS A CORPS"   # kept (root repeats)
    assert parsed[5]["header"] == "SABRE\tCOUP D'ARRÊT"    # kept
    assert parsed[6]["header"] == "DES CONTRES" and parsed[6]["body"] == "one-off"


def test_reconcile_page_numbers_blanks_seesaw_run():
    # 16 .. (5 unnumbered leaves) .. 17 : more leaves than the folio gap
    # -> blank them, don't fabricate [17][18][19][20][13].
    out = reconcile_page_numbers(["16", "", "", "", "", "", "17"])
    assert out[0] == "16" and out[-1] == "17"
    assert out[1:-1] == ["", "", "", "", ""]


def test_capitalize_definition_entries():
    assert _capitalize_definition_entries(
        "<i>riposte</i>. — The riposte is the blow after the parry.") \
        == "<i>Riposte</i>. — The riposte is the blow after the parry."
    assert _capitalize_definition_entries(
        "coup d'arrêt. — A stop hit.") == "Coup d'arrêt. — A stop hit."
    # a long lead-in before ". —" is a sentence, not a headword: leave it
    src = "he parried and then, after a pause. — well, it happened."
    assert _capitalize_definition_entries(src) == src


def test_clean_caption_strips_model_note():
    got = _clean_caption(
        "STOP-THRUST TO THE CHEST. (NOTE: THERE APPEARS TO BE AN ERROR IN "
        "THE ORIGINAL TEXT, AS THE PHRASE IS AMBIGUOUS.) GIVEN THE CRITICAL "
        "RULES, THE TRANSLATION SHOULD ADHERE TO THE TEXT, THUS: STOP-THRUST.")
    assert got == "STOP-THRUST TO THE CHEST."


def test_recover_plate_caption_from_fig_line():
    parsed = [{"page_raw": "24", "header": "", "figures": [],
               "body": "Fig. 1. — LA GARDE D'ÉPÉE. — Professor Bouché."}]
    recover_plate_captions(parsed)
    assert parsed[0]["figures"] and parsed[0]["figures"][0]["kind"] == "plate"
    assert "LA GARDE" in parsed[0]["figures"][0]["caption"]
    assert parsed[0]["body"] == ""


def test_recover_plate_caption_skips_duplicate_of_neighbour():
    parsed = [
        {"page_raw": "42", "header": "", "body": "",
         "figures": [{"kind": "plate", "caption": "Fig. 1. — LA GARDE D'ÉPÉE. — Professor Bouché."}]},
        {"page_raw": "24", "header": "", "figures": [],
         "body": "Fig. 1. — LA GARDE D'ÉPÉE. — Professor Bouché."},
    ]
    recover_plate_captions(parsed)
    assert parsed[1]["figures"] == []          # neighbour already has it
    assert parsed[1]["body"] != ""


def test_recover_plate_caption_borrows_from_short_neighbour():
    parsed = [
        {"page_raw": "", "header": "", "figures": [], "body": "Parade de quarte."},
        {"page_raw": "", "header": "PARADE DE SECONDE.", "body": "",
         "figures": [{"kind": "plate", "caption": ""}]},
    ]
    recover_plate_captions(parsed)
    assert parsed[1]["figures"][0]["caption"] == "Parade de quarte."


def test_recover_plate_caption_guard_skips_long_prose_neighbour():
    # (P-review, Prévost pdf 56) a neighbour whose body is a full
    # paragraph of prose that merely OPENS with a caption-shaped
    # sub-heading must not donate that line as if it were a plate's own
    # caption -- it produced "PARADE DE SECONDE — Parade de quarte"
    # instead of just "Parade de seconde", merging an unrelated parry's
    # inline sub-heading into this plate's caption. Only a genuinely
    # short, caption-only neighbour page (previous test) may donate.
    long_body = (
        "Parade de quarte. Placer la main à gauche, le pouce en dessus, "
        "le coude près du corps, la pointe à gauche, à la hauteur des "
        "yeux et légèrement en dehors de la ligne, en appuyant sur le "
        "fer de l'adversaire, sans quoi la parade serait incomplète et "
        "manquerait totalement de sécurité pour le tireur qui l'exécute, "
        "ce qui serait fort regrettable pour un débutant en escrime.")
    assert len(long_body.split()) > 40
    parsed = [
        {"page_raw": "", "header": "", "figures": [], "body": long_body},
        {"page_raw": "", "header": "PARADE DE SECONDE.", "body": "",
         "figures": [{"kind": "plate", "caption": ""}]},
    ]
    recover_plate_captions(parsed)
    assert parsed[1]["figures"][0]["caption"] == ""


def test_repair_source_ocr_misreads_consultes_pas_par():
    # 'PAR' (by) misread as 'PAS' (not) -- Gomard's bibliography running
    # head, which the translator then faithfully rendered as a false
    # negation ("NOT consulted by the author"). Case-preserving: the
    # running head is ALL CAPS, only the one word swaps.
    parsed = [{"header": "CONSULTÉS PAS L'AUTEUR.", "body": "royale de Paris."}]
    repair_source_ocr_misreads(parsed)
    assert parsed[0]["header"] == "CONSULTÉS PAR L'AUTEUR."
    # a real sentence using "pas" as a negation elsewhere is untouched
    parsed2 = [{"header": "", "body": "Il ne pare pas l'attaque."}]
    repair_source_ocr_misreads(parsed2)
    assert parsed2[0]["body"] == "Il ne pare pas l'attaque."


def test_repair_source_ocr_misreads_plonche_planche():
    # Real Gomard example: "Plonche XIX." (A/O misread) translates as
    # "Plunge XIX." since it's not a real French word -- the translator
    # treats it as one anyway. Every other correctly-spelled "Planche N."
    # header in the same book translates fine; this is a one-leaf miss.
    parsed = [{"header": "Plonche XIX.", "body": "x"}]
    repair_source_ocr_misreads(parsed)
    assert parsed[0]["header"] == "Planche XIX."


def test_phrase_repairs_translated_by():
    assert _phrase_repairs("Translated by Prévost and Jollivet") \
        == "By Prévost and Jollivet"
    # a real sentence is untouched
    assert _phrase_repairs("Translated by hand, the manuscript survives.") \
        == "Translated by hand, the manuscript survives."


def test_fix_known_garbles_thust_slash_plank():
    # Real Gomard bugs (batch-2 pass 8): repeatable model misspelling
    # "THUST" (headers and body), the glossary's "thrust/hit" copied
    # verbatim, and planche -> "plank" (carpentry) instead of plate.
    assert fix_known_garbles("CHOICE OF THE THUST") == "CHOICE OF THE THRUST"
    assert fix_known_garbles("simple thusts drawn") == "simple thrusts drawn"
    assert fix_known_garbles("FOR SIMPLE THROUGHS DRAWN") == "FOR SIMPLE THRUSTS DRAWN"
    assert fix_known_garbles("OF THE BLOW AND THE THRUST/HIT.") \
        == "OF THE BLOW AND THE THRUST."
    assert fix_known_garbles("PLANKS.\n\nPLANK I.", "DES PLANCHES.\n\nPLANCHE Ier.") \
        == "PLATES.\n\nPLATE I."
    # no "planche" in the source -> a real plank is left alone
    assert fix_known_garbles("a plank of oak", "une planche") != "a plank of oak"
    assert fix_known_garbles("a plank of oak", "une poutre") == "a plank of oak"
    # "thrust" itself untouched
    assert fix_known_garbles("the straight thrust") == "the straight thrust"


def test_harmonize_body_title_with_header():
    # Real Prévost bug (src 21 + "OF/ON THE COUNTER-RIPOSTE"): header and
    # body title translated independently, different wording. And Gomard
    # pdf 158/164: a body title restored to extracted.txt after the page
    # was translated -> translation has no title line; insert it.
    import json, tempfile, os
    SEP = "\n\x1e\n"
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(f"{d}/intermediate")
        ex = ["PAGE_NUMBER: 21\nHEADER: DE LA MANIÈRE DE SE RELEVER.\nBODY_TEXT:\n"
              "DE LA MANIÈRE DE SE RELEVER.\n\nReplier le bras droit.",
              "PAGE_NUMBER: 140\nHEADER: DU COUP ET DE LA BOTTE.\nBODY_TEXT:\n"
              "DU COUP ET DE LA BOTTE.\n\nLe coup est l'ensemble des mouvements.",
              "PAGE_NUMBER: 141\nHEADER: DU COUP ET DE LA BOTTE.\nBODY_TEXT:\n"
              "Suite du texte."]
        tr = ["METHOD OF STANDING UP.\n\nFold the right arm.",
              "The thrust is the set of movements of the attack. It is long.",
              "Continuation of the text."]
        open(f"{d}/intermediate/extracted.txt", "w").write(SEP.join(ex))
        open(f"{d}/intermediate/translation.txt", "w").write(SEP.join(tr))
        json.dump({"DE LA MANIÈRE DE SE RELEVER.": "ON THE WAY OF RISING TO ONE'S FEET.",
                   "DU COUP ET DE LA BOTTE.": "OF THE THRUST AND THE HIT."},
                  open(f"{d}/intermediate/headers.json", "w"))
        assert harmonize_titles_with_headers(d) == 2
        out = open(f"{d}/intermediate/translation.txt").read().split("\x1e")
        assert out[0].strip().startswith("ON THE WAY OF RISING TO ONE'S FEET.\n\nFold")
        assert out[1].strip().startswith("OF THE THRUST AND THE HIT.\n\nThe thrust is")
        assert out[2].strip() == "Continuation of the text."      # no body title -> untouched
        assert harmonize_titles_with_headers(d) == 0             # idempotent


def test_rotation_plan_book_rule_and_page_lists():
    # Gomard's one-word file keeps meaning "every figure page, clockwise";
    # books whose plates face different ways (Prévost, Baudry) list pages,
    # and a listed page wins over the book rule.
    import importlib
    import tempfile
    sys.path.insert(0, str(_PIPELINE))
    r = importlib.import_module("_render")
    with tempfile.TemporaryDirectory() as t:
        folder = f"{t}/demo_1900_x_translated"
        import os
        os.makedirs(folder)
        f = f"{folder}/demo_1900_x.rotate"
        open(f, "w").write("cw\n")
        assert r.rotation_plan(folder) == (("plates", 270), {})
        assert r.load_rotate(folder) == ("plates", 270)
        open(f, "w").write("all:ccw\n")
        assert r.rotation_plan(folder) == (("all", 90), {})
        open(f, "w").write("# Prévost\ncw: 35, 39, 55-57\nccw: 241   # the hall photo\n")
        book, pages = r.rotation_plan(folder)
        assert book is None
        assert pages == {34: 270, 38: 270, 54: 270, 55: 270, 56: 270, 240: 90}
        open(f, "w").write("cw\nccw: 10\n")
        assert r.rotation_plan(folder) == (("plates", 270), {9: 90})


def _load_overrides_module():
    import importlib
    sys.path.insert(0, str(_PIPELINE))
    return importlib.import_module("_overrides")



def test_caption_capital_after_dash_follows_french():
    # The model lowercased every caption segment after a dash (Baudry
    # "Fig. 2. — classical guard ...", Gomard "Plate VIII. — a thrust ...").
    assert match_dash_caps("Fig. 2. — Garde classique vue de face.",
                           "Fig. 2. — classical guard seen from the front.") \
        == "Fig. 2. — Classical guard seen from the front."
    assert match_dash_caps("Fig. 6.—La fente.", "Fig. 6.—the lunge.") == "Fig. 6.—The lunge."
    # several segments; an italic first word
    assert match_dash_caps("Planche I. — Première position. — Deuxième position.",
                           "Plate I. — first position. — <i>second</i> position.") \
        == "Plate I. — First position. — <i>Second</i> position."
    # French lowercase after the dash, or the segment counts differ: unchanged
    assert match_dash_caps("Fig. 1 — coup droit", "Fig. 1 — straight thrust") == "Fig. 1 — straight thrust"
    assert match_dash_caps("Fig. 1. — A. — B.", "Fig. 1. — a") == "Fig. 1. — a"


def test_read_heads_stand_in_for_a_missing_header_field():
    # 2026-09-29: on pages whose OCR reply had no HEADER field, the head
    # read from the detector's box is used (LaMarche scan 51 "L'Épée.",
    # left empty by the fill; Kirchhoffer 49 "OFFENSIVE", where the fill
    # copied the facing "ÉPÉE"). Folios and plate labels are skipped; a
    # near-miss takes the book's own spelling ("Applicatians").
    raws = ["PAGE_NUMBER: 25\nHEADER: Applications de la Leçon.\nBODY_TEXT:\nx",
            "PAGE_NUMBER: 30\nBODY_TEXT:\nEn général",        # no HEADER field
            "PAGE_NUMBER: 27\nBODY_TEXT:\nloin.",
            "PAGE_NUMBER: 28\nBODY_TEXT:\ntaires",
            "PAGE_NUMBER: 29\nBODY_TEXT:\nplate",
            "PAGE_NUMBER: 31\nBODY_TEXT:\ny",
            "PAGE_NUMBER: 32\nHEADER: none\nBODY_TEXT:\nz"]    # field present: untouched
    parsed = [{"header": "Applications de la Leçon."}, {"header": ""}, {"header": ""},
              {"header": ""}, {"header": ""}, {"header": ""}, {"header": ""}]
    read = {"1": {"text": "L'Épée."}, "2": {"text": "27 OFFENSIVE"}, "3": {"text": "28"},
            "4": {"text": "Pl. 3."}, "5": {"text": "Applicatians de la Leçon."},
            "6": {"text": "SOMETHING"}}
    # a plate label with its number ("Pl. 14") is still a plate label
    read["4"] = {"text": "Pl. 14"}
    assert apply_read_heads(parsed, raws, read) == 3
    assert parsed[1]["header"] == "L'Épée."
    assert parsed[2]["header"] == "OFFENSIVE"                 # folio stripped
    assert parsed[3]["header"] == "" and parsed[4]["header"] == ""   # folio only / plate label
    assert parsed[5]["header"] == "Applications de la Leçon."  # the book's own spelling
    assert parsed[6]["header"] == ""                          # the OCR answered "none"
    # the head already sits at the top of the body (Jacob 111): left for the
    # usual promotion, not duplicated
    p2 = [{"header": "", "body": "\nPREMIÈRE LEÇON.\n\nDégagez à la main"}]
    assert apply_read_heads(p2, ["BODY_TEXT:\nPREMIÈRE LEÇON."], {"0": {"text": "PREMIÈRE LEÇON."}}) == 0
    p2 = [{"header": "", "body": "\n26: INTRODUCTION.\n\nLes armes"}]          # with its folio (Gomard 42)
    assert apply_read_heads(p2, ["BODY_TEXT:\n26: INTRODUCTION."], {"0": {"text": "INTRODUCTION."}}) == 0
    # exact match takes the book's usual punctuation (LaMarche 113 "L'Épée")
    # ... the MOST common spelling, not the first met (LaMarche's title page
    # prints "L'ÉPÉE" once, every page after "L'Épée.")
    p3 = [{"header": "L'ÉPÉE"}, {"header": "L'Épée."}, {"header": "L'Épée."}, {"header": "", "body": "x"}]
    r3 = ["HEADER: L'ÉPÉE\nBODY_TEXT:\ny", "HEADER: L'Épée.\nBODY_TEXT:\ny",
          "HEADER: L'Épée.\nBODY_TEXT:\ny", "BODY_TEXT:\nx"]
    assert apply_read_heads(p3, r3, {"3": {"text": "L'Épée"}}) == 1
    assert p3[3]["header"] == "L'Épée."


def test_caption_case_no_capital_after_italic_term():
    # Sentence case capitalised the word after every kept (italic) term:
    # Baudry Fig. 7 "... to avoid the <i>riposte</i> Is hit on the arm",
    # La Boëssière "<i>Riposte</i> Of prime". Only the caption's start.
    src = "Fig. 7. — Le tireur qui reste fendu pour éviter la riposte est touché au bras."
    out = match_case(src, "Fig. 7. — The fencer who remains in the lunge to avoid the <i>riposte</i> is hit on the arm.")
    assert "<i>riposte</i> is hit" in out and out.startswith("Fig. 7.")
    out = match_case("Riposte de prime avec opposition de la main gauche.",
                     "<i>Riposte</i> of prime with opposition of the left hand.")
    assert out == "<i>Riposte</i> of prime with opposition of the left hand."
    # still capitalises the start, and all-caps / title case are unchanged
    assert match_case("La fente.", "the lunge.") == "The lunge."
    assert match_case("PARADE DE TIERCE", "Parry of <i>tierce</i>") == "PARRY OF <i>TIERCE</i>"

def test_overrides_caption_decisions():
    # An engraved caption the OCR misread (La Boëssière Pl. 4 read "Coup de
    # lance") is corrected in the overrides file: the corrected caption
    # replaces the OCR's, its stale English is dropped so step 2 translates
    # it, a page with no figure recorded gets one, a caption_en decision
    # wins, and re-applying changes nothing.
    ov = _load_overrides_module()
    o = {"headers": {}, "terms": {}, "fix": [], "pages": {
        346: {"caption": "Coup de tierce, 3e coup paré dans la même position en tierce."},
        362: {"caption": "Coup de tems pris en quarte sur les armes."},
        96: {"caption": "Fig. 3. — Épée à pointe d'arrêt.", "figure": "inset"},
        376: {"caption_en": "Plate 19. Riposte of prime with opposition of the left hand."}}}
    fmap = {"345": [{"kind": "plate", "caption": "Coup de lance 3e coup", "caption_en": "Lance hit."}],
            "375": [{"kind": "plate", "caption": "Pl. 49.", "caption_en": "Pl. 49."}]}
    changed, need = ov.apply_captions(fmap, o)
    assert changed == 4
    assert fmap["95"][0]["kind"] == "inset"          # a missed figure on a text page
    assert fmap["345"][0]["caption"].startswith("Coup de tierce") and "caption_en" not in fmap["345"][0]
    assert fmap["345"][0]["decided"] is True
    assert fmap["361"] == [{"kind": "plate", "caption": "Coup de tems pris en quarte sur les armes.", "decided": True}]
    assert fmap["375"][0]["caption_en"].startswith("Plate 19.")
    assert need == [96, 346, 362]                   # still to be translated
    fmap["345"][0]["caption_en"] = "Tierce attack, ..."
    assert ov.apply_captions(fmap, o)[0] == 0        # idempotent, English kept
    assert fmap["345"][0]["caption_en"] == "Tierce attack, ..."


def test_overrides_fix_prepend_is_idempotent_and_case_insensitive():
    # A decision recorded once must apply cleanly on every run: re-applying
    # never duplicates, and a retranslation that already produced the line
    # in another case ("PLATE V") counts as present.
    ov = _load_overrides_module()
    o = {"headers": {}, "terms": {}, "pages": {}, "fix": [{"scan": 1, "prepend": "Plate V."}]}
    tr = ["Thrust made in a lunge.", "x"]
    assert ov.apply_fixes(tr, o) == (1, [])
    assert tr[0] == "Plate V.\n\nThrust made in a lunge."
    assert ov.apply_fixes(tr, o) == (0, [])                 # idempotent
    tr2 = ["PLATE V\n\nThrust made in a lunge.", "x"]
    assert ov.apply_fixes(tr2, o) == (0, []) and tr2[0].startswith("PLATE V\n")


def test_overrides_fix_find_reports_obsolete():
    ov = _load_overrides_module()
    o = {"headers": {}, "terms": {}, "pages": {},
         "fix": [{"scan": 1, "find": "ON THE REMISE", "replace": "OF THE REMISE"},
                 {"scan": 2, "find": "gone", "replace": "new"}]}
    tr = ["title ON THE REMISE", "retranslated text"]
    applied, missing = ov.apply_fixes(tr, o)
    assert (applied, missing) == (1, [2]) and tr[0] == "title OF THE REMISE"
    assert ov.apply_fixes(tr, o) == (0, [2])                # already applied: silent


def test_overrides_extraction_wins_and_validation():
    # Gomard scan 314/315: two adjacent 9->0 misreads the reconciler can't
    # catch -- the recorded decision must win over reconciliation.
    import tempfile, os
    ov = _load_overrides_module()
    with tempfile.TemporaryDirectory() as d:
        open(f"{d}/b.overrides.toml", "w").write(
            '[pages.2]\npage_number = "298"\nheader = ""\n')
        o = ov.load("b", d)
        parsed = [{"header": "A"}, {"header": "DU DESARMEMENT."}]
        nums = ov.apply_extraction(parsed, ["207", "208"], o)
        assert nums == ["207", "298"] and parsed[1]["header"] == ""
        open(f"{d}/c.overrides.toml", "w").write('[pages.3]\npagenumber = "1"\n')
        try:
            ov.load("c", d); assert False, "typo key must be rejected"
        except ov.OverridesError:
            pass
        try:
            ov.apply_extraction([{}], ["1"], ov.load("b", d)); assert False, "past end"
        except ov.OverridesError:
            pass
    assert ov.load("missing", "/nonexistent")["pages"] == {}


def test_overrides_parse_page_spec():
    ov = _load_overrides_module()
    assert ov.parse_page_spec("12,40-42, 12") == [12, 40, 41, 42]
    for bad in ("12-", "a", "42-40"):
        try:
            ov.parse_page_spec(bad); assert False, bad
        except ov.OverridesError:
            pass


def _layout_mod():
    import importlib
    sys.path.insert(0, str(_PIPELINE))
    return importlib.import_module("_layout")


def test_layout_running_head_decisions():
    # Box shapes from real pages (label, score, [x0, y0, x1, y1] fractions).
    L = _layout_mod()
    band = (0.087, 0.114, 0.031)                       # Gomard's running-head position
    head = {"boxes": [["page_header", 0.91, [0.09, 0.090, 0.74, 0.100]],
                      ["text", 0.98, [0.08, 0.12, 0.74, 0.90]]]}
    assert L.running_head(head, band, vlm_header=True)[:2] == (True, False)
    # Gomard scan 271: the detector merged the running head into the first
    # paragraph box, which starts at the running-head position; olmOCR saw it.
    merged = {"boxes": [["text", 0.98, [0.08, 0.092, 0.74, 0.302]]]}
    assert L.running_head(merged, band, vlm_header=True)[:2] == (True, False)
    # Gomard scan 349: a sideways plate caption -- tall and narrow, not a head
    rotated = {"boxes": [["picture", 0.94, [0.12, 0.164, 0.86, 0.766]],
                         ["page_header", 0.55, [0.89, 0.191, 0.92, 0.741]]]}
    assert L.running_head(rotated, band, vlm_header=False)[:2] == (False, False)
    # Gomard scan 39, INTRODUCTION: title only, text starts well below the band
    opener = {"boxes": [["section_header", 0.85, [0.35, 0.21, 0.65, 0.23]],
                        ["text", 0.98, [0.08, 0.35, 0.74, 0.90]]]}
    assert L.running_head(opener, band, vlm_header=False)[:2] == (False, False)
    # the OCR claims a head the geometry doesn't show -> unsure, flagged
    assert L.running_head(opener, band, vlm_header=True)[:2] == (False, True)
    # band from confident heads; too few -> None
    pages = [head] * 12
    b = L.header_band(pages)
    assert b and b[0] < 0.095 < b[1]
    assert L.header_band([head] * 3) is None


def test_caps_line_split_word_and_tac_au_tac():
    # Real Qwen3.8 output (Gomard scan 239, translation model test): an
    # all-caps heading word reassembled in mixed case, and the idiom "du tac
    # au tac" rendered word for word.
    assert fix_known_garbles("RIPostes AFTER THE PARRIES OF THE HIGH LINES.") \
        == "RIPOSTES AFTER THE PARRIES OF THE HIGH LINES."
    assert fix_known_garbles("RIPPOSTES AFTER THE PARRIES") == "RIPOSTES AFTER THE PARRIES"
    # the full run: back to capitals at the end (scan 244), and "at the"
    # (scans 242, 246)
    assert fix_known_garbles("RIPostES <i>TAC-AU-TAC</i>.") == "RIPOSTES <i>TAC-AU-TAC</i>."
    assert fix_known_garbles("RIPOSTES AT THE <i>TAC-AU-TAC</i>.") == "RIPOSTES <i>TAC-AU-TAC</i>."
    assert fix_known_garbles("the speed of the <i>tac-au-tac</i>") == "the speed of the <i>tac-au-tac</i>"
    assert fix_known_garbles("je ne parle pas du tac au tac qui est foudroyant") \
        == "je ne parle pas du tac au tac qui est foudroyant"
    # ordinary prose is left alone (McDonald-style names, sentence case)
    assert fix_known_garbles("He met McDonald at the salle.") == "He met McDonald at the salle."
    src = "RIPOSTES DU TAC AU TAC."
    out = enforce_kept_terms("RIPOSTES FROM THE TAC TO THE TAC.", src)
    assert out == "RIPOSTES <i>TAC-AU-TAC</i>.", out
    # the older output's "ripostes of the tac-au-tac" (Prévost) reads the same way
    assert enforce_kept_terms("such as the ripostes of the tac au tac", "du tac au tac") \
        == "such as the ripostes <i>tac-au-tac</i>"
    assert "<i>tac-au-tac</i>" in enforce_kept_terms("a riposte du tac au tac follows", "du tac au tac")
    # Qwen3.8 left the French article: "the du tac-au-tac riposte"
    assert enforce_kept_terms("The du <i>tac-au-tac</i> <i>riposte</i> is", "du tac au tac") \
        == "The <i>tac-au-tac</i> <i>riposte</i> is"


def test_margin_notes_detect_and_copy_check():
    # Gomard scan 61 (detector boxes, as fractions): main column 0.07-0.75,
    # three shoulder notes at x 0.75-0.93. The first filter used the
    # book-wide median column edge (0.78) and missed all three -- pages
    # alternate left/right, so each page's own column must be used.
    import importlib
    sys.path.insert(0, str(_PIPELINE))
    M = importlib.import_module("_margins")
    page = {"boxes": [
        ["page_header", 0.89, [0.72, 0.09, 0.75, 0.11]],
        ["text", 0.98, [0.07, 0.12, 0.75, 0.65]],
        ["text", 0.91, [0.75, 0.18, 0.93, 0.23]],
        ["text", 0.92, [0.75, 0.41, 0.92, 0.48]],
        ["text", 0.98, [0.07, 0.65, 0.74, 0.82]],
        ["text", 0.92, [0.75, 0.66, 0.92, 0.68]],
        ["picture", 0.79, [0.62, 0.96, 0.82, 0.98]]]}
    notes = M.detect_notes(page)
    assert [round(n[1], 2) for n in notes] == [0.18, 0.41, 0.66]
    # the OCR model's pasted copy of a note is recognised, real prose isn't
    assert M._is_copy("Il reconnaît trois gardes principales.", "Il reconnaît trois gardes principales.")
    assert not M._is_copy("Giganti a fait faire un grand pas à l'escrime, et il mérite que son "
                          "nom soit placé à côté de ceux d'Agrippa.", "Progrès dus à Giganti.")
    # a chapter title shares all its few words with a longer note on the
    # page but is not a copy of it (Gomard scans 156 / 236 -- one-sided
    # overlap deleted 14 titles)
    assert not M._is_copy("DE LA RIPOSTE.", "Valeur de la riposte.")
    assert not M._is_copy("DU COUP ET DE LA BOTTE.",
                          "Pour définir une attaque, il faut nommer le coup et la botte.")


def test_margin_notes_duplicate_year_box_and_tie_break():
    # Gomard scans 37/38/60/72 (bibliography): the detector also boxed a
    # note's year line on its own -- "1778." inside "1778. Nicolas Demeuse.",
    # "1786." just above "1786. Encyclopédie méthodique." -- so the year came
    # out as a second note. And on scan 37 "1801. Bertrand." sits by the
    # line "trand, maître d'armes. Paris, an IX", whose words tie with the
    # page's first entry ("maître en fait d'armes. Londres"): the tie goes to
    # the entry at/after the previous note's, not the first on the page.
    import importlib
    sys.path.insert(0, str(_PIPELINE))
    M = importlib.import_module("_margins")
    notes = [{"box": [0.773, 0.232, 0.929, 0.255], "text": "1778. Nicolas Demeuse."},
             {"box": [0.829, 0.232, 0.876, 0.242], "text": "1778."},
             {"box": [0.828, 0.268, 0.875, 0.279], "text": "1786."},
             {"box": [0.765, 0.278, 0.939, 0.306], "text": "1786. Encyclopédie méthodique."},
             {"box": [0.13, 0.337, 0.176, 0.347], "text": "1841."},
             {"box": [0.08, 0.337, 0.222, 0.361], "text": "1841. Freidrich Köthe."},
             {"box": [0.108, 0.404, 0.196, 0.429], "text": "1841. Lafaugère."}]
    assert [n["text"] for n in M._dedupe(notes)] == [
        "1778. Nicolas Demeuse.", "1786. Encyclopédie méthodique.",
        "1841. Freidrich Köthe.", "1841. Lafaugère."]

    class Page:                                   # just what anchor() reads
        rect = type("R", (), {"width": 100.0, "height": 100.0})()
        def get_text(self, kind):
            return [(x, 31.0, x + 8, 33.0, w) for x, w in
                    ((10, "trand,"), (20, "maître"), (30, "d’armes."), (40, "Paris,"), (50, "an"))]
    paras = ["royale de Paris et maître en fait d'armes. Londres.",
             "La théorie pratique de l'Escrime pour la pointe seule.",
             "Encyclopédie méthodique. Paris, 1786.",
             "L'escrime appliquée à l'art militaire, par le citoyen Bertrand, "
             "maître d'armes. Paris, an ix."]
    box = [0.81, 0.31, 0.89, 0.33]
    assert M.anchor(Page(), {"boxes": []}, box, paras)[0] == 0          # no earlier note
    assert M.anchor(Page(), {"boxes": []}, box, paras, after=2)[0] == 3
    # sentence position: the same answer whatever order a set iterates in
    import subprocess
    code = ("import sys; sys.path.insert(0, %r); import _margins as M; "
            "print(M._position('Voici la garde du maître. Puis les lames se croisent.', "
            "['garde', 'lames']))" % str(Path(M.__file__).parent))
    outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env={**__import__("os").environ, "PYTHONHASHSEED": str(s)}).stdout
            for s in range(6)}
    assert len(outs) == 1, outs


def test_margin_note_renders_as_note_line():
    # ⟦MARGIN: …⟧ must never be drawn as raw brackets: it becomes a note line
    import importlib
    sys.path.insert(0, str(_PIPELINE))
    try:
        R = importlib.import_module("_render")
        lines = R.layout("⟦MARGIN: Riposting at a lost tempo.⟧\n\nThe <i>riposte</i> follows.",
                         300, 10)
    except (TypeError, AttributeError, ImportError):   # needs real PyMuPDF (in the image)
        return
    notes = [l for l in lines if getattr(l, "note", False)]
    assert notes and "⟦" not in "".join(t for l in lines for t, _ in l.spans)


def test_boundary_strips_trailing_hyphen():
    assert match_boundary_punctuation("the move was per-", "le mouvement fut par-") \
        == "the move was per"


def test_interrupted_translation_resumes_with_state():
    # A whole-book run saves after every page; a crash or cancel resumes
    # after the last saved page with the footnote state it had then. Saved
    # pages whose source changed since invalidate the save; later pages
    # changing don't (they weren't translated yet).
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        Path(d, "intermediate").mkdir()
        fps = ["a", "b", "c", "d"]
        st = {"fn_seen": {"feinte"}, "footnoted_seen": {"coulé"}, "fn_state": {"open": True}}
        stamps = [["cbd42c1", "2026-09-29"], ["cbd42c1", "2026-09-30"]]
        save_partial(d, fps, ["p1", "p2"], ["Qwen3.8", "Qwen3.8"], st, stamps)
        done, models, st2, stamps2 = load_partial(d, fps)
        assert done == ["p1", "p2"] and models == ["Qwen3.8", "Qwen3.8"]
        assert st2 == st and stamps2 == stamps
        assert load_partial(d, ["a", "b", "c", "CHANGED"]) is not None
        assert load_partial(d, ["a", "CHANGED", "c", "d"]) is None
        assert load_partial(d, ["a", "b", "c"]) is None          # page count changed
        Path(d, "intermediate", "translation.partial.json").write_text("{trunc")
        assert load_partial(d, fps) is None


def test_interrupted_ocr_resumes_and_rereads_failed_pages():
    # An OCR run saves the raw responses after every page; a rerun reuses
    # them, reads failed pages again, and ignores the save if the scan's
    # page count or crop changed.
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        crop = (0.0, 0.05, 0.0, 0.0)
        save_ocr_partial(d, 5, crop, ["PAGE_NUMBER: 1", "[VLM Error: timeout]", "BODY_TEXT: x"])
        assert load_ocr_partial(d, 5, crop) == ["PAGE_NUMBER: 1", None, "BODY_TEXT: x"]
        assert load_ocr_partial(d, 6, crop) == []                 # different scan
        assert load_ocr_partial(d, 5, (0.0, 0.0, 0.0, 0.0)) == []  # crop changed
        save_ocr_partial(d, 5, crop, [])                         # "in progress" marker
        assert load_ocr_partial(d, 5, crop) == []
        Path(d, "raw_ocr.partial.json").write_text("{trunc")
        assert load_ocr_partial(d, 5, crop) == []


def test_reconcile_headers_respects_layout_no_head():
    # Stage 2 (layout detector): on a page the detector is sure prints no
    # running head, the parse must not lift a body title into the header
    # (Prévost scan 203 "HISTORIQUE DU SALUT", set a third down the page)
    # nor carry a neighbour's head onto it (Prévost plate scans 35/39/...:
    # "THÉORIE PRATIQUE DE L'ESCRIME." inherited onto photo plates).
    def pages():
        return [{"header": "THÉORIE PRATIQUE DE L'ESCRIME.", "body": "La garde est la position."},
                {"header": "", "body": "", "figures": [{"kind": "plate"}]},
                {"header": "THÉORIE PRATIQUE DE L'ESCRIME.", "body": "Le coup droit est simple."},
                {"header": "", "body": "HISTORIQUE DU SALUT\n\nDe tout temps il a été d'usage."},
                {"header": "THÉORIE PRATIQUE DE L'ESCRIME.", "body": "Le salut se fait ainsi."}]
    old = pages(); reconcile_headers(old)
    # without layout the title is lifted into the header; the plate stays
    # headless either way now -- this book's plates print no running head
    assert old[3]["header"] == "HISTORIQUE DU SALUT" and old[1]["header"] == ""
    new = pages(); reconcile_headers(new, {1, 3})
    assert new[1]["header"] == "" and new[3]["header"] == ""
    assert new[3]["body"].startswith("HISTORIQUE DU SALUT")
    assert new[0]["header"] == old[0]["header"]                               # others untouched


def test_review_report_coup_blow_check_skips_non_fencing_blows():
    # After the coup -> attack retranslation, 58 "blow" flags survived; most
    # were right: hammer blows (La Boëssière mounting chapter), "blows and
    # wounds" (coups et blessures, Prévost/Jacob), a coup sec on the blade
    # (Spinnewyn x9), a bull's horn (Vauresmont). Real misses still flag.
    import importlib
    import re
    sys.path.insert(0, str(_PIPELINE))
    try:
        Q = importlib.import_module("quality_report")
    except (OSError, ImportError, SystemExit):   # reads /app paths: runs in the image
        return
    rx = re.compile(Q.TERM_RULES[0][1], re.I)
    for ok in ("a few light blows of the hammer", "the effect of the hammer blow",
               "the Penal Code on blows and wounds", "strike the blade with a sharp blow",
               "a furious blow of the horn", "end with a sharp blow on the opponent's blade",
               "the effect of a blow with a stick",
               "he throws himself onto the point"):
        assert not rx.search(ok), ok
    for bad in ("Blows called: threaten-disengage", "on the parried blow",
                "THE CUTTING BLOW", "the simple throws"):
        assert rx.search(bad), bad


def test_review_report_lost_title_check():
    # Added after 14 Gomard chapter titles vanished (margin-note copy check)
    # and the review report couldn't see it. Real shapes from Gomard.
    import importlib
    sys.path.insert(0, str(_PIPELINE))
    try:
        Q = importlib.import_module("quality_report")
    except (OSError, ImportError, SystemExit):   # reads /app paths: runs in the image
        return
    raw = "\nDE LA RIPOSTE.\n\nLa riposte est l'attaque qui suit la parade.\n"
    good_en = "ON THE <i>RIPOSTE</i>.\n\nThe <i>riposte</i> is the attack that follows the parry."
    # printed twice (running head + title): the header alone doesn't count,
    # and a margin note repeating its words ("Valeur de la riposte.") neither
    lost = "\nLa riposte est l'attaque qui suit la parade.\n\n⟦MARGIN: Valeur de la riposte.⟧\n"
    assert Q.title_issues(raw, "DE LA RIPOSTE.", lost, good_en, "DE LA RIPOSTE.")
    kept = "\nDE LA RIPOSTE.\n\nLa riposte est l'attaque qui suit la parade.\n"
    assert not Q.title_issues(raw, "DE LA RIPOSTE.", kept, good_en, "DE LA RIPOSTE.")
    # words shared across a sentence are not the title ("... de la retraite")
    assert Q.title_issues("\nDE LA RETRAITE.\n\nLa retraite.\n", "DE LA RETRAITE.",
                          "\nL'action de se remettre de la retraite.\n", "Retreat.", "DE LA RETRAITE.")
    # a plate label moved into the figure caption is not lost
    assert not Q.title_issues("\nPlanche III.\n\n", "", "\n", "", "", "Planche III. Salut")
    # a plate label joined to its caption on one line (Gomard pdf 350) is the title
    assert not Q.title_issues("\nPlanche V.\n\nCoup tiré en botte de prime\n", "",
                              "\nPlanche V.\n\nCoup tiré en botte de prime\n",
                              "Plate V. — Attack finished in a thrust in prime, parried by prime", "")
    # body title lowercased into the translation (Spinnewyn "preface")
    assert Q.title_issues("\nPRÉFACE\n\npublier ce livre\n", "", "\nPRÉFACE\n\npublier ce livre\n",
                          "preface\n\npublished with the precious collaboration", "")
    assert Q.lead_title("Du dégagement.\n\nLe dégagement est l'action") == "Du dégagement."
    assert Q.lead_title("Le dégagement est l'action de faire\npasser la pointe") == ""


def test_parry_name_cut_short_in_caps_caption():
    # Kirchhoffer scan 31 "PARADE DE QUINTE." came back "PARRY OF QUINT." on
    # two separate translations; the snap-back keeps the caption's capitals.
    assert fix_parry_names("PARRY OF QUINT.", "PARADE DE QUINTE.") == "PARRY OF QUINTE."
    assert fix_parry_names("parry of seventh", "parade de septime") == "parry of septime"
    assert fix_parry_names("A quint is five cards", "parade de quarte") == "A quint is five cards"


def test_page_numbers_roman_front_matter_and_blank_pages():
    # Jacob scans 13-24: roman front matter the OCR half-normalised to
    # arabic ("1, 2, 3, iv, 5, VI, 7, VIII, IX, 10, XI") -> all roman; the
    # main text restarts at 1 and stays arabic.
    raws = ["1", "2", "3", "iv", "5", "VI", "7", "VIII", "IX", "10", "XI", "12",
            "1", "2", "3", "4", "5", "6"]
    out = reconcile_page_numbers(raws, [False] * len(raws), ["texte"] * len(raws))
    assert out[:12] == ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"], out
    assert out[12:] == ["1", "2", "3", "4", "5", "6"], out
    # Spinnewyn scans 8-9: blank pages the OCR gave "10", "11" -- not trusted
    raws = ["10", "11", "8", "9", "10", "11", "12"]
    bodies = ["", "none", "texte", "texte", "texte", "texte", "texte"]
    out = reconcile_page_numbers(raws, [False] * 7, bodies)
    assert out[:2] != ["10", "11"] and all(not x or x.startswith("[") for x in out[:2]), out
    assert out[2:] == ["8", "9", "10", "11", "12"], out


def test_text_page_with_figures_not_folded_into_a_plate():
    # Spinnewyn scans 57 / 101 (the long-standing "56/100" bug): a text
    # page with two figures set in it, body opening mid-sentence. It was
    # folded whole into one "plate caption", which cost the page its folio
    # and turned its running head into a body title. A real plate page
    # (Gomard "Planche III.") still folds.
    text = {"header": "LE RASSEMBLEMENT", "figures": [], "body":
            "temps la main gauche dans le rang (fig. 16).\n\nFig. 16. — Premier mouvement du "
            "rassemblement.\n\nFig. 17. — Deuxième mouvement du rassemblement.\n\nRapporter "
            "ensuite et aussitôt le talon droit contre le talon gauche (fig 17)."}
    plate = {"header": "", "figures": [], "body": "Planche XVII.\n\nParade de Quarte en pointe volante."}
    pages = [text, plate]
    recover_plate_captions(pages)
    assert not pages[0]["figures"] and pages[0]["body"].startswith("temps la main")
    assert pages[1]["figures"] and pages[1]["body"] == ""


def test_page_numbers_shown_only_when_printed():
    # Gomard scan 39 (INTRODUCTION, p. 23 unprinted): OCR invented "1"
    out = reconcile_page_numbers(["21", "22", "1", "24", "25"])
    assert out[2] == "[23]", out
    # a real misread of a printed folio still shows (Prévost 198 read "108")
    assert reconcile_page_numbers(["196", "197", "108", "199"])[2] == "198"
    # roman front matter: arabic junk never becomes a shown roman page
    out = reconcile_page_numbers(["xvi", "xvii", "12", "xix", "xx"], None, ["t"] * 5)
    assert out[2].startswith("["), out
    # a malformed OCR reply (no PAGE_NUMBER field) says nothing: keep the
    # sequence value shown (Jacob scan 77 prints 29, reply omitted it) --
    # but an explicit "none" / invented number stays hidden
    out = reconcile_page_numbers(["27", "28", "", "30"], answered=[True, True, False, True])
    assert out == ["27", "28", "29", "30"], out
    out = reconcile_page_numbers(["27", "28", "none", "30"], answered=[True, True, True, True])
    assert out[2] == "[29]", out
    # old-style roman ending in j (La Boëssière "xij", "viiij")
    out = reconcile_page_numbers(["x", "xj", "xij", "xiij", "xiiij"], None, ["t"] * 5)
    assert out == ["x", "xi", "xii", "xiii", "xiv"], out


def test_kept_term_takes_capitals_in_capital_lines():
    from _rules import TX
    c = TX["caps_kept_terms"]
    assert c("OF THE <i>épée</i> OR THE FOIL.") == "OF THE <i>ÉPÉE</i> OR THE FOIL."
    assert c("PRAISE OF THE <i>épée</i>.") == "PRAISE OF THE <i>ÉPÉE</i>."
    assert c("MONTE-CARLO TOURNAMENT. (An <i>épée</i> bout.)") == "MONTE-CARLO TOURNAMENT. (An <i>épée</i> bout.)"
    assert c("THE <i>épée</i> is the oldest weapon") == "THE <i>épée</i> is the oldest weapon"
    assert c("Duel of the Duke.\n\n<i>épée</i>\n\nThe history", "Duel du duc.\n\nÉPÉE\n\nL'histoire") \
        == "Duel of the Duke.\n\n<i>ÉPÉE</i>\n\nThe history"
    assert c("Royale.\n\n<i>épée</i>\n\nThe history", "Royale.\n\nÉpée\n\nL'histoire") \
        == "Royale.\n\n<i>Épée</i>\n\nThe history"
    assert c("with the <i>épée</i>\n\n<i>épée</i>", "avec l'épée") == "with the <i>épée</i>\n\n<i>épée</i>"


def test_margin_false_notes_dropped():
    import importlib
    sys.path.insert(0, str(_PIPELINE))
    M = importlib.import_module("_margins")
    prose = "mot " * 80
    notes = [{"box": [0.8, 0.1, 0.9, 0.12], "text": t} for t in ("309", "ibid.", "Pag.", "36.141 6", "1843.")]
    assert [n["text"] for n in M._drop_false_notes(notes, prose)] == ["1843."]
    short = [{"box": [0.8, 0.1, 0.9, 0.12], "text": t} for t in
             ("Chez", "L'ÉCONOMIE", "Sous l'influence de ce même préjugé, que")]
    assert [n["text"] for n in M._drop_false_notes(short, "SE TROUVE chez M. La Boëssière")] \
        == ["Sous l'influence de ce même préjugé, que"]
    over = [{"box": [0.07, 0.244, 0.24, 0.274], "text": "1828. Encyclopédie moderne 1831"},
            {"box": [0.08, 0.268, 0.22, 0.29], "text": "1834. Heinrich Kiemann."}]
    assert M._trim_overlapping_year(over)[0]["text"] == "1828. Encyclopédie moderne"


def test_stamp_lines_removed_not_the_page():
    # Jacob scan 10: printed edition notice above a library stamp -- keep it
    p = [{"header": "", "figures": [], "body":
          "IL A ÉTÉ TIRÉ À PART :\n\nCent exemplaires sur papier du Japon numérotés à la presse "
          "(1 à 100).\n\nN° d'entrée ... NIG...20238.\nINSTITUT NATIONAL DU SPORT\n"
          "ET DE L'ÉDUCATION PHYSIQUE\nClassement ....L.R....JAC a"},
         # Spinnewyn scan 174: a stamp and nothing else
         {"header": "", "figures": [], "body": "INSTITUT NATIONAL DES SPORTS\nInventaire général :\nBibliothèque Centrale :"},
         # Jacob scan 9: stamp filed as the running head
         {"header": "INSTITUT NATIONAL DES SPORTS", "figures": [], "body": "LE\nJEU DE L'ÉPÉE"},
         # a prose page that mentions sous-officiers keeps its sentence
         {"header": "", "figures": [], "body": "Les sous-officiers de l'école de Joinville apprennent l'escrime "
          "à l'épée avec une méthode très complète et suivie par tous les élèves de la salle."}]
    drop_scan_apparatus(p)
    assert p[0]["body"].startswith("IL A ÉTÉ TIRÉ À PART") and "INSTITUT" not in p[0]["body"] \
        and "Classement" not in p[0]["body"], p[0]["body"]
    assert p[1]["body"] == ""
    assert p[2]["header"] == "" and "JEU DE L'ÉPÉE" in p[2]["body"]
    assert p[3]["body"].startswith("Les sous-officiers")


def test_image_description_becomes_plate():
    from _rules import drop_image_descriptions
    p = [{"body": "[The page contains a black and white illustration of a person in fencing attire, "
                  "holding a sword.]", "figures": []},
         {"body": "[Voir la planche ci-contre] La garde est la position.", "figures": []}]
    drop_image_descriptions(p)
    assert p[0]["body"] == "" and p[0]["figures"] == [{"kind": "plate", "caption": ""}]
    assert p[1]["body"].startswith("[Voir")


def test_small_repairs_commas_and_name_particles():
    assert fix_known_garbles("the arm,, extended") == "the arm, extended"
    assert fix_known_garbles("LETTER-PREFACE FROM MR. A. OF LA FORGE") == "LETTER-PREFACE FROM MR. A. DE LA FORGE"
    assert fix_known_garbles("Count F. of L'Angle-Beaumanoir") == "Count F. de L'Angle-Beaumanoir"
    assert fix_known_garbles("the art of La Boëssière's school") == "the art of La Boëssière's school"


def test_plate_pages_filled_only_if_the_books_plates_carry_heads():
    H = "THÉORIE PRATIQUE DE L'ESCRIME."
    def book(plate_head):
        ps = []
        for k in range(12):
            if k % 3 == 1:
                ps.append({"header": H if plate_head else "", "body": "", "figures": [{"kind": "plate"}]})
            else:
                ps.append({"header": H, "body": "Texte de la page."})
        ps[4]["header"] = ""                      # a plate the OCR gave no head
        return ps
    a = book(False); reconcile_headers(a); assert a[4]["header"] == ""   # Prévost: plates print none
    b = book(True);  reconcile_headers(b); assert b[4]["header"] == H    # Kirchhoffer: they do


def test_plate_label_joined_with_caption():
    assert fix_known_garbles("PLATE V.\n\nBlow delivered as a thrust in prime\nThe blades are crossed.") \
        == "PLATE V. — Blow delivered as a thrust in prime\nThe blades are crossed."
    long = "PLATE II.\n\nFirst paragraph.\n\nSecond paragraph.\n\nThird paragraph."
    assert fix_known_garbles(long) == long            # a text page is left alone


def test_caption_not_borrowed_from_a_neighbouring_plate():
    # Gomard Planche 1re (engraved captions unread) got Planche II's caption
    pages = [{"header": "", "body": "Planche 1re.", "figures": [{"kind": "plate", "caption": ""}]},
             {"header": "Planche II.", "body": "Troisième Position, ou la garde.", "figures": []}]
    recover_plate_captions(pages)
    assert pages[0]["figures"][0]["caption"] == ""


def _cover_module():
    import importlib
    sys.path.insert(0, str(_PIPELINE))
    try:
        return importlib.import_module("_cover")
    except (TypeError, AttributeError, ImportError):   # needs real PyMuPDF (in the image)
        return None


def test_cover_translation_line_single_run():
    # a book translated in one run: revision and date, no page counts
    C = _cover_module()
    if C is None:
        return
    line = C.provenance_line(["Q", "-", "Q"], [["cbd42c1", "2026-09-30"], None,
                                               ["cbd42c1", "2026-09-30"]])
    assert line == "Translated with FencingManualTranslator cbd42c1, September 30, 2026", line


def test_cover_translation_line_partial_retranslation():
    # a partial retranslation must not re-date the whole book: each
    # revision keeps its own pages and dates; pages from before the record
    # existed say "not recorded", never a guessed revision
    C = _cover_module()
    if C is None:
        return
    models = ["Q"] * 6
    stamps = [["54806ab", "2026-09-29"], ["54806ab", "2026-09-30"],
              ["cbd42c1", "2026-10-02"], None, None, None]
    line = C.provenance_line(models, stamps)
    assert line == ("Translated with FencingManualTranslator: "
                    "54806ab, September 29–30, 2026 (2 pages); "
                    "cbd42c1, October 2, 2026 (1 page); not recorded (3 pages)"), line
    assert C.provenance_line(["Q", "Q"], []) == \
        "Translated with FencingManualTranslator (revision and date not recorded)"


def test_cover_translation_line_filled_in_date_range():
    # pages stamped afterwards from the evidence carry a date range and no
    # revision: shown as the dates alone, never "not recorded"
    C = _cover_module()
    if C is None:
        return
    fill = ["", "2026-09-25", "2026-09-29"]
    assert C.provenance_line(["Q", "Q"], [fill, fill]) == \
        "Translated with FencingManualTranslator, September 25–29, 2026"
    assert C.provenance_line(["Q", "Q", "Q"], [fill, fill, ["cbd42c1", "2026-10-02"]]) == (
        "Translated with FencingManualTranslator: September 25–29, 2026 (2 pages); "
        "cbd42c1, October 2, 2026 (1 page)")

def test_cover_date_range_formats():
    C = _cover_module()
    if C is None:
        return
    assert C._date_range(["2026-09-26", "2026-10-02"]) == "September 26 – October 2, 2026"
    assert C._date_range(["2025-12-30", "2026-01-02"]) == "December 30, 2025 – January 2, 2026"


def test_save_fingerprints_stamps_only_retranslated_pages():
    # --pages retranslation stamps just those pages; the others keep theirs
    import tempfile, json
    from _rules import TX
    with tempfile.TemporaryDirectory() as d:
        Path(d, "intermediate").mkdir()
        Path(d, "intermediate", "translated_from.json").write_text(json.dumps(
            {"version": 1, "pages": ["a", "b", "c"], "models": ["Q", "Q", "Q"],
             "stamps": [["old1234", "2026-09-26"], None, None]}))
        TX["_RUN_STAMP"] = ["new5678", "2026-10-02"]
        TX["save_fingerprints"](d, ["a", "B", "c"], translated=[2])
        rec = json.loads(Path(d, "intermediate", "translated_from.json").read_text())
        assert rec["stamps"] == [["old1234", "2026-09-26"], ["new5678", "2026-10-02"], None]
        TX["_RUN_STAMP"] = None


def test_italian_cat2_new_terms_translated_and_noted_once():
    # post-OCR survey terms (2026-09-30): a bare Italian word left in the
    # English becomes its rendering, with one "Italian: <singular>" note
    # per book; "di striscio" wins over bare "striscio"
    from _rules import TX
    TX["BOOK_LANG"] = "Italian"
    try:
        seen = set()
        out = TX["enforce_footnoted_terms"]("A feint volante, then two feints volanti.", seen)
        assert "volant" not in out.replace("Italian: volante", ""), out
        assert out.count("off the blade") == 2 and out.count("<fn>Italian: volante</fn>") == 1, out
        out = TX["enforce_footnoted_terms"]("He hits di striscio.", seen)
        assert out == "He hits grazing<fn>Italian: striscio</fn>.", out
        out = TX["enforce_footnoted_terms"]("The controazioni of seconda.", seen)
        assert out == "The counter-actions<fn>Italian: controazione</fn> of seconda.", out
    finally:
        TX["BOOK_LANG"] = "French"


def test_italian_prompt_carries_survey_terms():
    from _rules import TX
    p = TX["prompt_for_language"]("Italian")
    for t in ("<i>coupé</i>", "picchio, picco → beat", "controazione → counter-action",
              "mezzocerchio → half-circle", "mezzo contro", "pancia → belly",
              "off the blade", "write <i>fianconata</i>"):
        assert t in p, t
    assert "picchio" not in TX["prompt_for_language"]("French")


def test_italian_kept_terms_backstop():
    # Italian sample 2026-09-30: Bruno "An sforzo is called..." left bare,
    # Masiello 1902 heading "OF THE RIPONSES" (DELLE RISPOSTE)
    from _rules import TX
    TX["BOOK_LANG"] = "Italian"
    try:
        out = TX["enforce_kept_terms"]("The sforzo and the simple sforzi.")
        assert out == "The <i>sforzo</i> and the simple <i>sforzi</i>.", out
        out = TX["enforce_kept_terms"]("OF THE <i>RIPONSES</i> FROM THE FINAL PARRY")
        assert "RIPOSTES" in out and "RIPONSES" not in out, out
    finally:
        TX["BOOK_LANG"] = "French"


def test_multiline_prepend_idempotent_and_heals_copies():
    # Jacob scan 49: a two-line prepend compared only the first line, so
    # every overrides run stacked another copy (six by 2026-09-30)
    import _overrides
    fx = {"fix": [{"scan": 1, "prepend": "THE GAME OF THE ÉPÉE\n\nINTRODUCTION"}], "pages": {}}
    pages = ["Are there two kinds of fencing? Yes."]
    _overrides.apply_fixes(pages, fx)
    once = pages[0]
    assert once == "THE GAME OF THE ÉPÉE\n\nINTRODUCTION\n\nAre there two kinds of fencing? Yes.", once
    _overrides.apply_fixes(pages, fx)
    assert pages[0] == once, pages[0]                   # second run: no change
    stacked = ["THE GAME OF THE ÉPÉE\n\nINTRODUCTION\n\n" * 3 + "Are there two kinds of fencing? Yes."]
    _overrides.apply_fixes(stacked, fx)
    assert stacked[0] == once, stacked[0]               # copies collapse to one
    one = {"fix": [{"scan": 1, "prepend": "Plate V."}], "pages": {}}
    pl = ["PLATE V — caption text"]
    _overrides.apply_fixes(pl, one)
    assert pl[0] == "PLATE V — caption text", pl[0]      # single-line: unchanged behaviour


def test_markdown_italics_rendered_markers_untouched():
    # Vauresmont pdf 64: "*Inside line*" printed with its asterisks
    import importlib
    sys.path.insert(0, str(_PIPELINE))
    try:
        R = importlib.import_module("_render")
        f = R.markdown_italics
    except (TypeError, AttributeError, ImportError):   # needs real PyMuPDF (in the image)
        return
    assert f("the weapon. — *Inside line*: the quarte") == "the weapon. — <i>Inside line</i>: the quarte"
    assert f("*<i>Reprise</i>.* — Duration") == "<i>Reprise.</i> — Duration"
    assert f("the authors of *Renée Mauperin* put") == "the authors of <i>Renée Mauperin</i> put"
    for keep in ("parry of quarte (*).", "   * In the bout", "* * *", "*", "th the skill* one may"):
        assert f(keep) == keep, keep


def test_dehyphenate_across_page_past_footnote_and_margin():
    # Kirchhoffer scan 12: "se mé-" then the author's footnote, "fier" on
    # the next page (translated "smell of" as two fragments)
    pages = [{"body": "tout le visage. Il faudra se mé-\n\n(1) Cette appellation vient du vieux français."},
             {"body": "fier de certains masques de prix modéré."}]
    dehyphenate_across_pages(pages)
    assert pages[0]["body"] == "tout le visage. Il faudra se méfier\n\n(1) Cette appellation vient du vieux français.", pages[0]
    assert pages[1]["body"] == "de certains masques de prix modéré.", pages[1]
    # a margin note opening the next page is stepped over, kept
    pages = [{"body": "en dedans, en pro-"},
             {"body": "⟦MARGIN: La quinte.⟧\n\nnation, la pointe en dessus."}]
    dehyphenate_across_pages(pages)
    assert pages[0]["body"] == "en dedans, en pronation,", pages[0]
    assert pages[1]["body"] == "⟦MARGIN: La quinte.⟧\n\nla pointe en dessus.", pages[1]


def test_dehyphenate_plain_join_not_cut_by_numbered_paragraph():
    # LaMarche scan 117: a "1." paragraph mid-page must not be taken for a
    # footnote block -- the page still ends "mar-" and joins as before
    pages = [{"body": "Premier point.\n\n1. Un apprentissage sévère et qui doit être mar-"},
             {"body": "qué, même dès les premières leçons."}]
    dehyphenate_across_pages(pages)
    assert pages[0]["body"].endswith("doit être marqué,"), pages[0]
    assert pages[1]["body"] == "même dès les premières leçons.", pages[1]


def test_prose_indent_flush_left_structure_kept():
    # LaMarche pp. 14-16: the OCR indents some paragraphs and not others;
    # drills / braced groups / numbered lists keep their indent
    import importlib
    sys.path.insert(0, str(_PIPELINE))
    try:
        R = importlib.import_module("_render")
        lines = R.layout("    You will therefore be moderate, my little man; but there are two things for the defense of which\n"
                         "Go straight { high\n      inside } on the hand, the arm.\n"
                         "    3° Of a guard or solid steel cup, of a diameter of 0.12 approximately\n"
                         "    Break.", 2000, 10)
    except (TypeError, AttributeError, ImportError):   # needs real PyMuPDF (in the image)
        return
    first = lambda k: next(l for l in lines if not l.gap and "".join(t for t, _ in l.spans).strip().startswith(k))
    assert first("You will").indent == 0
    assert not any("".join(t for t, _ in l.spans).startswith(" ") for l in lines if not l.gap)
    assert first("inside").indent > 0
    assert first("3°").indent > 0
    assert first("Break").indent > 0


def test_review_report_italian_rules_only_on_italian_books():
    # Italian glossary checks (2026-09-30). A French book has its own
    # "volant" (LaMarche's garde volante) and quotes Italian titles (Gomard's
    # bibliography): the Italian list must not reach it.
    import importlib
    import re
    sys.path.insert(0, str(_PIPELINE))
    try:
        Q = importlib.import_module("quality_report")
    except (OSError, ImportError, SystemExit):   # reads /app paths: runs in the image
        return
    rule = {r[2]: r for r in Q.TERM_RULES_IT}
    assert all(r[2] not in rule for r in Q.TERM_RULES)
    src, wrong, _ = rule["volante → off the blade"]
    assert re.search(src, "una finta volante", re.I)
    assert re.search(wrong, "a flying feint", re.I)
    assert not re.search(wrong, "the flying <i>cartoccio</i>", re.I)       # Florio §65
    src, wrong, _ = rule["misura → measure"]
    assert re.search(src, "fuori misura", re.I)
    assert not re.search(src, "la distanza ... fuori misura", re.I)         # the book says distance
    assert Q.still_french("Di Antonio Manciolino, Bolognese, opera nova, dove sono tutti "
                          "li documenti e vantaggi che si ponno avere nel mestier de l'armi")
    assert not Q.still_french("The hit must reach the target before the opponent has "
                              "completed his action, in the line of the parry.")


def test_page_context_off_by_default_and_body_only():
    # PAGE_CONTEXT (experimental): off unless asked for; when on, the
    # previous page's last words and the next page's first words, from
    # the body only (no running head, footnotes or margin notes)
    from _rules import TX
    assert TX["PAGE_CONTEXT"] is False
    assert TX["page_context_block"]({"context": {"prev": "x", "next": "y"}}) == ""
    pages = ["PAGE_NUMBER: 6\nHEADER: FLEURET\nBODY_TEXT:\nprotéger tout le visage. Il faudra se mé-\n\n(1) Cette appellation vient du vieux français.",
             "PAGE_NUMBER: 7\nHEADER: ENTRAINEMENT\nBODY_TEXT:\n⟦MARGIN: Les masques.⟧\n\nfier de certains masques de prix modéré.",
             ""]
    c = TX["neighbour_context"](pages, 1)
    assert c["prev"].endswith("Il faudra se mé-") and "appellation" not in c["prev"] and "FLEURET" not in c["prev"], c
    assert c["next"] == "", c
    c0 = TX["neighbour_context"](pages, 0)
    assert c0["prev"] == "" and c0["next"].startswith("fier de certains") and "MARGIN" not in c0["next"], c0
    TX["PAGE_CONTEXT"] = True
    try:
        blk = TX["page_context_block"]({"context": c})
        assert "do NOT translate" in blk and "se mé-" in blk and blk.endswith("\n\n")
        assert TX["page_context_block"]({"context": {"prev": "", "next": ""}}) == ""
    finally:
        TX["PAGE_CONTEXT"] = False


def test_join_split_sentences_moves_shorter_fragment():
    # Spinnewyn 101/102: "Bien" | "fermer alors la ligne..." was translated
    # "Well" / "then close"; the 1-word start moves onto the next page
    pages = [{"body": "un seul instant le contact de l'épée adverse. Bien"},
             {"body": "fermer alors la ligne de quarte et diriger la pointe en pleine poitrine.\n\nOBSERVATION"}]
    assert join_split_sentences(pages) == 1
    assert pages[0]["body"] == "un seul instant le contact de l'épée adverse."
    assert pages[1]["body"].startswith("Bien fermer alors la ligne de quarte"), pages[1]
    # the end is shorter: it comes back, before page 1's footnote block
    pages = [{"body": "Le soir même je parlais de ce jeune prévôt à mon vieux professeur, qui\n\n(1) Grisier."},
             {"body": "m'aimait tendrement. Il résista longtemps à mes prières répétées."}]
    join_split_sentences(pages)
    assert pages[0]["body"] == "Le soir même je parlais de ce jeune prévôt à mon vieux professeur, qui m'aimait tendrement.\n\n(1) Grisier.", pages[0]
    assert pages[1]["body"] == "Il résista longtemps à mes prières répétées.", pages[1]


def test_join_split_sentences_across_plate_and_margin_never_empties():
    # one plate page between the halves; a margin note opening the next
    # page stays first
    pages = [{"body": "Le coup droit se fait en allongeant le bras, la main"},
             {"body": ""},
             {"body": "⟦MARGIN: Coup droit.⟧\n\nhaute, et en se fendant aussitôt. La suite vient ensuite."}]
    assert join_split_sentences(pages) == 1
    assert pages[0]["body"] == "Le coup droit se fait en allongeant le bras, la main haute, et en se fendant aussitôt.", pages[0]
    assert pages[2]["body"] == "⟦MARGIN: Coup droit.⟧\n\nLa suite vient ensuite.", pages[2]
    # moving the end back would empty page 2: the start goes forward
    pages = [{"body": "Première phrase complète. Et la suite de cette longue phrase qui continue"},
             {"body": "ici."}]
    join_split_sentences(pages)
    assert pages[0]["body"] == "Première phrase complète.", pages
    assert pages[1]["body"] == "Et la suite de cette longue phrase qui continue ici.", pages


def test_join_split_sentences_rejoins_broken_word_without_hyphen():
    # Barbasetti 75: "il ferro av-" | "versario." became "av-versario"
    pages = [{"body": "Il colpo è robusto, specialmente quando non incontrano il ferro av-"},
             {"body": "⟦MARGIN: Nota.⟧\n\nversario. Poi segue il resto della pagina con altro testo."}]
    join_split_sentences(pages)
    assert pages[0]["body"].endswith("il ferro avversario."), pages[0]


def test_join_split_sentences_past_long_footnote_and_prose_ellipsis():
    # Florio 20/21: a footnote of several paragraphs (quotation, reference,
    # comment) after the split; Florio 52: "abbattimenti.... in voler" is
    # prose with an ellipsis, not a contents line
    pages = [{"body": "una diversa loro velocità effetti\n\n(1) Arma antiqua manus.\n\nLucrez. De rer. nat. lib. v.\n\nRilevasi da questa citazione."},
             {"body": "producevano più o meno vantaggiosi. Formò varie serie di movimenti."}]
    assert join_split_sentences(pages) == 1
    assert pages[0]["body"].startswith("una diversa loro velocità effetti producevano più o meno vantaggiosi.\n\n(1) Arma"), pages[0]
    pages = [{"body": "in fare abbattimenti.... in voler essere insomma Cavaliere"},
             {"body": "errante di tutto punto. Il resto della pagina segue qui."}]
    assert join_split_sentences(pages) == 1


def test_join_split_sentences_semicolon_does_not_end_sentence():
    # Jacob 26/27: stopping at "lui dis-je;" left "il deviendrait" on the
    # next page, apart from the prévôt it refers to
    pages = [{"body": "C'était vous. — Vous devriez bien prendre ce petit prévôt,"},
             {"body": "lui dis-je; entre vos mains, il deviendrait incomparable. Grisier résista longtemps."}]
    join_split_sentences(pages)
    whole = "Vous devriez bien prendre ce petit prévôt, lui dis-je; entre vos mains, il deviendrait incomparable."
    assert any(whole in p["body"] for p in pages), pages        # one page has it all


def test_join_split_sentences_leaves_complete_pages_contents_and_capitals():
    for a, b in (("Une phrase finie.", "suite en minuscule."),           # complete page
                 ("Le croisé . . . . . . . . 72", "parades . . . 73"),  # contents
                 ("Et la parade", "Quarte. Un titre en capitale.")):    # next starts upper
        pages = [{"body": a}, {"body": b}]
        assert join_split_sentences(pages) == 0 and pages == [{"body": a}, {"body": b}], (a, b)


def test_folio_witness_only_confirms_never_hides():
    # strip-read test pages (2026-10-01): Vauresmont 117 unread by the OCR,
    # La Boëssière xiij/xvj misread; a gathering mark "13", "NONE" on a
    # printed page and a misread "vij" must change nothing
    nums = ["xii", "[13]", "xiv", "[16]", "[8]", "90", "[193]", "[117]", "[150]"]
    strips = {"2": "xiii", "4": "xvi", "5": "vij", "6": "NONE", "7": "13", "8": "117", "9": "NONE"}
    assert apply_folio_witness(nums, strips) == ["xii", "xiii", "xiv", "xvi", "[8]", "90", "[193]", "117", "[150]"]
    assert apply_folio_witness(["XII", "[13]"], {"2": "xiii"}) == ["XII", "XIII"]     # neighbours' case
    # an unnumbered page inside a consistent run (Vauresmont 116, _, 118):
    # the run predicts 117; the strip read must agree. Without a run on both
    # sides, or with a gap that doesn't add up, nothing is shown.
    assert apply_folio_witness(["116", "", "118"], {"2": "117"}) == ["116", "117", "118"]
    assert apply_folio_witness(["116", "", "[118]"], {"2": "117"}) == ["116", "", "[118]"]
    assert apply_folio_witness(["116", "", "120"], {"2": "117"}) == ["116", "", "120"]
    # per-margin readings [top, bottom] (2026-10-09): either margin naming
    # the number confirms it (Jacob-André scan 14 "II"); a chapter number,
    # date or shelf mark in a margin (evals/folio_choice_test.py) and a
    # one-stroke misread ("XIII" for XII) change nothing
    assert apply_folio_witness(["I", "[2]", "[3]"], {"2": ["II", "NONE"], "3": ["NONE", "NONE"]}) == ["I", "II", "[3]"]
    assert apply_folio_witness(["XI", "[12]", "[81]", "[12]"],
                               {"2": ["XIII", "NONE"], "3": ["VIII", "NONE"], "4": ["1883", "267"]}) \
        == ["XI", "[12]", "[81]", "[12]"]
    assert apply_folio_witness(["449", "[450]"], {"2": ["NONE", "450"]}) == ["449", "450"]


def test_plain_math_from_ocr_latex():
    # Masiello's mechanics (~245 places): the OCR writes labelled points and
    # symbols as LaTeX; plain printed text instead, unknown forms left alone
    pages = [{"body": r"il braccio di leva sarà \( PF \), come nel caso; \( T = \frac{1}{2} M V^2 \) e \( \omega'_2 \)"},
             {"body": r"si ha \( \frac{1}{2} M V'_i{}^2 \) e \( \int_0^1 x dx \)"}]
    plain_math(pages)
    assert pages[0]["body"] == "il braccio di leva sarà PF, come nel caso; T = 1/2 M V² e ω'₂", pages[0]
    assert pages[1]["body"].startswith("si ha 1/2 M V'ᵢ² e "), pages[1]
    assert r"\( \int_0^1 x dx \)" in pages[1]["body"]           # no faithful plain form: kept


def test_join_split_sentences_past_section_break_and_quote_marks():
    # LaMarche 43/44: a "* * *" section break is not a footnote marker -- it
    # used to hide the split at the foot of the page
    pages = [{"body": "Premier paragraphe.\n\n* * *\n\nLa surface doit être lisse.\n"
                      "Enfin nous pensons qu'on doit renoncer absolument"},
             {"body": "aux coquilles dont les bords sont relevés. Dans l'attaque, la pointe glisse."}]
    assert join_split_sentences(pages) == 1
    assert "renoncer absolument aux coquilles dont les bords sont relevés." in pages[0]["body"] \
        or pages[1]["body"].startswith("Enfin nous pensons"), pages
    # a real "* " footnote still closes the running text
    pages = [{"body": "Le texte finit ici.\n\n* Note de l'auteur sans point"},
             {"body": "suite qui ne continue rien."}]
    assert join_split_sentences(pages) == 0
    # Gomard 44/45: a quotation running on opens the next page with "«"
    pages = [{"body": "puis la troisième; et enfin la dernière, portant"},
             {"body": "« la main à droite, en dedans vers le genou, sera « la quatrième. Telles sont les gardes."}]
    assert join_split_sentences(pages) == 1
    joined = pages[0]["body"] + " | " + pages[1]["body"]
    assert "portant la main à droite" in joined, joined


def test_report_running_head_spellings():
    # Masiello: one head read three ways; reported once, the reviewer picks
    # the spelling (the commoner can be the misreading: Barbasetti)
    import importlib
    sys.path.insert(0, str(_PIPELINE))
    try:
        Q = importlib.import_module("quality_report")
    except (OSError, ImportError, SystemExit):   # reads /app paths: runs in the image
        return
    where = {"FERDINANDO MASIELLO": [3, 5], "FERDINANDO MASIEMLO": [7], "FERDINANDO MASIello": [9],
             "INTRODUCTION": [11], "INTRODUCTION.": [12],             # punctuation only
             "CAPITOLO QUARTO": [13], "CAPITOLO QUINTO": [14],         # different chapters
             "CHAPITRE II": [15], "CHAPITRE III": [16],
             "LUIGI BARBASSETTI": [17], "LUIGI BARBASETTI": [18]}
    hmap = {"LUIGI BARBASSETTI": "LUIGI BARBASETTI", "LUIGI BARBASETTI": "LUIGI BARBASETTI"}
    out = Q.head_variants(where, hmap)                                 # Barbasetti: decided
    assert len(out) == 1 and "3 spellings" in out[0][2] and out[0][1] == 7, out


def test_report_point_cut_and_cutting_blow():
    # Gomard pdf 43 / Vauresmont pdf 115: coup de pointe came out "point cut",
    # coup de taille "cutting blow"
    import importlib, re
    sys.path.insert(0, str(_PIPELINE))
    try:
        Q = importlib.import_module("quality_report")
    except (OSError, ImportError, SystemExit):   # reads /app paths: runs in the image
        return
    def hits(rules, fr, en):
        return [r for f, w, r in rules if re.search(f, fr, re.I) and re.search(w, en, re.I)]
    assert "coup de pointe → thrust" in hits(Q.TERM_RULES, "Feinte de coup de pointe", "Feint of a point cut")
    assert not hits(Q.TERM_RULES, "Feinte de coup de pointe", "Feint of a thrust")
    assert "coup de taille → cut" in hits(Q.TERM_RULES, "de coup de taille", "of a cutting blow")
    assert "colpo di punta → thrust" in hits(Q.TERM_RULES_IT, "colpo di punta", "a point cut")


def test_translation_waits_out_a_server_outage():
    # 2026-10-02: the GPU system lost power and 33 pages were kept untranslated
    # within minutes. Remote mode now waits for the server and resends; a
    # server that answers but errors still uses up the attempts (no endless loop).
    import types, requests as real
    from _rules import TX
    saved = {k: TX[k] for k in ("requests", "bc", "time")}
    calls = {"post": 0, "health": 0}
    state = {"down_posts": 2, "health_down": 2}

    class Resp:
        def __init__(self, code, body=None):
            self.status_code, self._b = code, body
        def json(self):
            return self._b
    ok = Resp(200, {"choices": [{"message": {"content": "The thrust."}}], "model": "m.gguf"})

    def post(*a, **k):
        calls["post"] += 1
        if state["down_posts"]:
            state["down_posts"] -= 1
            raise real.ConnectionError("refused")
        return ok
    def get(*a, **k):
        calls["health"] += 1
        if state["health_down"]:
            state["health_down"] -= 1
            raise real.ConnectionError("refused")
        return Resp(200)
    fake_req = types.SimpleNamespace(post=post, get=get, ConnectionError=real.ConnectionError,
                                     exceptions=real.exceptions, RequestException=real.RequestException)
    fake_bc = types.SimpleNamespace(is_remote=lambda: True, translate_base_url=lambda: "http://x",
                                    auth_headers=lambda: {})
    import time as _t
    fake_time = types.SimpleNamespace(sleep=lambda s: None, time=_t.time, strftime=_t.strftime)
    try:
        TX.update(requests=fake_req, bc=fake_bc, time=fake_time)
        # down for two requests (health down twice, then up): waited, then translated
        assert TX["translate_with_llama"]("p") == "The thrust.", calls
        # a server that answers /health but keeps failing: 3 attempts, then None
        state["down_posts"] = 99
        calls["post"] = 0
        assert TX["translate_with_llama"]("p") is None and calls["post"] == 3, calls
        # local mode never waits
        fake_bc.is_remote = lambda: False
        state["down_posts"], calls["post"] = 99, 0
        assert TX["translate_with_llama"]("p") is None and calls["post"] == 3
    finally:
        TX.update(saved)


def test_cover_page_counts_singular():
    # "87c136a, October 2, 2026 (1 pages)" on Gomard's cover, 2026-10-02
    C = _cover_module()
    assert C._pages(1) == "1 page" and C._pages(2) == "2 pages"


def test_cover_dates_first_published_and_edition():
    # the cover places each book in time: first appearance, plus the scan's
    # edition when it is a later one (Jacob's scan is the 1896 fifth edition)
    C = _cover_module()
    assert C._dates({"year_first": "1845"}) == ["1845", ""]
    assert C._dates({"year": "1845"}) == ["1845", ""]                     # old field still read
    assert C._dates({"year_first": "1887", "year_edition": "1896", "edition": "fifth",
                     "publisher": "Paul Ollendorff, Paris"}) == \
        ["First published 1887", "This edition: fifth, Paul Ollendorff, Paris, 1896"]
    assert C._dates({"year_first": "1887", "year_edition": "1902", "edition": "second",
                     "first_as": "La scherma italiana"})[0] == "First published 1887 (as La scherma italiana)"
    assert C._dates({"year_first": "1847", "edition": "second", "publisher": "Garnier"}) == \
        ["1847", "This edition: second, Garnier"]



def test_no_running_heads_setting():
    # Florio / Bruno / Lambertini print only a page number at the top; the
    # layout detector calls it a running head (185 / 156 / 27 false flags,
    # and Florio's chapter title "CAPO II." carried onto 22 pages). The
    # reviewer's [book] running_heads = false takes the detector's
    # page_header boxes out of the running-head decision.
    import tempfile
    ov = _load_overrides_module()
    L = _layout_mod()
    with tempfile.TemporaryDirectory() as d:
        open(f"{d}/b.overrides.toml", "w").write("[book]\nrunning_heads = false\n")
        assert ov.load("b", d)["book"] == {"running_heads": False}
        assert L.no_running_heads("/data/processed/b_translated", d)
        assert not L.no_running_heads("/data/processed/c_translated", d)   # no file
        for bad in ('[book]\nrunning_heads = "no"\n', "[book]\nrunningheads = false\n"):
            open(f"{d}/c.overrides.toml", "w").write(bad)
            try:
                ov.load("c", d); assert False, bad
            except ov.OverridesError:
                pass
    assert ov.load("missing", "/nonexistent")["book"] == {}


def test_strip_fieldless_folio_on_no_head_page():
    # Bruno scan 196 / Lambertini 118: the OCR reply had no fields and the
    # page number came out as the first body line, on a page the detector
    # says prints no running head -- the line blocked the sentence join.
    assert strip_fieldless_folio("194\n\nposizione di partenza, compiendo", "194\n\nposizione") \
        == "posizione di partenza, compiendo"
    # a reply WITH fields answered the page-number question: body kept
    assert strip_fieldless_folio("194\nx", "PAGE_NUMBER: none\nBODY_TEXT:\n194\nx") == "194\nx"
    # a roman section number (Marchionni 9) and a year (LaMarche 14, the
    # dedication's "1898") are content
    assert strip_fieldless_folio("III.\n\nDel duello", "III.") == "III.\n\nDel duello"
    assert strip_fieldless_folio("1898\n\nA mon grand Georges", "1898") == "1898\n\nA mon grand Georges"


def test_report_spots_page_number_taken_for_running_head():
    # Measured 2026-10-05: on small header boxes the OCR reads no head on
    # 86-94% of pages in the page-number-only books, at most 66% (LaMarche)
    # in books with real running heads.
    import importlib
    sys.path.insert(0, str(_PIPELINE))
    try:
        Q = importlib.import_module("quality_report")
    except (OSError, ImportError, SystemExit):   # reads /app paths: runs in the image
        return
    folio = ["page_header", 0.9, [0.47, 0.067, 0.50, 0.082]]          # Bruno "19"
    page = {"w": 1000, "h": 1600, "boxes": [folio]}
    none_ = "PAGE_NUMBER: 19\nHEADER: none\nBODY_TEXT:\nx"
    head = "PAGE_NUMBER: 19\nHEADER: PARTE II.\nBODY_TEXT:\nx"
    assert Q.folio_shaped_heads([page] * 30, [none_] * 27 + [head] * 3) == (27, 30)
    assert Q.folio_shaped_heads([page] * 30, [none_] * 20 + [head] * 10) == ()   # 67%
    assert Q.folio_shaped_heads([page] * 10, [none_] * 10) == ()                 # too few
    wide = {"w": 1000, "h": 1600, "boxes": [["page_header", 0.9, [0.2, 0.06, 0.8, 0.08]]]}
    assert Q.folio_shaped_heads([wide] * 30, [none_] * 30) == ()


def _books_mod():
    import importlib
    sys.path.insert(0, str(_PIPELINE))
    return importlib.import_module("_books")


def test_books_named_or_all_and_final_lock():
    # 2026-10-05: no script runs on "every book" by default any more, and a
    # book marked [book] final = true is left alone without --unlock.
    import os, tempfile
    B = _books_mod()
    with tempfile.TemporaryDirectory() as d:
        B.PROCESSED, B.BOOKS = f"{d}/processed", f"{d}/books"
        for stem in ("a_1900_x", "b_1901_y"):
            os.makedirs(f"{d}/processed/{stem}_translated")
            os.makedirs(f"{d}/books", exist_ok=True)
            open(f"{d}/books/{stem}.pdf", "w").close()
        open(f"{d}/books/b_1901_y.overrides.toml", "w").write("[book]\nfinal = true\n")
        os.environ.pop("PDF_ONLY", None)
        for argv in ([], ["--unlock"]):
            try:
                B.select(argv, "usage", books_dir=f"{d}/books"); assert False, "no books named"
            except SystemExit as e:
                assert e.code == 2
        assert B.select(["a_1900_x"], "u", books_dir=f"{d}/books") == ["a_1900_x"]
        assert B.select(["--all"], "u", books_dir=f"{d}/books") == ["a_1900_x"]      # b is final
        assert B.select(["--all", "--unlock"], "u", books_dir=f"{d}/books") == ["a_1900_x", "b_1901_y"]
        assert B.select(["b_1901_y"], "u", writes=False, books_dir=f"{d}/books") == ["b_1901_y"]
        assert B.select(["--all"], "u", source="pdf", books_dir=f"{d}/books") == ["a_1900_x"]
        try:
            B.select(["b_1901_y"], "u", books_dir=f"{d}/books"); assert False, "final, named"
        except SystemExit as e:
            assert e.code == 1
        try:
            B.select(["nope"], "u", books_dir=f"{d}/books"); assert False, "unknown book"
        except SystemExit:
            pass
        os.environ["PDF_ONLY"] = "a_1900_x"                     # the older form still works
        try:
            assert B.select([], "u", books_dir=f"{d}/books") == ["a_1900_x"]
        finally:
            del os.environ["PDF_ONLY"]
        # a folder (reprocess_extracted.py, scratch copies) is taken as given
        f = f"{d}/elsewhere/a_1900_x_translated"
        assert B.select([f], "u", books_dir=f"{d}/books") == [f]
        assert B.stem_of(f) == "a_1900_x" and B.folder_of(f) == f


def test_hand_edits_replace_the_page_and_count_on_the_cover():
    # edit_page.py writes books/<stem>.edits/scan-NNNN.txt; the English
    # below the rule replaces the page on every run; the cover counts the
    # pages a person changed ([[fix]] + edits).
    import os, tempfile
    ov = _load_overrides_module()
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(f"{d}/b.edits")
        text = ov.edit_file_text("b", 2, "abc123", "Old <i>English</i>\n\n---\nrule-like line kept",
                                 original="CAPO II.\n\nDella guardia del corpo.")
        assert "#   Della guardia del corpo." in text
        open(f"{d}/b.edits/scan-0002.txt", "w").write(text.replace("Old", "New"))
        e = ov.load_edits("b", d)
        assert e == {2: {"text": "New <i>English</i>\n\n---\nrule-like line kept", "source": "abc123"}}
        tr = ["p1", "machine p2"]
        assert ov.apply_edits(tr, e) == 1 and tr[1].startswith("New") and ov.apply_edits(tr, e) == 0
        open(f"{d}/b.overrides.toml", "w").write('[[fix]]\nscan = 1\nfind = "a"\nreplace = "b"\n'
                                                 '[[fix]]\nscan = 2\nfind = "c"\nreplace = "d"\n')
        assert ov.hand_corrected_pages("b", d) == [1, 2]
        open(f"{d}/b.edits/scan-0003.txt", "w").write("no rule line")
        try:
            ov.load_edits("b", d); assert False, "an edit without its rule line"
        except ov.OverridesError:
            pass
    assert ov.load_edits("missing", "/nonexistent") == {}


def test_page_break_keeps_a_compound_hyphen():
    # La Boëssière "contre-" | "dégagement" became "contredégagement"; Jacob
    # "procès-verbal", Masiello Sciabola "scapolo-omerale" the same. The book
    # prints them hyphenated elsewhere -> the hyphen stays. A line-break
    # split ("av-" | "versario") is joined; so is one whose stray hyphenated
    # form is outnumbered by the whole word (Bruno "scia-bola" once,
    # "sciabola" everywhere).
    def run(pages):
        parsed = [{"body": b} for b in pages]
        dehyphenate_across_pages(parsed)
        return parsed[0]["body"]
    assert run(["suivre du contre-", "dégagement et de tous.", "le contre-dégagement."]) \
        .endswith("du contre-dégagement")
    assert run(["contre son av-", "versario. Ensuite.", "l'avversario."]).endswith("son avversario.")
    assert run(["la scia-", "bola dritta.", "scia-bola; sciabola, sciabola."]).endswith("la sciabola")
    p = [{"body": "il faut faire suivre du contre-"}, {"body": "dégagement et de tous les coups."},
         {"body": "Le contre-dégagement se fait."}]
    join_split_sentences(p)
    assert "contre-dégagement" in p[0]["body"] + p[1]["body"]


def test_french_title_keeps_escrime():
    # Joseph-Renaud scan 35 "Traité d'Fencing pratique", Barbasetti
    # "(Bibliographie de l'Fencing)": a French title half translated
    fix = _rules_mod_fix()
    assert fix("his Traité d'Fencing pratique") == "his Traité d'Escrime pratique"
    assert fix("(Bibliographie de l’Fencing).") == "(Bibliographie de l’Escrime)."
    assert fix("Fencing and the duel; the art of fencing") == "Fencing and the duel; the art of fencing"
    # Kirchhoffer: "Parades." came out "Parrhes." three times
    assert fix("Parrhes. — How to take them. Principal parrhes.") == "Parries. — How to take them. Principal parries."


def _rules_mod_fix():
    from _rules import TX
    return TX["fix_known_garbles"]


def test_render_paragraph_indent_and_star_divider():
    # Joseph-Renaud pdf 54: paragraphs separated by single line breaks ran
    # together; a paragraph after a finished sentence now starts indented.
    # Lists, capital headings and lines after a colon are left flush; a line
    # of stars ("★★", the OCR's section divider) is not drawn.
    import importlib
    sys.path.insert(0, str(_PIPELINE))
    try:
        R = importlib.import_module("_render")
    except ImportError:                     # needs PyMuPDF: runs in the image
        return
    ind = lambda text: [round(l.indent) for l in R.layout(text, 400, 9) if not l.gap]
    assert ind("He will return to guard.\nNo attacks yet.") == [0, round(9 * R.PARA_INDENT_EM)]
    assert ind("For example:\nfor the challenge, loss of office;") == [0, 0]
    assert ind("The lesson ends.\nCHAPTER II") == [0, 0]
    assert ind("The lesson ends.\n1° Lunge.\n2° Recover.") == [0, 0, 0]
    assert ind("Orchid, Dancer\nThe Golden Waltz") == [0, 0]
    assert ind("First part.\n\n★★\n\nSecond part.") == [0, 0]


def test_header_map_covers_two_part_heads_either_way_round():
    # Kirchhoffer prints "LA MISE EN GARDE / FLEURET" on left-hand pages:
    # the reversed head reuses the stored English, halves swapped
    import importlib, json, tempfile
    sys.path.insert(0, str(_PIPELINE))
    try:
        R = importlib.import_module("_render")
    except ImportError:
        return
    with tempfile.TemporaryDirectory() as d:
        json.dump({"FLEURET\tLA MISE EN GARDE": "FOIL\tON-GUARD POSITION", "ESCRIME.": "FENCING."},
                  open(f"{d}/headers.json", "w"))
        h = R.header_map(d)
        assert h["LA MISE EN GARDE\tFLEURET"] == "ON-GUARD POSITION\tFOIL"
        assert h["ESCRIME."] == "FENCING." and len(h) == 3


def test_refusal_ready_to_translate():
    # Rossi gold run: the whole reply on a near-empty page was the model
    # asking for text -- it must count as "nothing translated"
    from _rules import TX
    is_ref = TX["looks_like_refusal"]
    assert is_ref("I am ready to translate the text. Please provide the source text")
    assert is_ref("Please provide the source text you would like translated.")
    assert not is_ref("I am ready, said the fencer, to translate theory into practice.")


def test_verify_captions_verdicts():
    # 2026-10-07: the second, caption-only reading confirms a recorded caption
    # when one of its readings (or its lines joined) names the same caption
    import importlib
    sys.path.insert(0, str(_PIPELINE))
    V = importlib.import_module("verify_captions")
    # Masiello Sciabola scan 32: a description vs the printed caption
    assert V.verdicts(["Immagine di una mano impugnando una sciabola in posizione normale"],
                      ["Modo normale d'impugnare la sciabola."]) == ["unconfirmed"]
    assert V.verdicts(["Two fencers demonstrating a half-circle movement"], []) == ["unconfirmed"]
    # Tiboldi scan 44: printed, read again with small differences
    assert V.verdicts(["Fig. 20. — Diagramma delle parate semplici orizzontali."],
                      ["Fig. 20. - Diagramma delle parate semplici orizzontali"]) == ["printed"]
    # Edom scan 39: the labels of one figure, read one per line
    assert V.verdicts(["Prime — Tierce — Seconde — Quinte"], ["Prime", "Tierce", "Seconde", "Quinte"]) == ["printed"]
    assert V.verdicts(["Fig. 3. — Épée à pointe d'arrêt.", "Fig. 4. — Le masque."],
                      ["Fig. 3. Épée à pointe", "Fig. 4. — Le masque."]) == ["printed", "printed"]
    assert V._lines("NONE") == [] and V._lines("Fig. 1.\nFig. 2.") == ["Fig. 1.", "Fig. 2."]
    # where to read: caption boxes; around a recorded picture with none
    page = {"boxes": [["picture", 0.96, [0.2, 0.4, 0.4, 0.7]], ["caption", 0.7, [0.26, 0.72, 0.37, 0.74]],
                      ["caption", 0.5, [0.26, 0.72, 0.37, 0.74]]]}
    assert V.regions(page, True) == [("caption", (0.26, 0.72, 0.37, 0.74))]
    assert V.regions({"boxes": [["picture", 0.9, [0.2, 0.4, 0.4, 0.7]]]}, True)[0][0] == "around picture"
    assert V.regions({"boxes": [["text", 0.9, [0, 0, 1, 1]]]}, False) == []


def test_caption_descriptions_dropped_and_junk_cleaned():
    # 2026-10-07: step 1 sometimes recorded the OCR model's description of a
    # picture as its caption, or left its prompt / an image tag on a caption
    import importlib
    sys.path.insert(0, str(_PIPELINE))
    try:
        R = importlib.import_module("_render")
    except ImportError:
        return
    for d in ("Two fencers demonstrating a parry and counterattack",          # Barbasetti
              "Due fencers demonstrating the fourth parry",
              "Immagine di una mano impugnando una sciabola in posizione normale",  # Masiello
              "Diagram showing a triangle with labeled points P, F', and F",
              "Figure 1: A drawing of a person in a fencing stance",          # Robaglia
              "A man in a fencing stance with his right leg forward and left leg back"):
        assert R.describes_picture(d), d
    for c in ("Fig. 55. — Figure théorique. — Montrant que dans la fente",   # Dubois: printed
              "Molinello di testa a sinistra", "Le gant", "Veste. Masque.",
              "PREMIER MOUVEMENT DE LA MISE EN GARDE.",
              "Fig. 87. — Application de notre technique par deux de mes élèves",
              "Due schermatori in posizione di legamento"):   # ambiguous: kept, not guessed
        assert not R.describes_picture(c), c
    assert R.clean_caption("Fig. 11 -- the picture is essentially the whole page (little or no running text)") == "Fig. 11"
    assert R.clean_caption("FIG. 35. Velocimetro schermistico Rossi. — ![Vel") == "FIG. 35. Velocimetro schermistico Rossi."
    assert R.clean_caption("bassa. — ![x](a.png) — (1) Chiamasi") == "bassa. — (1) Chiamasi"
    assert R.figure_caption({"caption": "Two fencers demonstrating a parry", "caption_en": "Two fencers…"}) == ""
    assert R.figure_caption({"caption": "Two fencers demonstrating x", "decided": True}) == "Two fencers demonstrating x"
    assert R.figure_caption({"caption": "Le gant", "caption_en": "The glove"}) == "The glove"


def test_join_across_an_honorific():
    # Jacob 25/26: "... qui m'en diminueront le poids, MM." | "Anatole de La
    # Forge et Ranc. A trois ..." -- "MM." is not a full stop; 13 such breaks
    # in 11 books (Baudry 61 "nous dit M." | "Lafaugère ...")
    p = [{"body": "si je ne le partageais avec deux hommes qui m'en diminueront le poids, MM."},
         {"body": "Anatole de La Forge et Ranc.\nA trois on peut faire ce que seul on n'oserait pas."}]
    assert join_split_sentences(p) == 1
    assert "MM. Anatole de La Forge et Ranc." in p[0]["body"] + p[1]["body"]
    # a real sentence end before a capital is still left alone
    p = [{"body": "Il tirait au corps."}, {"body": "Anatole arriva."}]
    assert join_split_sentences(p) == 0

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
