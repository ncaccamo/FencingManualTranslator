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
- `reconcile_page_numbers()` ([`1_…py`](../pipeline/1_ocr_and_extract_vlm.py) L207):
  a folio is trusted **only when ≥ 2 consecutive pages agree on the same
  page-index→folio offset**. Otherwise the line is a bracketed sheet
  ordinal, `[12]`. It never emits a number ≤ 0.
- Bug found + fixed: Joseph-Renaud front matter got `[-27]…[-19]` when the
  post-anchor extrapolation ran below 1. Front-matter / would-be-≤0 now
  fall back to `[i+1]`.
- `page_numbers.txt` records the result, one line per page; `[n]` means
  "inferred, not a printed number".

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

### 1.7 Figure detection misses line-drawing plates

Not a model quirk — a limitation of `find_figure_band()` in
[`3_english_only_pdf.py`](../pipeline/3_english_only_pdf.py), which
profiles row ink-density and is tuned for framed half-tone photo plates.
Full-page line-drawing plates and faint half-tones are missed (Gomard: 4
of ~20 caught). It will not invent a figure from dense text.

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

**Failure:** the pipeline joins/splits pages on the literal string
`\n---\n`. The model, despite being told plain text, emitted a Markdown
thematic break — a lone `---` line — inside one page. That page split into
two, and **every page after it misaligned with its source scan** in the
side-by-side PDF.

**Mitigations:**

| Where | What |
|---|---|
| `2_translate.py` (after each candidate) | `^-{3,}$` → `* * *`; strip `![alt](url)`, `**bold**`, `^#{1,6} ` |
| `translation_prompt.txt` rule 6 | "Output plain text, NOT Markdown: no `#` headings, no `---` rules, no `![image](...)`, no `**bold**`. The single exception is `<i>...</i>`." |
| known residual risk | `1_…py`'s `extracted.txt` uses the same separator; OCR is told to use `* * *`, lower risk. A truly collision-proof separator (form feed / long sentinel) is a deferred change — noted in [STATUS](STATUS.md). |

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
| `contains_too_much_non_english()` | L129 | after stripping `<i>…</i>`, > 35 % of words are in a French/Italian/German/Spanish/Latin stop-word list |
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
  `croisé`, `liement`, `fente`, `appel`, the numbered lines
  (`tierce`, `quarte`, …), and `épée` (which *is* the English term).
  Plus Italian / German / Latin equivalents.
- **Rule 3 — TRANSLATE these** even though they look technical, and do
  **not** italicise: `fleuret`→foil, `sabre`→saber, `parade`→parry (never
  "riposte"), `coup droit`→straight thrust, `dérobement`→evasion,
  `doublement`→doubling, `une-deux`→one-two, bare `coup(s)`→thrust(s),
  and the heading connectives **DE / DES / SUR / PAR / AVEC** → of /
  against / with.
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

---

## Part 3 — shared / architectural

- **`\n---\n` page separator is content-fragile** — see 2.1. Every stage
  joins and splits on it. This is the single most fragile assumption in
  the pipeline.
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
