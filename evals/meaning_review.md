# Meaning review — instructions for a reviewing agent

The automatic checks (review report, `evals/retranslation_compare.py`) see
form: lost italics, length, untranslated text, line names. They don't see
a wrong sense. This is the brief given to a Claude agent that reads a
book's source against its English page by page. Fill in REPO (the repository folder), BOOKTITLE, STEM,
FIRST, LAST (a range of about 130-160 scans per agent keeps its reading
manageable), M (pages in the range) and SKIPLIST (pages being redone),
and give the agent this file. Its report goes to
`evals/reports/meaning-review/STEM.FIRST-LAST.md` (git-ignored: book
text). Check a few of its most serious items against the page before
using it; then each item becomes a checklist line for the reviewer.

Two modes. **Pages:** every error, worst first per page, for a book whose
English is final. **Patterns:** recurring term errors first (for the
glossary), then only meaning and term items, for a book that will be
retranslated anyway. The section "Priority for this review" below is the
patterns mode; leave it out for the pages mode. Its list of terms "already
being fixed" is the glossary as of 2026-10-11 -- update it from
`pipeline/translation_prompt.txt` before a run.

The brief is written for French; for an Italian book, replace the kept
terms and glossary conventions with those of `translation_prompt_it.txt`.

---

You are reviewing a machine translation of a French fencing manual — BOOKTITLE — for errors of MEANING. Read-only task: do not edit any file except the one report you write. No GPU, no docker, no git.

## Files
- Source (French OCR, one record per scanned page): REPO/processed/STEM_translated/intermediate/extracted.txt
- English: REPO/processed/STEM_translated/intermediate/translation.txt
Both are split into pages with the project's helper; page i (0-based) of each list is "scan i+1":
```python
import sys; sys.path.insert(0, "REPO/pipeline")
import _pageio
ex = _pageio.split_pages(open(".../extracted.txt", encoding="utf-8").read())
tr = _pageio.split_pages(open(".../translation.txt", encoding="utf-8").read())
```
Each source page has PAGE_NUMBER / HEADER / BODY_TEXT fields; compare BODY_TEXT with the English page. The English may carry a ___FOOTNOTES___ block ("†1 French: coulé" notes are expected: source references for translated terms). YOUR RANGE: scans FIRST to LAST. Go through ALL of them, in batches (print 5-8 pages of source+English at a time with a small python script). Skip pages that are only plates/captions/blank if there is nothing to compare.

## What to report
Only places where the English says something DIFFERENT from the French:
- a wrong line/parry name or number (quarte vs tierce, sixte vs septime, seconde as "second" where it is the parry), wrong weapon (épée rendered "foil"), wrong action (barré, flèche, coulé, froissement, etc. mistranslated), wrong side/direction (dedans/dehors, haut/bas, droite/gauche);
- a sentence or clause omitted, or content added that is not in the French;
- a misreading that changes the sense (negation lost, subject/object swapped, wrong tense that changes who does what, a number changed);
- French left untranslated in the English, or garbled words;
- a kept term (riposte, remise, reprise, croisé, corps à corps, volte, sentiment du fer, tac-au-tac, the line names prime…octave, épée) replaced by an English word.
Do NOT report style: the owner wants translations as LITERAL as correct English allows ("closest to the author's words wins, even if both read fine"). A stiff but accurate literal phrasing is correct, not an error. Don't suggest paraphrases. Glossary conventions that are correct, not errors: coup → attack / hit (never "blow"), botte → thrust, parade → parry, garde → guard, assaut → bout, fente → lunge, coup droit → straight thrust, coup de temps → time-hit, coup d'arrêt → stop-thrust, fer → blade, tireur → fencer, line names kept in French (italic or not doesn't matter). Already known — do not report: SKIPLIST.
Also note OCR errors in the French that make the English wrong (say so; the source text itself may be misread).

Be careful and precise: quote exact words. If unsure whether something is an error, include it with "unsure" and your reason — the owner judges fencing terminology but does not read French, so explain the French meaning in plain English.

## Output
Write REPO/evals/reports/meaning-review/STEM.FIRST-LAST.md (create the folder if needed). Format:
```
# BOOKTITLE — meaning review, scans FIRST-LAST
Pages read: N of M. Errors: X meaning-changing, Y kept-term, Z minor/unsure.

## scan N — <one-line summary>  [meaning | term | minor | unsure]  [fencing term: yes/no]
- French: «exact source words»
- English now: "exact current English"
- Should be: "literal corrected English"
- Why: one or two plain sentences.
```
Order by scan. Severity: "meaning" = a reader would learn the wrong thing; "term" = a kept/glossary term wrong but sense recoverable; "minor" = small slip (article, a dropped adverb) worth a one-word fix.
## Priority for this review: terms the glossary is missing
This book's review is mainly to find RECURRING term errors that a glossary line in the translation prompt would fix for every book. Put a "## Patterns" section at the TOP of the report, before the per-page items: each pattern = the French term or construction, what the English does now, what it should be (standard English fencing usage; if English fencing has no word for it, a literal rendering), the scans where it occurs (all of them, or the first 10 + a count), and one quoted example. Give the meaning of the French term in plain English and quote the book's own definition if it gives one (many of these books define their terms; search the whole book's French for a definition).
These terms are ALREADY being fixed in the glossary — do not list them as patterns (a page error involving them still counts in the per-page list only if it's something else): battement → beat, à fond → with a full lunge, contre (the parry) → counter, tirer → thrust, coup double / coup fourré → double hit, fausse attaque → false attack, redoublement → redoubling, contre-temps → counter-time, temps d'arrêt (pause), les avancés → advanced target, coquille → bell guard, jarret → back of the knee, saignée → crook of the elbow, de pied ferme → without moving the feet, à bras raccourci → with a shortened arm, dans/sur les armes → inside/outside, marche → advance, invite → invitation, à la muette → in silence, poule → pool, terrain → the ground, combat → combat, caver → angle, barré → bar, pointe d'arrêt (kept in French), coup → attack/hit, botte → thrust, froissement → graze.
After the patterns, the per-page list as above, but only "meaning" and "term" items (skip "minor").

Finally, reply with a short summary: counts by severity, the 5 most serious items (scan + one line), and any pattern you noticed (e.g. a word mistranslated consistently).
