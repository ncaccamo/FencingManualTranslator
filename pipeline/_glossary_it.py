# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Italian terms as the code handles them, alongside the glossary in
translation_prompt_it.txt. A term decision usually means a line in the
prompt and a line here: the footnoted terms (translated, "Italian: <term>"
note at first use), the kept terms (left in Italian, in italics) -- applied
by _tx_terms.py / _tx_notes.py after the model -- and the review report's
checks for renderings against the glossary and for translated words left in
Italian (quality_report.py). A book's own choices go in its overrides
file's [terms], not here."""

# The kept and the footnoted terms are held in the term table
# (glossary_it.toml) and read from it here; see _glossary_fr.py for what
# each list is.
#   _FOOTNOTED_IT: "filo" (glide) is left to the model -- as a bare word it
#     also means the blade's edge, so the backstop must not rewrite it.
#   _KEEP_TERMS_IT (translation_prompt_it.txt rule 2): only words that never
#     occur as ordinary English -- the hand positions and Maestro are left to
#     the model. riposte: kept in the body, but an isolated heading can garble
#     it (the French "RAPISES"; "RIPONSES" is fixed in fix_known_garbles).
#   _FOOTNOTED_IT_REFS: accepted as source references in the model's <fn>
#     notes, beyond _FOOTNOTED_IT.
from _glossary_table import code_lists as _code_lists

(_FOOTNOTED_IT, _FOOTNOTE_REF_IT, _FOOTNOTED_NOT_BEFORE_IT,
 _KEEP_TERMS_IT, _FOOTNOTED_IT_REFS) = _code_lists("Italian")


# Italian books only (glossary 2026-09-30, translation_prompt_it.txt) -- a
# French book has its own "volant" (garde volante) and quotes Italian titles
# in its bibliography. colpo: the strike -> hit, the whole action ->
# attack; never "blow" (same exceptions as coup)
TERM_RULES_IT = [
    (r"\bcolp[oi]\b|\burt[oi]\b", r"(?<!hammer )(?<!sharp )(?<!clean )(?<!light )\bblows?\b"
     r"(?!\s+(?:of\s+(?:the\s+)?(?:hammer|horn|chance)|and\s+wounds))", "colpo → hit / attack"),
    # misura -> measure; "distance" only where the book says distanza
    (r"^(?![\s\S]*\bdistanz)[\s\S]*\bmisura\b", r"\bdistances?\b", "misura → measure"),
    (r"\bsciabol", r"\bsabres?\b", "sciabola → saber (US spelling)"),
    (r"\bsciabolat", r"\bsab(?:er|re)[- ](?:blows?|strokes?)\b", "sciabolata → cut"),
    (r"\bcolp[oi] di punta\b", r"\bpoint[- ]cuts?\b", "colpo di punta → thrust"),
    (r"\bcolp[oi] di taglio\b", r"\bcutting blows?\b", "colpo di taglio → cut"),
    (r"\bpicch?i?o\b|\bpicco\b", r"\b(?:pecks?|knocks?|woodpeckers?)\b", "picchio → beat"),
    # except "azioni volanti sul ferro" -> "flying actions on the blade"
    # (glossary 2026-10-11; Barbasetti scan 90) and "punta volante" -> "flying
    # point", as the French books' "pointe volante" (Masiello 1887 scan 129)
    (r"\bvolant[ei]\b", r"\bflying\b(?!\s+(?:<i>)?cartocc)(?!\s+points?\b)(?![^.;]{0,30}\bon\s+the\s+blade\b)",
     "volante → off the blade"),
    (r"\bfil[oi]\b", r"\b(?:threads?|wires?)\b", "filo → glide / edge"),
    (r"\brispost[ae]\b", r"\b(?:responses?|repl(?:y|ies)|answers?)\b(?!\s+to\s+(?:the|his|a)\s+letter)",
     "risposta → riposte"),
    # From the meaning checks of Masiello 1887 / Barbasetti (2026-11-02):
    # rules the prompt states that the model still broke. tempo COUNTING the
    # movements ("al primo tempo", "in due tempi") -> tempo, never "time"
    # (Masiello pdf 322, 585)
    (r"\b(?:prim|second|terz|quart)[oa]\s+tempo\b|\b(?:due|tre|quattro|cinque)\s+tempi\b"
     r"|\bun\s+tempo\s+solo\b|\bin\s+un\s+(?:sol\s+)?tempo\b",
     r"\b(?:on|in|at|with|of) the (?:first|second|third|fourth) times?\b"
     r"|\b(?:in|executed in|of|composed of) (?:two|three|four|five) times\b"
     r"|\bthe other (?:two|three) times\b|\bin (?:a single|one single|one) time\b",
     "tempo counting movements → tempo"),
    # slancio of the rear foot -> drive (Masiello pdf 541, 575, 741, 746)
    (r"\bslanci", r"\b(?:spring|swing|impulse|thrust|throw|push|lunge|launch|dash)\s+of\s+the\s+"
     r"(?:left|rear|back|hind)\s+foot\b", "slancio → drive"),
    # col destro / col mancino, fencing against one (Masiello pdf 451, 453);
    # only where the hand would do the action -- "grip the sword with the
    # left hand" (Florio scan 137) is right
    (r"\bcol\s+(?:destro|mancino)\b|\bcon(?:tro)?\s+il\s+mancino\b",
     r"\bwith the (?:right|left) hand\b(?=[^.;]{0,20}\b(?:will|would|must|end|ends|ending|terminat))"
     r"|\bas well as with the (?:right|left)\b",
     "col destro / col mancino → against a right-hander / left-hander"),
    # the blade's edge, in positions of the fist (Masiello pdf 472);
    # "delivering the cut to the right" (Blengini scan 51) is a cut
    (r"\b(?:il|col|del)\s+taglio\s+(?:a|in|verso|rivolto|volto)\b",
     r"\b(?:the|its|with the) cut (?:up|down|upward|downward|turned|facing|to the ground"
     r"|to the (?:right|left)\s*[;,.])|\bturning the cut\b", "taglio → edge"),
    (r"\bcolp[oi] di punta\b", r"\bhits? (?:of|with) the point\b", "colpo di punta → thrust"),
    (r"\btocc[oh]i? di cambiament", r"\bchange[- ]beats?\b", "tocco di cambiamento → <i>tocco</i> of change"),
    (r"\ba studio\b", r"\bby study\b", "a studio → on purpose"),
    (r"\ba vuoto\b", r"\b(?:go|goes|went|gone|going) to waste\b", "andare a vuoto → miss"),
]
# Italian words the glossary translates that the model kept, in italics, as
# if they were kept terms -- the TERM_RULES above skip italic text, so these
# match the English with its tags (Masiello pdf 94, 544, 648, 685)
ITALIC_RULES_IT = [
    (r"\ba[- ]?fondo\b|\baffondo\b", r"<i>(?:a[- ]?)?f+ondo</i>", "a fondo / affondo → lunge"),
    (r"\bpuntat[ae]\b", r"<i>puntat[ae]</i>", "puntata → thrust"),
    (r"\bel[sz]a\b", r"<i>el[sz]a</i>", "elsa → hilt"),
    (r"\bcavazion[ei]\s+(?:sopra|sotto)\b", r"\b(?:high|low)\s+<i>cavazion", "cavazione sopra / sotto → above / below"),
]
