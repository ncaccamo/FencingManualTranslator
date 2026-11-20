# How the PDF steps work

Steps 3 and 4 turn `translation.txt` (+ the source scan + a couple of
sidecar files) into the two output PDFs. This is the map of how — the
tools, the shared layout engine, and the knobs — for anyone making
formatting decisions.

- [`pipeline/_render.py`](../pipeline/_render.py) — the shared text
  layout engine
- [`pipeline/3_english_only_pdf.py`](../pipeline/3_english_only_pdf.py) —
  the flowing English reading copy
- [`pipeline/4_side_by_side_pdf.py`](../pipeline/4_side_by_side_pdf.py) —
  scan-left / translation-right

---

## The tool: PyMuPDF (`fitz`)

One library does everything. It's imported as `fitz`. Only a handful of
its primitives are used:

| Call | What it does |
|---|---|
| `fitz.open()` | new empty PDF (output); `fitz.open(path)` opens the scan |
| `doc.new_page(width, height)` | add a blank sheet; sizes are in **points** (72 pt = 1 inch) |
| `page.insert_text((x, y), s, fontsize, fontname)` | one short string at a point (labels, headers) |
| `page.insert_image(rect, stream=…)` | drop a JPEG into a rectangle |
| `page.draw_line`, `page.draw_rect` | the divider, the footnote rules |
| `page.show_pdf_page(rect, src, i)` | paint page `i` of another PDF into a rectangle — this is how the scan gets onto the left of the side-by-side |
| `src_page.get_pixmap(matrix, clip)` | rasterise (part of) a scan page to pixels; `.tobytes("jpeg", jpg_quality=…)` for embedding |
| `src_page.get_text("blocks")` | the scan's own embedded OCR text layer, as positioned boxes — used for paragraph alignment and (`_textlayer`) printed paragraph starts; never for its words |

Coordinates: origin is **top-left**, y grows **downward**. A "rect" is
`(x0, y0, x1, y1)`.

Fonts are the PDF base-14 (`times-roman`, `times-italic`, `times-bold`) —
always present, no font files to ship.

---

## `_render.py` — the text layout engine

Both PDF steps flow body text through this. It exists because the old
code did `text.replace("\n", " ")` and one `insert_textbox`, which threw
away every line break, indent and `<i>` span (turning fencing drills into
a wall of prose) and **silently truncated** anything that didn't fit.

### The model

Text → a list of `Line` objects. A `Line` has:

- `indent` — left offset in points (from counting leading spaces in the
  source line: drill blocks, braced command groups keep their shape).
  `indent < 0` is a **sentinel** meaning "draw a horizontal rule here"
  (the footnote separator).
- `spans` — `[(text, italic_flag), …]`, so `<i>…</i>` becomes real
  italics.
- `gap` — a blank line (paragraph break); drawn as partial leading, not a
  full empty line.

### The two functions that matter

- **`layout(text, width, size) -> [Line]`** — splits on `\n`, derives
  each line's indent, greedy-word-wraps the rest to `width` at `size`.
  Wrapping widths use `fitz.Font(...).text_length()` so accented and
  italic glyphs advance correctly (an earlier version summed
  `get_text_length()` and words like `DEUXIÈME` collided with the next
  word). It first applies `line_name_italics()`: the French lines /
  parries (*prime* … *octave*) are italicised here, at drawing time, like
  *riposte* -- the translation mostly left them roman. "prime" only as the
  fencing term ("past his prime" stays), "octave" not beside verse or
  music; text already in `<i>…</i>` (an italic running head) is untouched;
  in a compound only the line name: counter-*quarte*. A PDF rebuild applies
  it to every book; no retranslation. Footnotes are not passed through it.
- **`draw(page, rect, lines, size, *, dry=False) -> (leftover, y_end)`** —
  draws as many `Line`s as fit in `rect`, using a `fitz.TextWriter` (one
  batched write, correct glyph metrics). **Returns the lines that didn't
  fit** — the caller flows them to the next slot/page. Nothing is ever
  dropped. `dry=True` measures without drawing (used to pick a font size
  that fits).

### Paragraph style, headings (body text)

The OCR marks paragraphs at random -- a blank line or a bare line break,
0, 2 or 4 leading spaces on the same page (Kirchhoffer pdf 13) -- so
body text is laid out in **one style per book**: `layout(...,
paras="indented"|"spaced", centre=...)` (`_layout_paras`). The style is
`[book] paragraphs` in the overrides file, `"indented"` unless set.

**Where a paragraph starts** comes from the scan's text layer when it has
one (`_textlayer.apply`, steps 3 and 4, before `layout()`): its words are
too poor to use, but its line positions are the print's. A line runs on
from the one above only when that one reaches the right margin and this
one starts at the left margin (on a page that indents its paragraphs), or
round a drop capital; a line at the page's indent always starts one. So
an OCR row that runs on in print is joined to the row above (Kirchhoffer
pdf 12: «Jusqu'à» beside the drop capital), and a page whose first printed
line is not indented opens mid-paragraph -- `_textlayer.CONT` in front of
the text, and `layout()` gives it no indent (pdf 14). A blank line the OCR left
before a row -- a run-in title ("L'épée. — ") or any other -- is dropped where
the print has normal line spacing above it (pdf 13); a space is never
added from the layer, since a line missing from it looks like a gap
(pdf 38-40). Left as they were:
pages without a layer or with a noisy one (Internet Archive pages whose
line starts are scattered), table pages, rows that open a new item even
when printed flush (dialogue dashes and quotes, `1°`, `a)`, run-in titles,
italic glossary heads, margin notes) and words split by a hyphen. In the
side-by-side, an indented book adds no space between the blocks
`render_aligned` anchors (the OCR's blank lines; pdf 13).

The text layer also says **where each row starts**: a row the book sets at
the paragraph indent gets it even when it is a short line the layout would
otherwise set flush as structure (drill commands, the first of a numbered
run, a dialogue dash -- Jacob-André pdf 112), and a row the book sets flush
loses an OCR indent (`_textlayer.INDENTED` / `FLUSH` in front of the row;
`layout()` honours them). The **first text line** is found by its words
(the OCR's first rows), so a running head in capitals or a number in the
margin is never mistaken for it; when the first row began at the foot of
the page before, the line there decides whether it opened a paragraph.
A **margin note the OCR also read as a body row** (Gomard: «⟦MARGIN: Elles
ne conviennent …⟧» then «Elles ne conviennent …») is dropped from the
source rows when the English has it once, so the rows pair (pdf 195); and
a row's printed line that begins with its margin note («Du salut du mur.
Le salut du mur est celui …», pdf 281) or starts far out in the margin (a
note the layer garbles, pdf 295) gives no start -- read as flush, the
paragraph lost its indent. A mark a `[[fix]]` puts on a row stands: the
layer adds none in front of it.
**Dividers** the OCR never reads (Joseph-Renaud's ⁂) come back from the
layout detector (`_layout.ornaments`): a small mark centred in the column,
in a gap between paragraphs (or under the page's last line: a section's
end), with no line of text through it and no title right above.

**Page text, before layout** (steps 3 and 4, `_render`):
`english_quotes` -- «…» and straight "…" become “…” (a lone » is a ditto
mark and stays; old books' per-line « -- read by the OCR as • on
Prévost-Jollivet pdf 237 -- is one quotation); `drop_margin_echoes` -- a
margin note the OCR also read into the running text (Gomard) is drawn
once, as the note -- but the same note twice on one page is a lesson's
label column the margin step misread (Masiello pdf 581): the notes go and
the labels stay in the text; `split_note_run` -- "(1) … (2) …" run together in one
line are separate notes; `<sup>2</sup>` is drawn as ², not as the tag.

**Columns and hanging indents** (format review, 2026-10-22). A page the
book sets with *hanging indents* -- a glossary entry's first line flush,
its other lines in (Prévost-Jollivet pdf 314-319) -- is recognised from
the text layer (`_textlayer.profile`) and its entries drawn that way
(`HANGING` row mark). A lesson set as a *table of paragraphs* --
Maestro | Allievo (| Allievo) side by side, the OCR reading one column
after the other (Masiello) -- is rebuilt as a table from where each
paragraph's first line sits on the scan (`_textlayer.lesson_columns`, on
the layer's unmerged pieces): 2+ columns beside each other, at least two
rows pairing an action with a reply of 8+ words; an intro ending ":" or
a § heading ends it. Each row finds its printed piece by its whole
opening (up to 40 letters), the best pairs first: by the first 15 alone a
row moved from the page before took a look-alike's place and the rows
below it moved up one (Masiello pdf 292); on a tie, a line printed once goes
to the row it is the whole of (pdf 292 §49), lines printed alike to their
rows top to bottom (pdf 313). The OCR's margin copies of a label inside
the run are passed over (pdf 581), and "narrow" is measured against the
text's full span, not its widest line -- a page of lessons only may have
no full-width line (pdf 582). A reply the OCR broke into two
rows, the second in lowercase, is one cell -- here and in the side-by-side's
column tables (`_columns.build`; never into a column's head, «A. | B.»). A
line the page repeats («Si eseguisce in due tempi:» under each §), printed
as often as the source has it, goes to its copies top to bottom (`_columns.place`;
it had shown twice under one § and rows under several §§ had swapped).
A lesson with numbered steps keeps `_pair_lesson`,
and the side-by-side no longer splits it at the blank line between its
halves (`lesson_glue`). A two-column contents ("49. … | 53. …") is read
column by column. **Captions:** on a plate page the caption is centred
(level with the scan's caption when the detector boxed it); a wide
caption under a picture on a page of text (Kirchhoffer pdf 12) is drawn
centred, level with it, above the text (`_layout.picture_caption`).

- A line is a **paragraph of running text** only when it reads as one:
  60+ letters, or ending a sentence / list item (`. ! ? ; :`) right after
  another paragraph ("The thumb guard, ...;"). A long numbered paragraph
  ("4° Le bras droit ...") counts too.
- Everything else is **structure** and keeps the OCR's own lines and
  blank lines: short drill steps, two-column dialogue tables (Barbasetti
  scan 134), brace groups (from a `{` line to the next blank line; Gomard
  scan 232), word lists, legends.
- *indented*: each paragraph gets a first-line indent; a blank line
  between two paragraphs (one of them long) is dropped -- except before a
  run-in section title ("L'épée. — ", `_RUN_IN`) and after a line ending
  `:`, where the book leaves space. *spaced*: a blank line between
  paragraphs, no indent.
- A page that opens mid-sentence (a small letter) gets no indent.
- A sentence the OCR broke at a printed line end is rejoined (`_rejoin`:
  the line runs on with a comma or word, the next opens in lower case).
- **Headings in capitals are centred** when the book centres its headings
  (`centres_headings()`: 60 %+ of the layout detector's heading boxes sit
  centred over the text -- most books; Bruno, Danet, Saint-Martin don't).
- Punctuation glued to an italic term never starts a line on its own.

- **Section dividers**: a `[[DIVIDER]]` line (step 2 keeps the book's
  divider -- stars, ⁂, bullets, a tilde rule -- where the OCR read it;
  `restore_dividers` puts it back in an existing translation when the
  English has the source's lines one for one; a `[[fix]]` can add one
  the OCR never read -- `_textlayer.apply` then carries it on the next row
  so the rows still pair with the source's and the page keeps its
  paragraph starts) is drawn as centred
  `*   *   *` with space around it. A divider the OCR missed can't be
  recovered.
- **Section breaks as the book marks them** (owner, 2026-11-16): stars
  only where the book prints a mark (⁂, stars, an ornament: `[[DIVIDER]]`);
  `[[BREAK]]` where it leaves space only -- a blank line (two in a book
  whose paragraphs are spaced), no stars (Jacob-André pdf 56); `[[RULE]]`
  where it prints a short rule -- a short centred rule (Jacob-André pdf
  208, Barbasetti pdf 97). Both are written by a `[[fix]]`; the text layer
  carries them like a divider. A space-only break can't be found from the
  OCR text; the text layer shows it as a gap of ~2 lines, but so are the
  gaps before headings and footnotes, so it is not detected (a dry run over
  the release books, 2026-11-16, was right on one book only).
- **Closing signatures flush right** (`_textlayer.signature_rows`, mark
  `RIGHT` = `\x06`): a short line of names (initials, capitalised names,
  particles: «F. Corso.», «PAUL DE CASSAGNAC.», «L'ACADÉMIE D'ARMES.») set
  well in from the margin and ending at the text's right, right after a
  paragraph's end or a closing salutation, is set flush right. Found on the
  scan's text layer, matched to the OCR row by its words (or, in a garbled
  layer, the one name-like row that reads nearly the same: «KWClillOFFEH.»
  for «KIRCHHOFFER.»); never «Pag.», a plate head or a lesson's «I. TEMPO».
  A corpus dry run found only signatures and the attributions the books
  set right (Tasso under a quotation).
- **A sentence in the future tense is never a title** (`_title_line`): a
  short line alone between blank lines, with «will» / «shall», is text --
  a lesson's label at a page's foot («He will engage in terza.», Masiello
  pdf 291) and a paragraph after a divider (Joseph-Renaud pdf 142) had
  been set bold or centred as headings.
- **Rows the book centres** (`_textlayer.centred_rows`, mark `CENTRE` =
  `\x07`): a source row whose printed lines (at most three) all sit in
  from both sides of the text by about the same (6 %+ in, within 5 %), and
  start neither at the margin nor at the paragraph indent, is set centred
  -- as a heading where it reads as a title or is in capitals, else in body
  weight. Headings the book centres had been set as body lines wherever
  the text layer marked them as a paragraph start (page sweep 2026-11-15;
  six of the seven release books). An item of a lettered list («c) le
  parate …;») that only happens to sit midway is not centred when another
  item starts at the same place uncentred (Masiello pdf 374, 628); a
  lettered heading the book centres has no such neighbour. On a chapter's
  first page the running head repeats the heading below it («110 DU
  DÉVELOPPEMENT.» / «DU DÉVELOPPEMENT.»): the second capitals line is the
  heading, set centred where it sits so, else unmarked as before (Gomard
  pdf 127; it had taken the first paragraph's indent).
- **Items of one list set alike** (`even_lists`): 3+ rows opening with
  the same kind of marker («1°», «2.º», «a)», «— ») take the mark and
  indent most of them have.
- **Not titles**: a line with a «;» clause (Masiello pdf 736), or a «1°» /
  «2.º» item unless title-cased (Gomard pdf 240; «2° The Sixte and Its
  Counter.» stays one).
- **Lines kept apart**: two lines opening with the same word (not a common
  article or pronoun) and the second ending as a list item does are a
  list, not one sentence broken at a line end (`_parallel`, Barbasetti
  scan 71); a display page's lines are kept as printed (`as_printed`,
  Joseph-Renaud pdf 17).
- **A name inside a block of capitals** («THE FRIEND AND MAESTRO
  Ferdinando Masiello», Masiello pdf 10): a line opening with 2+ words in
  capitals, the rest a name, between two lines in capitals is set as they
  are (centred, bold where the book's headings are) (`_in_caps_block`).
- **An indented sentence under a line ending ":"**, alone between blank
  lines and 6+ words, is a quotation: kept at its indent in body weight,
  like verse above it -- not a bold centred title (Masiello pdf 67).
- **Quotations**: each paragraph of a quotation opens with “ again, as
  English sets it (Joseph-Renaud's dialogues); a line of verse the old
  book opens with « does not (the line before must be a full line of
  prose ending a sentence).
- **Margin-note echoes**: a body line the margin note repeats goes, but a
  line standing alone that is only the start of a longer note is the
  book's own sub-heading and stays (Gomard pdf 173).
- **Source rows rejoined for pairing**: where the English has fewer rows
  than the OCR source, source lines that break a sentence (no closing
  punctuation, the next in lower case) are rejoined when that makes the
  rows pair exactly (Prévost-Jollivet pdf 319); a glossary entry run on
  from the page before keeps the hanging indent on a hanging page.
- **Chapter summaries** (`[book] chapter_summaries = "centred"`): the
  topics under a chapter heading centred in small type (Masiello 1887).
- **Caption-only plate pages** (side-by-side): a page with a plate whose
  only text is short (45 words, no table) has it drawn as the recorded
  captions are -- small and grey -- not at body size or bold.
- **A short page keeps its notes** (English-only): a page of a few lines
  (a title: Masiello's synoptic table titles) whose notes would not fit
  under it at a sheet's foot starts the next sheet, its page label with it.
- **A page label alone on a sheet moves with its picture**, as with its
  text (Masiello 1887 sheet 70).
- **Line breaks**: the pieces of one word in different spans («<i>terza</i>-in-<i>quarta</i>»,
  «(<i>seconda</i>», «“<i>flanconnade</i>”») go to the next line together;
  a table's header cell in italics is set in bold italics.
- **Captions on turned plates** (side-by-side, `turned_caption_spots`): on
  a scan shown turned (90° / 270°), the caption boxes are mapped through
  the turn, and a caption-only page's text is set level with them, under
  the picture; two captions side by side are set side by side (Masiello's
  sideways plates, Kirchhoffer pdf 59).
- **Space above a heading in capitals** is never taken away by the text
  layer's spacing step (Joseph-Renaud «SECOND EXERCISE»).
- **Notes that do not fit at their page's foot** (English-only): a size
  smaller when that fits, else on the next sheet under «Notes to page N».
- **Hanging lines** of notes and contents rows are wrapped to the width
  they have after the hanging indent (Gomard pdf 32, Jacob-André pdf 324).
- **Drawing-time typography** (`layout()` and table cells, both PDFs;
  `translation.txt` and the fixes keep the plain form): straight `'` ->
  `’` after a letter, digit or closing mark, `‘` otherwise
  (`curly_apostrophes`, owner 2026-11-16; tags left alone; running heads
  too); an ending glued to an italic word goes inside the italics
  (`whole_word_italics`: «<i>épée</i>s» -> «<i>épées</i>», «<i>riposte</i>d»).
- **Two-column lessons** (Barbasetti: the master's actions left, the
  pupil's numbered steps "I. TEMPO …" right) arrive as two blocks; when
  the master has as many lines as the pupil has steps they are paired back
  into a two-column grid (`_pair_lesson`), so the actions alternate as
  printed.
- **Passages set in two parallel columns** -- master | pupil (Masiello),
  A | B (Barbasetti's «Esempi»), hits | parries (Enrichetti) -- the OCR
  reads one column after the other, so the English gives each fencer's
  lines in turn. Before the text-layer step, both PDFs rebuild such a
  passage from the scan (`_columns.column_tables`): the layout boxes give
  the two columns and the gutter, the scan's words each printed line's
  side and height, and each source line is found there by its first
  words; the English line with the same index takes its place (the
  translation keeps the source's lines one for one). Headings across the
  page stay between the tables, once (the OCR repeats them per column);
  in a table a line level with one in the other column shares its row,
  one set lower starts a new row. Drawing only: `translation.txt` is
  unchanged. Left as it is when anything is uncertain: the line counts
  differ, a line in or after the passage not found on the scan, a list of
  names or titles (Blengini, Joseph-Renaud), or any English line that
  would be lost.
  - **Labels, intros and page turns** (2026-11-13, Masiello pdf 294–600,
    Enrichetti pdf 125): two labels each centred over its column on one
    printed line («Maestro.» … «Allievo.», «COLPI» … «PARATE») are split
    by where the words *start* -- a line of up to three words, one well
    inside each column (the text layer's box for a small-capitals word can
    run far past it). A source line the scan spaces across both columns
    («Si eseguisce … in due tempi:») is found as its left and right pieces
    on one baseline. An intro ending ":" with nothing beside it is set
    above the table, so the label row after it becomes the table's header
    row. A table run on from the page before may open a page with one line
    on a side (one Maestro command beside the pupil's lines); anywhere
    else a table still needs two lines a side.
  - **A scan with no text layer** (Masiello 1902 foil, a photographed
    copy): the layout boxes alone place the lines (`box_positions`), one
    source line per box. The OCR read these pages in different orders --
    one column then the other, row by row, or the left half first -- so
    every order consistent with how it reads (the left half top to bottom,
    each right box after the left line level with it and before the next
    heading) is tried against the boxes' sizes (a line's length against
    the box's width × lines). The page becomes a table only when one order
    fits clearly best; otherwise, or when the boxes and lines don't count
    the same (a heading and its first sentence in one box), it is left as
    it is.
  - **Column widths as printed**: the table records the book's split
    (`<table data-split="0.45">`) and `draw_table` keeps it, so a master's
    short commands are not squeezed by the pupil's long lines. The left
    column runs from where its lines start to the column rule printed
    between the columns, found on the scan (`column_rule`: centred columns
    start well after it, short commands end well before it); with no rule
    found, the rule is taken as just before the right column. Each page is
    measured, so the split follows that page's print. The split is a
    ceiling: a left column of short commands takes only its longest line
    plus an em, so on the wider sheet the commands sit next to the
    pupil's lines instead of far from them. Tables from other
    sources (no `data-split`) are sized by their content as before.

- **A table with no English is drawn from the scan** (`untranslated_table_crops`):
  one the OCR could not read (Kirchhoffer's order of bouts) is cropped to
  the layout detector's table box and set where its `[[TABLE]]` marker was,
  in the English-only PDF; the page's tables that have English are drawn in
  the text as before.
- **A table at the foot of a sheet goes to the next one**
  (`_render.draw(fresh_top=)`): one that did not fit in the space left was
  skipped and the text went on without it. In the side-by-side, the
  paragraph alignment measures a table at its drawn height (it counted it
  as one line). A table too big for a whole column is still skipped (Bruno's
  synoptic tables in the side-by-side's right column).

- **Heading levels** (`heading_level`, `HEAD_SCALE`): a part ("FIRST
  PART", "PART II.", "BOOK III") is set 1.45 × the body size, a chapter
  or a front / back matter title ("CHAPTER III.", "PREFACE",
  "INTRODUCTION") 1.2 ×, centred, roman capitals as the books print them
  (not bold), with space before and under. A title in capitals set right
  under it («DE LA MARCHE.» under «CHAPITRE II.») goes with it, even
  where the text layer placed the row; a numbered section under a chapter
  («14. Inviti») does not. Parts and chapters are centred even in a book
  whose other headings are flush left (Danet). Every other heading is as
  before (level 3, body size). The level comes from the heading's wording:
  the printed sizes are not in the OCR (the layout boxes and the text
  layer measure them too unevenly to rank). Each `Line` carries its
  `scale` and `bold`; `draw`, `measure`, `line_height` and the side-by-side
  paragraph heights use them.
- **Bold section headings** (`[book] bold_headings`, `bold_title`): for a
  book that prints them so, a level-3 heading is set in bold, its number
  label («§ 4. —») in roman. A span's flag carries it (1 italic, 2 bold,
  3 both; `_face`). In such a book a «§ N. —» line is a heading even when
  long or followed directly by its text.

- **A letter's opening page is not a title page** (`display_page`): a page
  with no printed folio and only short blocks is centred as a display
  page -- except when one of its first three blocks is a salutation («MY
  DEAR JACOB,», «Monsieur,», «Caro …,») with 15+ words of the letter under
  it (Jacob-André pdf 42); a closing formula («SIR, / OF YOUR IMPERIAL
  MAJESTY,», Grisier pdf 12) stays one. One page in the corpus changed.

Captions, notes and the old callers use `paras=None` -- the previous
rules, unchanged.

### Footnotes

`2_translate.py` appends a `___FOOTNOTES___` block to a page's text.
`split_footnotes(text)` peels it off; `footnote_lines(notes, …)` lays it
as: a thin rule, an italic **"Translator's notes (AI):"** label (so it
reads as distinct from the author's own inline `(1)(2)(3)` notes), then
each note with a hanging indent.

---

## Step 3 — English-only (`3_english_only_pdf.py`)

**Sheet:** portrait US Letter (612 × 792 pt), 56 pt margins → one text
column `COL`. Body text 10 pt.

**The `Flow` class** owns "the current output page and the y cursor" so
the rest of the code never thinks about page breaks:

- **Page numbers in the margin** (`MARGIN_FOLIOS = True`, the default;
  user, 2026-11-20): the book's running heads alternate by printed page,
  which only makes sense beside the print, so the English-only copy draws
  none. `section_label` → `margin_folio`: the page's number (a trusted
  folio only, not a bracketed "[5]") waits and is set small and grey in
  the left margin beside the page's first line of its own text or the top
  of its first picture (`_mark`; not beside the page before's notes,
  Kirchhoffer pdf 11). A margin note opening the page is that page's
  first line and takes the number (Gomard pdf 15: «37» by "First attempt
  at the lunge…"; owner, 2026-11-20). The first number in the PDF reads
  «page 6», so a reader knows the numbers count pages, not lines or
  paragraphs. No rule, no head. A page opening mid-paragraph
  (its sentence ran on, `page_left_open`, or the scan's text layer starts
  it with `\x02`) goes on **on the same line** as the page before's last
  line, with a thin grey bar where the printed page turns (`_join`: plain
  prose only, and only when nothing was drawn after that line); when that
  line is full it goes on flush on the next line. Each page's notes are
  held (`Flow.held`) until the next page shows whether its paragraph runs
  on: then after that paragraph, else before the new page's text; a
  picture comes after them. A page starting a new paragraph is indented
  (a blank line before it in a `spaced` book). Kirchhoffer 74 → 67 sheets,
  Masiello 1887 369 → 342.
  **Author's margin notes** here (`_render.NOTES_SNUG`, `NOTE_BAR`):
  a grey italic label over its paragraph, with a small space above it,
  none below, and no bar (the bar means a page turn here). The paragraph
  under it is indented, as printed: every Gomard note stands beside a
  paragraph's indented first line, which the text layer reads as flush.
  A page whose English opens with a note opens a paragraph (Gomard
  page 37: the paragraph the print starts at the foot of 36 is translated
  whole on 37). The side-by-side copy keeps its notes as they were
  (bar, the text's own blank lines). Gomard 128 → 126 sheets; Masiello
  and Barbasetti (their few notes) are unchanged.
- `MARGIN_FOLIOS = False`: the old label line,
  `section_label(page_num, header)` — draws `— [n] —   HEADER` + a rule.
  Starts a fresh sheet if the label (plus a reserved height for a figure
  that's about to follow) wouldn't fit — so a figure's label is never
  orphaned at the bottom of a sheet; on a page of text it also keeps room
  for a heading and a line of text, so a label never ends a sheet alone;
  when what follows still makes no progress there (a table that moves whole
  to the next sheet), `_flow` takes the label off the sheet and sets it
  again above its text on the next.
  `header` is only a **genuine repeated running head** by the time it gets
  here (`reconcile_headers()` in step 1 sends one-off titles to the body);
  a book's two-part heads (`left\tright`, Kirchhoffer) are joined on one
  line, the half seen in more of the book's heads first ("FOIL · ON-GUARD
  POSITION") -- the book swaps the halves on facing pages.
- `_flow` passes `keep_heads` to `_render.draw`: a heading-like line
  (larger, bold, centred or in capitals) that opens a paragraph moves to
  the next sheet with its text when no line of that text fits under it.
  The side-by-side doesn't use it (one scan page, one English page).
- `body(text)` — `split_footnotes`, then `_flow(layout(body))`, then the
  footnote block smaller and grey. When the page's last sentence runs on
  to the next page, its notes are held (`Flow.held`) and drawn at the foot
  of their own page, just before the next page's head (`section_label`),
  as the book prints them: the head already breaks the sentence there
  (user, Barbasetti pdf 66, 2026-11-13; until then they waited for the
  sentence's end, inside the next page's text). Pictures still wait until
  the next page's first paragraph, where the sentence ends (Barbasetti
  pdf 63 and 76, 2026-11-04). `_flow` loops `draw()` → new page →
  `draw()` until there's no leftover.
- `figure(src_page, band, caption)` — rasterise the scan page (or the
  `band` sub-rectangle) at 150 dpi, embed as JPEG scaled to the column
  width (capped at 82 % of column height), translated caption beneath.
  A picture that misses the sheet's foot by up to a tenth of its height
  is drawn that much smaller instead of going alone onto the next sheet
  (`_render.fit_on_sheet`; Danet pdf 127, an ornament the book prints at
  the page's foot). The room kept under a picture is its whole caption's
  height, so a long caption is never cut off at the foot (Danet pdf 226,
  Vauresmont's cover); one longer than a sheet goes on over the next.

**Per source page**, the loop:

1. Gets `page_num` / `header` from `extracted.txt` (`header` looked up in
   `headers.json` for the English version).
2. Builds figure **placements** for the page:
   - Each entry in `figures.json` for this page → `band_for_figure()`.
     `plate` = whole page (cropped to the density profiler's ink band
     when portrait, to strip the running header / folio / scan margin);
     `inset` = the profiler's band.
   - If `figures.json` flagged **nothing**, run `find_figure_band()`
     anyway — the VLM under-reports. A profiler hit → an `inset`
     placement with a generic `[illustration from the original]` caption.
   - **With a `layout.json`** (every book since 2026-10), the layout
     detector's picture boxes replace both for every page but a
     `figures.json` plate: `_layout.pictures()` (score 0.8+, or 0.6 on a
     page with no text of its own; slivers, the "Digitized by Google"
     strip, decorated capitals and near-blank boxes left out; a weaker
     box round a stronger one, with a margin on every side, that it
     fills a third of or more -- a plate's printed frame -- is not a
     second picture: Kirchhoffer pdf 17) → one
     placement per picture, cropped to its box (a printed caption inside
     the box's foot cut off; a printed line of the text the box takes in
     at its top or foot cut off too -- `_layout.clip_at_text`: a line of
     the scan's text layer that runs across 80 %+ of the box and on past
     its side, never words inside the drawing or text set beside a
     vignette; owner 2026-11-16), drawn at its size against the text column.
     Captions: a page's standalone "Fig. N" lines move under their
     pictures when the counts match, else the `figures.json` captions;
     a label read off the scan under the picture is drawn as printed,
     parentheses included («(Fig. 30)», Barbasetti pdf 74). A
     `[pages.N] caption` set to such a label makes that one line the
     picture's label (`_layout.label_lines()`); other «(Fig. N)» lines
     are references under headings and stay in the text.
     A label printed **over** its picture (the caption box above it:
     Prévost-Jollivet's parry labels «Parade de sixte.», then the
     picture, then its text; Cordelois's notice over the signature)
     stays in the text, and the picture is drawn right after it with
     nothing under it -- the page drawn in pieces when it has more than
     one. Where nothing in the text marks a picture's place,
     `[pages.N] picture_after` names the paragraph it follows.
     A picture with no caption box and no figure record is a
     **decoration**: drawn unless `books/<stem>.pictures` leaves it out
     (the decoration review sheet writes that file). Step 3 lists every
     decoration it meets, and every picture `.pictures` drops, in
     `intermediate/decorations.json`; `decoration_sheet.py` turns that
     into the review page. A page with no text
     and nothing the detector can crop is left out before / after the
     text (covers, endpapers, blank leaves) and drawn whole inside the
     book (Saint-Martin's plates). A recorded "plate" before / after the
     text is left out only when its page is dark (a cover, a marbled
     endpaper: median grey under 168) or a white blank (`page_tone()`).
     Further rules from the agent review of the release books:
     - a figure is recorded, captioned on the scan, or a large picture
       (15 %+ of the page) on a page whose only text is its caption (an
       unrecorded plate); only figures take that text as their caption;
     - the book's `.rotate` turns figures only, never a stamp or an
       endpaper on a plate-like page;
     - pictures go in reading order (rows, then left to right); one with
       more of the page's text above it than below is drawn after the
       text; a page whose text is one caption paragraph per picture puts
       each paragraph under its picture;
     - a printed caption inside or across the box's edge is cut off (any
       side: Gomard's sideways plates); small pictures keep their size
       against the text column; small ornaments the detector is sure of
       (0.85+) are kept; ink is measured against the box's own paper;
     - a picture listed in `.pictures` is dropped whatever it is;
     - a large picture on a page whose English opens on a plate heading
       ("PLATE V." over a two-line caption: Gomard's unrecorded Planches)
       is a figure, so the book's `.rotate` turns it;
     - on a page the book's `.rotate` turns, a sideways printed caption
       (a tall thin strip down the picture's side that the detector rates
       only 0.35-0.5) is cut off too.
     Found 2026-11-04: the profiler missed
     line engravings and second / third figures (Barbasetti pdf 42, 63;
     Masiello's plates; Gomard's Planches) and drew covers and stamps
     (Jacob-André).
   - general rule 2 (2026-11-06): a picture's printed caption that the
     OCR also set as a paragraph of the text («Schéma A.», Kirchhoffer
     pdf 52; Prévost-Jollivet pdf 282-305) is found by its place — the
     scan's text layer prints it inside the detector's caption box under
     that picture (`_layout.caption_paras`) — and drawn once, as that
     picture's caption; a page's one such picture goes where the line was
     (before the text, after it, or after the paragraph before it). The
     side-by-side keeps the line: it is that PDF's only copy of the caption.
     Only when the source and the English have as many paragraphs.
     A caption a reviewer set (`[pages.N] caption`) that the English text
     does not carry is drawn in the side-by-side's English column above the
     text; a picture's alt text in the OCR («![caption](…)») does not count
     as the source carrying it -- the translation drops it
     (Prévost-Jollivet pdf 307, 2026-11-13). A
     two-line printed caption (a credit over a description, Blengini pdf
     49-58) is matched whole: the next paragraph joins when the detector's
     larger caption box, or the one right under, holds its line; a letter
     or two misread in the layer still matches — but not a paragraph
     opening on a small letter (the rest of a sentence, Prévost-Jollivet
     pdf 218). A caption line the OCR set mid-sentence («against the |
     horseman», André pdf 387; «com- | bat», Prévost-Jollivet pdf 305):
     the sentence's halves join and the picture follows it. Captions take
     English quotation marks, as the text does.
3. `section_label`, then draw each figure, then `body(tr)` — **unless**
   the body is just the figure's caption again (≤ 20 words, matches a
   caption), in which case the body is skipped. When the page before
   stopped mid-sentence, this page's figures wait until this page's
   first paragraph is drawn, so none lands inside the sentence (its notes
   were drawn before this page's head) (`_render.page_left_open`: also when its last line has an
   unclosed "(" — "(see fig." with the number on the next page, Barbasetti
   pdf 92 — but never after a title page, whose last line is an imprint:
   Kirchhoffer pdf 12). A page set `[pages.N] leave_out = true` (library
   or digitiser text) is left out.

Text flows continuously — consecutive source pages pack onto a sheet;
a page's text can resume mid-sentence after an inserted run of plates,
which is faithful to books that bind their plates mid-chapter.

**`find_figure_band(page)`** — the density profiler. Rasterises the page
small, profiles each row's dark-pixel fraction, finds the tallest
contiguous run of near-solid rows in the interior 8–92 % that also spans
> 30 % of the width. Returns `(y0, y1, x0, x1)` fractions or `None`. Tuned
for framed half-tone plates; weak on faint line art (hence the VLM flag
carries detection, this just does geometry).

---

## Step 4 — Side-by-side (`4_side_by_side_pdf.py`)

**Sheet:** landscape US Letter (792 × 612 pt). Left rect `LEFT` for the
scan, a vertical divider at x≈396, right rect `RIGHT` for the translation.

**Left column:** `page.show_pdf_page(LEFT, src, i)` paints the whole scan
page, fit-and-centred. No cropping, no detection — the figure comes along
for free (this is why figures were easy here and hard in step 3).

**Right column:**

- Header row: `Page [n]` + the (translated) running header + a rule,
  **in the book's places** (see "Page number and running head" below).
- **A plate page with only its caption** (from `figures.json`): drawn
  level with the scan's caption box (`_layout.caption_places`; Kirchhoffer
  pdf 92), else at the top.
- **Pages of a few short paragraphs** (picture captions, title pages, a
  dedication: at most 8, each 30 words or fewer): each paragraph is drawn
  level with its own block on the scan, and centred where the book centres
  it (`_layout.block_places`: the detector's text / caption / heading
  blocks, used only when there are as many as English paragraphs;
  Kirchhoffer pdf 66, its title pages). Whether a block is centred is
  measured against the frame of the page's boxes at least a fifth of the
  page wide, so a library stamp or a corner badge doesn't skew it.
- **Display pages** (`_render.display_page`: such a page with no printed
  page number -- title page, half-title, a plate's title leaf, a
  publisher's notice): every block centred, in both PDFs, since the
  geometry can't tell centring on a page of two or three short lines; the
  blocks still go level with the scan's when they pair up. A line the OCR
  filed as the page's running head is the page's title there, drawn first,
  not as a head (Kirchhoffer pdf 10). A block in capitals counts however
  long (a colophon, a dedication: Joseph-Renaud pdf 311, Masiello pdf 10);
  a title page with a long block in ordinary case is set with `[pages.N]
  display = true` (Gomard pdf 12). Display type sizes are not
  reproduced: everything is body size.
- **Tables**: step 2 translates each table's cells (`translate_tables`,
  `intermediate/tables.json`); the grid is drawn in the text where the
  page's `[[TABLE]]` marker is (`place_tables`, `draw_table`: rowspan /
  colspan honoured, columns sized by content, the font shrunk to 5.5 pt to
  fit, a blank line before and after). A table with no translation -- not
  run yet, or one the OCR read as headings only (Kirchhoffer's bout order)
  -- keeps the `[table — see facing page]` pointer (step 3: the scan
  image).
- **Contents and index pages** (3+ lines ending in a page number with dot
  leaders, "Page N" or "» N"): each entry's page number is set flush
  right with dot leaders, as printed; a two-column row joined by "|"
  (Barbasetti) is split in two; no paragraph indent (`_layout_toc`).
  In a book that centres its headings, headings in capitals are centred,
  and so is the contents' own title in ordinary case ("Table of
  Contents.", Kirchhoffer pdf 104); other short lines keep the book's
  place (Jacob-André's lesson summaries). The page-number column's own
  label ("Pages.", "Pag.") is set flush right over the numbers; when the
  OCR gave it as the first entry's page number ("Note n° 2 …… Pages."),
  the label goes above and the entry stays without a number (restore it
  with a `[[fix]]`). Not a "» same page" reference, nor an inline
  lower-case "pag." followed by "» 14" dittos (Tiboldi).
- Body: try font size **9, then 8, then 7 pt** — the largest that fits
  the column (`draw(dry=True)`). If even 7 pt overflows, draw what fits
  and spill the rest onto a `(cont.)` continuation spread (same scan
  repeated on the left). Text is never truncated.
- Footnotes: a fixed strip is reserved at the **bottom** of the right
  column; the body flows above it.
- **Plate pages** (no body text): instead of `[blank / no text in
  original]`, if `figures.json` has the page → `[plate — see facing
  page]` + the translated caption. The scan on the left carries the
  picture.

### Paragraph alignment

Since 2026-11-16: a heading stays with its text -- the space the
anchoring puts under a heading goes above it -- and a table is not
anchored (no text in the scan's layer to place it by: it follows the text
above). Passages in two columns (`_columns.place`) keep a repeated line in
reading order within its column («It is executed in two tempi:» under each
§, Masiello pdf 592), and a "table" with one column empty goes back to
text (a list set to one side, Masiello pdf 611).

When it works, each translated paragraph on the right is top-anchored to
roughly the same fraction down the page as its source paragraph sits on
the scan, so reading across lands in the right place.

- `paragraph_fracs(src_page)` — from `src_page.get_text("blocks")`
  (the scan's embedded OCR layer): the y-fraction where each source
  paragraph starts. `None` if the scan has no usable layer (many library
  scans don't).
- `reconcile_fracs(fracs, n_paras)` — maps those anchors onto the
  translation's paragraph count. If the scan split into **line-level**
  blocks (more anchors than paragraphs, > 2×), it resamples evenly — a
  raw one-to-one map pinned whole pages' text to the top. `None` for < 2
  anchors.
- `render_aligned()` — anchoring can only *add* whitespace (push a
  paragraph down to its source fraction), never remove it, so the dry pass
  measures three things: total added whitespace `W`, the largest single
  anchor gap `Gmax`, and overflow `X`.
  - `Gmax` > ~55 pt, or `W` > 35 % of the column → the anchors are
    untrustworthy (patchy Google Books layer) → **plain flow**.
  - `0 < X ≤ 0.8·W` → compress every gap by `1 − X/W` so a bigger font
    still fits (better than shrinking to 7 pt with full gaps).
  - The planned placement is dry-run first: a page that would lose a line
    at the foot takes the plain flow (it lost the last line silently:
    Masiello pdf 14, Barbasetti pdf 179, page sweep 2026-11-15).
  - otherwise → draw anchored as-is.

Alignment rate is a rough scan-quality indicator: Gallica ~70–80 %,
Google Books ~30–45 %.

---

## Cover page (`_cover.py`)

Both steps append a first page built by `_cover.render(doc, stem, …)` —
one shared renderer so the two covers can't drift (they did once: a
hard-coded model name outlived a model swap and sat wrong on every cover).

What it shows and where each piece comes from:

| Block | Source |
|---|---|
| title · English title · full title + English (when longer) · author · first published · this edition (when later) | `books/<stem>.meta.json` (`title_en`, `title_full`, `title_full_en`, `year_first`, `year_edition`, `edition`, `first_as`, `publisher` hand-checked against the scan; `year_first` is also the stem's year, so stems sort as a timeline) |
| "The original work is in the public domain." + digitisation source/URL | `meta.json` (`rights`, `source`, `source_id`, `source_url`, `source_terms`) |
| disclosure sentence, method, reading-conventions | constants in `_cover.py` |
| OCR / translation model names | OCR: live from `VLM_MODEL_PATH` (label drops the quant tag). Translation: step 2's per-page record (`intermediate/translated_from.json`), with page counts if models were mixed; pages with no record (translated before 2026-09-25) are credited to Qwen2.5-14B-Instruct-Q4_K_M |
| `Translated with FencingManualTranslator <sha>, <date>` | step 2's per-page record (`translated_from.json` `stamps`: revision + date each page was translated). After a partial retranslation it lists each revision with its dates and page count; pages translated before 2026-09-30 show as "not recorded" |
| `PDF generated <date> with FencingManualTranslator <sha>` | render time. The revision (`pipeline/_version.py`) comes from `PIPELINE_VERSION`, else the repo's `.git` mounted read-only at `/data/git`; omitted if neither is reachable. Commit before a run you want to keep -- uncommitted changes aren't flagged |
| licence line (CC BY-NC-SA 4.0) + GitHub URL | constants |
| `pipeline id: <stem>` footer | the stem, for traceability back to `processed/` |

`books/<stem>.meta.json` is generated from `books/library.md` by
`scripts/make_book_meta.py` (`--force` to overwrite, or pass stems).
`publisher` is usually left `""` for you to hand-add. The `books/`
sidecars are git-ignored, like the rest of the private corpus.

### Other per-book override files (next to the PDF in `books/`)

| File | Effect |
|---|---|
| `<stem>.crop` | `left,top,right,bottom` fractions trimmed before OCR |
| `<stem>.rotate` | Turns sideways-printed plates upright in both PDFs (steps 3 & 4, via `_render.rotation_plan()`). One rule per line, `#` comments. `cw` / `ccw` / `180`, optionally `all:` (default `plates:` = figure pages only) — the whole book one way (Gomard). `cw: 35, 39, 55-229` / `ccw: 241` — listed scan pages, for books whose plates face different ways (Baudry's face the spine); a page rule wins over the book rule. Directions can be read off a Google/Gallica text layer (caption text running up the page → `cw`), but check each page by eye. `none: 31` keeps a page as scanned; `auto: off` stops the detection below. |
| (no file needed) | **Sideways plates found** (`_layout.sideways_plates`): a page is turned when its signals agree, or one says so and the other is silent. The layout boxes: a level caption under, over or inside the foot of a picture means upright; one tall, narrow caption beside a large picture, centred on its height, means sideways with its foot on that side; captions on both sides mean sideways, direction unknown (turned only by a book rule, Gomard). The text layer (Google / Gallica scans): real words running up or down, none across. Junk read from an engraving's hatching, words whose run is too short for their letters (Gomard), a sideways caption read as one-word level lines (André) and the digitiser's footer mark do not count. A page listed in `.rotate` wins. |

### Tables and folios in the PDFs

- A scoring / poule grid is a `[[TABLE]]` sentinel in `translation.txt`.
  Step 2 replaces every `<table>…</table>` (and a `<table>` the OCR
  truncated at the page foot) with the sentinel, collapses repeats, and
  — if what's left is only a short heading — translates just that
  heading and skips the model on the grid. If the model still mangles a
  surviving sentinel to a bare `TABLE` line, a post-pass snaps it back.
  Step 4 renders `[table — see facing page]` among the prose; step 3
  embeds the source page and drops the sentinel.
- A folio the reconciler only inferred (`[n]`) — or left blank (`""`) —
  produces **no** "Page" line at all in either PDF; front matter and
  unnumbered plate leaves just get the section rule. A run of ≥2 unread
  leaves between two trusted folios that spans more leaves than the folio
  gap covers is left blank rather than seesaw-interpolated
  (`[17][18][19][20][13][14]…` off the nearest anchor). Tight-gap-forced
  folios (incl. a single misread slot between two trusted folios, e.g.
  Gomard `903`→`303`, and monotonic-chain folios across unnumbered plate
  leaves) were promoted upstream and *do* show.
- **Two-part running head** (`SABRE` / `ATTAQUE ET PARADE`): drawn with
  the first half at the column's left edge and the second flush right --
  only in a book that really prints them (`_layout.two_part_heads`: two
  separate head boxes on 3+ such pages; Kirchhoffer). Step 1 also splits
  a head the OCR read on two lines; everywhere else the halves are joined
  into one line ("CONSEILS AUX TÉMOINS.", Jacob-André).

### Page number and running head

The side-by-side PDF puts the page number and running head where the book prints
them (`_layout.folio_plan`, `_render.folio_sides`). Learned per book
from the layout detector: on pages with a read folio, the narrowest box
in the top or bottom margin is the number, the widest other top box the
head; their place (left / centre / right of the page) is counted per
**odd / even folio** -- not assumed: Kirchhoffer prints odd numbers on
the left. A side needs 10+ pages and 80 % agreement; the head goes where
most pages print it, never on the number's side of a shared line. A page
with no read folio takes its side from the nearest one (pages alternate).

- Side by side: number and head on one line in their places (stacked
  when they would collide); a number printed at the **bottom** is drawn
  at the foot of the English half; `(cont.)` stays at the top.
- English-only: no running heads; the page number in the left margin
  where the printed page begins (see Step 3, `MARGIN_FOLIOS`; 2026-11-20,
  replacing the fixed number-and-head line above a rule of 2026-11-14).
- No clear plan (no layout.json, too few pages, disagreement): the old
  fixed layout. `[book] page_numbers = "outer" | "inner" | "centre" |
  "bottom-…"` sets it by hand for scans the detector can't read
  (Jacob-André).
- A full-page decorative vignette is **not** embedded as a whole-page
  "Figure N" in the English-only PDF when the page also has 40+ words of
  translated prose — the text wins, the vignette is dropped.

## Sidecar files (all in `processed/<book>_translated/intermediate/`)

| File | Written by | Used by | Contents |
|---|---|---|---|
| `translation.txt` | step 2 | 3, 4 | one translated page per record, `\x1e`-separated |
| `extracted.txt` | step 1 | 2, 3, 4 | `PAGE_NUMBER / HEADER / BODY_TEXT` scaffold per page |
| `page_numbers.txt` | step 1 | (reference) | one reconciled folio per line; `[n]` = interpolated, not printed |
| `figures.json` | step 1 (captions filled by step 2) | 3, 4 | `{ page_index: [ {kind: plate\|inset, caption, caption_en} ] }` |
| `headers.json` | step 2 | 3, 4 | `{ raw_running_header: english }` |
| `tables.json` | step 2 (`--tables`) | 3, 4 | `{ scan index: {"src": [table html], "en": [english html or null]} }` |
| `<book>_OCR.pdf` | step 1 | 3, 4 | verbatim copy of the source scan |

---

## Knobs

All near the top of each file:

- **Page size / margins** — `PAGE_W`, `PAGE_H`, `MARGIN`, `COL`, `LEFT`,
  `RIGHT`, `DIVIDER_X`.
- **Font sizes** — step 3 `FS = 10`; step 4 the book's `[book] text_size` (9 unset), then a point less at a time to 7 (`_render.size_ladder`: `(9, 8, 7)` for a book at 9); notes 7 pt × size / 9. Each text page's size goes to `intermediate/side_sizes.json`; `text_size_survey.py` compares sizes before one is set.
- **Figure height cap** — step 3 `FIGURE_MAX_H = COL.height * 0.82`.
- **Figure JPEG** — 150 dpi, `jpg_quality=72` (in `Flow.figure`). Raise
  for sharper plates at the cost of file size.
- **Density-profiler thresholds** — inside `find_figure_band()`: row
  darkness `> 0.30`, height `> 8 %` (or `> 3 %` if very dense), width
  `> 30 %`. Loosen to catch fainter illustrations, at the cost of false
  positives on dense text.
- **Alignment resample trigger** — `reconcile_fracs`: `len(fracs) >
  n_paras * 2`.
- **Leading / paragraph gap** — `_render.draw`'s `leading=1.28`,
  `gap_frac=0.55`.
