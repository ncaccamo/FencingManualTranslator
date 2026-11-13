#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Tests for the term tables (pipeline/glossary_*.toml, _glossary_table.py).

    python3 evals/test_glossary_table.py

The first two hold the tables to the prompt files while both exist: a term
decision changed in one and not the other fails here.
"""
from pathlib import Path
import sys

PIPELINE = Path(__file__).resolve().parent.parent / "pipeline"
if not PIPELINE.is_dir():                  # in the image: evals at /data/evals, pipeline at /app
    PIPELINE = Path("/app")
sys.path.insert(0, str(PIPELINE))

import _glossary_table as G  # noqa: E402

FR = (PIPELINE / "translation_prompt.txt").read_text(encoding="utf-8").strip()
IT_PART = (PIPELINE / "translation_prompt_it.txt").read_text(encoding="utf-8").strip()


def italian_prompt() -> str:
    """As _translate.prompt_for_language("Italian") builds it."""
    rules, _, examples = IT_PART.partition("=== EXAMPLES ===")
    a, b = FR.index("2. KEEP"), FR.index("4. Headings")
    p = FR[:a] + rules.strip() + "\n\n" + FR[b:]
    c, d = p.index("EXAMPLES\n"), p.index("Text to translate:")
    return p[:c] + "EXAMPLES\n\n" + examples.strip() + "\n\n" + p[d:]


PROMPT = {"French": FR, "Italian": italian_prompt()}


def _table_entries(lang):
    return [("note" if t["kind"] == "note" else t["kind"], t["line"])
            for t in G.load(lang) if t["kind"] != "keep"]


def test_tables_hold_every_prompt_entry_in_order():
    for lang in PROMPT:
        want, got = G.prompt_entries(PROMPT[lang]), _table_entries(lang)
        for i, (w, g) in enumerate(zip(want, got)):
            assert w == g, f"{lang} entry {i}: prompt {w!r} != table {g!r}"
        assert len(want) == len(got), f"{lang}: prompt has {len(want)} entries, table {len(got)}"


def test_whole_table_rebuilds_rules_3_and_3b():
    """Every entry given back -> the same entries the prompt has, and the
    rest of the prompt untouched."""
    for lang in PROMPT:
        terms = G.load(lang)
        p = PROMPT[lang]
        a, b = p.index("3. TRANSLATE"), p.index("4. Headings")
        rebuilt = p[:a] + G.glossary_block(p, terms) + p[b:]
        # the wording, in order (an entry with no "→" of its own, alone on its
        # line now, reads as a remark to prompt_entries: kinds are the test above)
        assert [e for _, e in G.prompt_entries(rebuilt)] == [e for _, e in G.prompt_entries(p)]
        assert rebuilt[:a] == p[:a] and rebuilt.endswith(p[b:])


def test_kept_terms_of_rule_2_are_all_in_the_table():
    for lang in PROMPT:
        p = PROMPT[lang]
        flat = lambda x: G.norm(x).replace("-", " ")
        rule2 = flat(p[p.index("2. KEEP"):p.index("3. TRANSLATE")])
        for t in G.load(lang):
            if t["kind"] == "keep":
                assert any(flat(f).rstrip("*") in rule2 for f in t["source"]), t["id"]


def test_source_forms_match_printed_forms():
    fr, it = G.load("French"), G.load("Italian")
    by = lambda terms, i: next(t for t in terms if t["id"] == i)
    yes = [(fr, "tirer", "Tirez droit."), (fr, "coup-droit", "DES COUPS DROITS"),
           (fr, "degagement", "le dégage-\nment de quarte"), (fr, "epee-blade", "l’EPEE"),
           (fr, "tac-au-tac-kept", "riposte du tac-au-tac"), (fr, "or", "la parade. Or, le tireur"),
           (fr, "ne-plus-que", "il n'a plus qu'à riposter"), (fr, "tierce-basse", "en quarte haute"),
           (it, "colpo", "i colpi semplici"), (it, "attacco", "gli attacchi"), (it, "tempi-count", "in due tempi"),
           (it, "line-numerals", "parata di 3.a"), (it, "affondo", "andare a fondo")]
    no = [(fr, "fer", "de pied ferme"), (fr, "or", "il est fort, or le tireur"), (fr, "contre", "à l'encontre de"),
          (it, "filo-edge", "il profilo"), (it, "arma", "armonia")]
    for terms, i, text in yes:
        assert G.on_page(by(terms, i), text), (i, text)
    for terms, i, text in no:
        assert not G.on_page(by(terms, i), text), (i, text)


def test_page_prompt_carries_only_the_pages_entries():
    p = PROMPT["French"]
    out = G.page_prompt(p, "French", "Sur un battement, parez quarte et ripostez par un liement.")
    assert "battement → beat" in out and "liement → bind" in out
    assert "soie (of a blade)" not in out and "bourrer →" not in out and "ordinary words these books" not in out
    a, b = p.index("3. TRANSLATE"), p.index("4. Headings")
    assert out.startswith(p[:a]) and out.endswith(p[b:])          # rules 0-2 and 4 onward word for word
    assert "In a HEADING (rule 4) translate the term but omit the <fn> tag." in out   # a note travels with its entries
    assert out == G.page_prompt(p, "French", "Sur un battement, parez quarte et ripostez par un liement.")
    none = G.page_prompt(p, "French", "Table des matières.")
    assert none.count("(none of these terms is on this page)") == 2


def test_checks_read_the_source_strictly_and_leave_tables_out():
    fr, it = G.load("French"), G.load("Italian")
    by = lambda terms, i: next(t for t in terms if t["id"] == i)
    d, t3, tempi = by(fr, "double-kept"), by(it, "terza-kept"), by(it, "tempi-count")
    # choosing prompt entries is loose; a check is not
    assert G.on_page(d, "un double contre") and not G.check_applies(d, "un double contre")
    assert G.check_applies(d, "Le doublé de quarte") and not G.check_applies(d, "une veste doublée de toile")
    assert G.on_page(t3, "il terzo capitolo") and not G.check_applies(t3, "il terzo capitolo")
    assert G.check_applies(t3, "parate di terza e di quarta")
    # a kept term: missing, or there in any form; accents count
    assert G.kept_missing(d, "Make the double in quarte.") and not G.kept_missing(d, "Make the <i>doublé</i> in quarte.")
    assert not G.kept_missing(by(fr, "riposte-kept"), "he <i>riposted</i> at once")
    # forbidden forms, with English endings; a pattern replaces the bare words
    assert G.forbidden_found(by(fr, "coup-botte"), "two blows of the fist") == ["blows"]
    assert G.check_applies(tempi, "Si eseguisce in due tempi") and not G.check_applies(tempi, "a quei tempi")
    assert G.forbidden_found(tempi, "executed in two times") == ["two times"]
    assert G.forbidden_found(tempi, "at the same time, in two tempi") == []
    # a table's cells are not on the English page
    assert "cavazione" not in G.running_text("Testo.<table><tr><td>cavazione</td></tr></table> Fine.")


def test_page_entries_at_the_end_leave_one_start_for_every_page():
    for lang, one, two in (("French", "Sur un battement, parez quarte.", "La soie de la lame; rompez la mesure."),
                           ("Italian", "Parata di quarta e filo al petto.", "Il legamento di terza, a fondo.")):
        p = PROMPT[lang] + "\n\nBOOK-SPECIFIC TERMS -- for this book these override the lists above:\n   - x → y"
        a, b = G.page_prompt(p, lang, one, at_end=True), G.page_prompt(p, lang, two, at_end=True)
        mark = "TERMS ON THIS PAGE\n\n"
        assert a.count(mark) == 1 and a.split(mark)[0] == b.split(mark)[0]        # the part the server can reuse
        head, tail = a.split(mark)
        assert "3. TRANSLATE" not in head and "3 and 3b." in head and "EXAMPLES" in head
        assert tail.index("3. TRANSLATE") < tail.index("3b. TRANSLATE") < tail.index("Text to translate:") < tail.index("BOOK-SPECIFIC TERMS")
        # the same entries as the in-place form
        inplace = G.page_prompt(p, lang, one)
        assert [e for _, e in G.prompt_entries(inplace)] == [e for _, e in G.prompt_entries(tail.split("Text to translate:")[0] + "4. Headings")]


def test_a_books_own_terms_outrank_the_table():
    fr, it = G.load("French"), G.load("Italian")
    by = lambda terms, i: next(t for t in terms if t["id"] == i)
    jeu, maestro = by(fr, "jeu"), by(it, "maestro-kept")
    jacob = {"le jeu de l'épée": "the game of the <i>épée</i>"}
    found = lambda en, terms: G.forbidden_found(jeu, G.without_book_terms(jeu, en, terms))
    assert found("THE GAME OF THE ÉPÉE", {}) == ["game"]
    assert found("THE GAME OF THE ÉPÉE", jacob) == []                               # the book's own phrase
    assert found("his game is irregular", jacob) == ["game"]                        # «jeu» elsewhere in that book
    assert found("THE GAME OF THE ÉPÉE", {"fausse attaque": "the game of the épée"}) == ["game"]   # another word's line
    # a remark after the rendering is not part of it
    assert G._book_rendering("thrust -- Gomard's defined term, the movements") == "thrust"
    # a kept term the book translates: the key is the entry's own word
    assert G.book_translates(maestro, {"maestro": "master"})
    assert not G.book_translates(maestro, {"maestro": "<i>maestro</i> (as printed)"})
    assert not G.book_translates(by(fr, "epee-kept"), jacob)                        # a phrase is not the word itself


def test_code_lists_come_from_the_tables_in_a_safe_order():
    """The kept and footnoted lists step 2's code corrections use are built
    from the tables (code_lists). They are applied in table order, so a form
    that contains another as whole words must come first ("en cavant" before
    "cavant", "pointes d'arrêt" before nothing shorter that it holds)."""
    import re
    for lang in PROMPT:
        footnoted, note_under, not_before, kept, model_notes = G.code_lists(lang)
        assert footnoted and kept
        for forms in ([f for f, _ in footnoted], [t for t, _ in kept]):
            assert len(forms) == len(set(forms)), f"{lang}: a form is listed twice"
            for i, a in enumerate(forms):
                for b in forms[:i]:
                    assert not re.search(r"(?<![\w-])" + re.escape(b) + r"(?![\w-])", a) or a == b, \
                        f"{lang}: {a!r} holds {b!r} and must be listed before it"
        assert all(u for u in note_under.values())
    fr = G.code_lists("French")
    assert ("liement", "bind") in fr[0] and fr[1]["barrés"] == "barré" and "froissement" in fr[2]
    assert ("riposte", ["rapier(?:[- ]?(?:rapier|response|riposte|reply))?", "riposte response", "rapises?"]) in fr[3]
    assert "se loger" in fr[4] and ("déroulé", "glide") in fr[0] and fr[1]["déroulé"] == "coulé"
    it = G.code_lists("Italian")
    assert ("legamento", "engagement") in it[0] and ("cavazione", []) in it[3] and "filo" in it[4]
    # the modules step 2 imports hold the same lists
    import _glossary_fr, _glossary_it
    assert _glossary_fr._FOOTNOTED_FR == fr[0] and _glossary_it._KEEP_TERMS_IT == it[3]


def test_a_faulty_table_stops_the_load():
    import tempfile, os
    bad = ['[[term]]\nid = "a"\nkind = "translate"\nsource = ["x"]\nline = "x → y"\nsurce = ["z"]\n',     # unknown field
           '[[term]]\nid = "a"\nkind = "translate"\nline = "x → y"\n',                                   # nothing to match
           '[[term]]\nid = "a"\nkind = "keep"\nsource = ["x"]\n[[term]]\nid = "a"\nkind = "keep"\nsource = ["y"]\n']
    for text in bad:
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False, encoding="utf-8") as fh:
            fh.write(text)
        try:
            G.load("French", fh.name)
            raise AssertionError(f"loaded: {text!r}")
        except G.GlossaryError:
            pass
        finally:
            os.remove(fh.name)


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in tests:
        try:
            f()
            print(f"  ok   {n}")
        except Exception as e:                                   # noqa: BLE001
            failed += 1
            print(f"  FAIL {n}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
