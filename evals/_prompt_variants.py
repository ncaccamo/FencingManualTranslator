# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Candidate prompt changes, tried by the test harnesses before they go into
pipeline/translation_prompt.txt: PROMPT_VARIANT=<name> rewrites step 2's
BASE_PROMPT (shared by the French and Italian prompts) in the harness only,
so nothing under pipeline/ changes until a variant is adopted.
"""
import os

# 2026-10-10: the reviewer wants the author's words over paraphrase, even
# where both read fine; the prompt asked for "clear" English and had no
# closeness rule. A slightly foreign phrasing is acceptable; false friends
# and real ungrammaticality are not.
LITERAL_RULE = (
    "0. Translate closely: keep the author's own words, sentence structure and order; where English "
    "has a word from the same root that means the same thing, use it; never add explanatory words, "
    "merge or reorder sentences, or omit anything. A phrasing that reads slightly foreign is fine. "
    "Depart from the author's wording only where a literal rendering would be wrong English or would "
    "change the meaning (false friends: French actuellement → currently, assister à → attend; Italian "
    "eventualmente → possibly, attualmente → currently).\n\n")

# Version 2 (2026-10-10), after reading version 1's output: it made "WARNING" of
# avvertenza, "guarantee" of garantir, "at last" of enfin, "a thrust of
# cavazione" of "di cavazione" (the reviewer: "by" is right) and dropped the
# italics of a kept term (volte). Same rule, plus those three guards.
LITERAL_RULE_2 = (
    "0. Translate closely: keep the author's own words, sentence structure and order; where English "
    "has a word from the same root that means the same thing, use it; never add explanatory words, "
    "merge or reorder sentences, or omit anything. A phrasing that reads slightly foreign is fine. "
    "Depart from the author's wording only where a literal rendering would be wrong English or would "
    "change the meaning:\n"
    "   - false friends: use the sense, not the look-alike — avvertenza → note, garantir (a part of the "
    "body) → protect, enfin → after all / finally, actuellement / attualmente → currently, "
    "eventualmente → possibly, assister à → attend;\n"
    "   - prepositions follow their sense: di / de naming the means of an action is \"by\" (colpo di "
    "cavazione → thrust by <i>cavazione</i>; di molinello → by <i>molinello</i>);\n"
    "   - rule 0 never overrides rules 2-3: kept terms stay in their language, in <i>…</i>, exactly as "
    "listed.\n\n")

VARIANTS = {
    "literal": [
        ("into clear, modern English for scholarly reading.",
         "into faithful, modern English for scholarly reading, as close to the author's own words as correct English allows."),
        ("CRITICAL RULES — FOLLOW EXACTLY:\n\n", "CRITICAL RULES — FOLLOW EXACTLY:\n\n" + LITERAL_RULE),
    ],
    "literal2": [
        ("into clear, modern English for scholarly reading.",
         "into faithful, modern English for scholarly reading, as close to the author's own words as correct English allows."),
        ("CRITICAL RULES — FOLLOW EXACTLY:\n\n", "CRITICAL RULES — FOLLOW EXACTLY:\n\n" + LITERAL_RULE_2),
    ],
}


def apply(ns):
    """Rewrite ns["BASE_PROMPT"] for PROMPT_VARIANT (if set); returns the name."""
    name = os.environ.get("PROMPT_VARIANT", "")
    if not name:
        return ""
    p = ns["BASE_PROMPT"]
    for old, new in VARIANTS[name]:
        if p.count(old) != 1:
            raise SystemExit(f"prompt variant {name!r}: {old[:40]!r} not found once in the prompt")
        p = p.replace(old, new)
    ns["BASE_PROMPT"] = p
    return name
