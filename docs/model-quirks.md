# Model quirks & prompt tweaks — a field guide

Everything the two models (olmOCR-2-7B-1025 for OCR, Qwen2.5-14B-Instruct
for translation — both env-overridable) get *wrong* in this corpus, the concrete example that
surfaced it, why it happens, and the mitigation that's now in the code or
the prompt. Written to be studied — if you're tuning a prompt or adding a
book, read this first so you don't re-derive a solved problem.

The two prompt files are the living source of truth for *what* the models
are told; this doc is *why*:

- [`pipeline/vlm_ocr_prompt.txt`](../pipeline/vlm_ocr_prompt.txt)
- [`pipeline/translation_prompt.txt`](../pipeline/translation_prompt.txt)

Mitigations live in three places: the prompt files, the Python guards in
[`1_ocr_and_extract_vlm.py`](../pipeline/1_ocr_and_extract_vlm.py) /
[`2_translate.py`](../pipeline/2_translate.py), and a couple of
architectural choices. Each entry says which.

---

## Part 1 — OCR (olmOCR-2-7B-1025, vision, port 11435)

olmOCR-2 is a Qwen2.5-VL fine-tune for document OCR. It is good at prose
and layout and bad in a handful of specific, repeatable ways.

### 1.1 Repetition loops on dot-leader and ornament pages

**Seen:** Spinnewyn pp. 27 & 71 (table of contents, `Préface . . . . 7`),
Gomard ~10 ToC pages, any page with a `* * *` scene-break ornament.

**Failure:** the model latches onto a repeating visual pattern —
row-of-dots leaders, a line of asterisks — and emits it thousands of
times. One Spinnewyn ToC page produced ~4 000 words of `. . . . . .`.
That then **overflows the translation model's 4096-token context** on the
next stage, so the page comes back `[translation unavailable]` and (before
the fix) the raw garbage also shifted later pages.

**Mitigations (three layers):**

| Where | What |
|---|---|
| VLM request, `1_…py` ~L413 | `"repeat_penalty": 1.12` on the completion — makes the loop less likely to start |
| `collapse_runs()`, `1_…py` L135 | clips a pathological run *after* the response comes back, before parsing: `x x x x …` (≥8 reps → keep 4), one char ×12+ → keep 6, a whole line ×4+ → keep 2. Also applied by [`reprocess_extracted.py`](../pipeline/reprocess_extracted.py) so an already-OCR'd book can be fixed without paying for OCR again |
| `2_translate.py` L390 | oversized-input guard: `src_words > 2500` → translate the first 2500 words, keep the rest verbatim, flag the page. Stops one bad page burning 3 dead API calls |
| `vlm_ocr_prompt.txt` | "Render a decorative separator … as a line containing only: `* * *`" and "Do NOT paraphrase, summarize, or silently skip words or lines, even if they look repetitive" |

### 1.2 Fabricated / normalised page numbers

**Seen:** engraving plates with a plate number in the image, library
accession stamps, roman-numeral front matter.

**Failure:** the model reports a number that isn't the folio — a plate
number baked into an illustration, a stamp — or silently converts `VIII`
to `8`. Jacob Jules (Gallica, 68 ppi) is the extreme case: only **27 % of
folios are trustworthy** because the low-res page numbers are barely
legible, so the model guesses.

**Mitigations:**

- `vlm_ocr_prompt.txt` PAGE_NUMBER field: "Do NOT report a number that
  appears inside an illustration or engraving plate, a library stamp, or a
  catalogue/accession annotation. … if it is set as a roman numeral (e.g.
  VIII), give the roman numeral, not an arabic conversion."
- `reconcile_page_numbers()` ([`1_…py`](../pipeline/1_ocr_and_extract_vlm.py)):
  a folio is trusted when **a neighbouring sheet's folio continues the
  printed sequence** (`i-1 → v-1` or `i+1 → v+1`, also across a short run
  of *unread* pages). Non-trusted pages are interpolated from the nearest
  trusted anchor and bracketed `[n]`. It never emits ≤ 0.
- **Tight-gap promotion:** a run of *unread* pages bracketed by two read
  folios `P … Q` where `Q − P == run + 1` is filled in exactly (`P+1,
  P+2, …`) and shown as real numbers, not `[n]`. A run of **unnumbered
  plate leaves** breaks that arithmetic (text 16 → 3 plates → text 17,
  `Q−P = 1 ≠ 4`) and stays bracketed. The PDF steps then *suppress* a
  bracketed folio line entirely rather than print `Page —`.
- Was a single **book-wide** index→folio offset: any folio off that offset
  was discarded. Joseph-Renaud inserts a plates section partway through,
  which shifts the offset, so the genuine run "20 21 22" got thrown away
  and shown as the sheet ordinal `[40]`. The local-run rule fixes it —
  a real "20 21 22" corroborates itself regardless of a later offset.
- `page_numbers.txt` records the result, one line per page; `[n]` means
  "inferred, not a printed number".

- *2026-09-18:* a plate leaf's OWN engraver's plate number could still
  get trusted as if it were a real folio, when it happened to match
  what the surrounding sequence's arithmetic predicted. Gomard pdf 375:
  the plate reads "Planche XVII." (folio 17), which coincidentally lined
  up with the real run around it, so `Page XVII` rendered as if genuine.
  `is_plate[i]` already kept a plate leaf's bogus number from breaking
  OTHER pages' gap checks, but not from being trusted for ITSELF -- three
  separate places in `reconcile_page_numbers()` could each independently
  mark it trusted (the direct-neighbour check, the monotonic-chain
  endpoints, and the fixed-point interpolation pass); all three now
  exclude a plate leaf's own read. Falls through to the existing
  "inferred plate leaf → no folio" path instead (blank), which was
  already correct behaviour, just unreachable for a leaf whose own bogus
  number happened to agree with the real sequence. Regression test:
  `test_reconcile_plate_own_read_never_trusted`.

- *2026-09-20:* the SAME plate-own-number bug, second instance -- two
  Gomard plates ("Planche XIV."/"Planche XV.") the VLM never flagged
  with a `FIGURES:` entry at all (no figure, no caption block -- body
  just opens directly with the plate line) fell through all three
  `is_plate[i]`-gated exclusions above, since that signal was keyed
  entirely on `figures.json`. `is_plate_leaf()` (1.2e) adds a
  body-content fallback so a plate is recognised as one even when the
  VLM's own figure detection missed it.

- *2026-09-20, known unresolved gap (worked around by hand, not fixed in
  code):* two ADJACENT pages can each independently misread the SAME
  digit position the SAME way (Gomard: real folios 298/299, both OCR'd
  with a 9→0 confusion to "208"/"209") and end up MUTUALLY self-
  consistent -- 209 − 208 = 1 satisfies "rises by exactly one across one
  sheet," which is exactly what a genuine correct pair looks like, so
  BOTH the direct-neighbour check (`d=1`) and the monotonic-chain check
  trust the pair off each other, never consulting the wider context (a
  real anchor just one page later, 300, that would immediately reveal
  the contradiction). A cross-check against nearby anchors was
  prototyped and reverted: the trap already happens in the FIRST
  (direct-neighbour) pass, so by the time any later pass runs its own
  "did a real anchor already vouch for this" snapshot, the poisoned
  pair is already in it, and widening the fix to reach back before that
  pass touches the most heavily-relied-on, most heavily-tested part of
  this function for one confirmed instance -- not worth the regression
  risk this late without a second real example to design against.
  Patched directly instead: `extracted.txt` and `page_numbers.txt` for
  the two affected Gomard pages were hand-corrected to 298/299. **This
  survives until the next `reprocess_extracted.py` run on this book**,
  which will regenerate both from `raw_ocr.txt` and silently reintroduce
  208/209 -- the algorithm itself is unchanged. If this pattern turns up
  in another book, that's the signal to actually fix the general case.

**2026-09-25 — false agreement between adjacent pages, fixed in general.**
Two misreads that differ by exactly 1 vouch for each other in the checks
above. Three real cases, all found by the review report (not by reading):
Gomard scans 314-315 (298/299 read `208/209`, blurred 9), Prévost 262-263
(198/199 in old-style figures read `108/109`), Spinnewyn 170-171 (a blank and a
title page with no folio, given `10/11`). A short run (≤ 3) of trusted pages
is now untrusted when the trusted pages on either side agree with each
other across the gap (exactly, allowing for plates) and the run doesn't fit.
The existing tight-gap promotion then supplies the forced values. A genuine
renumbering (roman → arabic) can't trigger it. Same pass: a folio repeated
by the next trusted page is dropped from the later page (Kirchhoffer scan 65, a plate
read `38` after the real p. 38), **unless the two pages carry the same
text**, i.e. the source scanned one leaf twice (Jacob scans 201-202, both
p. 155), which is kept. Dry-run across all 6 books changed exactly these
cases. The Gomard 298/299 hand patch is retired.

### 1.2b Folio / running-header leaked into BODY_TEXT

**Seen:** Joseph-Renaud — a page's translation opened with a stray line
`25` then `THE GUARD` (the folio and the running head "LA GARDE"), before
the real body.

**Failure:** the VLM sometimes drops the printed folio and the running
header *into* `BODY_TEXT` instead of the `PAGE_NUMBER` / `HEADER` fields.
Gomard 1845 does this on **~50 body pages** — line 1 is `246 DES ATTAQUES
SUR PRÉPARATIONS.` (folio + running head together), which also empties
`PAGE_NUMBER`, so every folio ends up interpolated and bracketed.

**Mitigations** (all in `postprocess_parsed_pages()`, shared with
`reprocess_extracted.py`):

- `salvage_leaked_folio_header()` — line 1 matching `<num> <ALL-CAPS
  HEAD>` (either order) → the number goes to `PAGE_NUMBER` as a **trusted**
  read, the caps run to `HEADER`, the line is stripped from the body.
  Guarded: the caps run must match a header seen elsewhere in the book or
  otherwise look like a running head.
- `strip_leaked_headmatter()` — still the backstop for a bare leaked
  number or header with no companion, using the book's whole header
  vocabulary.

**1.2b-2 (2026-09-11) `clean_vlm_extraction()`'s empty-body fallback also
leaked header text into a page's *translated* body.** A plate leaf with
a genuinely empty `BODY_TEXT:` and a multi-word `HEADER:` (common once
1.2c stopped inventing bracketed folios — a blank folio + a real header
is now routine) fell through to a fallback meant to strip stray labels:
`re.sub(r'PAGE_NUMBER:\s*\S+', ...)`. `\s*` also matches the newline
between `PAGE_NUMBER:` and `HEADER:`, and `\S+` doesn't stop at a colon,
so it ate `PAGE_NUMBER:` *and* the `HEADER:` label in one bite — leaving
the header's own text (`FLEURET LES PARADES`) stranded, unlabeled, and
indistinguishable from real body prose. It then got machine-translated
("FOIL THE PARRIES") and rendered in place of the real plate caption
(already sitting correctly in `figures.json`, from 1.2d below) — 40/110
sheets on one Kirchhoffer plate run alone. Fixed: an empty `BODY_TEXT:`
(the primary regex's `(.+)` can't match zero characters) now returns `""`
directly instead of falling through to the label-stripper.

**1.2b-3 (2026-09-18) the tail-catchword strip's own docstring example
was never actually caught.** `strip_leaked_headmatter()`'s tail check
only recognised a bare number OR a line matching a CONFIRMED PAGE header
(`known_headers`) -- but a printer's signature catchword (a short line
at the foot of a gathering's first page, matching the BOOK's own overall
title, not any specific page's running head) is never itself a page
header, so it was never in that set. Kirchhoffer: "ESCRIME." (from the
title page, "L'Escrime : fleuret, épée, sabre") sat at the foot of src
17 and 33, untouched, on every gathering. Widened the TAIL check only
(never the top -- a different risk profile; a leaked folio/header at the
START of a page is common and expected, a stray short line there is more
likely genuine content) to also catch a standalone line that's very
short (<=2 words) and title-shaped (mostly uppercase), regardless of
whether it matches a confirmed page header. Regression tests:
`test_strip_tail_catchword_not_a_known_page_header`,
`test_strip_tail_catchword_keeps_only_line`,
`test_strip_tail_catchword_does_not_eat_real_short_ending`.

### 1.3 Accents added to bare display capitals

**Seen:** section headings like `CROISÉ`, `ÉPÉE`, `ÉCOLE`, `ÉPREUVE`.

**Failure:** period French typography routinely sets display/title
capitals *without* accents (`EPEE`, `ECOLE`). The model "corrects" them to
the modern accented form — or, going the other way, drops a letter when it
does. Downstream this collides with the translation prompt's rule about
keeping kept-terms intact.

**Mitigation:** `vlm_ocr_prompt.txt`: "Do NOT add accents to capital
letters that are printed without them … transcribe the bare capital
exactly as set. (Do not remove an accent that IS printed.)"

### 1.4 Publisher's / printer's device motto lettering

**Seen:** the Larousse **sower colophon** — the "Je sème à tout vent"
motto curled around the engraved logo — transcribed as a garbled fragment
in the body text.

**Mitigation:** `vlm_ocr_prompt.txt` IGNORE NON-PAGE CONTENT: "a
publisher's/printer's device (an engraved logo or colophon) with motto
lettering worked into it. Do not transcribe any of that." Belt-and-braces:
`translation_prompt.txt` rule 8 tells the translator to drop it if it
slips through.

### 1.5 Command lists and drills flatten

**Seen:** lesson chapters with one imperative per line, indented, and
`Doublez { dessus / dedans / dessous` brace groupings.

**Failure:** the model reads a column of short commands and returns them as
a comma-separated sentence, losing the drill structure.

**Mitigation:** `vlm_ocr_prompt.txt` COMMAND LISTS AND DRILLS section:
one command per line, preserve leading indentation, render a brace group
as `command { ` then each item on its own aligned line. Still only
partly effective — see 2.4.

### 1.6 Printed marginalia — inconsistent

**Seen:** Gomard 1845 — printed marginal shoulder-notes next to the main
text.

**Behaviour:** olmOCR *mostly* omits them, but sometimes appends one to
`BODY_TEXT` as a trailing standalone paragraph, which then gets translated
and dumped at the bottom of the column by the alignment pass — reading as
a body non-sequitur.

**Mitigation:** `label_margin_notes()` ([`2_…py`](../pipeline/2_translate.py))
— a trailing single-line paragraph of ≤ 20 words that isn't a heading /
footnote / list item is moved to the note strip as `[margin note] …`, so
it's clearly not body. Non-destructive (never deleted).

*2026-09-01:* tightened after three false positives on Prévost (a plate's
second caption title tagged `[margin note]`). It now bails when the page
carries a figure (`has_fig`), when the trailing line matches a figure
caption shape (`_FIG_TITLE_RE` — "Parry of …", "Position", "Fig."), or
when the page isn't prose-dominant (< 3 real paragraphs). Cost: a genuine
shoulder-note on a 2-paragraph page now stays in the body — judged the
lesser evil.

*2026-09-11:* the real remaining false-positive source was the page
*boundary*, not the figure/caption cases above — a page's actual last
paragraph, cut off mid-sentence by where OCR split the pages, looks
exactly like a short standalone note. First pass excluded a trailing
`,`/`;`/`:` (Prévost src 15/17); a corpus scan then found **40/67** tagged
notes with *no* terminal punctuation at all — mid-word, mid-clause,
clearly page-boundary cuts, not blacklistable one at a time (Gomard "One
breaks the distance to disengage from an enemy"). Replaced the blacklist
with a whitelist: a genuine note is a complete remark, so now **require**
the paragraph to end in `.`/`!`/`?` (optionally + a closing quote/paren)
to be eligible at all. Dropped false positives to 0 in the three books
retranslated against it; 67→26 tagged notes corpus-wide.

*2026-09-18:* the note isn't always the page's actual last paragraph
either -- real body prose can continue after it. Gomard src 32: the
genuine note, "He recognizes only three ways of striking.", sits ONE
paragraph before the end, immediately followed by a real closing
paragraph ("Like Marozzo, his cuts are directed at the shoulder,
chest…") that used to get wrongly tagged as the note instead. The two
are shape-IDENTICAL by every check above (both short, both end a
sentence, neither uppercase-heavy) -- position was the only thing
distinguishing them, and the old code only ever checked the true last
paragraph. Fixed with a principled reordering, not just "check one more
paragraph back": a genuine printed shoulder-note is a summary that
INTRODUCES what follows it in the main text (a marginal "argument", not
a conclusion), so it is essentially never the page's actual last
paragraph. Check the second-to-last paragraph FIRST for exactly that
reason, and only fall back to the true last paragraph if that one isn't
note-shaped. (An earlier attempt just widened the search to "last 2, closest
first" without this reordering -- it still picked the wrong one on this
exact real page, since the true-last paragraph is checked before the
second-to-last and both pass the shape test.) Kept narrow on purpose:
never reaches back to paragraph 1, since a 3-paragraph page's own
opening paragraph can incidentally satisfy the same shape checks without
being anywhere near a margin note. Regression test:
`test_label_margin_notes_prefers_note_that_precedes_more_prose`.

*2026-09-18: this function still only ever finds ONE candidate note per
page.* Gomard src 32 actually has two margin-note-shaped paragraphs; the
disambiguation fix above correctly identifies the right one to remove
from body, but a reader seeing `[margin note] <text>` rendered in the
notes strip has no way to know it's paired with THIS specific spot in
the body rather than some other spot -- especially on a page with more
than one. Rather than build (and then have to trust) a more elaborate
multi-note detector, changed what happens on a hit: still detect it and
remove it from body (so it doesn't pollute body prose), but do NOT
render it anywhere -- no `[margin note]` line, no footnote-strip entry.
Simplest honest fix: stop claiming to faithfully reproduce something the
pipeline can only partially, unreliably locate.

### 1.6b Cross-page word hyphenation

**Seen:** Gomard 1845 — a word split by end-of-page hyphenation in the
original (`… déjà pu-` | `bliés, on …`) — **19 pages**. Each fragment
translated on its own is garbage ("already pu- / blés, one could…").

**Mitigation:** `dehyphenate_across_pages()` — when page *i* ends
lowercase+hyphen and page *i+1* starts lowercase, the completion fragment
(plus glued punctuation) is spliced up to close the word on page *i*. A
capitalised continuation (`Sainct-` | `Didier`, `La-` | `Boëssière`) is a
real hyphenated name and is left alone.

**Extended 2026-09-30 — the halves aren't always adjacent.** Kirchhoffer
scan 12 ends "Il faudra se mé-" and then the author's footnote, so the
text did not *end* with the hyphen and the join never ran: "se mé-" /
"fler" (the OCR's misread of *fier*) became "smell of" instead of "beware
of". Now the join also looks past a footnote block closing the page ("(1) …"
or "* …") and past margin notes opening the next page. Found on 4 page
pairs (Baudry 67, Gomard 24 and 28, Kirchhoffer 12).
- **Regression caught before it shipped:** the first version also treated
  a numbered paragraph ("1. …") as a footnote block, cut the page there,
  and so *un*-joined four words that were already correct (LaMarche
  117/200/267, Spinnewyn 112). Only "(1)" / "*" markers count now, and the
  plain end-of-page case runs first, exactly as before. Found by
  reprocessing scratch copies of all 9 books and diffing every page before
  touching the real files — the pattern to repeat for any parse change.
- **Not handled:** Gomard 81/82 ("pro-" / "nation"), where a margin note
  sits between the halves *in the raw OCR* and is only moved out after
  the join has run. One instance: fixed in the overrides file.

### 1.6c Glossary / "termes techniques" sections

**Seen:** Kirchhoffer — a multi-page *TERMES TECHNIQUES* section, plus
shorter *Particularités* runs (`Headword. — definition`).

**History:** `apply_glossary_mode()` (2026-08-31) kept every headword in
the source language, italic, and translated only the definition.
**Removed 2026-09-01** — leaving the term untranslated left the reader
without the English word the body text uses for it. A glossary section
now goes through the normal term policy like body text; the facing scan
still carries the original headword.

**Headword case:** a kept term (category 4) kept lowercase-italic loses the
capital the source gives it at the start of an entry (`<i>riposte</i>. —
…` where the book prints "Riposte."). `_capitalize_definition_entries()`
(2026-09-02) upper-cases the first letter of any line shaped
`<short headword>. — <definition>`, preserving the `<i>` wrapper; a long
lead-in before `. —` is treated as a sentence and left alone.

### 1.2c Running header vs. one-off title — `reconcile_headers()`

**Seen:** all three review books — a part title (`QUATRIÈME PARTIE`), a
section opener (`DES CONTRES`), a title-page title, a plate caption: any
of these lands in the VLM's `HEADER:` field on the page where it sits at
the top, and in `BODY_TEXT` elsewhere, so the same kind of line renders
as a small italic running head on one page and body text on the next.

**Mitigation:** `reconcile_headers()` resolves it by the one reliable
signal — repetition. A `HEADER:` string seen on **< 2 pages** is not a
running head: it is prepended to the body as its own line and the field
cleared. A `HEADER:` that *is* repeated stays in the bar on every page of
that section. The inverse also fires: an empty `HEADER:` whose body opens
with a line that is *exactly* a confirmed running head lifts that line
back up. No heading tier, no size/weight/centring — a title in the body
just renders as a plain line where it falls (deliberate scope limit; a
real heading hierarchy needs a VLM-prompt change + re-OCR).

Repetition is counted per **running-head root**: a two-part head
`LEFT⇥RIGHT` (section + per-page topic — Kirchhoffer `SABRE⇥CORPS A
CORPS`, `ÉPÉE⇥CROISÉ`) keys the count on `LEFT` only. The right half is
unique to one page; counting the whole string sent every plate-page
header in a section into the body (batch-2 regression).

*2026-09-11: stopped demoting one-off headers at all.* The repeat-count
demotion (`< 2 pages → body`) turned out to also catch genuine running
heads for a section that's only one page long — it can never repeat, by
definition. Visually confirmed against the actual scan: Kirchhoffer
`PARTICULARITÉS` (src 13) sits in the *exact* folio+title+rule position
of every other running head; there's no separate body-level duplicate to
tell it apart from noise by pattern-matching, either — the body just
opens directly with the section's prose. A repeat-count filter cannot
distinguish "real header, one-page section" from "OCR noise"; per review
feedback, fidelity wins — `reconcile_headers()` now trusts whatever
`HEADER:` olmOCR-2 reported, full stop. The one known real noise source
(a plate caption filed in `HEADER:`) is caught separately and earlier, by
1.2d below, before this function ever sees the page. The **lift**
direction (promote a confirmed header from a matching body line when
`HEADER:` is empty) is unaffected — still gated on repetition, since
that direction is inference from pattern-matching, not a value the model
directly reported.

*2026-09-14, three more gaps in the same mechanism, found on a third
review pass over the same three books:*

- **Some section-openers never get a real `HEADER:` value at all, ever
  -- the model consistently writes `HEADER: none` and leaves the title
  only as the body's own opening line, on every page of that section**
  (Kirchhoffer `TERMES TECHNIQUES`, `PARADES ET RIPOSTES`,
  `ROMPRE -- DIVERS`; Gomard `CONSULTÉS PAR L'AUTEUR.`). Since it's never
  reported as an actual header value anywhere, it can never become
  "confirmed" for the lift direction either, no matter how many pages
  exist. Added a second, narrower lift condition: a body-opening line
  standing alone (its own paragraph), short (<=6 words) and mostly
  uppercase gets lifted even without prior confirmation. Deliberately
  excludes an ordinary glossary headword ("Croisé. -- Parade qui
  consiste...", mixed case, dash-definition on the line) by the case
  ratio alone.
- **An isolated single-page OCR miss inside an otherwise heavily-
  confirmed running-head section** -- neither the header field nor the
  body carries the title on that one page (Kirchhoffer `ÉPÉE` src 27/30;
  Gomard `PRÉFACE.` src 13, and a 70-page `INTRODUCTION.` run whose very
  first page has nothing to recover -- a genuine, unrecoverable gap, left
  as-is). Added sandwich interpolation: a still-headerless page whose
  immediate neighbours on both sides already agree on the same confirmed
  header inherits it, mirroring how `reconcile_page_numbers()` already
  interpolates folios. Deliberately narrow -- single-page gap only, both
  neighbours must already agree, so a page next to a freshly-*lifted*
  one-off section-opener (previous bullet) won't match and stays open
  (Kirchhoffer src 30, sandwiched between `PARADES ET RIPOSTES` and an
  unrelated one-off `CONTRE LE BRAS TENDU` -- residual at the time; fixed
  2026-09-18, see below).
- **Order-swapped two-part header, tab dropped** (already known-deferred:
  Kirchhoffer `FLEURET`/`LA MISE EN GARDE` plate run reads
  `LA MISE EN GARDE FLEURET` on some leaves instead of `FLEURET`+tab+
  `LA MISE EN GARDE`). `FLEURET` is confirmed dozens of times elsewhere;
  when a tab-less header's word sequence starts or ends with an
  already-confirmed root, split there and rebuild the canonical
  root+tab+remainder form. Gotcha: the swapped form repeated identically
  on >=2 leaves, so it satisfied its own count>=2 threshold as an
  accidental standalone root and skipped the reorder check on the first
  attempt -- the reorder now runs for any tab-less header regardless of
  whether the swapped text is itself "confirmed".

Both new lift paths depend on the body still carrying the duplicate text
when `reconcile_headers()` runs -- but `strip_leaked_headmatter()` (runs
earlier, in `postprocess_parsed_pages()`) strips any body line matching
*any* known header value as apparatus, which would erase that exact
evidence first. `known_headmatter()` restricts what counts as "known" to
CONFIRMED header values only, so a one-off's body duplicate survives long
enough for `reconcile_headers()` to see it (Gomard `CONSULTÉS PAR
L'AUTEUR.` on src 21 -- its only other reading, on src 19, is also a
one-off, so without this restriction the duplicate was stripped and an
unrelated same-root sandwich interpolation from `LISTE DES OUVRAGES`
neighbours won instead).

*2026-09-18, two more gaps found reviewing a rebuilt PDF against the
real scans (not just the OCR text) -- both caught real bugs the earlier
text-only diagnosis got wrong the first time:*

- **A page genuinely has no header at all in raw OCR, sandwiched between
  two DIFFERENT one-off (never independently confirmed) headers.**
  Kirchhoffer src 30: raw OCR literally reports `HEADER: none`, between
  `PARADES ET RIPOSTES` (src 29, a one-page interlude) and `CONTRE LE
  BRAS TENDU` (src 31, a different one-page interlude) -- neither
  neighbour repeats anywhere else in the book, so sandwich interpolation
  (which requires both neighbours to already agree) correctly declines.
  First fix attempt was wrong: "carry the preceding header forward"
  would have wrongly propagated `PARADES ET RIPOSTES` onto src 30.
  Rendering the actual scan page settled it -- src 30's real running head
  reverts to the parent section, `ÉPÉE`, which last appeared 2 pages
  back and is independently confirmed (8 native occurrences). Fixed with
  a new pass: search outward in both directions (capped at a 4-page gap)
  for the nearest header that IS independently confirmed (>=2 native
  occurrences), preferring whichever is closer. Deliberately runs AFTER
  sandwich interpolation, which is more precise when it applies.
- **A native (non-empty) `HEADER:` is itself the wrong half of a
  two-line compound title block, printed within the body area on a
  section's one opening page, no small running head there at all.**
  Gomard: `HEADER: LISTE DES OUVRAGES`, body opens with `CONSULTÉS PAR
  L'AUTEUR.` -- rendering the actual scan showed a LARGE two-line title
  ("LISTE DES OUVRAGES" / "CONSULTÉS PAR L'AUTEUR.") sitting in the body
  column, not the margin; the genuine small running head (just "LISTE
  DES OUVRAGES", confirmed by repetition) only starts on the section's
  SECOND page. This is a different code path from the empty-`HEADER:`
  lift above -- the model read the first line natively into `HEADER:`
  itself, so it never reaches the promotion logic at all. Fixed with a
  local per-page signal: a native header immediately followed by a
  SECOND title-shaped body line (short, standalone, uppercase-heavy) is
  a compound title block -- demote the header back into body, both lines
  together, as printed. Runs last, after the gap-fill above (ordering
  matters: demoting first just let the gap-fill put the header right
  back from the neighbouring confirmed page).
  **Must NOT fire when the body's first line is a near-duplicate of the
  header itself** (normalises the same, punctuation aside) -- that's a
  different, deliberately-fine convention this corpus already relies on
  (Prévost src 21: header carries `DE LA MANIÈRE DE SE RELEVER.`, body
  repeats it verbatim before its own subtitle -- "header and a body
  title both is fine if that's what the book does", confirmed earlier).
  The Gomard bug is a header plus a DIFFERENT second title line forming
  one block; an echo of the header is not that.

*2026-09-18, two severe regressions in the two mechanisms just above,
both caught before they shipped further -- both already in a PDF sent to
the user as "verified" when found, both fixed the same day.*

- **The nearest-confirmed-header gap-fill had no bound relative to the
  TRUE source.** It searched from LIVE, in-progress results: once page N
  got (correctly) gap-filled from a genuine confirmed header, page N+4
  could then treat page N's fresh value as if IT were a genuine anchor,
  chaining indefinitely. Gomard: a real `TABLE DES MATIÈRES.` header
  cascaded across an entire ~54-page run of otherwise-blank plate leaves,
  each individually <=4 pages from the PREVIOUS (already wrongly-filled)
  page rather than from any genuine source -- reaching all the way to an
  unrelated `Planche XVI.`/`Plonche XIX.` plate run. Fixed: snapshot the
  original confirmed-header positions before the loop starts, search only
  from that fixed snapshot, and apply every fill together at the end --
  no fill can feed another fill within the same pass. Regression test:
  `test_reconcile_headers_gap_fill_does_not_cascade`.
- **The native-header compound-title demotion (just above) was built as
  a GENERAL shape rule and is structurally indistinguishable from an
  extremely common, totally normal pattern: a book-wide running head
  followed by a new chapter's own title starting the body under it.**
  Prévost: `THÉORIE PRATIQUE DE L'ESCRIME.` -- the book's actual running
  head, confirmed 69x across the whole book -- was wrongly demoted on
  20+ pages, once for every chapter that happened to open with a short
  uppercase title of its own underneath it. A genuine compound-title-
  block page (Gomard) and a genuine running-head-plus-new-chapter page
  (Prévost, and very common generally) cannot currently be told apart by
  text shape alone -- both are "native header + a second, different
  title-shaped body line" -- without visually checking whether the scan
  actually shows a small margin header on that specific page. The
  confirmation-COUNT gap between the two real examples was huge (Gomard
  "LISTE DES OUVRAGES": 3 native occurrences; Prévost's running head: 69)
  but that's two data points, not a calibrated threshold, and this bug
  had already shipped once. Reverted to a narrow, explicit, named set of
  confirmed real instances (`_KNOWN_COMPOUND_TITLE_BLOCKS`) instead of a
  general shape rule -- matching this file's other narrow, evidence-based
  repairs (`_OCR_PHRASE_FIXES` below) -- add to the set only after
  visually confirming a NEW instance against the actual scan, same as
  this one was. Regression test:
  `test_reconcile_headers_does_not_demote_unlisted_running_head`.

  **Process lesson:** a demotion mechanism changes what text is actually
  IN the body, not just which field displays it -- any page a demotion
  (or the reverse: a promotion) touches has DIFFERENT source text now,
  so its ALREADY-SAVED translation is stale and silently missing/wrong
  content until that one page's body is retranslated (Gomard "LISTE DES
  OUVRAGES" / "List of Works": the demotion fix correctly added the line
  back into body, but the old translation predated that change and only
  ever translated what was in body at the time -- patched as a single
  targeted line-translation, not a full retranslate, once identified).

**Recurred the same day** on the ESCRIME catchword strip (1.2b) --
same lesson, same fix pattern (single targeted patch on the affected
page's already-saved translation, not a full retranslate).

*2026-09-18, two more real gaps in the same neighbourhood, found on the
NEXT review pass after the two regressions above:*

- **A genuinely blank, unprinted leaf could still inherit a header** from
  either sandwich interpolation or the nearest-confirmed gap fill.
  Prévost: several leaves before "PREMIÈRE PARTIE" begins are blank in
  the actual scan -- no header, no body text, no figure, nothing at all
  printed but faint digitisation specks (confirmed by rendering the
  actual page) -- yet kept inheriting a neighbouring section's running
  head. Unlike a plate page (no body text, but IS a real printed page
  that typically still carries the running head), a truly blank leaf has
  nothing printed on it, including no header. Added a shared
  `_is_blank_leaf()` check (no figure AND no real body text) and
  excluded it from both mechanisms. First attempt at the check missed
  the real cases entirely: `BODY_TEXT: none` is parsed into the LITERAL
  STRING "none" at this stage, not cleaned to an empty string until
  later in the pipeline -- a blank check looking only for "" found
  nothing to exclude. Fixed to also treat the literal sentinel as blank.
  Regression tests: `test_reconcile_headers_skips_genuinely_blank_leaf`,
  `test_reconcile_headers_skips_blank_leaf_with_literal_none_body`.
- **A part-division title ("<Ordinal> PARTIE") reported natively in
  HEADER: instead of BODY_TEXT:, inconsistent with every other instance
  in the same book.** Prévost has 6 part titles; 5 are natively in
  BODY_TEXT (confirmed by direct comparison) and this book's own
  convention is titles-as-body-text throughout (no tiering) -- but
  "TROISIÈME PARTIE" specifically landed in HEADER: on its one page,
  model noise rather than a genuine different printing convention for
  that one part. Narrow, closed-set shape match (a specific list of
  French ordinals + "partie", not a general "short uppercase line"
  rule) demotes it unconditionally -- regardless of what follows, unlike
  the compound-title-block rule above which requires a second
  title-shaped body line. Regression test:
  `test_reconcile_headers_demotes_part_title_from_header`.

### 1.2d Plate caption in `HEADER:`; scanner apparatus in `HEADER:`

* `fold_caption_header_into_figure()` — on a page that carries a figure
  and little/no body, a `HEADER:` matching a caption shape
  (`_CAPTION_SHAPE_RE` — `Parade de …`, `Planche N`, `Nᵉ position`,
  `Salut`, …) is folded into the figure caption and the field cleared, so
  a plate page never carries a running head and the caption renders as
  one unit. Runs before `reconcile_headers()`.
* `drop_scan_apparatus()` — a scanner target card ("Contraste
  insuffisant"), a library plate ("Bibliothèque Larousse 15720"), a bare
  shelfmark ("NF Z 43-120-14"), a BnF catalogue line ("… / par …",
  "Auteur du texte") in `HEADER:` or a near-empty body is blanked.
  `drop_google_boilerplate()` also covers the Gallica terms-of-use page.
* `recover_plate_captions()` case (1) — a figure-less page whose short
  body **opens with** `Planche N.` **or** `Fig. N.` is a plate whose
  image the VLM missed: synthesise the plate, promote the body to its
  caption. Skipped when a neighbour ±2 already carries a plate with the
  same caption (OCR read one leaf twice under different folios — e.g.
  Kirchhoffer's épée `FIG. I` at both the mis-numbered leaf and folio 42).
* case (2) — a flagged figure with an empty caption borrows a
  caption-shaped line (`_PLATE_CAPTION_RE` — `Parade de …`, `Fig. N`, a
  numbered position, …) from this page or a neighbour ±2. *2026-09-14:*
  the neighbour search had no length guard, so it could pull a
  caption-shaped SUB-HEADING off a full page of ordinary prose as if it
  were that neighbour's whole caption (Prévost: "Parade de quarte."
  merely opens a paragraph describing that parry on a text page, wrongly
  borrowed into the NEXT plate's caption — "PARADE DE SECONDE — Parade de
  quarte" instead of just "Parade de seconde"). A genuine facing-leaf
  caption IS that leaf's whole (short) body; a neighbour's line is now
  only eligible when its own body is ≤15 words.
* *2026-09-16:* `parse_figures_block()` was silently dropping a reliable
  caption. On a plate-only page (nothing else to report), the model
  reports the caption twice: once inside `FIGURES:` (its own restatement
  — case-unreliable, e.g. `Parade de seconde.`) and once repeated in the
  slot it defaults to when there's no real body text. When the model
  labels that repeat `BODY_TEXT:`, `_fold_caption_only_body()` already
  recovers it correctly. When it doesn't (skips straight from `FIGURES:`
  into the bare repeat, no label), the repeat fell inside the FIGURES:
  block text and `parse_figures_block()`'s line loop didn't recognise it
  as anything — not a new `kind |` row, not a lowercase continuation — so
  it hit the trailing `else: continue` and was discarded, leaving the
  unreliable FIGURES: version as the caption. Caught after the user
  visually confirmed the physical book prints these captions in
  consistent full caps (initial guess — that a clean, consistent
  per-plate case pattern in the raw OCR meant genuine source variation —
  was wrong; the pattern was an artifact of which pages happened to get
  the `BODY_TEXT:` label, not of the printed page). Fixed: a leftover
  line that normalizes (case/accent/punctuation-insensitive) to the same
  text as the figure's own caption is now treated as a same-caption
  repeat and its casing is preferred, instead of being dropped. Hit 8/109
  Kirchhoffer pages (src 26-33, 54, 88 — one plate run); zero in Gomard
  or Prévost. Fixed via `reprocess_extracted.py` (parser-only, no re-OCR)
  + a standalone re-run of `translate_figure_captions()` for just this
  book's figures (no full retranslate needed — steps 3/4 already pick up
  the change). Regression tests:
  `test_figures_block_prefers_unlabeled_caption_repeat_case`,
  `test_figures_block_unlabeled_repeat_must_match_caption`.
  **Process note:** `reprocess_extracted.py` overwrites `figures.json` in
  place from the parser alone, which does NOT carry `caption_en` — back
  it up before running it on a book that's already been translated, or
  you'll need a standalone `translate_figure_captions()` pass to refill
  it (which is what happened here — not a data loss, but avoidable).

*2026-09-18:* a caption's second line starting with "(" was silently
dropped. Kirchhoffer pdf 47: `FIGURES: plate | Fig. 4. — LE CROISÉ AU
FLANC.` followed by `(La main de l'attaquant reste haute.)` on its own
line with no label -- a parenthetical continuation of the caption, not a
fresh figure. `parse_figures_block()`'s continuation-line check only
recognised a line starting with a digit or lowercase letter (`2e TEMPS :
…`) as a follow-on; a leading "(" fell through to the trailing
`else: continue` and was discarded. Fixed by widening the check to also
accept a leading "(". Regression test:
`test_figures_block_keeps_parenthetical_continuation`.

*2026-09-18: `fold_caption_header_into_figure()` required a figure to
already be flagged.* A "Planche N." / "Fig. N." header always reads as a
caption, never a running head (review feedback: put it in body, "it
reads like a caption so therefore body text") -- but the fold only ran
`if figs:`, so a plate the VLM failed to flag at all (Gomard "Planche
V./VI.": HEADER carries the plate number, body is a plain description
with no `FIGURES:` block whatsoever) left the header stuck as a running
head, inconsistent with plates the VLM DID flag (which folded fine).
Extended: when there's no flagged figure to fold into, prepend the
header text to body instead and clear HEADER — same "reads as a caption,
belongs in body" outcome, just without a figure caption to attach it to.
Regression test:
`test_fold_caption_header_into_body_when_no_figure_flagged`.

*Also 2026-09-18:* "Planche" (plate) misread as "Plonche" (A/O OCR
confusion) isn't a real French word, so the translator treats it as one
anyway and renders it "Plunge" (as if related to "plonger", to dive)
instead of "Plate" (Gomard pdf 379: "Plonche XIX." → "Plunge XIX.").
Every other correctly-spelled "Planche N." header in the same book reads
and translates fine — a one-leaf OCR miss, not a systemic misread.
Narrow, case-preserving fix added to `_OCR_PHRASE_FIXES` (1.2c), same
pattern as the "pas"/"par" fix there. Regression test:
`test_repair_source_ocr_misreads_plonche_planche`.

### 1.2e Header/body reconciliation, round 7 — five more real gaps

A single review pass on Gomard/Prévost/Kirchhoffer surfaced five more
distinct header/body edge cases, all found by tracing the *actual*
`raw_ocr.txt` → `extracted.txt` transformation for a flagged page rather
than guessing, and each fixed narrowly with its own regression test:

* **`leaked_top_header()`** — `strip_leaked_headmatter()`'s TOP match
  intentionally strips a CONFIRMED header's body duplicate (see
  `known_headmatter()`'s own docstring), trusting `reconcile_headers()`'s
  later `_MAX_GAP=4`-capped gap-fill to restore it into `HEADER:`. That
  cap is a distance from the nearest OTHER confirmed page — a run of
  several consecutive *malformed*-VLM-response pages (no `HEADER:`
  field at all, just raw text starting with the running head — Gomard's
  "TABLE DES MATIÈRES." back-of-book index, src 334) can put one page
  just outside it, and by then the text has already been deleted from
  body with nothing left to infer from. `leaked_top_header()` promotes
  the match into `HEADER:` on the spot, using THIS page's own evidence,
  before `strip_leaked_headmatter` gets a chance to discard it — strictly
  narrower than the gap-fill (no cross-page inference at all).
* **`strip_leaked_headmatter()`'s top-strip now checks what follows** —
  a KNOWN header's body duplicate is *usually* noise, but not when the
  very next line is ALSO a standalone title-shaped line with no blank
  line between them: that's a genuine two-line compound title printed on
  the page (Gomard "DE L'EMPLOI DE LA MAIN GAUCHE" / "COMME MOYEN DE
  PARADE ET D'OPOSITION." — together one title, "On the Use of the Left
  Hand as a Means of Parry and Opposition"; confirmed on 3 separate
  section-openers this pass). Stripping line 1 alone silently dropped
  half the title from the only place it was legible (the HEADER bar
  renders it separately, in isolation). The check is deliberately
  adjacency-sensitive: `"LE DUEL\n\nAVANT-PROPOS"` (blank line between)
  is the extremely common, unrelated "running head, then a new section's
  own title starts below it" pattern and must NOT trigger this — only
  `"LINE1\nLINE2"` with no blank line between reads as one continuous
  printed title block.
* **`is_plate_leaf()`** — the `reconcile_page_numbers()` plate-exclusion
  guard (1.2, "a plate's own printed number must never be trusted as a
  folio for itself") was keyed entirely on `figures.json`'s flagged
  `kind == "plate"` rows. A plate the VLM never emitted a `FIGURES:`
  block for at all — body just opens directly with `"Planche N."`, no
  header, no figure (Gomard "Planche XIV."/"Planche XV.") — fell
  through that net: its own `PAGE_NUMBER: XIV` read straight through as
  a trusted folio and rendered "Page XV" on a plate leaf. Added a
  body-content fallback (`^planche\s+[ivxlcdm\d]+\b` on body's first
  line) alongside the figures-based check. Deliberately narrower than
  `_CAPTION_SHAPE_RE` (1.2d) — that regex also matches ordinary in-body
  description lines ("Parade de sixte...") that legitimately open a
  normal text page too; only the "Planche N." branch specifically means
  "this whole leaf IS a plate."
* **`_reorder_swapped_two_part()` now requires the leftover to be
  header-shaped too** — this Kirchhoffer fix (below) reorders a
  no-tab, order-swapped two-part header ("LA MISE EN GARDE FLEURET"
  instead of "FLEURET\tLA MISE EN GARDE") onto its confirmed root. It had
  no check on the *other* half: a title-page leaf whose own one-off title
  happens to END in words that are ALSO an unrelated confirmed running
  head elsewhere in the book (Prévost: half-title page reads "L'Escrime
  et le Duel" — the book's own title — which coincidentally ends "le
  Duel", the later duel chapter's confirmed running head) satisfied the
  same match and produced a nonsense reversed split ("LE DUEL." /
  "L'Escrime et"). Now also requires the non-root leftover to look
  header-shaped (`_looks_like_section_title` — short, mostly-uppercase);
  "L'Escrime et" (lowercase "et", apostrophe-elided "L'Escrime") fails
  that and the reorder is skipped. When the reorder ALSO fails and the
  page has no body at all and the whole phrase isn't independently
  confirmed either, it's not a running head in the first place — it's a
  title-only leaf (a half-title page), and the header text is routed
  into body instead of staying wedged in the small margin-header bar.
* **Gap-filled header on a split-line confirmed title** — same root
  cause as `leaked_top_header()`, opposite direction: Prévost's own
  book-wide running head ("THÉORIE PRATIQUE DE L'ESCRIME.") appears
  split across the top TWO lines of body on the book's actual opening
  page (`"THÉORIE PRATIQUE"` / `"DE L'ESCRIME"`, native `HEADER: none`),
  followed by the chapter's own title on a third line. The two-line
  shape correctly blocks the ordinary "lift" promotion (compound-title
  guard, 1.2c), but `_MAX_GAP` gap-fill then fills the header anyway
  from a confirmed neighbour a few pages later — reproducing the book's
  own grand opening title as a small running-head bar above itself.
  Added a check, run last (after gap-fill): when body's own first TWO
  lines, joined, exactly reconstruct an independently-confirmed header
  root, clear whatever header ended up there (however it got there) —
  this is the book's title page, not a place for its own running head.
  Needed an apostrophe fold (`’` vs `'` — the SAME phrase's two
  occurrences used different Unicode apostrophes) since `_norm_head()`
  doesn't normalise that; folded locally rather than widening
  `_norm_head()` for every caller.

Regression tests: `test_leaked_top_header_returns_known_match` (+2
guard tests), `test_strip_top_keeps_compound_title_block` /
`test_strip_top_still_strips_plain_duplicate`,
`test_is_plate_leaf_detects_unflagged_planche_body` (+2),
`test_reconcile_headers_swap_reorder_skips_bare_title_leaf`,
`test_reconcile_headers_clears_gap_filled_header_on_split_title_page` /
`..._ignores_ordinary_chapter_opener`.

### 1.2f Round 8 (batch-2 pass 8) — three more, each scan-verified

**Bracketed outlier in the monotonic chain (`reconcile_page_numbers`).**
Kirchhoffer src idx 13-18: p.8, an "8*" extra plate leaf, two flagged
plates around an unnumbered *inset* plate the VLM read as "10", then a
real p.9. The chain paired 8 → 10 across one leaf (rise ≤ sheet gap) and
trusted both, printing "Page 10" on a leaf with no folio. Guard: a read
whose neighbouring reads bracket each other consistently while it falls
outside that bracket is not a chain link. Dry-run across all 6 books
changed only Kirchhoffer idx 14 / 16 (both now blank).

**Gap-fill onto title-only leaves (`reconcile_headers`).** The function
already recognised a compound title block ("QUATRIÈME PARTIE" / "DE LA
RIPOSTE") and a demoted half-title as "no running head here", then its own
sandwich / `_MAX_GAP` fills gave the page a neighbour's head anyway
(Prévost pdf 14, 132; Gomard pdf 339). Such pages are now marked
`_title_leaf` and skipped by both fills. 28 pages changed across 4 books,
all scan-checked; 26 correct, 2 Spinnewyn plate leaves (56/100) were
already wrong via an older demotion (real running head "LE RASSEMBLEMENT"
demoted to body) — logged in STATUS, not reprocessed.

**Title stripped as a "leaked header" (`strip_leaked_headmatter`).** Gomard
chapter openers print a small running head AND a centred title with the
same text (scan-verified on 7). olmOCR-2 reports both — HEADER: and body
line 1 — and the body copy was stripped as a leak. Now kept when the
page's OWN pre-promotion HEADER matches (`own_header` arg). A real leak
is the head read into body with HEADER empty; that path is unchanged.

**Not solvable from text (open):** a title-only opener whose title the VLM
files as body with `HEADER: none` (Gomard src 23 INTRODUCTION) is
text-identical to a page whose real running head was misfiled into body
with `HEADER: none` (Prévost idx 50 / 124 — scan shows a real head). A
printed folio doesn't separate them either. Next step: test a `TITLE:`
field in `vlm_ocr_prompt.txt` on ~20 already-diagnosed pages (STATUS).

### 1.6d Printed margin notes: dropped by the OCR, now read from the layout (2026-09-25)

olmOCR-2 transcribes the main text block and mostly drops printed shoulder
notes: of Gomard's 384 it kept about 25, usually pasted into the prose as
stray paragraphs. Text-shape heuristics can't recover them -- a short
sentence-ending paragraph is as likely a sub-heading, a signature or a
closing line (the old label_margin_notes() deleted Prévost's "Le rôle du
médecin." heading). The layout detector sees them directly: narrow text
boxes outside the page's **own** main column. (A book-wide column edge
missed all of them -- left and right pages differ.) Each crop is read by the
OCR model, which gets them right where the scan's Google text layer is
garbled. See `_margins.py`.

Placement lessons (Gomard, 2026-09-26): the detector sometimes also boxes a
note's first line alone -- in the bibliography, the year ("1778." as well
as "1778. Nicolas Demeuse."), 11 duplicates on scans 37/38/60/72 -- so a
note inside a bigger one, or whose text starts one at the same height, is
dropped. A note's line can share its words with an earlier paragraph
("maître d'armes. Paris" in two entries); ties go to the paragraph at or
after the previous note's, since both run down the page. Ties must not
depend on a set's iteration order: that made placement differ between
runs of the same code. Removing the OCR's pasted copy of a note needs the
word overlap to hold both ways: a short chapter title ("DE LA RIPOSTE.")
shares all its words with a longer note ("Valeur de la riposte.") and was
deleted as a "copy" -- 14 Gomard chapter titles lost in the first full
Qwen3.8 run, caught only by the review checklist, not the report.

### 1.7 Figure detection — VLM flag + density-profiler geometry

The pixel-only `find_figure_band()` (row ink-density, tuned for framed
half-tone photo plates) missed full-page line-drawing plates and faint
half-tones — Gomard: 4 of ~20. So the VLM now does *detection* and the
profiler only does *geometry*:

- `vlm_ocr_prompt.txt` has a `FIGURES:` section → the model reports each
  page as `plate` (whole-page picture) or `inset` (picture + running
  text) plus the caption verbatim. → `figures.json` (step 1).
- `2_translate.py` translates each caption through the **full**
  `translation_prompt.txt` template (they're headings — see 2.9).
- `3_english_only_pdf.py` embeds a `plate` whole (cropped to the
  profiler's ink band when portrait, to strip the running header / folio
  / scan margin), an `inset` from the profiler band, caption beneath.

**Quirks found building this:**

- **olmOCR-2 parrots a concrete few-shot example.** The first prompt gave
  a worked example line (`inline | 0.18 | 0.55 | Fig. 4. — Le salut`); the
  model copied `Fig. 4. — Le salut` and `0.18 | 0.55` onto *every* flagged
  page instead of reading the real caption. Fix: no concrete example —
  abstract format description plus "describe what is actually on THIS
  page — do not copy the wording above."
- **No spatial grounding.** Asked for the figure's top/bottom as page
  fractions, it returned the example's numbers or guesses. Dropped the
  coordinate ask entirely; the density profiler supplies geometry, gated
  by the VLM's presence flag (so it can trust a hit instead of being
  conservative).
- **Rotation can't be inferred from page geometry.** Joseph-Renaud's
  photo plates are *landscape leaves* whose content is already upright;
  Gomard's are *portrait leaves* holding a sideways fold-out engraving.
  Same `width/height` signal, opposite correct action — auto-rotate broke
  JR. Figures are now embedded in source orientation; a sideways fold-out
  stays sideways.
- **Under-reporting, confirmed.** Gomard flagged 8; Joseph-Renaud skipped
  plates in a run of near-identical ones ("SECOND MOVEMENT…"), and missed
  engravings dropped into text pages (the Montmorency-Boutteville duel).
  Fix: `3_english_only_pdf.py` runs `find_figure_band()` on **every** page
  `figures.json` didn't flag — a profiler hit on an unflagged page is a
  real miss more often than a false positive. Profiler-caught figures get
  a generic `[illustration from the original]` caption (no VLM caption to
  translate). JR went 31 → 44 figures, Gomard 8 → 10.
- **Caption duplicated.** A plate page's OCR body *is* its caption line,
  so the English-only PDF drew it once under the figure and again as body
  text. `3_` now skips the body when it's ≤ 20 words and matches a
  figure's caption.
- **`FIGURES: none` leaked into the body.** The `BODY_TEXT` split only cut
  a `FIGURES:` marker alone on its line; the VLM writes `FIGURES: none`
  inline, so on near-blank pages the literal string became the page body.
  Split at `^FIGURES:` regardless of what follows.
- **A full-page plate crop-clipped the art.** For a `plate`,
  `band_for_figure()` handed the profiler's ink band straight through, to
  trim the running header / folio / scan margin. On a faint line engraving
  with no dense core the band collapses to `seed ±22%` and crops the
  picture. Fix: for a `plate` only, keep the band when it spans ≥ 60 % of
  the page height, else embed the whole page (a plate *is* the page;
  cropping is worth ~15 %, not 45 %). JR pp. 45 / 47.
- **A plate's two-line caption split across FIGURES and BODY_TEXT.**
  The `FIG. N — TITLE` / `Nᵉ TEMPS : …` plates (JR pp. 34–36) came out of
  olmOCR-2 with line 1 in a `FIGURES:` row and both lines in `BODY_TEXT`,
  fields out of order (`FIGURES:` before `BODY_TEXT:`), and `no caption`
  on its own line. Three parser bugs compounded: the `FIGURES:` capture
  ran to end-of-response and swallowed the leaked `BODY_TEXT:`; `"text" in
  kind` matched the literal line `BODY_TEXT:` and made it a spurious
  `inset`; `no caption` wasn't a recognised empty sentinel. Fixes: cut the
  `FIGURES:` block at a following `BODY_TEXT:`/`PAGE_NUMBER:`/`HEADER:`
  marker; match `kind` with `startswith("inset"/"inline")` only; treat
  `no caption` as empty. Plus `_fold_caption_only_body()`: when a page has
  a figure and a short caption-shaped body, the body is folded into the
  figure caption (translated once, as a caption) and the page collapses to
  one image — so the English-only PDF stops dropping the second line and
  the side-by-side stops showing a worse duplicate translation.
- **A figure emitted as a Markdown image ref, not a FIGURES entry.**
  olmOCR-2 rendered a labelled line diagram ("Schéma A", straight lines +
  lettered points — invisible to the density profiler) as
  `![Schéma A.](http://…fabricated-url…)` inside BODY_TEXT, so
  `parse_figures_block` saw nothing and step 2 stripped the tag. Now the
  parser scans the whole response for `![alt](url)`, makes each an `inset`
  (caption = alt text, or the short line after it), and `band_for_figure`
  embeds the **whole source page** for a captioned inset the profiler
  can't localize (the un-localizable *caption-less* vignette is still
  skipped — see below). Prompt also now forbids Markdown image syntax.
- **PAGE_NUMBER field skipped, folio left as the first body line.** Same
  page: no `PAGE_NUMBER:` line at all, the response just opened with
  `29\n\n…`. `parse_vlm_response` now salvages a leading bare
  roman/arabic line as the folio *only* when the field is entirely absent
  (an explicit `PAGE_NUMBER: none` still means "no folio").
- **A small drawing blanketed a whole text page.** The VLM correctly
  flagged a tiny vignette on folio 74 of Joseph-Renaud (a dense page of
  regulation text) as `inset | no caption`. The profiler couldn't
  localize something that small, so `band_for_figure()` fell back to
  embedding the *whole source page* — dropping a full page of translated
  regulations behind an untranslated scan. Fix: when the fallback would
  be a whole-page embed **and** the page carries ≥ 60 words of translated
  body, skip the figure entirely (`band_for_figure` returns `None`); the
  side-by-side PDF still shows the original. A localized band (< 85 % of
  the page) is still placed inline.
- **A results / poule table linearised into garbled `BODY_TEXT`.** A
  tournament bout table (2-D grid, ruled cells) is not prose; the VLM
  read it row-major into an unusable word salad. `vlm_ocr_prompt.txt`
  `FIGURES:` now lists "a printed grid or table that only makes sense as
  a 2-D layout" as a picture to report there instead of transcribing —
  so the page image is embedded and the garbled text never reaches the
  translator. JR p. 46. *(Takes effect on re-OCR.)*

`find_figure_band()` remains the sole detector for books OCR'd before
`figures.json` existed (still misses line art). Sideways fold-out
engravings (Gomard) are embedded as-is — page geometry can't tell a
landscape photo leaf (upright content) from a portrait leaf with a
sideways plate.

### 1.8 Server death looked like a slow page

**Failure:** a transient RADV / model-load failure left `llama-server`
dead; the page-wait loop had no liveness check and ran out a generic 120 s
timeout per page.

**Mitigation:** the wait loop now checks `proc.poll()` and prints the exit
code + log tail on a dead server ([`1_…py`](../pipeline/1_ocr_and_extract_vlm.py) L326),
matching what `2_translate.py` already did.

### 1.9 Operational notes (not failures, but non-obvious)

- **Image size:** pages are rendered at 300 DPI then downscaled to a fixed
  **1288 px** on the long edge (`VLM_MAX_DIMENSION`). The old adaptive
  2200→1400 px scheme is gone.
- **`.crop` sidecar:** `books/<stem>.crop` = `left,top,right,bottom`
  fractions, trimmed from every page image before OCR. Use it to cut a
  dark scanner-bed border or gutter shadow the model keeps transcribing.
- **`raw_ocr.txt` is written first, always** — the untouched responses,
  before any parsing or `collapse_runs`. It's the recovery point;
  `reprocess_extracted.py` rebuilds `extracted.txt` + `page_numbers.txt`
  from it.

---

### 1.10 Printed italics come back as Markdown asterisks (2026-09-30)

**Seen:** `*Inside line*`, `*The Adventuress*` — the OCR marks italics
Markdown-style and the translation keeps the asterisks; 21 places, 12 in
Vauresmont's glossary, shown literally in the PDFs.
**Mitigation:** `_render.markdown_italics()` renders a pair as italics.
Only a pair within one line that opens on a letter / digit / quote and
closes on a non-space, so a note marker "(*)", a bulleted "* In the bout",
"* * *" and a lone "*" are untouched.

### 1.11 Old-style roman page numbers misread (2026-09-30)

**Seen:** La Boëssière's front matter prints *vij*, *viij*, *xiij*, *xvj*
(final i written j). The parse accepts the j; the problem is misreads by
one stroke — *viij* → "vij", *xiij* → "xiiij", *xvj* → "xvij". The
page-number step rightly distrusts a value that breaks the sequence, works
out the right one, and then hides it, because it can't tell "printed but
misread" from "not printed" (and the same pages carry printer's gathering
marks "1", "6" the OCR also reports).
**Open:** neither the layout detector (~75% recall on known folios, false
hits on unnumbered pages) nor the scan's text layer (~35%) settles it.
Planned: an OCR read of just the top and bottom strip of each uncertain
page (`docs/STATUS.md`, page numbers).

### 1.12 Paragraph indents, inconsistently (2026-09-30)

**Seen:** the OCR indents some paragraphs with leading spaces and not
others (LaMarche pp. 14-16), and `_render.py` keeps leading spaces as
indentation — meant for drills and braced command groups (LaMarche pp.
82-83), so running prose came out randomly indented (~330 lines, 8 books).
**Mitigation:** a long prose line (60+ characters, starts with a letter or
quote, no list marker, no brace) is set flush left; drills, braces,
numbered lists and short lines keep their indent. Accepted loss: long
quoted passages the OCR indented (Baudry) go flush left, keeping their
quotation marks.

### 1.13 Engraved captions read into the page text (2026-09-30)

**Seen:** La Boëssière's 20 plates: on 4 of them the OCR also transcribed
the engraving into the page's *text*, and the PDF showed that text instead
of the plate caption — no "Pl. N." label, one line per figure, and Pl. 16
with its misread "La poche" (the pocket) for *La passe*.
**Fix:** those four pages' text emptied in the overrides file, like the
other 16, so the transcribed caption (`[pages.N] caption` /
`caption_en`) shows. Where a reviewer has set a plate's caption, an OCR
copy of it in the text is always wrong — a rule candidate if it recurs.

## Part 2 — Translation (Qwen3.8-27B i1-Q4_K_S, port 11434)

Qwen3.8-27B is the default since 2026-09-25 (translation model selection
test, `evals/translation_model_test.py`); `TRANSLATION_MODEL_PATH`
overrides it. Most entries below were found on Qwen2.5-14B-Instruct-Q4_K_M,
the default from 2026-08; the guards stay in place for any model, and
entries that name Qwen3.8 were seen on it. The 14B was
chosen because the term policy's `<fn>` emission (Rule 3b) needs its
stronger instruction-following — the 7B it replaced emitted almost none.
The model follows the term policy well and the output-hygiene rules
intermittently; every guard below exists because the prompt alone wasn't
enough. Notes that still say "the 7B" describe a failure mode the 14B
shares to some degree unless stated otherwise.

### 2.1 Markdown emission → page-separator collision  ⚠ highest-stakes

**Seen:** Spinnewyn run — 178 source pages became 179 in `translation.txt`.

**Failure:** the pipeline used to join/split pages on the literal string
`\n---\n`. The model, despite being told plain text, emitted a Markdown
thematic break — a lone `---` line — inside one page. That page split into
two, and **every page after it misaligned with its source scan** in the
side-by-side PDF.

**Mitigations:**

| Where | What |
|---|---|
| `pipeline/_pageio.py` | the separator is now `PAGE_SEP = "\n\x1e\n"` — U+001E, a non-printing control char that **cannot** occur in OCR text or model output. `split_pages()` still reads legacy `\n---\n` files so old `processed/` folders don't need a re-run. This is the real fix. |
| `2_translate.py` (after each candidate) | strips `![alt](url)`, `**bold**`, `^#{1,6} `, a hallucinated `Image of a hand …` body line, and a decorative-ornament line (`---`, `* * *`, `• • •`) — the ornament is dropped to a blank line, not rendered as `* * *` (that was hit-or-miss and looked wrong on Gomard p255) |
| `translation_prompt.txt` rule 6 | "Output plain text, NOT Markdown: no `#` headings, no `---` rules, no `![image](...)`, no `**bold**`. The single exception is `<i>...</i>`." |

### 2.2 Model meta-responses leak in as page content

**Seen:** across **all five books**. LaMarche had ~14, Jacob Jules ~12.

Two distinct shapes:

1. **Near-blank pages** (a verso, a section-break page) where OCR returned
   a stray mark or a bare page number. The model gets ~1 word of input and
   replies **"Please provide the text you would like to translate."** —
   which then *becomes* that page's translation.
2. **Preamble / refusal leakage** despite rule 6: `Here is the
   translation:`, `Following the critical rules, here is the translation:`,
   `This text does not appear to be related to historical fencing…`, and
   politeness-prefixed variants (`Certainly. Here is the translation:`,
   `Sure, please provide the text…`).

**Mitigations — three guards in [`2_translate.py`](../pipeline/2_translate.py):**

| Guard | L | Behaviour |
|---|---|---|
| `is_near_blank()` | ~196 | `< 2 words or < 8 letters` in the cleaned source → emit `[Empty page]`, **no model call**. Deliberately conservative so a real terse caption ("Planche IV") still goes through |
| `looks_like_refusal()` | ~190 | whole-output "give me text" / "not fencing" shapes → reject the attempt, never keep it even as a low-confidence fallback |
| `strip_model_preamble()` | ~181 | drop leading `Here is…` / `Sure:` / `Note:` lines; if the candidate is preamble-only, retry |

`_PREAMBLE_RE` / `_REFUSAL_RE` are narrow **on purpose** — an earlier
version matched "any first line ending in a colon" and would have stripped
real page-initial prose like *"In this case, the appreciation of the
thrust and the hit is easy … Example:"* (genuine Gomard scoring text).
Every alternative now has to name the act of translating or be a fixed
filler token. If you widen them, re-test against that false-positive set.

### 2.3 Footnotes: model-emitted `<fn>`, deterministically backstopped

**Seen (old design):** the 7B emitted almost **zero** `<fn>` tags on its
own, so footnotes used to be driven by a source-keyed definitional
glossary (`footnote_glossary.txt`).

**Now (2026-08-30 term-policy revision):** the glossary is deleted — its
definitions were translator commentary. Footnotes are **only** the bare
`†Fr. <term>` source reference on a footnoted term (category 2; see 2.8). The prompt
tells the model to write `English<fn>Fr. term</fn>` on first use;
`enforce_cat3_footnotes()` is the backstop (translates every stray French
Cat-3 occurrence, footnotes the first). `pull_footnotes()` turns `<fn>`
tags into `†N` + a `___FOOTNOTES___` block, deduped once per book, max 4
per page, and normalises the note text to **`French: <term>`** (accepts
either `Fr.` or `French:` from the model). `_render.py` splits that block
off; `3_` renders it in small grey type, `4_` reserves a strip at the
foot of the right column.

*2026-09-01:* the block's `"Notes:"` label was removed — each line now
self-identifies (`(1)` from the book, `[margin note] …`, `French: …`), so
the label just cost a line.

### 2.4 Braced command groups still flatten ~half the time

**Seen:** every book with drill chapters.

Despite `translation_prompt.txt` rule 7 and a worked `Doublé { high /
inside / low` example, the model merges braced alternatives into a
sentence on roughly half the pages where they occur. Partially mitigated,
not solved. Open item in [STATUS](STATUS.md), "Known translation soft spots".

### 2.5 ToC / dotted-leader pages half-translate kept terms

**Seen:** contents pages list entries like `Coupé . . . . 44`. The model
translates the entries unevenly, and `enforce_cat3_footnotes` would
staple the once-per-book `<fn>` note onto a contents line if the book's
first `coupé` happens to be there.

**Mitigation (2026-09-01):** `is_toc_page()` — a page where ≥ 50% of the
non-blank lines match `text … dot-leader … page-number`. On a detected
ToC page, `enforce_cat3_footnotes(..., footnote=False)` (translate the
Cat-3 term, emit **no** marker) and skip
`retranslate_stray_french_headings` / `fix_subheading_spacing` (every
entry looks like a heading). Cat-1 / Cat-2 rules run unchanged, so the
entries still translate consistently with the body.

### 2.5b Two more residue/casing gaps, round 7

* **`_ALWAYS_TRANSLATE`: "LISTE"** — Gomard's bibliography section header
  "LISTE DES OUVRAGES" ("List of Works") translated to "LISTE OF WORKS":
  "DES OUVRAGES" → "OF WORKS" translated fine, but bare "LISTE" survived
  untranslated on this specific isolated header string — even though the
  SAME word translates correctly as part of the book's other two-part
  header for the same section, "LISTE DES OUVRAGES\tCONSULTÉS." → "LIST
  OF WORKS\tCONSULTED.". A one-off model quirk on the shorter isolated
  string, consistent enough across the header path's majority-vote retry
  (2.10) that voting alone didn't catch it — same class of bug as the
  "riposte"/"rassemblant" entries already in this list, fixed the same
  way: added to `_ALWAYS_TRANSLATE`.
* **`enforce_kept_terms()` forced every kept term (category 4) to lower-case,
  unconditionally** — including inside an all-caps context, e.g. a
  body-level title line: Prévost's Part Four opens with the all-caps
  body line "DE LA RIPOSTE" (immediately under the "QUATRIÈME PARTIE"
  division title, 2.11a), which rendered "OF THE riposte" — capitalised
  everywhere else in the line, lower-case-italic only on the kept term.
  This was never an intended universal rule; it was a side effect of the
  garbled-word repair this function's `_KEEP_TERMS` list exists for
  ("RAPIER RAPIER" → riposte, "RAPISES" → ripostes) always emitting the
  literal lower-case string from the list, discarding whatever case
  prompted the substitution. Fixed with the same case-matching
  convention already used by `apply_always_translate`'s own substitution
  (`_match_term_case()`: match the case of the text being replaced —
  all-caps in, all-caps out; Capitalized in, Capitalized out; otherwise
  lower). Mid-sentence usage ("a riposte follows…") is unaffected — only
  changes when the surrounding context is itself shouting.

Regression tests: `test_always_translate` (LISTE case added),
`test_enforce_kept_terms_matches_surrounding_case`.

### 2.6 Literary prose defeats the translation model

**Seen:** LaMarche (`Le Fleuret et l'Épée`) — florid 19th-c. literary
passages like *"Arrêtons-nous, et de face regardons le fencer ainsi
campé."* come back **substantially still in French** under a
`[low-confidence translation — review]` flag. Technical drill text from
the same book translates fine.

This is a model-capacity limit, not a prompt bug. The quality-gate chain
(2.7) catches it and flags rather than shipping a bad translation
silently.

### 2.7 The quality gate — how a page is judged, in order

Each page gets up to **3 attempts**. An attempt is rejected if:

| Check | `2_…py` | Rule |
|---|---|---|
| `looks_like_refusal()` | ~L430 | whole output is a refusal/echo → not even a fallback |
| `strip_model_preamble()` → empty | ~L437 | preamble with nothing after it |
| `has_wrong_script()` | L118 | CJK / Cyrillic / Hebrew / Arabic present (Greek is allowed — treatises quote it). Never kept, even as fallback |
| `contains_too_much_non_english()` | L129 | after stripping `<i>…</i>`: > 35 % of words in a small FR/IT/DE/ES/LA stop-word list, **or** > 5 % French-only constructs — elisions (`d'une`, `qu'il`; English contractions put a consonant after the apostrophe so they don't match) and endings needing French letters (`-ité`, `-eaux`, …). The stop-word list alone missed a page returned untranslated in dense technical French — its content words (`fendre`, `réserve`, `convenable`) aren't stop words |
| too short | L456 | `len(candidate) < 0.5 × src_words` — but **only when `src_words ≥ 25`**, because plate captions and drill lines are legitimately terse and were being rejected |

If all 3 attempts fail: keep the best non-garbled attempt as
`[low-confidence translation — review]`; if there was none and the source
was tiny, `[Empty page]`; otherwise `[translation unavailable — original
text follows]` with the source text. **A page is never dropped** — the
page count out must equal the page count in, or the side-by-side PDF
misaligns.

*2026-09-18:* `contains_too_much_non_english()` false-positived on a
page that's legitimately almost all correctly-untranslated proper names.
Kirchhoffer src 38 lists duel participants and a tournament name ("DUEL
ANDRÉ GAUCHER — JOSEPH-RENAUD. … TOURNOI DE MONTE-CARLO.") -- names
correctly stay unchanged, but the French articles/prepositions embedded
IN them ("Cte J. **de** Reverseaux", "Parc **des** Princes", "**d'**Hurcourt")
tripped both the stop-word ratio and the elision backstop, which can't
tell "a real untranslated French sentence" from "a French preposition
sitting inside a name". Fixed by exempting a stop-word match immediately
followed by a Capitalized token (name context), and an elision match
whose captured letter is itself uppercase (`d'Hurcourt`, not `d'une`).
A genuine failure (real lowercase French prose) is unaffected -- verified
against both the false-positive case and a real echoed-French page.

### 2.8 The translation prompt's term policy, condensed

**Current (2026-09-30): one glossary per source language, three categories.**
French rules in `translation_prompt.txt`; an Italian book gets
`translation_prompt_it.txt` spliced in place of the French term rules
(`prompt_for_language()`), chosen by the book's `meta.json` language. The
categories are in manual Section I (step 2):

1. **Translated** — plain English (*parade* → parry, *colpo* → hit / attack).
2. **Footnoted** — English in the text, the original in a bare footnote on
   first use per book (`† French: liement`). The model emits
   `English<fn>Fr. term</fn>`; `enforce_footnoted_terms()` translates any
   stray original and notes the first; `pull_footnotes()` builds the block.
3. **Kept** — the original in italics, no note (*riposte*, *quarte*,
   *cavazione*). Backstop: `enforce_kept_terms()` re-italicises and repairs
   known garbles.

**An "explained" category was tried and dropped (2026-09-30):** kept terms
with a one-line explanatory note on first use. The notes drafted well, but
a note is redundant where the book defines the term itself, and *where* a
book defines a term can't be detected reliably — a pattern match was right
on ~2/3 of the definitions it found and missed others (ordinary homonyms
like *sforzo* "effort", tables, descriptions). Doing it properly meant
per-book research for every term; the benefit didn't justify it (the
side-by-side shows the original, and most books define their terms). The
earlier 2026-08 decision against definitional glosses in footnotes stands.
A one-page glossary of kept terms at the end of each PDF is an open idea,
undecided.

Unchanged from before: **rule 4** (headings are translated in full, a kept
term keeps its accents), **rule 6** (output only the translation), and the
EXAMPLES few-shots for all-caps `<ACTION> DE <THING>` headings.

The earlier three-category scheme (2026-08-30) numbered these differently —
"Cat 2" = kept, "Cat 3" = footnoted — which is why the code now uses names,
not numbers.

### 2.9 Figure captions need the *full* prompt, not a one-liner

**Seen:** the first `translate_figure_captions()` fed each caption to a
bare "translate this caption line" prompt. Results ignored the term
policy: `PARADE DE SIXTE` → "Sixteen Step Parade" (read "sixte" as
"sixteen"), `PRISE DE FER SUR LE BRAS TENDU` → "GRIP ON THE STRETCHED
ARM", `LE CROISÉ AU FLANC` → "THE CROSSBLOW TO THE FLANK".

**Fix:** plate/figure captions *are* headings (`PARADE DE …`, `Fig. N. —
…`), so run them through the same `PROMPT_TEMPLATE` as body text — the
rule-2/3/4 term policy and the `PARADES DE X` few-shots then apply.
Result: "PARRY OF SIXTE", "PRISE DE FER ON THE EXTENDED ARM", "THE
CROISÉ AT THE FLANK". `2_translate.py --captions` re-runs this pass —
captions **and** running headers — across all books (no body
re-translate) for iterating.

`_clean_caption()` also strips model commentary that leaks into a
caption: a `(NOTE: …)` aside (often long, sometimes unclosed) and the
`GIVEN THE … RULES, … THUS: <restated line>` tail it appends
(Kirchhoffer Fig. 7 came back with a 400-char translator's note).

*2026-09-18: `match_case()` breaks on a caption that combines TWO
different case registers in one string.* A caption can pair an all-caps
main title with a trailing parenthetical annotation set in ordinary
sentence case in the actual book (Kirchhoffer pdf 47: "LE CROISÉ AU
FLANC." all-caps, then "(La main de l'attaquant reste haute.)" in normal
sentence case — confirmed against the real scan). `match_case()`
measures case over the WHOLE source string to decide which mode to
apply; the parenthetical's genuinely-lowercase letters dilute the main
title's `upper_ratio` below the 0.8 all-caps threshold, so the real
all-caps title fell through to a broken middle case instead ("the
croisé To the side"). Root cause is actually the 1.2d fix above (merging
the parenthetical into the caption text) — before that fix, this
combined-casing scenario never arose at all, because the parenthetical
was simply dropped. Fixed IN `match_case()`: split off a trailing
"(...)" and case-match each side independently, only when BOTH src and
dst end in one — the main title gets its own (all-caps) mode, the
parenthetical gets its own (sentence-case, first letter capitalised)
mode. Regression test:
`test_match_case_splits_parenthetical_case_register`.

**2026-09-18 (same day, found on the next real page checked):** the
identical problem recurs with a DIFFERENT separator — a trailing
"— <clause>" annotation instead of parens (Kirchhoffer's "PRISE DE FER
SUR LE BRAS TENDU." plate run, 3 captions: "FIG. N. — [ALL-CAPS TITLE].
— [genuinely lower-case clause, confirmed identical across all 3
plates]"). Same fix, same guard: only split at the LAST em-dash when the
trailing segment actually STARTS lower-case (letter OR digit, to catch
ordinals like "1er"/"3rd") — a caption using "—" only to join two
SAME-case segments must stay whole. Recurses, so more than one trailing
clause peels off one at a time. Regression tests:
`test_match_case_splits_dash_tail_case_register`,
`test_match_case_dash_tail_requires_lowercase_start`.

That surfaced a small side artifact: the sentence-case recaser forces a
capital onto the first LETTER it finds, which for a clause starting
with an ordinal ("3rd tempo…") lands mid-token ("3Rd"). Fixed: only
capitalise a genuine word-initial letter (not preceded by a digit or
another letter). Regression test:
`test_match_case_sentence_skips_ordinal_mid_token_capital`.

Of the 3 captions in that run, 2 came out fully fixed; the third still
came back lowercase — one of the 3 majority-vote samples apparently
phrased the trailing clause without the em-dash separator the split
depends on, so it fell through to the old single-mode behaviour on that
attempt. A residual of the underlying model-sampling variance, not a
further code bug — left as a known small gap rather than over-fit the
regex to one more example.

### 2.10 Running headers translated separately

`translate_headers()` collects each **distinct** running-head string from
`extracted.txt`, translates it once through the full prompt (rule 4), and
writes `headers.json` (`{ raw → english }`). The PDF steps look it up for
the section label / right-column header — `FLEURET` → `FOIL`,
`LES PARADES` → `THE PARRIES`, `LA GARDE` → `THE GUARD`. A book-title
running head gets translated too (no per-book "keep in French" exception
yet). A trailing lone `:` is stripped (`DES CONTRES:` → `DES CONTRES`).
Only genuine repeated running heads reach here now — one-off titles and
scanner apparatus are filtered upstream (1.2c / 1.2d).

**Two-part running heads** (`SABRE` left, `ATTAQUE ET PARADE` right —
Kirchhoffer): `parse_vlm_response()` keeps them as `left\tright`;
`translate_headers()` translates each half separately; steps 3/4 draw the
halves left- and right-aligned on the header line. `reconcile_headers()`
counts repetition on the **left root** so a `SABRE⇥…` with a per-page
right half is not mistaken for a one-off (1.2c).

**Case is not preserved by the model** — `ÉPÉE` came back `épée`,
`LA GARDE` as `The Guard`, `SOCIÉTÉS` as `Societies`, inconsistently
across a run. `match_case(src, dst)` now forces the translated head/caption
to follow the *source's* case style: source ALL-CAPS → `dst.upper()`;
source Title Case → Title Case (articles/prepositions stay lower); else
sentence case. A manual that sets every running head in capitals (most of
this corpus) gets uniform all-caps English heads. `<i>kept term</i>` spans
keep their own casing. Applied in `translate_headers()`,
`translate_figure_captions()` and `retranslate_stray_french_headings()`.
Regenerating needs `2_translate.py --captions` (forces both) or deleting
`headers.json` — an unforced pass skips strings already mapped.

*2026-09-18: headers and captions had no retry/quality gate at all,
unlike body text.* Both `translate_headers()`'s `_one()` and
`translate_figure_captions()` called the model exactly once per string,
with no context beyond the bare phrase and no check on the result --
body text, by contrast, gets full-page context plus a 3-attempt retry
ladder with real quality checks. A small quantized model translating a
short isolated phrase cold occasionally garbles it: `ROMPRE` ->
`ROMPRER`, `PARADES ET RIPOSTES` -> `PARRIES AND RAPISES`, `THRUST` ->
`THUST`, a duplicated gloss ("GATHERING THE FEET" OF THE FEET"). Fixed by
taking 3 independent temperature-varied attempts and majority-voting the
result (by lowercase-normalized string) instead of trusting the first --
a one-off sampling fluke is very unlikely to repeat identically 2+ times,
while the correct rendering usually does.

Majority-vote alone didn't fully fix `RIPOSTES` -> `RAPISES`, though --
the model made that SAME mistake on all 3 independent attempts, so it's
a repeatable confusion, not noise. `enforce_kept_terms()` already had a
known-garble list for "riposte" (comment: "OF THE RAPIER RAPIER", from an
earlier-seen corruption) but didn't include this spelling, and its
replacement always forced the singular "riposte" regardless of the
garbled match's own plurality. Added `rapises?` to the known-garble
list and made the replacement plural-aware (checks whether the matched
garble ends in "s").

**Isolated translation also doesn't guarantee AGREEMENT with the body's
own translation of the same source text**, even when neither is wrong.
Prévost src 21: header `DE LA MANIÈRE DE SE RELEVER.` and the body's own
duplicate of that exact phrase translate to two different (both
legitimate) English renderings, since they're two independent model
calls with different context. Majority-vote makes a single call more
internally consistent across retries; it does nothing for two SEPARATE
calls translating the same source text differently. Full fix would mean
reusing the body's own translation when a header duplicates verbatim
into it -- not implemented; flagged as a known residual, not silently
called fixed.

*2026-09-18: a body-level TITLE line (not a `HEADER:` field value) can
also stay untranslated, for a different reason than either bug above.*
`retranslate_stray_french_headings()` re-checks a heading-shaped body
line only when it contains a generic French STOP word (le, de, et…) --
Prévost src 23's "ROMPRE" stands alone as its own body-level title line
(the header carries the duplicate, already fine -- see the echo
exception above) with no stop words in it at all, so the heuristic had
literally nothing to detect and never even attempted it, even though the
SAME word translates correctly moments later mid-paragraph on the SAME
page (sentence context there carries real stop words). Widened: a short
(<=2 word) all-caps heading is now checked regardless of stop-word
count, and the acceptance test switched from "strictly less French than
before" (blind whenever the count starts at 0) to "genuinely different
from the original AND not itself flagged as still-French".

That retranslate call then surfaced a SEPARATE bug: it came back `TO
BREAK GROUND / TO RETREAT` -- the model echoing the translation
prompt's own rule text verbatim, because rule 3 offered BOTH as
apparently-interchangeable options for "rompre". This also directly
explains an earlier report: Kirchhoffer's `ROMPRE — DIVERS` header
translated as `RETREAT — MISCELLANEOUS`, i.e. the model picking the
second, WRONG option. Per user (fencing-specific correction): "rompre"
is one deliberate step back; "retreat"/"retraite" is a different,
more general multi-step withdrawal -- treating them as interchangeable
is a real terminology error, not just a style choice. Fixed in the
prompt itself (`translation_prompt.txt`): moved `rompre` out of rule 3
into rule 3b as a single canonical `to break ground` (dropping "to
retreat" as an alternative entirely), with an explicit note to keep it
distinct from "retraite" if both appear, and now gets the `<fn>Fr.
rompre</fn>` footnote-on-first-use treatment other rule-3b terms get.
The prompt's own worked examples ("Rompez." → "Break.") already assumed
this and never matched rule 3's stated two-option text -- the rule text
was wrong relative to the examples, not the other way round.

### 2.10b Repeatable garbles, coup/botte, header ↔ body-title wording (pass 8)

**Repeatable garbles** survive the header/caption majority vote because
the model makes them identically every time, and they appear in body text
too: `THUST` (13 Gomard lines + 4 headers), `THROUGHS` (pdf 174 retry),
`THRUST/HIT` (the glossary's slash copied verbatim), `PLANK(S)` for
*planche* (carpentry sense). `fix_known_garbles()` (called from
`enforce_kept_terms()`, so body, headers and captions all get it) repairs
them case-preservingly; plank → plate only when the source says *planche*.

**coup vs botte** (user decision, 2026-09-25, revised the same day): Gomard
defines them himself (pdf 158 / scan 156): *le coup est l'ensemble des
mouvements de l'attaque… la botte est la figure de l'attaque à sa
terminaison* (the line it finishes in and the hand position). So the
**coup → blow** (the whole action), **botte → thrust** (its terminal
delivery). This matches the model's own majority rendering, and
period sources on Gomard say the same. A first attempt (coup → thrust,
botte → hit) got it backwards and was reverted. Named techniques keep their
own names: coup droit → straight thrust, coup de temps → time-hit, coup
d'arrêt → stop-thrust. The prompt carries the rule, Gomard's overrides
file carries it as [terms] plus the header wording, and the 33 pages the
review report showed as inconsistent were retranslated with `--pages`.
Limit: a bare *coup* rendered "thrust" elsewhere can't be told apart
automatically from the legitimate "straight thrust"; only a full
retranslation would guarantee it.

**Header vs body title wording.** Header and body are translated
independently, so a page that prints the same title in both got two
renderings (Prévost "OF/ON THE COUNTER-RIPOSTE", src 21). The fix is
`harmonize_titles_with_headers()`, which runs after `translate_headers()`.
When body line 1 of the source equals the page's header, the
translation's first line becomes the header's translation. If that line
isn't a title (the body title was restored after the page was
translated), the header's translation is inserted instead. This needs
no model call. The header translation must be good, because it's what
gets propagated: Prévost's `DES CONTRES.` → "PARRIES AGAINST" had to be
corrected in headers.json first. Mid-page section titles are out of
scope (only line 1 is compared); two were hand-patched.

### 2.11a A structural division title dropped entirely, not mistranslated

**Seen:** Prévost -- "PREMIÈRE/DEUXIÈME/QUATRIÈME/CINQUIÈME/SIXIÈME
PARTIE" as the body's own first line, on every single occurrence in the
book (5/5 confirmed), while the very next title line on the SAME page
translates fine.

**Failure:** unlike every other bug in this file, the model doesn't
mistranslate or garble this line -- it omits it from the output
entirely, as if a "Part" division marker were a structural label rather
than body content to translate. `retranslate_stray_french_headings()`
(2.10/2.11) can't help here: it re-checks a heading-shaped line that's
STILL PRESENT but still in French; there's nothing in the output to
re-check when the line is simply gone.

**Mitigation:** `restore_dropped_part_title()` -- narrow, evidence-based
(like `_OCR_PHRASE_FIXES` in step 1): if the source's first body line is
exactly `<French ordinal> PARTIE` and the translated candidate's own
first line doesn't already contain "part", translate just that line
(a small closed-set FR->EN ordinal map, not a live model call needed)
and prepend it. Six French books' worth of "part" vocabulary covered
(première..dixième); extend the map if a book uses a different
division word ("livre", "section", …) with the same drop pattern.

### 2.11 Author's footnotes vs. ours

A page can carry the **author's** own `(1) (2) (3)` footnotes *and*
**our** footnoted-term `<fn>` source references. Both land in the same grey
ruled block (`_render.footnote_lines`, no label since 2026-09-01), so a
reader can always tell body from note:

- `extract_source_footnotes()` ([`2_…py`](../pipeline/2_translate.py)) —
  a body paragraph starting `(N) …` is moved to the note block. Prévost
  sets its notes as a bare `1. … 2. …` run at the page foot instead: a
  **trailing** run of paragraphs numbered sequentially from 1 is treated
  as notes too, guarded against a numbered drill (first body paragraph
  also numbered → no; line above the run ends `:` → no; single short
  note → no). A footnote that runs off the bottom of a page (no terminal
  punctuation) sets a cross-page flag; the next page's leading lower-case
  paragraph is taken as its continuation and rendered `… <tail>` in
  *that* page's block (halves aren't stitched into one note, but each
  renders as a note, not as body).

  *2026-09-14, two more gaps found from a real instance (Gomard src
  220/221, footnote (1) on "tac au tac"):* (a) the continuation isn't
  always the very next thing OCR reads — it can be deferred to the FOOT
  of the next page, after that page's own unrelated new body text. Now
  also checks the next page's LAST paragraph when the first isn't a
  match. (b) the lower-case check was a bare `[:1].islower()`, which
  missed a continuation opening on a kept italic term
  (`<i>riposte</i>, but by the second word…` — `"<"` isn't a letter).
  Both checks now skip a leading `<i>`/`<b>`-style tag before looking at
  the actual first letter. The first attempt at (a) passed its unit
  tests (plain-text fixtures) and still silently failed on the real
  translated data because of (b) — worth remembering when writing a test
  fixture for a passage with kept terms in it.
- `pull_footnotes()` — our `†N` footnoted-term markers, deduped once per book.

### 2.12 Italian sample, and two cache/heading traps (2026-09-30)

**Italian sample (30 pages, 10 books):** the new glossary held — footnoted
terms noted once per book, *picchio* → beat, *volante* → off the blade,
Florio's flying *cartoccio*. Three misses, each now a backstop:
*sforzo* / *riposte* left bare (added to the Italian kept-term list); a
heading "DELLE RISPOSTE" → "OF THE *RIPONSES*" (`fix_known_garbles()`, the
Italian twin of "RAPISES"); *urto* → "blow" (prompt: *urto* → knock).

**Running heads are cached, so a glossary change doesn't reach them.**
Retranslating the 102 *mesure* pages left Gomard's head "DE LA MESURE." as
"OF DISTANCE.": heads are translated once per distinct string into
`headers.json` and reused. After a glossary change, clear the affected
entries and rerun a page of that book so they're retranslated.

**An isolated title drops its article.** LaMarche's running head and title
*L'Épée* came out "Épée." / "ÉPÉE" on 137 pages, while the body always says
"the épée". Decided: "The Épée" (overrides file). Only when the word stands
alone — longer heads keep the article.

## Part 3 — shared / architectural

- **Page separator** — `pipeline/_pageio.py::PAGE_SEP` (U+001E). Every
  stage joins / splits the multi-page files on it. Was `\n---\n`, which a
  stray Markdown rule could split (see 2.1); the control char can't occur
  in content. `split_pages()` still reads legacy `\n---\n` files.
- **olmOCR-2, not Tesseract** — decided after an 8-page three-way eval
  (Gallica text layer / forced Tesseract / olmOCR-2). Forced Tesseract is
  unusable on any page with an in-text figure and misreads stains
  (`matière` → `2 latiére`). No per-page fallback scheme by design.
- **Two models, loaded one at a time** — 16 GB VRAM can't hold both.
  Step 1 loads the vision model on :11435 and unloads it; step 2 loads
  the text model on :11434. This is why they're separate scripts and why
  the pipeline (not the container entrypoint) owns server lifecycle.
- **Iterating cheaply** — every intermediate is kept. Tweak
  `translation_prompt.txt` → retranslate the pages concerned with
  `2_translate.py --pages <stem> <pages>`, then steps 3–4 (a prompt change
  alone triggers nothing: step 2 retranslates only pages whose *source*
  changed; running heads are cached separately, see 2.12).
  Tweak `vlm_ocr_prompt.txt` → you need a real re-OCR (`rm -rf` the
  book's folder). Tweak the *parse/reconcile* logic only → 
  `reprocess_extracted.py` rebuilds from `raw_ocr.txt`, no OCR cost.
  Full detail in [`setup/MANUAL_SECTION_III_USAGE.md`](../setup/MANUAL_SECTION_III_USAGE.md).

- **Overrides must be safe to apply twice.** Every run re-applies the
  overrides file. A two-line `prepend` compared only its first line against
  the page, never matched, and was added again on each run — Jacob scan 49
  had six copies of "THE GAME OF THE ÉPÉE / INTRODUCTION" in the published
  PDF (2026-09-30). Now compared over all its lines, and stacked copies
  collapse to one. Same care for `find`/`replace`: a `find` contained in
  its own `replace` ("in pro" → "in pronation") would fire on every run —
  include the line break in the `find`.
- **The review report respects reviewer decisions.** A running head set in
  the overrides file (`[pages.N] header`) is not re-flagged against the
  layout detector — the decision is the answer.
- **`books/_skip/` is not read by anything.** A finished or out-of-scope
  book moves there; to work on it again, move it back to `books/`.
