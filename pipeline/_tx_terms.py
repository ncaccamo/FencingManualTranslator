# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Step 2's term rules, applied to the model's English: footnoted, kept and
always-translated terms, known mistranslations and garbles, the house
italics. The lists here go with the glossary in translation_prompt.txt /
translation_prompt_it.txt. Functions that depend on the book's language
take it as `lang`."""

import re

from _tx_case import _match_term_case, caps_kept_terms, source_caps_lines, source_case_kept_terms
from _tx_common import _LANG_REF


# Footnoted terms (see translation_prompt.txt): translate to the English
# rendering, and on the FIRST occurrence in a book mark it as
# "English<fn>Fr. <term></fn>" -- a bare source reference, never an
# explanatory gloss. The model does this from the prompt;
# enforce_footnoted_terms() is the backstop for when it leaves the term in
# French or forgets the note. Plural before singular so the longer pattern
# wins.
_FOOTNOTED_FR = [
    ("prise de fer",   "taking the blade"),
    ("rassemblements", "gatherings of the feet"), ("rassemblement", "gathering of the feet"),
    ("rassemblant",    "gathering the feet"),     ("rassembler",   "to gather the feet"),
    ("dérobements",    "evasions of the blade"),   ("dérobement",   "evasion of the blade"),
    ("dégagements",    "disengagements"),          ("dégagement",   "disengagement"),
    ("liements",       "binds"),                   ("liement",      "bind"),
    ("froissements",   "grazes"),                  ("froissement",  "graze"),
    ("coup de temps",  "time-hit"),
    ("coupés",         "cut-overs"),               ("coupé",        "cut-over"),
    ("coulés",         "glides"), ("coulé", "glide"), ("déroulé", "glide"),
    ("à-propos",       "timing"),
    # caver was "to drop the point" until 2026-10-11 -- wrong: the books define it
    # as the opposite of opposition, an angled thrust (Kirchhoffer, Joseph-Renaud
    # scan 144). barré: Robaglia's "croisé de seconde ... chasse vigoureusement le
    # fer"; coup fourré = coup double (Kirchhoffer's glossary). Reviewer's choices.
    ("en cavant",      "by angling"),             ("cavant",       "angling"),
    ("caver",          "angle"),
    ("barrés",         "bars"),                    ("barré",        "bar"),
    ("coups fourrés",  "double hits"),             ("coup fourré",  "double hit"),
    ("tendre la perche", "stick the arm out stiffly"), ("tendre la broche", "stick the arm out stiffly"),
    ("la perche tendue", "the arm stuck out stiffly"),
    # bourrer: drawing the arm back and driving the point in hard (Joseph-Renaud
    # pdf 290, Kirchhoffer pdf 61, Cordelois); larder: in-and-out jabs.
    # Reviewer's choices, 2026-10-31 (were shove / pressing / larding).
    ("bourrades",      "ramming thrusts"),         ("bourrade",     "ramming thrust"),
    ("bourreurs",      "rammers"),                 ("bourreur",     "rammer"),
    ("en bourrant",    "by ramming"),              ("bourrant",     "ramming"),
    ("bourrer",        "ram"),
    ("en lardant",     "jabbing"),                 ("larder",       "jab"),
    # fencer types and slang, reviewer's choices 2026-10-31 (terms questionnaire)
    ("picoteurs",      "prickers"),                ("picoteur",     "pricker"),
    ("tendeurs à outrance", "extenders to excess"), ("tendeur à outrance", "extender to excess"),
    ("bricoleurs",     "tinkerers"),               ("bricoleur",    "tinkerer"),
]

# Italian footnoted terms (translated, first use footnoted "It. <term>"),
# 2026-09-30. "filo" (glide) is left to the model -- as a bare word it also
# means the blade's edge, so the backstop must not rewrite it.
_FOOTNOTED_IT = [
    ("legamenti",       "engagements"),     ("legamento",   "engagement"),
    ("filo sottomesso", "forced glide"),
    ("trasporti",       "transports"),      ("trasporto",   "transport"),
    ("uscita in tempo", "action in tempo"),
    ("deviamenti",      "deviations"),      ("deviamento",  "deviation"),
    ("parata di picco", "beat parry"),
    ("controtempo",     "countertime"),
    ("controazioni",    "counter-actions"), ("controazione", "counter-action"),
    ("circolazioni",    "circlings"),       ("circolazione", "circling"),
    ("di striscio",     "grazing"),         ("striscio",    "graze"),
    ("volanti",         "off the blade"),   ("volante",     "off the blade"),
    ("spratico",        "practice bout"),
    ("parate di tasto", "opposition parries"), ("parata di tasto", "opposition parry"),
    ("forconatori",     "pitchforkers"),    ("forconatore", "pitchforker"),
]

# canonical singular French for the "Fr. <term>" note, so a plural form
# ("appels") doesn't produce a second note distinct from "Fr. appel".
_FOOTNOTE_REF_IT = {"parate di tasto": "parata di tasto", "legamenti": "legamento", "trasporti": "trasporto",
                 "deviamenti": "deviamento", "controazioni": "controazione",
                 "circolazioni": "circolazione", "di striscio": "striscio",
                 "volanti": "volante", "forconatori": "forconatore"}
_FOOTNOTE_REF_FR = {
    "en cavant": "caver", "cavant": "caver", "barrés": "barré", "coups fourrés": "coup fourré",
    "rassemblements": "rassemblement", "rassemblant": "rassemblement",
    "rassembler": "rassemblement", "dérobements": "dérobement",
    "dégagements": "dégagement", "liements": "liement",
    "froissements": "froissement", "coupés": "coupé",
    "coulés": "coulé", "déroulé": "coulé", "appels": "appel",
    "bourrades": "bourrade", "bourreurs": "bourreur", "en bourrant": "bourrer", "bourrant": "bourrer",
    "en lardant": "larder", "picoteurs": "picoteur", "tendeurs à outrance": "tendeur à outrance",
    "bricoleurs": "bricoleur", "vous loger": "se loger", "se logeant": "se loger", "loger": "se loger",
    "désuni": "se désunir", "se désunit": "se désunir", "s'enferre": "s'enferrer",
    "en filant": "filer", "filer au corps": "filer", "fléchards": "fléchard",
    "attaque d'emblée": "d'emblée", "coups amenés": "coup amené",
}


def _footnoted(lang: str):
    return _FOOTNOTED_IT if lang == "Italian" else _FOOTNOTED_FR


def _footnote_ref(lang: str):
    return _FOOTNOTE_REF_IT if lang == "Italian" else _FOOTNOTE_REF_FR



# a footnoted term inside an ordinary phrase is not the fencing action:
# "froissement d'amour-propre" is hurt pride, not a graze (Jacob 1887, 2026-10-13)
_FOOTNOTED_NOT_BEFORE = {"froissement": r"(?!\s+d['’]\s*amour)",
                         "froissements": r"(?!\s+d['’]\s*amour)"}


def enforce_footnoted_terms(text: str, seen: set, footnote: bool = True, lang: str = "French") -> str:
    """Backstop for footnoted terms. Translate EVERY occurrence the model
    left in French (bare or italicised) to its English rendering, and on
    the FIRST one in a book append "<fn>Fr. <term></fn>". Case of the
    surrounding token is preserved. `<fn>...</fn>` spans already in the
    text (the model's own source refs) are masked so a French term inside
    one is never re-wrapped."""
    if not text:
        return text
    masked = []

    def _mask(m):
        masked.append(m.group(0))
        return f"\x00{len(masked) - 1}\x00"

    text = re.sub(r"<fn>.*?</fn>", _mask, text, flags=re.DOTALL | re.IGNORECASE)

    label = _LANG_REF.get(lang, _LANG_REF["French"])[1]
    for fr, en in _footnoted(lang=lang):
        pat = re.compile(r"(?:<i>\s*)?(?<![\w-])" + re.escape(fr)
                         + r"(?![\w-])" + _FOOTNOTED_NOT_BEFORE.get(fr, "")
                         + r"(?:\s*</i>)?(?!\s*(?:</i>\s*)?\()", re.I)   # not a glossary headword: "<i>Caver</i> (to angle)"
        first = [True]
        note_fr = _footnote_ref(lang=lang).get(fr, fr)

        def _sub(m, en=en, note_fr=note_fr, first=first):
            g = m.group(0)
            r = (en.upper() if g.isupper()
                 else en.capitalize() if g[:1].isupper() else en)
            if footnote and first[0] and note_fr not in seen:
                # mask the tag we add too, so a later footnoted-term pass can't
                # match the French term sitting inside it
                masked.append(f"<fn>{label}: {note_fr}</fn>")
                r += f"\x00{len(masked) - 1}\x00"
                seen.add(note_fr)
            first[0] = False
            return r

        text = pat.sub(_sub, text)

    text = re.sub(r"\x00(\d+)\x00", lambda m: masked[int(m.group(1))], text)
    return text


# Rule-3 "always translate" terms the 7B drops inconsistently (leaves
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
    # "leçon d'assaut" = a free-play lesson; the 7B collapses it to just
    # "the bout" (drops "leçon"). Bare "assaut" -> "bout" is left to the
    # prompt (rule 3) -- "assaut" alone is sometimes "assault" in a duel.
    (r"leçons d['’]assaut", "bout lessons"), (r"leçon d['’]assaut", "bout lesson"),
    # "fer" (the opponent's steel) -> "the blade" (Category 1). NOT bare
    # "fer": leave "prise de fer" / "sentiment du fer" for their own
    # handlers (Cat 3 / Cat 2).
    (r"attaques au fer", "attacks on the blade"), (r"attaque au fer", "attack on the blade"),
    (r"sur le fer", "on the blade"), (r"au fer", "on the blade"),
    (r"le fer", "the blade"),
    # "l'escrime au fleuret" -> "foil fencing"; the 7B renders it
    # "épée fencing with the fleuret" / "épée fencing with the foil".
    (r"escrime au fleuret", "foil fencing"), (r"au fleuret", "with the foil"),
    (r"escrime à l['’]épée", "épée fencing"), (r"à l['’]épée", "with the épée"),
    # "au bout d'épée" -- idiom for a bout fenced with the épée, not a
    # literal "at the point of the sword"; the 7B renders it word-for-word.
    (r"au bout d['’]épée", "an épée bout"),
    # "un assaut d'épée" is just an épée bout -- there is no "leçon" in it,
    # but the 7B pulls "lesson" in from the leçon-d'assaut rule and writes
    # "a bout lesson with the épée". Repair the output.
    (r"a bout lesson with the épée", "an épée bout"),
    (r"bout lesson with the épée", "épée bout"),
]


# French around the word: it is inside a French title or quotation kept as
# written ("L'exercice des armes ou le maniement du fleuret", "Escrime
# pratique", "Histoire de l'escrime" -- Masiello scans 102, 113, 128), not
# an English sentence, which never has "du foil" or "fencing pratique".
_FRENCH_BEFORE = re.compile(r"(?:\b(?:du|de|des|au|aux)\s+|\b[dl][’'])$", re.I)
_FRENCH_AFTER = re.compile(r"^\s+(?:pratique|française|italienne|ancienne|moderne|théorique|"
                           r"simplifiée|militaire|nouvelle|et\s+de|en\s+)", re.I)


def apply_always_translate(text: str) -> str:
    if not text:
        return text
    for pat, repl in _ALWAYS_TRANSLATE:
        def _sub(m, r=repl):
            g = m.group(0)
            if _FRENCH_BEFORE.search(m.string[max(0, m.start() - 8):m.start()]) \
                    or _FRENCH_AFTER.match(m.string[m.end():m.end() + 24]):
                return g
            # a glossary headword kept in French (prompt rule 7b): "<i>Escrime</i> (fencing)"
            if m.string[max(0, m.start() - 3):m.start()] == "<i>" \
                    and re.match(r"\s*</i>\s*\(", m.string[m.end():m.end() + 8]):
                return g
            return r.upper() if g.isupper() else r.capitalize() if g[:1].isupper() else r
        text = re.sub(r"\b" + pat + r"\b", _sub, text, flags=re.IGNORECASE)
    return _phrase_repairs(text)


# Free-standing repairs for known 7B phrasings that _ALWAYS_TRANSLATE's
# \b-anchored substitution can't reach (e.g. a phrase split by an <i> tag).
def _phrase_repairs(text: str) -> str:
    if not text:
        return text
    # "épée fencing with the foil/fleuret" (épée possibly tagged) -> foil fencing
    text = re.sub(r"(?:<i>)?épée(?:</i>)?\s+fencing with the (?:foil|fleuret)",
                  "foil fencing", text, flags=re.IGNORECASE)
    # "Nᵉ temps" -> "Nth tempo"  (leaves "coup de temps", "temps d'arrêt" alone)
    text = re.sub(
        r"\b(\d{1,2}(?:er|re|nd|e|ème)?\.?)\s+temps\b",
        lambda m: m.group(1) + (" TEMPO" if m.group(0).isupper() else " tempo"),
        text, flags=re.IGNORECASE)
    # model already wrote "against-disengage..." for "contre-dégage..."
    text = re.sub(r"\b([Aa])gainst-(?=(?:dis)?engag|dégag)",
                  lambda m: ("C" if m.group(1).isupper() else "c") + "ounter-", text)
    # "glacé fabric" / "neither varnished nor glacé" -> glazed
    text = re.sub(r"\bglac[ée]s?\b",
                  lambda m: "Glazed" if m.group(0)[:1].isupper() else "glazed", text)
    # a title-page byline: French "PAR X ET Y" -> "By X and Y", not
    # "Translated by ..." (the model reads "par" as the translation credit).
    text = re.sub(
        r"(?m)^([ \t]*)Translated by(?=[ \t]+[\"“'’A-ZÀ-Þ][^\n]{0,60}$)",
        r"\1By", text)
    return text


# Kept terms: "keep in the source language, italic" that the 7B
# drops the <i>...</i> off or mistranslates -- plate captions especially.
# Only the tight retained set; riposte / remise / reprise / the parry
# numbers the model handles on its own. Each entry: term -> English
# mistranslations to snap back to it.
_KEEP_TERMS_FR = [
    ("corps à corps", [r"body[- ]to[- ]body", r"body contact"]),
    ("sentiment du fer", [r"feeling of (?:the )?(?:blade|iron|steel)"]),
    ("croisé", []),
    ("volte", []),
    # riposte / remise / reprise: normally kept by the model, but a bare
    # heading ("DE LA RIPOSTE.") gets mangled -- "OF THE RAPIER RAPIER".
    # Isolated-heading translation (no body context, no retry ladder) is
    # where this shows up -- "PARADES ET RIPOSTES" -> "PARRIES AND
    # RAPISES" is the same failure with a different garbled spelling.
    ("riposte", [r"rapier(?:[- ]?(?:rapier|response|riposte|reply))?",
                 r"riposte response", r"rapises?"]),
    # kept, never "stop-point(s)" (Joseph-Renaud pdf 46, Kirchhoffer pdf 39)
    ("pointes d'arrêt", [r"stop[- ]points"]), ("pointe d'arrêt", [r"stop[- ]point"]), ("pointe d’arrêt", []),
    ("flanconnade", []),
    ("appels", []), ("appel", []),
    ("remise", []),
    ("reprise", []),
    # doublé / dédoublé kept in French (reviewer, 2026-10-13: the books explain
    # them; Prévost-Jollivet had seven renderings). Plurals first.
    ("dédoublés", []), ("dédoublé", []), ("doublés", []), ("doublé", []),
    # "du tac au tac": parry and riposte in one instant -- kept in French in
    # English fencing writing. Qwen3.8 rendered it word for word ("FROM THE
    # TAC TO THE TAC", Gomard scan 239); older output left it bare/unitalic.
    ("tac-au-tac", [r"(?:(?:from|of)\s+the\s+|du\s+)?tac[- ]to[- ]the[- ]tac",
                    r"(?:(?:from|of)\s+the\s+|du\s+)?tac[- ]au[- ]tac"]),
]

# Italian kept terms (translation_prompt_it.txt rule 2), 2026-09-30. Only
# words that never occur as ordinary English -- the hand positions and
# Maestro are left to the model.
_KEEP_TERMS_IT = [(t, []) for t in (
    "controcavazione", "cavazione", "cartoccio", "fianconata", "inquartata",
    "intrecciata", "appuntata", "passata sotto", "intagliata", "molinello",
    "molinelli", "fendente", "traversone", "raddoppio", "ripigliata",
    "copertino", "balestrata", "scandaglio", "traccheggio", "ricasso",
    "imbroccata", "mandritto", "coupé", "sforzi", "sforzo", "rimessa", "ripresa")] + [
    # riposte: kept in the body, but an isolated heading can garble it
    # (the French "RAPISES"; "RIPONSES" is fixed in fix_known_garbles)
    ("riposte", [r"rapier(?:[- ]?(?:rapier|response|riposte|reply))?",
                 r"riposte response", r"rapises?"])]


# Maestro is kept every time in an Italian book (reviewer, 2026-10-11), in the
# book's case (2026-10-18: "il maestro" -> <i>maestro</i>, "Maestro Parise"): the
# lesson's master, a named one (Maestro Parise) and masters in general. The
# literal prompt had the model write "master" in prose (Masiello scan 434,
# 74 -> 56 kept). Each "master(s)" outside <i>...</i> becomes <i>Maestro</i> /
# <i>Maestri</i>, no more of them than the page's source has maestro/maestri
# (a "master" can render padrone); the verb ("to master") is left alone.
_MAESTRO_SRC = re.compile(r"(?<!\w)maestr[oi](?!\w)", re.I)
_MASTER_EN = re.compile(r"(?<![\w-])(?<!to )(master)(s?)(?![\w-])", re.I)


# rapière → "riposte": the model reads the weapon as the fencing term even
# with a glossary line (Prévost-Jollivet scans 21, 248; LaMarche 289 twice).
# The source's riposte / rapière words are paired in order with the English's
# riposte / rapier words; only when the counts match does a "riposte" standing
# where the source has rapière become "rapier". Otherwise nothing changes.
_RIPOSTE_OR_RAPIER_SRC = re.compile(r"\b(?:ripost\w*|rapi[eè]res?)\b", re.I)
_RIPOSTE_OR_RAPIER_EN = re.compile(r"(?:<i>)?\b(ripost(?:e|es|ed|ing)|rapiers?)\b(?:</i>)?", re.I)


def restore_rapier(text: str, src: str) -> str:
    if not text or not src or not re.search(r"rapi[eè]re", src, re.I):
        return text
    src_words = _RIPOSTE_OR_RAPIER_SRC.findall(src)
    out = list(_RIPOSTE_OR_RAPIER_EN.finditer(text))
    if len(src_words) != len(out):
        return text
    for sw, m in reversed(list(zip(src_words, out))):
        if sw.lower().startswith("rapi") and m.group(1).lower().startswith("ripost"):
            word = "rapiers" if m.group(1).lower().endswith("s") else "rapier"
            word = word.capitalize() if m.group(1)[:1].isupper() else word
            text = text[:m.start()] + word + text[m.end():]
    return text


# épée meaning the blade in set phrases (prompt rule 3): the model kept "the
# épée" on six Prévost-Jollivet pages and many Gomard ones after the rule
# said otherwise, headings included ("ATTACKS WITH THE ÉPÉE"). Only where the
# French page has the phrase.
_E = r"(?:the\s+)?(?:<i>\s*)?[ée]p[ée]es?(?:\s*</i>)?"
_BLADE_PHRASES = [
    (r"\battaques?\s+à\s+l['’]\s*épée", r"\b(attacks?)\s+(?:with|on|to|against)\s+" + _E + r"(?!\w)", r"\1 on the blade"),
    (r"\babsences?\s+d['’]\s*épée", r"\b(absences?)\s+of\s+" + _E + r"(?!\w)", r"\1 of blade"),
    (r"\bdonne\w*\s+(?:l['’]|de\s+l['’])\s*épée", r"\b(giv(?:e|es|en|ing)|gave)\s+(?:the\s+)?(?:<i>\s*)?[ée]p[ée]e(?:\s*</i>)?(?!\w)(?!(?:\s*</i>)?\s+or\s+the\s+blade)", r"\1 the blade"),
    (r"\b(?:joindre|joint|joignez|engager|engagez|engage)\s+l['’]\s*épée", r"\b(join\w*|engag\w*)\s+(?:the\s+)?(?:<i>\s*)?[ée]p[ée]e(?:\s*</i>)?(?!\w)", r"\1 the blade"),
]


def restore_blade_phrases(text: str, src: str, lang: str = "French") -> str:
    if not text or not src or lang == "Italian":
        return text
    for fr, en, repl in _BLADE_PHRASES:
        if re.search(fr, src, re.I):
            def sub(m, repl=repl):
                out = m.expand(repl)
                return out.upper() if re.sub(r"</?i>", "", m.group(0)).isupper() else out
            text = re.sub(en, sub, text, flags=re.I)
    return text


# "reprise d'attaque" written as "re-attack" despite prompt rule 0
# (Kirchhoffer scan 23, Joseph-Renaud scans 158, 226; 2026-10-12): back to
# the kept term, never more times than the source has the phrase. Italian
# books are untouched -- their source has no "reprise d'attaque".
_REPRISE_SRC = re.compile(r"\breprises?\s+d['’]\s*attaques?\b", re.I)
_REATTACK_EN = re.compile(r"(?:<i>\s*)?\b(re-attack)(s?)\b(?![-\w])(?:\s*</i>)?", re.I)


def restore_reprise(text: str, src: str) -> str:
    budget = [len(_REPRISE_SRC.findall(src or ""))]
    if not text or not budget[0]:
        return text

    def sub(m):
        if budget[0] <= 0:
            return m.group(0)
        budget[0] -= 1
        word = "reprises" if m.group(2) else "reprise"
        if m.group(1).isupper():
            return f"<i>{word.upper()}</i> OF ATTACK"
        word = word.capitalize() if m.group(1)[:1].isupper() else word
        return f"<i>{word}</i> of attack"
    return _REATTACK_EN.sub(sub, text)


# Italian «colpo diritto / dritto / diretto» is the straight thrust (rule 3);
# the model keeps its "colpo → hit" habit and writes "straight hit" even with
# the spelling in the rule (Marchionni 1847: 120 of 142, 2026-10-14). As many
# as the source has, Italian books only.
_STRAIGHT_SRC = re.compile(r"(?i)\bcolp[oi]\s+(?:di?ritt[oi]|dritt[oi]|dirett[oi])\b")
_STRAIGHT_EN = re.compile(r"\b(straight|Straight|STRAIGHT)\s+(hit|attack|HIT|ATTACK)(s|S)?\b")


def restore_straight_thrust(text: str, src: str, lang: str = "French") -> str:
    if lang != "Italian":
        return text
    budget = [len(_STRAIGHT_SRC.findall(src or ""))]
    if not text or not budget[0]:
        return text

    def sub(m):
        if budget[0] <= 0:
            return m.group(0)
        budget[0] -= 1
        word = "THRUST" if m.group(2).isupper() else "thrust"
        return f"{m.group(1)} {word}{m.group(3) or ''}"
    return _STRAIGHT_EN.sub(sub, text)


def enforce_maestro(text: str, src: str, lang: str = "French") -> str:
    if lang != "Italian" or not text:
        return text
    budget = [len(_MAESTRO_SRC.findall(src or ""))]
    if not budget[0]:
        return text

    def sub(m):
        if budget[0] <= 0:
            return m.group(0)
        budget[0] -= 1
        word = "Maestri" if m.group(2) else "Maestro"
        if m.group(1).isupper():
            word = word.upper()
        return f"<i>{word}</i>"

    out = []
    for part in re.split(r"(<i>.*?</i>)", text, flags=re.S | re.I):
        if not part.lower().startswith("<i>"):
            part = _MASTER_EN.sub(sub, part)
        else:
            budget[0] -= len(re.findall(r"maestr[oi]", part, re.I))
        out.append(part)
    # in the book's case (reviewer, 2026-10-18): "il maestro" -> <i>maestro</i>
    return source_case_kept_terms("".join(out), src)


def _keep_terms(lang: str):
    return _KEEP_TERMS_IT if lang == "Italian" else _KEEP_TERMS_FR


# A source-language token the 7B reliably garbles. rassemblant is now a
# footnoted (translated) term, so snap the garble back to the BARE French
# word -- enforce_footnoted_terms() then translates it and adds the note.
_MANGLE_PRONE = [(r"rassembl", "rassemblant")]


def repair_mangled_terms(dst: str, src: str) -> str:
    """When the SOURCE has a term the 7B is known to mangle and the output
    has lost it, swap the first quoted non-English token back to the term."""
    if not dst or not src:
        return dst
    for needle, term in _MANGLE_PRONE:
        if re.search(needle, src, re.I) and not re.search(needle, dst, re.I):
            dst = re.sub(
                r'([«"“(])\s*[A-Za-zÀ-ÿ]{5,}\s*([»"”)])',
                lambda m: f'{m.group(1)}{term}{m.group(2)}', dst, count=1)
    return dst


def fix_known_garbles(text: str, src: str = "") -> str:
    """Deterministic repairs for REPEATABLE model mistakes -- ones that
    survive the 3-sample majority vote because the model makes them the
    same way every time (Gomard, batch-2 pass 8):
      * "THUST" for "thrust" -- in headers AND body, 13 lines + 4 headers.
        Also "SIMPLE THROUGHS" (pdf 174 retranslation). Neither is an
        English word, so safe to fix anywhere.
      * "THRUST/HIT" -- the prompt glossary's old "botte -> thrust/hit"
        copied verbatim. botte -> thrust (coup -> attack: Gomard pdf 158
        defines the coup as the whole action, the botte as its finish).
      * "plank(s)" for planche(s) -- the carpentry sense; in these books a
        planche is always an illustration plate. Only when the source
        actually has "planche", so a genuine plank elsewhere survives."""
    if not text:
        return text

    def _case(m, word):
        g = m.group(0)
        plural = g.lower().endswith("s")
        w = word + ("s" if plural else "")
        return w.upper() if g.isupper() else w.capitalize() if g[:1].isupper() else w
    text = re.sub(r"\bthusts?\b", lambda m: _case(m, "thrust"), text, flags=re.I)
    text = re.sub(r"\bthroughs\b", lambda m: _case(m, "thrust"), text, flags=re.I)
    # "RIPONSES" for riposte(s) -- an Italian heading "DELLE RISPOSTE"
    # (Masiello 1902, sample 2026-09-30), already italicised. Not a word in
    # any of the languages here, so safe to fix anywhere.
    text = re.sub(r"\briponses?\b", lambda m: _case(m, "riposte"), text, flags=re.I)
    # "Parrhes" for parades (Kirchhoffer scans 22, 105 -- three times, the
    # same way). Not a word in any of the languages here.
    text = re.sub(r"\bparrhes\b", lambda m: "PARRIES" if m.group(0).isupper()
                  else "Parries" if m.group(0)[0].isupper() else "parries", text, flags=re.I)
    # "the du tac-au-tac riposte": the French article left in front. Only
    # after an English article -- French kept in a translation ("je ne
    # parle pas du tac au tac", LaMarche scan 128) must stay as it is
    text = re.sub(r"\b(the|a|an)\s+du\s+((?:<i>)?tac[- ]au[- ]tac)", r"\1 \2", text, flags=re.I)
    # a French book title with its *escrime* turned into English by the
    # glossary: "Traité d'Fencing pratique" (Joseph-Renaud scan 35),
    # "Bibliographie de l'Fencing" (Barbasetti). English never writes
    # "d'Fencing" / "l'Fencing", so it is always such a title -- or French
    # quoted as it stands, lower case: "Histoire de l'fencing" (Masiello
    # scan 133), "dans l'fencing" (Barbasetti scan 36).
    text = re.sub(r"(\b[dlDL][’'])(fencing|Fencing|FENCING)\b",
                  lambda m: m.group(1) + {"fencing": "escrime", "Fencing": "Escrime",
                                          "FENCING": "ESCRIME"}[m.group(2)], text)
    # "RIPOSTES AT THE <i>TAC-AU-TAC</i>." (Gomard scans 242, 246): the
    # idiom read as a place. Only "at the" -- "of the" can be a real noun
    # use ("the speed of the tac-au-tac")
    text = re.sub(r"\bat\s+the\s+((?:<i>)?tac[- ]au[- ]tac)", r"\1", text, flags=re.I)
    text = re.sub(r"\bripposte(s?)\b", lambda m: _case(m, "riposte") + m.group(1).lower()
                  if not m.group(0).isupper() else "RIPOSTE" + m.group(1).upper(), text, flags=re.I)
    # A capitals line with one word the model split while spelling it
    # ("RIPostes AFTER THE PARRIES", Gomard scan 239): the model reads an
    # all-caps word as fragments and can reassemble it in mixed case.
    # Restore capitals when the rest of the line is capitals.
    def _caps_line(line):
        plain = re.sub(r"</?i>", "", line)
        words = re.findall(r"[^\W\d_]+", plain)
        # "RIPostes" (scan 239) and "RIPostES" (scan 244)
        odd = [w for w in words if re.fullmatch(r"[A-Z]{2,}[a-z]+[A-Z]*", w)]
        rest = [w for w in words if w not in odd and len(w) > 1]
        if odd and rest and all(w.isupper() for w in rest):
            for w in odd:
                line = re.sub(r"\b" + w + r"\b", w.upper(), line)
        return line
    text = "\n".join(_caps_line(l) for l in text.split("\n"))
    text = re.sub(r"\bthrusts?\s*/\s*hits?\b",
                  lambda m: _case(re.search(r"thrusts?", m.group(0), re.I), "thrust"),
                  text, flags=re.I)
    if src and re.search(r"\bplanches?\b", src, re.I):
        text = re.sub(r"\bplanks?\b", lambda m: _case(m, "plate"), text, flags=re.I)
    # a plate's label and its caption on one line: "Plate V. — Blow
    # delivered …" (operator preference, Gomard review 2026-09-28)
    text = re.sub(r"\A(\s*)((?:<i>)?(?:PLATE|Plate|PLANCHE|Planche)\s+[IVXLC]+[a-z]*\.?(?:</i>)?)[ \t]*\n\s*\n?(?=\S)(?![\s\S]*\n\s*\n[\s\S]*\n\s*\n)",
                  lambda m: m.group(1) + m.group(2).rstrip() + " — ", text, count=1)
    # ",," -- a print mark beside a comma read as a second one (Prévost scan 157)
    text = re.sub(r",\s*,(?!,)", ",", text)
    # a name's particle translated: "Mr. A. OF LA FORGE" -> "A. DE LA FORGE"
    # (Jacob), "Count F. of L'Angle-Beaumanoir" (Spinnewyn) -- after an initial
    text = re.sub(r"\b([A-Z]\.\s+)(of|OF|Of)(\s+(?:La|LA|Le|LE|L['’]))",
                  lambda m: m.group(1) + ("DE" if m.group(2) == "OF" else "de") + m.group(3), text)
    # a lesson's step label printed "I. TEMPO" stays "I. TEMPO", never
    # "I. TIME" (Barbasetti, 38 of 86; reviewer, 2026-10-18)
    if src and re.search(r"(?im)^\s*[IVX]+\.?\s+TEMPO\b", src):
        text = re.sub(r"(?m)^(\s*[IVX]+\.?\s+)(TIME|Time)\b",
                      lambda m: m.group(1) + ("TEMPO" if m.group(2).isupper() else "Tempo"), text)
    text = source_caps_lines(source_case_kept_terms(text, src), src)
    return caps_kept_terms(text, src)


# A French book's line names are stored plain and drawn italic: the PDF
# italicises every one (_render.line_name_italics, reviewer 2026-10-11,
# confirmed 2026-10-31), and the model italicised only ~5%. One stored form
# means a [[fix]] matches whichever way the model wrote them.
_ITALIC_LINE_NAME = re.compile(
    r"<i>((?:prime|seconde|tierce|quarte|quinte|sixte|septime|octave)s?)</i>", re.I)


def plain_line_names(text: str) -> str:
    return _ITALIC_LINE_NAME.sub(r"\1", text) if text else text


# riposte / remise in every form -- the plural, and the English verb made
# from the kept noun ("he ripostes", "has remised") -- italic like the noun
# (reviewer, 2026-10-31: one word, one look; JR and Kirchhoffer had both).
_BARE_KEPT_FORM = re.compile(r"(?<![\w>])(?:riposte[sd]?|riposting|remise[sd]?|remising)(?![\w<])", re.I)


def house_italics(text: str, french: bool = True) -> str:
    """The italics rules applied to finished text: line names stored plain
    (French books; drawn italic), riposte / remise italic in every form."""
    if not text:
        return text
    if french:
        text = plain_line_names(text)
    return _BARE_KEPT_FORM.sub(lambda m: f"<i>{m.group(0)}</i>", text)


def enforce_kept_terms(text: str, src: str = "", lang: str = "French") -> str:
    """Make sure a kept term is present and italicised: repair a
    known mistranslation, then wrap any bare occurrence in <i>...</i>
    (skipping what is already inside a tag), matching the case of
    whatever prompted the wrap."""
    if not text:
        return text
    text = repair_mangled_terms(text, src)
    text = fix_known_garbles(text, src)
    for term, wrongs in _keep_terms(lang=lang):
        for w in wrongs:
            def _sub_term(m, term=term):
                # preserve plurality of the garbled match ("RAPISES" ->
                # "ripostes", not the always-singular "riposte")
                g = m.group(0)
                plural = (g.rstrip().lower().endswith("s") and not term.endswith("s")
                          and not term.split()[0].endswith("s"))   # "pointes d'arrêt" is already plural
                disp = term + ("s" if plural else "")
                return f"<i>{_match_term_case(disp, g)}</i>"
            text = re.sub(r"(?<!<i>)\b" + w + r"\b(?!</i>)",
                          _sub_term, text, flags=re.IGNORECASE)
        # wrap a bare occurrence not already tagged
        text = re.sub(
            r"(?<![\w>])" + term.replace(" ", r"\s+") + r"(?![\w<])",
            lambda m: f"<i>{_match_term_case(term, m.group(0))}</i>",
            text, flags=re.IGNORECASE)
        # collapse any accidental double-wrap, keeping whichever case is
        # already inside it rather than reinserting the lower-cased term
        text = re.sub(
            rf"<i>\s*<i>({re.escape(term)})</i>\s*</i>",
            lambda m: f"<i>{m.group(1)}</i>", text, flags=re.IGNORECASE)
    return house_italics(text, lang != "Italian")


_PARRY_WRONG = {
    "first": "prime", "second": "seconde", "third": "tierce", "fourth": "quarte",
    "fifth": "quinte", "sixth": "sixte", "seventh": "septime", "eighth": "octave",
    # the name cut short in a capitals caption ("PARRY OF QUINT.", Kirchhoffer
    # scan 31, on two separate translations)
    "quint": "quinte",
}


def fix_parry_names(en: str, src: str) -> str:
    """Numbered parries (prime..octave) are kept terms -- kept, not
    translated. When the source names one and the model turned it into an
    English ordinal, snap it back."""
    if not en or not re.search(
            r"parade\s+d|de\s+(?:prime|seconde|tierce|quarte|quinte|sixte|septime|octave)",
            src, re.I):
        return en
    for wrong, right in _PARRY_WRONG.items():
        en = re.sub(r"\b(of\s+|parry\s+of\s+)(" + wrong + r")\b",
                    lambda m, r=right: m.group(1) + (r.upper() if m.group(2).isupper() else r),
                    en, flags=re.I)
    return en
