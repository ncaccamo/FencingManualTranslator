# Meaning review — instructions for a reviewing agent

The automatic checks (review report, `evals/retranslation_compare.py`) see
form: lost italics, length, untranslated text, line names. They don't see
a wrong sense. This is the brief given to a Claude agent that reads a
book's source against its English page by page.

Fill in REPO (the repository folder), LANG (French / Italian), BOOKTITLE,
STEM, PROMPTFILE (`translation_prompt.txt` or `translation_prompt_it.txt`),
FIRST and LAST (scan numbers: 130-190 scans per agent keeps its reading
manageable; run two or three agents at a time), M (pages in the range) and
SKIPLIST (pages being retranslated now, or "none"), and give the agent the
part below the line. For an Italian book, add one line of book-specific
notes if the book's `[terms]` or decisions need it (e.g. "tiratore →
swordsman"). The report goes to
`evals/reports/meaning-review/STEM.final.FIRST-LAST.md` (git-ignored: book
text). Check a few of its most serious items against the page before using
it; then the items go to the reviewer (decision list, checklist) — see
`docs/evaluation-and-tuning.md` §4 for the loop.

To re-review only the pages of a retranslation round, replace "YOUR RANGE"
with the page list and add: "For each page, say whether each error listed
for it in REPORT is fixed, and report anything new."

---

You are reviewing a machine translation of a LANG fencing manual — BOOKTITLE — for errors of MEANING. Read-only task: do not edit any file except the one report you write. No GPU, no docker, no git.

## Files
- Source (LANG OCR, one record per scanned page): REPO/processed/STEM_translated/intermediate/extracted.txt
- English: REPO/processed/STEM_translated/intermediate/translation.txt
- The translation rules the English follows (glossary): REPO/pipeline/PROMPTFILE — read rules 0, 2, 3 and 3b first. Every rendering they prescribe is CORRECT by decision of the owner, even if you would choose otherwise. Two things are done when the PDF is drawn, so don't report them: line names italicised, and Italian "quarta bassa" / numerals "4ª", "3ª-in-4ª" turned into "low <i>quarta</i>" / <i>quarta</i>, <i>terza</i>-in-<i>quarta</i>.
- Corrections already made: REPO/books/STEM.overrides.toml — step 2 applies its [[fix]] entries (scan, find → replace) to translation.txt at the end of each run; fixes added since still show their "find" text. Do not report anything a [[fix]] corrects. A [terms] section there, if any, is a book-specific glossary — also correct by decision.

Split both files into pages with the project's helper; page i (0-based) of each list is "scan i+1":
```python
import sys; sys.path.insert(0, "REPO/pipeline")
import _pageio
ex = _pageio.split_pages(open(".../extracted.txt", encoding="utf-8").read())
tr = _pageio.split_pages(open(".../translation.txt", encoding="utf-8").read())
```
Each source page has PAGE_NUMBER / HEADER / BODY_TEXT fields; compare BODY_TEXT with the English page. The English may carry a ___FOOTNOTES___ block ("†1 French: coulé" notes are expected: source references for translated terms). YOUR RANGE: scans FIRST to LAST. Go through ALL of them, in batches (print 5-8 pages of source+English at a time with a small python script). Skip pages that are only plates/captions/blank if there is nothing to compare. Skip these pages (being retranslated): SKIPLIST.

## What to report
Only places where the English says something DIFFERENT from the source:
- a wrong line/parry name or number, wrong weapon, wrong action (one technique named for another), wrong side/direction (inside/outside, high/low, right/left, over/under);
- a sentence or clause omitted, or content added that is not in the source;
- a misreading that changes the sense (negation lost — watch "ne … que", "ne … plus", "ne … guère", "non … che" —, subject/object swapped, wrong tense that changes who does what, a number changed);
- source-language text left untranslated where it should be English (a quotation in a third language is translated, with a note "X in the original"), or garbled words;
- a kept term (rule 2 of the prompt file) replaced by an English word or by another kept term.
Also list, at the top of the report: a page whose English stops mid-sentence where the source page goes on (cut off); body text moved into the ___FOOTNOTES___ block; a source table that became a bare "TABLE" line or vanished instead of "[[TABLE]]".
Do NOT report style: the owner wants translations as LITERAL as correct English allows ("closest to the author's words wins, even if both read fine"). A stiff but accurate literal phrasing is correct, not an error. Don't suggest paraphrases. Don't report italics. Also note OCR errors in the source that make the English wrong (say so; the source text itself may be misread).

Be careful and precise: quote exact words. If unsure whether something is an error, include it with "unsure" and your reason — the owner judges fencing terminology but does not read LANG, so explain the source meaning in plain English.

## Output
Write REPO/evals/reports/meaning-review/STEM.final.FIRST-LAST.md (create the folder if needed). Format:
```
# BOOKTITLE — meaning review, pdf FIRST+1 – LAST+1
Pages read: N of M. Errors: X meaning-changing, Y kept-term, Z minor/unsure.

## pdf P — <one-line summary>  [meaning | term | minor | unsure]  [fencing term: yes/no]
- Source: «exact source words»
- English now: "exact current English" (an exact substring of the page, so it can be used as a [[fix]] find)
- Should be: "literal corrected English"
- Why: one or two plain sentences.
```
Page references: the owner reads the side-by-side PDF, so every page in the report (headings, "Also:" lines, the summary) is "pdf P" where P = scan + 1. Never write scan numbers. Order by page. Severity: "meaning" = a reader would learn the wrong thing; "term" = a kept/glossary term wrong but sense recoverable; "minor" = small slip (article, a dropped adverb) worth a one-word fix.

Finally, reply with a short summary: counts by severity, the 5 most serious items (pdf page + one line), and any recurring pattern (a word mistranslated consistently) that a glossary line would fix — give the source meaning in plain English, where it occurs (pdf pages), and the book's own definition if it gives one.
