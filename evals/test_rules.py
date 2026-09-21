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
    enforce_cat3_footnotes, pull_footnotes, fix_subheading_spacing, is_toc_page,
    repair_mangled_terms, contains_too_much_non_english,
    match_boundary_punctuation, deshout, _phrase_repairs,
    parse_figures_block, reconcile_page_numbers, _fold_caption_only_body,
    strip_leaked_headmatter,
    leaked_top_header, is_plate_leaf,
    extract_source_footnotes, label_margin_notes,
    _clean_caption, _capitalize_definition_entries, fix_parry_names,
    salvage_leaked_folio_header, dehyphenate_across_pages,
    drop_google_boilerplate, mark_handwritten_inscriptions,
    drop_scan_apparatus, fold_caption_header_into_figure, reconcile_headers,
    recover_plate_captions, parse_vlm_response, clean_vlm_extraction,
    repair_source_ocr_misreads, restore_dropped_part_title,
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
    assert reconcile_page_numbers(["14", "", "16"]) == ["14", "15", "16"]


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


def test_label_margin_notes():
    # A detected margin note is removed from body but NOT rendered
    # anywhere (2026-09-18: showing only the single best-guess candidate
    # when a page can carry more than one left the reader unable to tell
    # which printed note it corresponded to -- simplest honest fix is to
    # just not claim to reproduce it).
    txt = ("The main body paragraph goes on for a while about the left hand "
           "and its uses in a serious affair.\n\n"
           "A second full paragraph of ordinary prose so the page reads as "
           "prose-dominant and not just a caption with a stray line.\n\n"
           "The lesson must teach in what cases one can use the left hand.")
    out = label_margin_notes(txt)
    assert "[margin note]" not in out
    assert "The lesson must teach" not in out
    assert "___FOOTNOTES___" not in out


def test_label_margin_notes_skipped_on_figure_page():
    # Realistic-length filler paragraphs -- a toy one-sentence filler like
    # "A second paragraph." is itself short enough to accidentally look
    # note-shaped now that the search checks the last 2 paragraphs, not
    # just the last 1; real translated prose doesn't run that short.
    txt = ("The main body paragraph goes on for a while about the left hand "
           "and its uses in a serious affair.\n\n"
           "A second full paragraph of ordinary prose so the page reads as "
           "prose-dominant and not just a caption with a stray line.\n\n"
           "Parry of septime.")
    assert label_margin_notes(txt, has_fig=True) == txt
    # even without has_fig, a caption-shaped trailing line is not a note
    assert "[margin note]" not in label_margin_notes(txt)


def test_label_margin_notes_skips_page_boundary_continuation():
    # (P-review D) a paragraph the page boundary cut off mid-sentence --
    # ends on a comma, continues on the next page -- is not a standalone
    # shoulder note, even though it's short enough to look like one.
    txt = ("The main body paragraph goes on for a while about the left hand "
           "and its uses in a serious affair.\n\n"
           "A second full paragraph of ordinary prose so the page reads as "
           "prose-dominant and not just a caption with a stray line.\n\n"
           "To move from the second to the third position: extend the arm, "
           "the hand in supination,")
    assert label_margin_notes(txt) == txt


def test_label_margin_notes_skips_continuation_with_no_terminal_punctuation():
    # A comma-ending check alone missed most of these -- a paragraph cut
    # off mid-word/mid-clause with NO trailing punctuation at all (a
    # corpus scan found 40/67 tagged notes like this, e.g. Gomard "One
    # breaks the distance to disengage from an enemy"). A genuine shoulder
    # note is a complete remark: require the sentence to actually end.
    txt = ("The main body paragraph goes on for a while about the left hand "
           "and its uses in a serious affair.\n\n"
           "A second full paragraph of ordinary prose so the page reads as "
           "prose-dominant and not just a caption with a stray line.\n\n"
           "One breaks the distance to disengage from an enemy")
    assert label_margin_notes(txt) == txt


def test_label_margin_notes_prefers_note_that_precedes_more_prose():
    # Real Gomard src 32 example: two paragraphs are EQUALLY note-shaped
    # (both short, both end a sentence, neither uppercase-heavy) -- shape
    # alone can't tell them apart. The genuine note, "He recognizes only
    # three ways of striking.", sits one paragraph before the end, with a
    # real closing paragraph still following it; the old "just check the
    # last paragraph" logic wrongly grabbed that closing paragraph instead
    # and left the actual note sitting in the body. A margin note
    # introduces what follows on the page -- it's essentially never the
    # true last paragraph -- so the second-to-last candidate must win.
    txt = ("A long opening paragraph establishing the guard positions in "
           "enough words to read as real prose and not a stray caption.\n\n"
           "A second full paragraph of ordinary prose so the page reads as "
           "prose-dominant and not just a caption with a stray line.\n\n"
           "A third paragraph continuing the discussion at some length so "
           "the page still reads as prose-dominant overall, definitely.\n\n"
           "He recognizes only three ways of striking.\n\n"
           "Like Marozzo, his cuts are directed at the shoulder, chest, "
           "flank, and calf; his thrusts prefer the face.")
    out = label_margin_notes(txt)
    # dropped from body, not rendered (2026-09-18) -- but confirms the
    # RIGHT paragraph was the one identified and removed
    assert "[margin note]" not in out
    assert "He recognizes only three ways of striking." not in out
    assert "Like Marozzo" in out   # the real closing paragraph stays
    assert "Like Marozzo" in out.split("___FOOTNOTES___")[0]   # stays in body


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
    # 24 matches the run's projection -> promoted. The misread "12" sits in
    # a tight EXACT single-slot gap (22 .. 24) -> forced to the real folio.
    assert reconcile_page_numbers(["20", "21", "22", "12", "24"]) \
        == ["20", "21", "22", "23", "24"]
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
    out = strip_leaked_headmatter("Body text continues for a while.\n\nESCRIME.", set())
    assert out == "Body text continues for a while."


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
    # in the book; reorder onto the canonical root-first form.
    parsed = (
        [{"header": "FLEURET", "body": "p" + str(i), "figures": []} for i in range(4)]
        + [{"header": "FLEURET\tLA MISE EN GARDE", "body": "", "figures": []},
           {"header": "LA MISE EN GARDE FLEURET", "body": "", "figures": []}]
    )
    reconcile_headers(parsed)
    assert parsed[4]["header"] == "FLEURET\tLA MISE EN GARDE"
    assert parsed[5]["header"] == "FLEURET\tLA MISE EN GARDE"


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
    assert parsed[6]["header"] == "FLEURET\tLA MISE EN GARDE"
    assert parsed[7]["header"] == "FLEURET\tLA MISE EN GARDE"


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
