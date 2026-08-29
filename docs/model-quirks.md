# Model quirks & prompt tweaks — a field guide

Everything the two models (olmOCR-2-7B for OCR, Qwen2.5-7B for
translation) get *wrong* in this corpus, the concrete example that
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
  printed sequence** (`i-1 → v-1` or `i+1 → v+1`). Non-trusted pages are
  interpolated from the nearest trusted anchor and bracketed `[n]`. It
  never emits ≤ 0.
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

**Mitigation:** `strip_leaked_headmatter()` peels leading body lines that
are a bare number or that match a header seen elsewhere in the same book
(it runs after all pages are parsed, so it has the book's whole header
vocabulary). Also in `reprocess_extracted.py`.

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

### 1.6 Printed marginalia are dropped

**Seen:** Gomard 1845 — printed marginal side-notes next to the main text.

**Behaviour:** olmOCR silently omits them and transcribes the main column
cleanly. **Accepted as-is** — the main text stays uncontaminated, and the
side-notes in this book are cross-references, not substance. Flagged here
in case a future source carries real content in the margin.

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

## Part 2 — Translation (Qwen2.5-7B-Instruct, port 11434)

The 7B model follows the term-policy well and the output-hygiene rules
intermittently. Every guard below exists because the prompt alone wasn't
enough.

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
| `2_translate.py` (after each candidate) | still rewrites `^-{3,}$` → `* * *` and strips `![alt](url)`, `**bold**`, `^#{1,6} ` — defence-in-depth, and keeps the reading clean regardless |
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

### 2.3 Won't self-select footnotes; won't keep terms in French inline

**Seen:** across many test pages, the model emitted **zero** `<fn>` tags
(rule 9), and it will not leave a term in French mid-sentence to anchor an
inline marker — it translates everything (which is rule 1, so that part is
correct).

**Consequence / design pivot:** footnotes are **source-keyed**, not
model-selected. [`pipeline/footnote_glossary.txt`](../pipeline/footnote_glossary.txt)
holds ~16 curated `term | gloss` lines. `pull_footnotes()`
([`2_…py`](../pipeline/2_translate.py) L79) scans the **source** text of
each page for those terms and appends a `___FOOTNOTES___` block the first
time each one appears in a book — glossed once, then never again. Max 4
per page. `_render.py` splits that block off; `3_` renders it end-of-
section in small grey type, `4_` reserves a strip at the bottom of the
right column. The model `<fn>` path still works but is rarely taken.
Glossary keys are accent-stripped (`_strip_accents`) so `dégagement` and
`degagement` both match.

### 2.4 Braced command groups still flatten ~half the time

**Seen:** every book with drill chapters.

Despite `translation_prompt.txt` rule 7 and a worked `Doublé { high /
inside / low` example, the model merges braced alternatives into a
sentence on roughly half the pages where they occur. Partially mitigated,
not solved. Open item in [STATUS](STATUS.md) #1.

### 2.5 ToC / dotted-leader pages half-translate kept terms

**Seen:** contents pages list entries like `Coupé . . . . 44`. The model
translates the surrounding structure but leaves scattered kept-terms
(`coupé`, `battement`) untranslated and *un-italicised* — inconsistent
with how it handles them in body text.

Low priority (contents pages are navigational), noted for awareness.

### 2.6 Literary prose defeats the 7B model

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

The prompt (`translation_prompt.txt`) went through several passes. The
current shape:

- **Rule 2 — KEEP in French, wrap `<i>…</i>`:** named guards/actions with
  no plain-English equivalent — `garde`, `dégagement`, `coupé`, `doublé`,
  `riposte`, `remise`, `reprise`, `prise de fer`, `froissement`,
  `croisé`, `liement`, `fente`, `appel`, `corps à corps`, `volte`, the
  numbered lines (`tierce`, `quarte`, …), and `épée` (which *is* the
  English term). Plus Italian / German / Latin equivalents. (Each book's
  terminology page tends to surface one or two more — `corps à corps`
  came back "body to body", `volte` was OK but pinned explicitly.)
- **`enforce_kept_terms()` backstop** — the 7B drops the `<i>…</i>` off a
  rule-2 term on short fragments (plate captions especially) and sometimes
  mistranslates outright: `prise de fer` → "PRIZE OF THE BLADE" ("prise"
  read as "prize"), `liement` left bare and unglossed. This deterministic
  pass snaps a known mistranslation back to the term and wraps any bare
  occurrence in `<i>…</i>` (lower-cased, even inside an all-caps caption)
  so `pull_footnotes` / `footnote_glossary.txt` supply the gloss. Only the
  unambiguously-French terms (`prise de fer`, `corps à corps`, `liement`,
  `dégagement`, `coupé`, …) — a bare "riposte" / "reprise" is often real
  English now and is left to the model.
- **Rule 3 — TRANSLATE these** even though they look technical, and do
  **not** italicise: `fleuret`→foil, `sabre`→saber, `parade`→parry (never
  "riposte"), `coup droit`→straight thrust, `dérobement`→evasion,
  `doublement`→doubling, `une-deux`→one-two, bare `coup(s)`→thrust(s),
  `soie` (of a blade)→tang (not "silk/thread"), `caver`→dig/drop the
  point (not "cave"), `fort`/`faible`→forte/foible, and the heading
  connectives **DE / DES / SUR / PAR / AVEC** → of / against / with.
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

### 2.10 Running headers translated separately

`translate_headers()` collects each **distinct** running-head string from
`extracted.txt`, translates it once through the full prompt (rule 4), and
writes `headers.json` (`{ raw → english }`). The PDF steps look it up for
the section label / right-column header — `FLEURET` → `FOIL`,
`LES PARADES` → `THE PARRIES`, `LA GARDE` → `THE GUARD`. A book-title
running head gets translated too (no per-book "keep in French" exception
yet). Library-catalogue lines that leak into `HEADER` (`Bibliothèque
Larousse 15720`, an author-credit line) pass through mangled — cosmetic.

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

A page can carry the **author's** own `(1) (2) (3)` footnotes (plain prose
in the OCR body, translated and flowed inline) *and* **our** glossary /
`<fn>` notes (a `†N` marker + a grey ruled block). The dagger-vs-number
split is the standard scholarly convention; on top of that our block now
opens with an italic **"Translator's notes (AI):"** label
(`_render.footnote_lines`). If it still reads as too much on
footnote-dense pages, the lever is to defer a glossary term's gloss to a
later occurrence that isn't already crowded.

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
