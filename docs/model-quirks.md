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

### 1.6b Cross-page word hyphenation

**Seen:** Gomard 1845 — a word split by end-of-page hyphenation in the
original (`… déjà pu-` | `bliés, on …`) — **19 pages**. Each fragment
translated on its own is garbage ("already pu- / blés, one could…").

**Mitigation:** `dehyphenate_across_pages()` — when page *i* ends
lowercase+hyphen and page *i+1* starts lowercase, the completion fragment
(plus glued punctuation) is spliced up to close the word on page *i*. A
capitalised continuation (`Sainct-` | `Didier`, `La-` | `Boëssière`) is a
real hyphenated name and is left alone.

### 1.6c Glossary / "termes techniques" sections

**Seen:** Kirchhoffer — a multi-page *TERMES TECHNIQUES* section, plus
shorter *Particularités* runs (`Headword. — definition`).

**History:** `apply_glossary_mode()` (2026-08-31) kept every headword in
the source language, italic, and translated only the definition.
**Removed 2026-09-01** — leaving the term untranslated left the reader
without the English word the body text uses for it. A glossary section
now goes through the normal term policy like body text; the facing scan
still carries the original headword.

**Headword case:** a Category-2 term kept lowercase-italic loses the
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
  unrelated one-off `CONTRE LE BRAS TENDU` -- documented residual, not
  fixed).
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

## Part 2 — Translation (Qwen2.5-14B-Instruct-Q4_K_M, port 11434)

Default since 2026-08; `TRANSLATION_MODEL_PATH` overrides it. The 14B was
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
`†Fr. <term>` source reference on a Category-3 term (see 2.8). The prompt
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
not solved. Open item in [STATUS](STATUS.md) #1.

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

### 2.8 The translation prompt's term policy, condensed

Revised 2026-08-30 to a **translate-by-default** policy — the earlier
inherited list kept too much in French. Three categories:

- **Cat 1 — translate, plain text (default).** Everything not listed
  below, plus explicitly `garde`→guard, `engagement`→engagement,
  `parade`→parry, `doublé`→double, `fer`→the blade, `temps`→tempo,
  `temps d'arrêt`→stop-hit, `soie`→tang, `fort`/`faible`→forte/foible,
  `rompre`→to break ground / to retreat (2026-09-11: was missing from
  every list, so the model improvised the false cognate "romp"), the
  heading connectives **DE / DES / SUR / PAR / AVEC**.
- **Cat 2 — KEEP in French, `<i>…</i>`** (the tight retained set English
  fencing literature itself keeps): `riposte`, `remise`, `reprise`,
  `croisé`, `corps à corps`, `volte`, `sentiment du fer`, the numbered
  parries `prime`…`octave`, `épée`. Plus Italian / German / Latin.
  Backstop: `enforce_kept_terms()` (`_KEEP_TERMS` = `corps à corps`,
  `sentiment du fer`, `croisé`, `volte`) re-italicises and repairs
  "body to body" etc.
- **Cat 3 — translate, + a bare `†Fr. <term>` footnote on the first
  occurrence per book** (`rule 3b`): `prise de fer`→taking the blade,
  `liement`→bind, `coupé`→cut-over, `dégagement`→disengagement,
  `froissement`→graze, `coulé`→glide, `caver`→drop the point,
  `à-propos`→sense of timing, `coup de temps`→time-hit, `appel`→stamp of
  the foot, `rassemblement`/`-er`/`-ant`→gathering of the feet. The model
  emits `English<fn>Fr. term</fn>`; `enforce_cat3_footnotes()` is the
  backstop (translates every stray French occurrence, footnotes the
  first). NOT an explanatory gloss — just the source word.
- **`footnote_glossary.txt` deleted** — the definitional glosses were
  translator commentary, which the "clean translation, no editorial
  voice" goal rules out. `pull_footnotes()` now only turns the model's
  own `<fn>` tags into `†N` + a `___FOOTNOTES___` block, deduped once per
  book. The PDF label is "Original terms:".
- **Rule 4 — headings are normal text.** All-caps line ⇒ almost always a
  heading, translate fully, no `<i>`, but keep a kept-term's accents
  intact (`PARRIES OF THE CROISÉ`, never `CROIS`).
- **Rule 6 — output only the translation**, first character = first
  character of the translation, no preamble, no Markdown.
- **EXAMPLES block** — few-shots for the `<ACTION> DE/DES/SUR/PAR <THING>`
  all-caps heading pattern (`PARADES DE UNE-DEUX` → `PARRIES OF THE
  ONE-TWO`, `ATTAQUES PAR UN COUP DROIT, AVEC DEMI-FENTE` → `ATTACKS WITH
  A STRAIGHT THRUST, WITH A HALF-FENTE`, …), added because zero-shot the
  model left the connectives in French or mistranslated `coup droit` as
  "straight right".

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

### 2.11 Author's footnotes vs. ours

A page can carry the **author's** own `(1) (2) (3)` footnotes *and*
**our** Category-3 `<fn>` source references. Both land in the same grey
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
- `pull_footnotes()` — our `†N` Category-3 markers, deduped once per book.

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
  `translation_prompt.txt` → `rm translation.txt`, re-run steps 2–4.
  Tweak `vlm_ocr_prompt.txt` → you need a real re-OCR (`rm -rf` the
  book's folder). Tweak the *parse/reconcile* logic only → 
  `reprocess_extracted.py` rebuilds from `raw_ocr.txt`, no OCR cost.
  Full detail in [`setup/MANUAL_SECTION_III_USAGE.md`](../setup/MANUAL_SECTION_III_USAGE.md).
