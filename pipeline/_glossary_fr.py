# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""French terms as the code handles them, alongside the glossary in
translation_prompt.txt. A term decision usually means a line in the prompt
and a line here: the footnoted terms (translated, "French: <term>" note at
first use), the always-translated words, the kept terms (left in French, in
italics) with the mistranslations snapped back to them -- all applied by
_tx_terms.py / _tx_notes.py after the model -- and the review report's
checks for renderings against the glossary (quality_report.py). A book's
own choices go in its overrides file's [terms], not here."""

# The kept and the footnoted terms are held in the term table
# (glossary_fr.toml: fields forms / refs / model_notes / not_before /
# enforce, described at the top of that file) and read from it here, so a
# term's status is decided in one place. Before 2026-11-12 the lists were
# written out in this file and had drifted from the prompt (dérobement was
# footnoted here and plain there).
#
#   _FOOTNOTED_FR          [(French form, English)]: translate to the English
#       rendering, and on the FIRST occurrence in a book mark it as
#       "English<fn>Fr. <term></fn>" -- a bare source reference, never an
#       explanatory gloss. The model does this from the prompt;
#       enforce_footnoted_terms() is the backstop for when it leaves the term
#       in French or forgets the note. A longer form goes before a shorter one
#       it contains ("en cavant" before "cavant").
#   _FOOTNOTE_REF_FR       the singular each form is noted under ("French:
#       appel"), so a plural ("appels") doesn't produce a second note
#   _FOOTNOTED_NOT_BEFORE  a footnoted term inside an ordinary phrase is not the
#       fencing action: "froissement d'amour-propre" is hurt pride, not a graze
#       (Jacob 1887, 2026-10-13)
#   _KEEP_TERMS_FR         kept terms the code puts back in italics, each with the
#       English mistranslations snapped back to it (see below)
#   _FOOTNOTED_FR_REFS     footnoted terms left to the model (accepted as source
#       references in its <fn> notes) -- ordinary words elsewhere, so the
#       backstop must not rewrite them
#
# Why some of them read as they do (reviewer's choices):
#   caver was "to drop the point" until 2026-10-11 -- wrong: the books define it
#     as the opposite of opposition, an angled thrust (Kirchhoffer, Joseph-Renaud
#     scan 144). barré: Robaglia's "croisé de seconde ... chasse vigoureusement le
#     fer"; coup fourré = coup double (Kirchhoffer's glossary).
#   bourrer: drawing the arm back and driving the point in hard (Joseph-Renaud
#     pdf 290, Kirchhoffer pdf 61, Cordelois); larder: in-and-out jabs.
#     2026-10-31 (were shove / pressing / larding).
#   picoteur, tendeur à outrance, bricoleur: fencer types and slang, 2026-10-31
#     (terms questionnaire).
#   riposte / remise / reprise: normally kept by the model, but a bare heading
#     ("DE LA RIPOSTE.") gets mangled -- "OF THE RAPIER RAPIER"; "PARADES ET
#     RIPOSTES" -> "PARRIES AND RAPISES" is the same failure.
#   pointe d'arrêt: kept, never "stop-point(s)" (Joseph-Renaud pdf 46,
#     Kirchhoffer pdf 39).
#   doublé / dédoublé: kept in French (2026-10-13: the books explain them;
#     Prévost-Jollivet had seven renderings).
#   tac-au-tac: parry and riposte in one instant -- kept in French in English
#     fencing writing. Qwen3.8 rendered it word for word ("FROM THE TAC TO THE
#     TAC", Gomard scan 239); older output left it bare / unitalic.
from _glossary_table import code_lists as _code_lists

(_FOOTNOTED_FR, _FOOTNOTE_REF_FR, _FOOTNOTED_NOT_BEFORE,
 _KEEP_TERMS_FR, _FOOTNOTED_FR_REFS) = _code_lists("French")

# Rule-3 "always translate" terms the old 7B model dropped inconsistently (leaves
# "an arm called a fleuret" in the body, "ENTRAINEMENT" as a header).
# Only unambiguous ones -- these have no other meaning in this corpus.
# Longest first so "fleurettiste" wins before "fleuret".
_ALWAYS_TRANSLATE = [
    # "LISTE DES OUVRAGES" -> "LISTE OF WORKS" (Gomard bibliography section
    # header): "DES OUVRAGES" translates fine but bare "LISTE" survives
    # untranslated on this specific isolated header string, even though
    # the SAME word translates correctly as part of the two-part header
    # "LISTE DES OUVRAGES\tCONSULTÉS." -- a one-off model quirk on the
    # shorter isolated string, consistent enough across sampling that
    # majority-vote alone doesn't catch it.
    (r"listes", "lists"), (r"liste", "list"),
    (r"fleurettistes", "foil fencers"), (r"fleurettiste", "foil fencer"),
    (r"fleurets", "foils"), (r"fleuret", "foil"),
    # "contre-" as a compound prefix = "counter-", not "against-"
    (r"contre-dégagements", "counter-disengagements"),
    (r"contre-dégagement", "counter-disengagement"),
    (r"contre-dégagez", "counter-disengage"), (r"contre-dégager", "counter-disengage"),
    # equipment / material vocab the model hedges on and leaves in French
    (r"crispins", "cuffs"), (r"crispin", "cuff"),
    (r"cuissards", "thigh guards"), (r"cuissard", "thigh guard"),
    (r"schémas", "diagrams"), (r"schéma", "diagram"),
    (r"entra[iî]nements", "training sessions"), (r"entra[iî]nement", "training"),
    (r"escrimes", "fencing"), (r"escrime", "fencing"),
    (r"sociétés", "societies"), (r"société", "society"),
    (r"divers", "miscellaneous"),
    (r"demi-fentes", "half-lunges"), (r"demi-fente", "half-lunge"),
    (r"fentes", "lunges"), (r"fente", "lunge"),
    # "leçon d'assaut" = a free-play lesson; the old 7B model collapses it to just
    # "the bout" (drops "leçon"). Bare "assaut" -> "bout" is left to the
    # prompt (rule 3) -- "assaut" alone is sometimes "assault" in a duel.
    (r"leçons d['’]assaut", "bout lessons"), (r"leçon d['’]assaut", "bout lesson"),
    # "fer" (the opponent's steel) -> "the blade" (Category 1). NOT bare
    # "fer": leave "prise de fer" / "sentiment du fer" for their own
    # handlers (Cat 3 / Cat 2).
    (r"attaques au fer", "attacks on the blade"), (r"attaque au fer", "attack on the blade"),
    (r"sur le fer", "on the blade"), (r"au fer", "on the blade"),
    (r"le fer", "the blade"),
    # "l'escrime au fleuret" -> "foil fencing"; the old 7B model renders it
    # "épée fencing with the fleuret" / "épée fencing with the foil".
    (r"escrime au fleuret", "foil fencing"), (r"au fleuret", "with the foil"),
    (r"escrime à l['’]épée", "épée fencing"), (r"à l['’]épée", "with the épée"),
    # "au bout d'épée" -- idiom for a bout fenced with the épée, not a
    # literal "at the point of the sword"; the old 7B model renders it word-for-word.
    (r"au bout d['’]épée", "an épée bout"),
    # "un assaut d'épée" is just an épée bout -- there is no "leçon" in it,
    # but the old 7B model pulls "lesson" in from the leçon-d'assaut rule and writes
    # "a bout lesson with the épée". Repair the output.
    (r"a bout lesson with the épée", "an épée bout"),
    (r"bout lesson with the épée", "épée bout"),
]

# Terms the model is known to render against the glossary. (source regex,
# wrong-English regex, what it should be). Only fires when the SOURCE has
# the term, so a legitimate English word elsewhere is left alone.
TERM_RULES = [
    # coup = the whole offensive action -> attack (or hit: the touch that
    # lands); never "blow" (operator decision 2026-09-28, replacing the
    # pass-8 "blow"). botte = its finish -> thrust (Gomard pdf 158).
    # "throws" only as a noun for coups ("the throws", "simple throws", a
    # THROWS heading) -- "he throws himself onto the point" is correct English
    (r"\bcoups?\b", r"(?:\b(?:the|these|those|such|all|two|three|simple|compound|straight|single|of)"
     r"\s+throws\b|\b(?-i:THROWS)\b|\b(?:simple|compound) throw\b|"
     # "blow" is right where coup isn't the fencing action: hammer blows
     # (La Boëssière's mounting chapter), "coups et blessures" (the legal
     # "blows and wounds"), a coup sec on the blade ("a sharp blow"), a bull's
     # horn, "coups du hasard"
     r"(?<!hammer )(?<!sharp )(?<!clean )(?<!light )\bblows?\b"
     r"(?!\s+(?:of\s+(?:the\s+)?(?:hammer|horn|chance)|and\s+wounds|on\s+the\s+(?:opponent|blade)|with\s+a\s+(?:stick|cane))))",
     "coup → attack"),
    (r"\bbottes?\b", r"\bthrusts?\s*/\s*hits?\b", "botte → thrust"),
    # a thrust is not a cut, and a cut is not a "blow" (Gomard pdf 43,
    # Vauresmont pdf 115: "point cut", "cutting blow"; 2026-10-02)
    (r"\bcoups? de pointe\b", r"\bpoint[- ]cuts?\b", "coup de pointe → thrust"),
    (r"\bcoups? de (?:taille|tranchant)\b", r"\bcutting blows?\b", "coup de taille → cut"),
    # "stiffly" belongs only to the slang tendre la perche / la broche; plain
    # tendre (le bras, le fer, l'épée) is extend (Gomard, Jacob-André
    # 2026-11-01). «raide» is stiff in its own right
    (r"^(?![\s\S]*\b(?:perche|broche|raid|roid))[\s\S]*\bt[eé]nd", r"\bstiffly\b",
     "tendre → extend; stiffly only for tendre la perche / la broche"),
    (r"\bplanches?\b", r"\bplanks?\b", "planche → plate"),
    (r"\btac[- ]au[- ]tac\b", r"\btac[- ]to[- ]the[- ]tac\b", "du tac au tac → tac-au-tac"),
    (r"\brompre|\brompez|\brompu", r"\bromp(?!re\b|ez\b|u\b)", "rompre → break ground"),
    # of a blade only -- "fil de soie" is real silk thread
    (r"\bsoie\b", r"\bsilk\b(?=.{0,40}\b(?:blade|hilt|handle|guard|pommel|grip)\b)"
     r"|\b(?:blade|hilt|handle|guard|pommel|grip)\b.{0,40}\bsilk\b", "soie → tang"),
]
