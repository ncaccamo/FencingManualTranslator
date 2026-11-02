# Meaning review — two readers, then the owner

The automatic checks (review report, `evals/retranslation_compare.py`) see
form, and a list of recurring wrong renderings. They don't see a wrong
sense. This is the method for a book that is otherwise ready: Claude agents
read the source against the English page by page, a second agent checks
each finding blind, and the owner sees only what the two don't agree on.

On three already-reviewed books (2026-11-01/02) it found 171 errors, about
one every 7 pages; the blind reader confirmed nearly all, and the owner got
a handful of questions. `docs/model-quirks.md` 2.21 has what kinds they were.

## The loop

1. **Readers.** One agent per ~95 scan pages, at most four at a time
   (more hit the session limit and stop all of them). Brief A below. Each
   writes a JSON list of findings, each with an exact `find` / `replace`
   ready to become a `[[fix]]`.
2. **Merge.** Number the findings (`m1`, `m2` …). Set aside any whose fix
   would replace a page shown as "[Empty page]": that page's text is
   drawn from elsewhere (a plate caption: `figures.json`, `[pages.N]
   caption`).
3. **Blind second reader.** Four groups, one agent each, brief B below.
   Each finding is given with its source, its English and the stated
   problem, but **not** the first reader's fix. The second reader writes its
   own verdict and fix.
4. **Apply what both call an error.** Where the two fixes differ only in
   wording, take one (the second reader's was written against the current
   text; prefer whichever is closer to the author's words). Check every
   `find` against the current page before writing it: it occurs exactly
   once, never spans `___FOOTNOTES___`, and the `replace` doesn't contain
   it. A fix that adds or removes a note becomes one fix from the first
   change to the end of the page, with the notes renumbered. Hold back any
   fix on a page that has an open question for the owner. Table cells go
   in `[[table_fix]]`, not `[[fix]]`.
5. **The owner** gets what's left, with full context: the source, the
   English now, what it means, and the options with a recommendation.
   "unsure" verdicts, and the term choices the glossary leaves open, go
   here; a rendering the glossary prescribes doesn't.
6. **Run the review report** afterwards. If a kind of error turned up on
   several pages, add a check for it (TERM_RULES, FALSE_FRIENDS) and run it
   over every book: it usually finds the same mistake elsewhere.

Findings and verdicts contain book text: keep them out of the repository
(`evals/reports/` is git-ignored).

---

## Brief A — the reader

Fill in REPO, STEM, LANG, PROMPTFILE (`translation_prompt.txt`, or for an
Italian book `translation_prompt_it.txt` plus rules 0, 1 and 4 of the French
file), FIRST-LAST (scan numbers), OUT, and anything already queued for the
book (a list of known items, so they aren't reported twice).

> You check an English translation of a historical LANG fencing manual
> against its source, page by page, for errors of MEANING that an earlier
> review missed. Examples of what earlier readings found: a negation that
> flipped («il ignorait» → "he knew"); OCR misreads translated as real words
> («tissant» for *toussant* → "weaving"); an idiom taken literally («avoir
> la partie belle» → "had the good part"); a word added to a term ("the high
> quarte" where the source has no "high").
>
> Nothing you write is applied directly: a second reader checks each
> finding blind. Report only what you'd defend. Precision matters more than
> quantity; a page with no error is the normal case.
>
> **Read first, fully:** REPO/pipeline/PROMPTFILE (kept terms, glossary,
> footnoted terms, quotation marks: a rendering these rules require is not
> an error); `_KEEP_TERMS_*`, `_ALWAYS_TRANSLATE`, `_FOOTNOTED_*` in
> REPO/pipeline/2_translate.py; REPO/books/STEM.overrides.toml (`[terms]`
> are the book's decisions; `[[fix]]` entries are already made). The owner's
> rules: literal over paraphrase, but never a word-for-word rendering that
> loses the meaning; letter case as printed; English “quotes”. Two things
> are done when the PDF is drawn, so don't report them: line names
> italicised, and Italian "quarta bassa" / "4ª" turned into "low
> <i>quarta</i>" / <i>quarta</i>.
>
> **Data:** REPO/processed/STEM_translated/intermediate/extracted.txt is the
> source, translation.txt the English, tables.json the tables (keyed by
> scan − 1). Split pages with `_pageio.split_pages()`
> (`sys.path.insert(0, "REPO/pipeline")`); page k (1-based) is scan k. The
> source is OCR: when the English looks odd, check whether the source word
> is a misread. For a real doubt about the print, `docker exec
> fmt-translator python3 /app/show_page.py STEM <scan>` prints the page and
> writes its scan image (processed/_audit/show/) for you to open. Use docker
> for nothing else.
>
> **Report:** a changed or lost meaning (wrong sense, flipped negation, wrong
> person, wrong number); an omitted clause; added content; an idiom taken
> literally; an OCR misread translated as a word; a term against the
> prompt's rules or the book's `[terms]`; a quotation in a third language
> left as printed. **Don't report:** style, a stiff but correct literal
> rendering, layout, italics, case. A fencing-term choice the rules leave
> open: report it as class "term" with your reasoning; the owner decides.
>
> **Output OUT:** a JSON list; each finding has `scan`, `pdf` (scan + 1, or
> from the side-by-side PDF), `source` (exact), `english` (exact), `problem`
> (one sentence), `class` ("general" | "term"), `confidence` (high | medium
> | low), `fixes` (a list of `{"scan", "find", "replace"}`; `find` copied
> exactly from that page, `<i>` tags included, occurring exactly once —
> check with Python —, never spanning `___FOOTNOTES___`, not contained in
> `replace`; minimal). A table cell: empty `fixes`, and a note naming the
> tables.json key and the cell. Rewrite the file every ~10 pages. Read every
> page in the range, source and English side by side, paragraph by
> paragraph. Edit nothing else, delete nothing, no GPU. Finish with: pages
> read, findings by class and confidence, the 3 most serious.

## Brief B — the blind second reader

Same reading and the same rules as brief A; give the agent brief A as well.
Then:

> **Input:** a JSON list of findings, each with its id, scan, pdf, the
> source words, the English as the first reader reported it, and the
> problem the first reader stated. Their fix is withheld: decide yourself
> whether the current English is wrong, and write the fix you would make.
> The stated problem is a claim to test against the source, not a fact.
> Look at the scan whenever the item turns on an OCR misread, a number or a
> footnote. If you agree, the fix is applied without the owner looking —
> so answer "unsure" rather than guess.
>
> **Footnotes:** correct a wrong translator's note rather than delete it
> (they read `†N French: …` / `†N Italian: …`). If a note must go, remove
> its † marker and its line, and the whole `___FOOTNOTES___` block if no
> note is left. A new "… in the original" note: write it as
> `<fn>Latin in the original</fn>` right after the translated quotation,
> and the applier builds the block.
>
> **Table cells:** a fix is `{"table_key": "…", "find": "…", "replace":
> "…"}`, the find unique within that key's English HTML.
>
> **Output:** an object, id → `{class, verdict ("error" | "ok" |
> "already_fixed" | "unsure"), confidence, note, fixes}`. Finish with the
> counts, and anything wrong you noticed outside your items (not fixed).
