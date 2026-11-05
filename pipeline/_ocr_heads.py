# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Step 1: running heads across the book -- a head the OCR left in the body,
catchwords, heads read from the layout detector's box, disputed and
alternating heads, and the book-wide reconciliation (reconcile_headers)."""

import re
from collections import Counter

from _ocr_parse import _norm_head, has_header_field


def _looks_like_section_title(line: str) -> bool:
    s = line.strip()
    words = s.split()
    letters = [c for c in s if c.isalpha()]
    return (1 <= len(words) <= 6 and len(letters) >= 3
            and sum(c.isupper() for c in letters) >= 0.85 * len(letters))


def leaked_top_header(body: str, known_headers: set) -> str:
    """If body's first line is a genuine KNOWN running head (not a bare
    apparatus number -- see strip_leaked_headmatter), return it so the
    caller can promote it into the page's own HEADER field before
    stripping destroys the evidence.

    Needed because strip_leaked_headmatter's TOP match intentionally
    also strips a CONFIRMED (repeated) header's body duplicate -- see
    known_headmatter()'s docstring -- trusting reconcile_headers()'s
    later _MAX_GAP-capped gap-fill to restore it. That cap is a distance
    from the nearest OTHER confirmed page; a run of several consecutive
    malformed-VLM pages (no HEADER: field at all, just raw text starting
    with the running head -- Gomard's "TABLE DES MATIÈRES." index, src
    334) can put one page just outside it, and by then the text has
    already been deleted from body with nothing left to infer from. This
    promotes on the spot, using this page's OWN evidence, before that
    happens -- strictly narrower than the gap-fill (no cross-page
    inference at all) so it carries none of that mechanism's risk."""
    first, _, rest = body.lstrip("\n").partition("\n")
    first = first.strip()
    # "LE JEU DE L'ÉPÉE" / "INTRODUCTION" (Jacob scan 49): two title lines
    # opening the text are its titles, not a running head + title
    second = rest.lstrip("\n").partition("\n")[0].strip()
    # (a contents entry "DES ATTAQUES SUR PRÉPARATIONS. 245" is not a title)
    if second and _looks_like_section_title(second) and not re.search(r"(?:\d+|\bid)\.?\s*$", second):
        return ""
    return first if first and _norm_head(first) in known_headers else ""


def _catchword_shaped(s: str) -> bool:
    """Short title-shaped line: 1-2 words, >=4 letters, mostly capitals, and
    not a name with an initial ("G. JOLLIVET.")."""
    words = s.split()
    letters = [c for c in s if c.isalpha()]
    return (1 <= len(words) <= 2 and len(letters) >= 4
            and sum(c.isupper() for c in letters) >= 0.85 * len(letters)
            and not re.search(r"(?:^|\s)[A-ZÀ-Þ]\.(?:\s|$)", s))


def tail_catchwords(parsed: list) -> set:
    """Printer's catchwords of this book: a catchword-shaped last body line
    that recurs at the foot of >= 2 pages (one per gathering -- Kirchhoffer
    "ESCRIME." on src 17 / 33). A one-off short capitals line at a page foot
    is content: a signature ("PAUL BOURGET"), a title-page line, "FIN."."""
    from collections import Counter
    seen = Counter()
    for p in parsed:
        lines = [l.strip() for l in (p.get("body") or "").split("\n") if l.strip()]
        if len(lines) > 1 and _catchword_shaped(lines[-1]):
            seen[_norm_head(lines[-1])] += 1
    return {k for k, n in seen.items() if n >= 2}


def strip_leaked_headmatter(body: str, known_headers: set, own_header: str = "",
                            catchwords: set = frozenset()) -> str:
    """Peel bookbinding apparatus off BODY_TEXT.

    TOP: the VLM sometimes drops the running header and/or the printed
    folio into the body, so the page opens with a stray "25" / "THE GUARD".

    BOTTOM: printer's signature marks -- a lone small number, or the title
    catchword (a short line matching one of the book's header words, e.g.
    "ESCRIME.") -- sit at the foot of the first page of each gathering.

    A line is apparatus when it is a bare roman/arabic number (<=4 digits,
    optional trailing '.', optional leading superscript letter) or its
    normalised form matches a header seen elsewhere in this book.

    *2026-09-18:* the "known header" match alone missed the catchword
    case the docstring's own example describes -- "ESCRIME." (Kirchhoffer)
    is the BOOK's overall title (from the title page), never any specific
    PAGE's running head, so it's never in `known_headers` and slipped
    through on every gathering (Kirchhoffer src 17, 33). Widened, but
    TAIL-ONLY (never the top -- a different risk profile; a leaked folio/
    header at the top of a page is a known, common shape, while a stray
    short line at the very start of body is more likely genuine content):
    a standalone tail line that's very short (<=2 words) and title-shaped
    (mostly uppercase) is also apparatus, regardless of whether it
    matches a confirmed page header.

    *2026-09-26:* that widening stripped 16 real lines across the six
    books for 2 real catchwords -- signatures (PAUL BOURGET, G. JOLLIVET.,
    A. SPINNEWYN.), title-page lines, Gomard's half-title "DE L'ESCRIME.",
    "FIN.". Now the short-capitals tail line must also be one of the
    book's `catchwords` (tail_catchwords(): recurs at >= 2 page feet)."""
    def _is_bare_number(s):
        # not a year: "1818." at the foot of La Boëssière's title page
        if re.fullmatch(r"1[5-9]\d\d\.?", s):
            return False
        return bool(re.fullmatch(r"[a-z]?[\dIVXLCDM]{1,4}\.?", s, re.I))

    def _is_apparatus(s):
        return _is_bare_number(s) or _norm_head(s) in known_headers

    def _is_tail_apparatus(s):
        if _is_apparatus(s):
            return True
        return _catchword_shaped(s) and _norm_head(s) in catchwords

    lines = body.split("\n")
    while lines:
        top = lines[0].strip()
        if not top:
            lines.pop(0)
            continue
        if _is_bare_number(top):
            lines.pop(0)
            continue
        if own_header and any(_norm_head(top) == _norm_head(h)
                              for h in own_header.split("\t") if h.strip()):
            # The VLM itself reported this text as the page's running head
            # AND transcribed it again as the first body line -- it saw it
            # printed twice. Gomard's chapter openers print a small running
            # head AND a large centred title below it (src 140 "DU COUP ET
            # DE LA BOTTE.", src 146 "DES COUPS SIMPLES.", ~40 more; same on
            # Prévost/Spinnewyn openers -- checked against the scans). A
            # true leak is the running head read INTO body with HEADER:
            # empty, which is the case below. Keep the title.
            break
        if _norm_head(top) in known_headers:
            # A KNOWN header's body duplicate is normally noise (the VLM
            # re-reading the running head into body) -- but if what
            # follows is ALSO a standalone title-shaped line, this is a
            # genuine two-line compound title block printed on the
            # section's opening page (Gomard "DE L'EMPLOI DE LA MAIN
            # GAUCHE" / "COMME MOYEN DE PARADE ET D'OPPOSITION." -- the
            # header carries only the first half; stripping it here
            # would silently drop "on the use of the left hand" from the
            # only place the full title is legible). Same precedent as
            # Prévost's confirmed "header and a body title both is fine
            # if that's what the book does" -- keep both lines.
            nxt = lines[1].strip() if len(lines) > 1 else ""
            if nxt and _looks_like_section_title(nxt):
                break
            lines.pop(0)
            continue
        break
    # Tail: a signature is at most catchword + number, so strip at most 2
    # apparatus lines, and never strip the last line if it is the only
    # content left (a one-line page could be a legitimate short entry).
    for _ in range(2):
        while lines and not lines[-1].strip():
            lines.pop()
        if len(lines) > 1 and _is_tail_apparatus(lines[-1].strip()):
            lines.pop()
        else:
            break
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Structural salvages run after parse_vlm_response() and before header
# normalisation / folio reconciliation. Shared by the main step-1 flow and
# reprocess_extracted.py via postprocess_parsed_pages().
# ---------------------------------------------------------------------------

_LEAKED_HEAD_RE = re.compile(
    r"^\s*(?:"
    r"\(?(?P<num1>\d{1,4}|[ivxlc]{1,7}j?)\)?[.\s]+(?P<head1>[A-ZÀ-Þ][A-ZÀ-Þ0-9 '’.,\-]{3,}?)"
    r"|(?P<head2>[A-ZÀ-Þ][A-ZÀ-Þ0-9 '’.,\-]{3,}?)[.\s]+\(?(?P<num2>\d{1,4}|[ivxlc]{1,7}j?)\)?"
    r")\s*\.?\s*$")


def _looks_like_running_head(s: str) -> bool:
    s = s.strip().rstrip(".")
    letters = [c for c in s if c.isalpha()]
    if len(letters) < 4 or len(s) > 60:
        return False
    return (sum(c.isupper() for c in letters) >= 0.85 * len(letters)
            and bool(re.fullmatch(r"[A-ZÀ-Þ0-9 '’.,\-]+", s)))


def salvage_leaked_folio_header(parsed: list, known_headers: set) -> None:
    """olmOCR-2 sometimes dumps the printed folio + running head into
    BODY_TEXT as line 1 ("246 DES ATTAQUES SUR PRÉPARATIONS.") instead of
    the PAGE_NUMBER / HEADER fields -- Gomard does this on ~50 pages, which
    also empties PAGE_NUMBER so every folio ends up interpolated. Recover
    it: line 1 = <num> <ALL-CAPS HEAD> (either order) -> move the number to
    page_raw, the head to header, strip the line from body. Only fires when
    the caps run matches a header seen elsewhere in the book or otherwise
    looks like a running head."""
    for p in parsed:
        body = (p.get("body") or "").lstrip("\n")
        nl = body.find("\n")
        line1 = (body[:nl] if nl >= 0 else body).strip()
        rest = body[nl + 1:] if nl >= 0 else ""
        m = _LEAKED_HEAD_RE.match(line1)
        if not m:
            # italic title-case head after the folio ("XIV    Table des
            # matières.", LaMarche scans 19 / 321) -- only a known head
            t = re.match(r"\s*(\d{1,3}|[IVXLCivxlc]{1,7})\s+([A-ZÀ-Þ][^\n]{2,48}?)\s*$", line1)
            if t and len(t.group(2).split()) <= 6 and _norm_head(t.group(2)) in known_headers:
                if not p.get("page_raw"):
                    p["page_raw"] = t.group(1)
                if not p.get("header"):
                    p["header"] = t.group(2).strip()
                p["body"] = rest.lstrip("\n")
            continue
        num = m.group("num1") or m.group("num2")
        # a year is not a page number: "REPRODUCTION D'UNE GRAVURE DE
        # BONNART (1680)" (LaMarche scan 10) is a caption, not folio + head
        if num.isdigit() and int(num) >= 1000:
            continue
        head = (m.group("head1") or m.group("head2") or "").strip().rstrip(".")
        if not head or not (_norm_head(head) in known_headers
                            or _looks_like_running_head(head)):
            continue
        if not p.get("page_raw"):
            p["page_raw"] = num
        if not p.get("header"):
            p["header"] = head
        p["body"] = rest.lstrip("\n")


def _confirmed_header_keys(parsed: list) -> set:
    """Running-head roots seen on >= 2 pages, keyed by a two-part head's
    LEFT half only (see reconcile_headers()). Used only to LIFT a header
    from a matching body line when HEADER is empty -- that inference stays
    conservative (only a header seen elsewhere in the book is trusted
    enough to read off body-text pattern matching)."""
    from collections import Counter

    def _key(h):
        return _norm_head(h.split("\t", 1)[0] if "\t" in h else h)

    counts = Counter(_key(p["header"]) for p in parsed if (p.get("header") or "").strip())
    return {h for h, c in counts.items() if h and c >= 2}


def known_headmatter(parsed: list) -> set:
    """Normalized header VALUES safe to pass to strip_leaked_headmatter()
    as "known headmatter" -- only those whose root is confirmed (see
    _confirmed_header_keys()). A one-off header must keep its body
    duplicate intact until reconcile_headers() has run: stripping it here
    erases the exact evidence that function's section-opener promotion
    needs to see (P-review, Gomard "CONSULTÉS PAR L'AUTEUR." on src 21 --
    its only OTHER appearance, as a real HEADER: value on src 19, is a
    one-off too, so without this restriction the body duplicate gets
    stripped as "known headmatter" before it can be promoted, and a
    same-root sandwich-interpolation from unrelated neighbours wins
    instead)."""
    def _key(h):
        return _norm_head(h.split("\t", 1)[0] if "\t" in h else h)

    confirmed = _confirmed_header_keys(parsed)
    return {_norm_head(p["header"]) for p in parsed
            if p.get("header") and _key(p["header"]) in confirmed}


def apply_read_heads(parsed: list, raw_pages: list, read: dict) -> int:
    """Running heads read straight from the layout detector's box
    (intermediate/heads.json, _margins.read_heads) stand in for the HEADER
    field on pages whose OCR reply left it out altogether. Measured
    2026-09-29 on 9 books: the neighbour fill agreed with the reading on
    343 of 345 pages; on the other two the reading was right (Kirchhoffer
    scan 49 "OFFENSIVE", Spinnewyn 99 "LE LIEMENT"), and it gave the head on
    16 pages the fill left empty (LaMarche 14, Jacob 2). Entering as the
    OCR's own answer, a read head also anchors the fills around it.

    Skipped: a reading that is only a page number or a plate label ("Pl.
    3."); a reading that nearly matches a head the book prints elsewhere
    takes that spelling (LaMarche 190 "Applicatians" -> "Applications").
    Returns the number of pages given a head."""
    import difflib
    from collections import Counter
    spellings = {}
    for p, raw in zip(parsed, raw_pages):
        h = (p.get("header") or "").strip()
        if h and has_header_field(raw):
            spellings.setdefault(_norm_head(h), Counter())[h] += 1
    # the most common spelling of each head (LaMarche prints "L'ÉPÉE" once, on
    # the title page, and "L'Épée." on every page after)
    native = {k: c.most_common(1)[0][0] for k, c in spellings.items()}
    n = 0
    for i, (p, raw) in enumerate(zip(parsed, raw_pages)):
        v = read.get(str(i))
        if not v or has_header_field(raw) or (p.get("header") or "").strip():
            continue
        text = (v.get("text") or "").strip()
        if re.match(r"(?i)^\s*(?:pl(?:anche)?\b\.?|planche)", text):
            continue                                   # a plate label, not a running head
        text = re.sub(r"^\s*(?:\d+|[ivxlcdmj]+\.?)\s+|\s+(?:\d+|[ivxlcdmj]+\.?)\s*$", "",
                      text, flags=re.I).strip()
        if not text or re.fullmatch(r"[\W\d]*(?:[ivxlcdmj]+)?[\W\d]*", text, re.I):
            continue                                   # only the page number
        key = _norm_head(text)
        # the OCR did read the head, as the body's first line: the usual
        # promotion (leaked_top_header) moves it up -- setting it here would
        # leave the line in the body too (Jacob 111, LaMarche 30, Baudry 26)
        top = [l.strip() for l in (p.get("body") or "").split("\n") if l.strip()][:2]
        nodigits = lambda t: re.sub(r"[^a-z]", "", _norm_head(t))       # letters only
        if any(nodigits(l) == nodigits(text) for l in top):   # "26: INTRODUCTION." (Gomard 42)
            continue
        if key in native:
            text = native[key]                        # the book's own spelling
        elif native:
            close = difflib.get_close_matches(key, list(native), n=1, cutoff=0.85)
            if close:
                text = native[close[0]]
        p["header"] = text
        n += 1
    return n


def settle_disputed_heads(parsed: list, disputed: set) -> set:
    """The OCR reported a running head the layout detector doesn't see
    (`disputed`). In these books a section's running head starts on its
    SECOND page; the first page prints the section's title in the text. So:
      * the "head" repeats the body's first line -> it is that title, not a
        running head: drop the header (Spinnewyn "AVANT-PROPOS", Jacob "DUEL
        AU SABRE", La Boëssière "SE TROUVE:" -- 2026-09-28 review);
      * else, on the first page of that head's run (the page before has a
        different head) -> it is the section's title, set only in the
        header field: move it into the body (Jacob "PRÉFACE DE M. A. RANC."
        over "I", "PREMIÈRE LEÇON"; La Boëssière "A MM. LES OFFICIERS").
    Returns the pages settled, for reconcile_headers' `no_head`."""
    def _n(s):
        return _norm_head(s or "")
    done = set()
    for i in sorted(disputed):
        p = parsed[i]
        head = (p.get("header") or "").strip()
        if not head or "\t" in head:
            continue
        body = (p.get("body") or "").strip()
        first = body.split("\n", 1)[0].strip()
        if _n(first) == _n(head):
            p["header"] = ""
            done.add(i)
            continue
        prev = (parsed[i - 1].get("header") or "").strip() if i else ""
        if body and _n(prev.split("\t")[0]) != _n(head.split("\t")[0]) and _looks_like_section_title(head):
            p["body"] = f"{head}\n\n{body}"
            p["header"] = ""
            done.add(i)
    return done


def _alternating_heads(parsed: list) -> bool:
    """Running heads alternate by page side: among pages with a head, the
    page two on repeats it clearly more often than the next page does."""
    keys = [_norm_head(((p.get("header") or "").split("\t")[0])) for p in parsed]
    def rate(d):
        pairs = [(a, b) for a, b in zip(keys, keys[d:]) if a and b]
        return (sum(a == b for a, b in pairs) / len(pairs)) if len(pairs) >= 10 else None
    r1, r2 = rate(1), rate(2)
    return r1 is not None and r2 is not None and r2 > r1 + 0.15


def reconcile_headers(parsed: list, no_head: set = frozenset()) -> None:
    """The header bar is for a genuine running header -- one repeated at
    the top of the page across a section, OR a one-page subsection's own
    title standing as its running head. Both are real: visual inspection
    of the scan (K-review-2, ``PARTICULARITÉS`` -- src 13) confirmed a
    single-occurrence header sits in the *exact* folio + title + rule
    position of every other running head; it's just that this particular
    section is one page long, so it can never repeat. A repeat-count
    threshold can't tell that apart from noise, and review feedback wants
    fidelity over an engineered "only show headers that repeat" rule.
    olmOCR-2's one real known failure mode here -- filing a plate's
    caption line in HEADER: -- is caught earlier and separately, by
    fold_caption_header_into_figure() (run just before this). So: trust
    whatever HEADER: this page reported, full stop.

      * HEADER empty but the body opens with a line that is exactly a
        CONFIRMED (repeated >= 2x) running head -> lift that line into
        HEADER. This direction stays conservative -- inferring a header
        from body-text pattern matching is only trusted when that header
        text is independently confirmed elsewhere in the book.
      * HEADER empty, body opens with a standalone, short, mostly
        uppercase line that ISN'T confirmed anywhere -> lift it anyway
        (P-review-3). Some section-openers (Kirchhoffer `TERMES
        TECHNIQUES`, `PARADES ET RIPOSTES`, `ROMPRE — DIVERS`; Gomard
        `CONSULTÉS PAR L'AUTEUR.`) are read by the model as `HEADER: none`
        with the title duplicated only in the body, EVERY time -- so they
        can never become "confirmed" no matter how many pages of that
        section exist. Visually these sit in the exact running-head
        position (confirmed against the scan for two of them already).
        `_looks_like_section_title()` is deliberately narrow (short,
        standalone, uppercase-heavy) so it doesn't also promote an
        ordinary glossary headword ("Croisé. — Parade qui consiste…",
        mixed case, dash-definition on the same or next line).
      * HEADER present but has no tab and isn't itself confirmed -> check
        whether it's an order-swapped two-part header (Kirchhoffer
        `LA MISE EN GARDE FLEURET` instead of `FLEURET\tLA MISE EN
        GARDE`): if its word sequence starts or ends with an
        already-confirmed root, split there and rebuild the canonical
        `root⇥remainder` form the rest of that run already uses.
      * A single page with HEADER empty and NOTHING in its body to lift
        either, sandwiched between two pages that both already carry the
        identical CONFIRMED header -> inherit it (mirrors
        reconcile_page_numbers()'s folio interpolation). Deliberately
        conservative: only a single-page gap, both neighbours must
        already agree. A gap next to a page whose own header was itself
        just inferred from a one-off section-opener title (previous
        bullet) won't match this and stays open -- that needs multi-hop
        lookback this doesn't attempt.

    Repetition (for the lift/interpolate above) is counted per *running-
    head root*: a two-part head ``LEFT\tRIGHT`` (section name + per-page
    topic, e.g. ``SABRE\tCORPS A CORPS``) keys on LEFT only -- the right
    half is unique to one page and must not fragment the count, or every
    plate page in a section loses its header (K-batch2 regression).

    `no_head`: page indices the layout detector is sure print no running
    head (_layout.no_head_pages) -- nothing here lifts or carries a header
    onto them; the OCR model's own HEADER is still trusted."""
    def _key(h):
        return _norm_head(h.split("\t", 1)[0] if "\t" in h else h)

    confirmed = _confirmed_header_keys(parsed)

    # Canonical display spelling for each confirmed root, taken from
    # wherever it's already correctly cased elsewhere in the book -- so a
    # reordered header matches the casing the rest of its section uses,
    # not whatever casing this one stray instance happened to have.
    # (the spelling the book prints most often -- not the first one seen,
    # which can be a title page's "L'ÉPÉE" for a running head "L'Épée.")
    _spellings = {}
    for p in parsed:
        h = (p.get("header") or "").strip()
        if not h:
            continue
        k = _key(h)
        if k in confirmed:
            d = (h.split("\t", 1)[0] if "\t" in h else h).strip()
            _spellings.setdefault(k, Counter())[d] += 1
    root_display = {k: c.most_common(1)[0][0] for k, c in _spellings.items()}

    def _reorder_swapped_two_part(head):
        words = head.split()
        norm_words = _norm_head(head).split()
        if len(words) != len(norm_words) or len(words) < 2:
            return None
        for root_key, display in root_display.items():
            root_words = root_key.split()
            n = len(root_words)
            if not (0 < n < len(words)):
                continue
            if norm_words[:n] == root_words and _looks_like_section_title(" ".join(words[n:])):
                return f"{display}\t{' '.join(words[n:])}"
            if norm_words[-n:] == root_words and _looks_like_section_title(" ".join(words[:-n])):
                # printed with the section name on the RIGHT (a left-hand
                # page): keep that order -- Kirchhoffer alternates sides
                # (scan 16 "LA MISE EN GARDE ... FLEURET", scan 17
                # "FLEURET ... LA MISE EN GARDE"; 2026-10-07 review)
                return f"{' '.join(words[:-n])}\t{display}"
        return None

    for i, p in enumerate(parsed):
        head = (p.get("header") or "").strip()
        if not head and i in no_head:
            continue
        if head:
            # Try the reorder whenever there's no tab, regardless of
            # whether the swapped form is itself "confirmed" -- a
            # consistently-misordered leaf (K-review "LA MISE EN GARDE
            # FLEURET" read the same way on >=2 plate leaves) satisfies
            # its OWN count>=2 threshold as an accidental one-word-longer
            # root, which used to skip this check entirely.
            if "\t" not in head:
                reordered = _reorder_swapped_two_part(head)
                if reordered:
                    p["header"] = reordered
                elif not (p.get("body") or "").strip() and _key(head) not in confirmed:
                    # No reorder matched (the whole phrase isn't itself a
                    # confirmed running head either), and there's no body
                    # beneath it -- this isn't a running head at all, it's
                    # a title-only leaf (a half-title page: no folio, no
                    # prose, the VLM just filed the whole printed title
                    # into HEADER: since that's the most heading-shaped
                    # text on the page). Prévost: "L'Escrime et le Duel"
                    # is the book's OWN title on its half-title page --
                    # rendering it as a running head squeezes it into the
                    # small margin-header bar instead of a proper title;
                    # it belongs in body. A genuine running head always
                    # sits above real body prose, so this guard can't
                    # touch one (see _reorder_swapped_two_part's own
                    # title-shape check for why a genuine swapped plate
                    # header, e.g. Kirchhoffer FLEURET, still reorders
                    # successfully above and never reaches this branch).
                    p["body"] = head
                    p["header"] = ""
                    p["_title_leaf"] = True
            continue
        body = p.get("body") or ""
        first, _, rest = body.lstrip("\n").partition("\n")
        first = first.strip()
        second, _, _ = rest.lstrip("\n").partition("\n")
        second = second.strip()
        # A big two-line opening title (Gomard: "LISTE DES OUVRAGES" /
        # "CONSULTÉS PAR L'AUTEUR.") is NOT a running head -- visual check
        # against the scan showed it's a body-level title block on the
        # section's one opening page, with no small margin header at all;
        # the genuine small running head (just "LISTE DES OUVRAGES") only
        # starts on the section's SECOND page, which the model reads on
        # its own. A single title-shaped line immediately followed by
        # ANOTHER title-shaped line means "compound title block, leave it
        # all in body" -- a genuine section-opener is followed by ordinary
        # prose, not a second title line.
        # a title set over several lines ("TRAITÉ" / "DE" / "L'ART DES
        # ARMES", La Boëssière's title page) continues on the next line
        continues = bool(re.match(r"(?:DE|DU|DES|D['’]|ET|À|A|AU|AUX|SUR|PAR|POUR|EN)\b", second))
        if first and (_key(first) in confirmed or _looks_like_section_title(first)) \
                and not (second and _looks_like_section_title(second)) and not continues:
            p["header"] = first
            p["body"] = rest.lstrip("\n")
        elif first and second and _looks_like_section_title(first) \
                and _looks_like_section_title(second):
            # the compound title block itself -- this page is a section
            # opener with no running head; the fills below must not
            # invent one for it.
            p["_title_leaf"] = True

    # A leaf with NEITHER a figure NOR any real body text is genuinely
    # blank -- nothing at all is printed on it (Prévost: several such
    # leaves before "PREMIÈRE PARTIE" begins, confirmed against the
    # actual scan -- just faint digitisation specks, no header, no text,
    # no plate). Unlike a plate page (no body text, but IS a real printed
    # page that typically still carries the section's running head), a
    # truly blank leaf has nothing printed on it at all, including no
    # header -- neither mechanism below should ever assign one to it.
    def _is_blank_leaf(p):
        b = (p.get("body") or "").strip()
        return ((not b or b.lower() == "none") and not p.get("figures")) \
            or p.get("_title_leaf")

    # Books whose running heads alternate left/right (book title on one
    # side, chapter on the other): the page two on repeats the head, the
    # next page almost never does (Jacob, La Boëssière, LaMarche, Prévost,
    # Spinnewyn -- measured on the OCR's own heads). There a page takes its
    # head from its own side: the fills below look 2 pages away, not 1
    # (LaMarche scan 23 "L'Épée." took the facing page's chapter title; La
    # Boëssière scan 207 the facing "TRAITÉ").
    step = 2 if _alternating_heads(parsed) else 1

    # Plate pages carry a running head only in some books (Kirchhoffer's
    # "FLEURET" plates do; Prévost's photo plates and La Boëssière's
    # engravings don't). Fill a plate page only if this book's plates show
    # a head of their own often enough.
    def _is_plate_page(p):
        return any(f.get("kind") == "plate" for f in (p.get("figures") or [])) \
            and not (p.get("body") or "").strip()
    _plates = [p for p in parsed if _is_plate_page(p)]
    _plates_carry_heads = bool(_plates) and \
        sum(bool((p.get("header") or "").strip()) for p in _plates) >= 0.3 * len(_plates)

    # Sandwich interpolation: a page still headerless after everything
    # above, with both same-side neighbours already agreeing on the same
    # confirmed header, inherits it.
    n = len(parsed)
    for i, p in enumerate(parsed):
        if (p.get("header") or "").strip() or i < step or i >= n - step or _is_blank_leaf(p) \
                or i in no_head or (_is_plate_page(p) and not _plates_carry_heads):
            continue
        prev_h = (parsed[i - step].get("header") or "").strip()
        next_h = (parsed[i + step].get("header") or "").strip()
        if prev_h and next_h and _key(prev_h) == _key(next_h) and _key(prev_h) in confirmed:
            p["header"] = prev_h

    # A page still headerless after everything above, flanked by one-page
    # interludes that were never themselves independently confirmed
    # (Kirchhoffer src 30: sits between "PARADES ET RIPOSTES" and "CONTRE
    # LE BRAS TENDU", each read natively on exactly one page -- neither
    # neighbour is trustworthy). The scan confirmed src 30's real running
    # head reverts to the parent section ("ÉPÉE"), reached by looking
    # further out. Inherit the nearest header that IS independently
    # confirmed (>=2x), searching outward in both directions and
    # preferring whichever is closer -- capped at a short gap so this
    # can't reach across an unrelated part of the book.
    _MAX_GAP = 4
    # Snapshot the ORIGINAL confirmed-header positions before this loop
    # writes anything -- searching from live, in-progress results let one
    # gap-filled page become a fake anchor for the next, chaining past
    # _MAX_GAP indefinitely (Gomard: a real "TABLE DES MATIÈRES." at one
    # spot cascaded across an entire ~54-page run of otherwise-blank
    # plate leaves -- each was individually <=4 pages from the PREVIOUS
    # (already wrongly-filled) page, never from a genuine source). All
    # assignments are collected first and applied together at the end, so
    # no page's fill can feed another page's search within this pass.
    _orig_confirmed = []
    for i, p in enumerate(parsed):
        h = (p.get("header") or "").strip()
        k = _key(h)
        if h and k in confirmed:
            _orig_confirmed.append((i, k, h.split("\t", 1)[0] if "\t" in h else h))
    # a section opener is a barrier: a running head never carries across the
    # page that starts a new section (LaMarche scan 22 "L'ÉPÉE / CHAPITRE I"
    # between the contents and chapter I)
    def _opens_section(p):
        if p.get("_title_leaf"):
            return True
        first = (p.get("body") or "").lstrip("\n").split("\n", 1)[0].strip()
        return not (p.get("header") or "").strip() and bool(first) and _looks_like_section_title(first)
    _barriers = [b for b, p in enumerate(parsed) if b in no_head and _opens_section(p)]
    _fills = {}
    for i, p in enumerate(parsed):
        if (p.get("header") or "").strip() or _is_blank_leaf(p) or i in no_head \
                or (_is_plate_page(p) and not _plates_carry_heads):
            continue
        best = None
        best_dist = _MAX_GAP + 1
        for j, k, disp in _orig_confirmed:
            if any(min(i, j) < b < max(i, j) for b in _barriers):
                continue
            d = abs(j - i)
            if d % step:                     # alternating book: own side only
                continue
            d //= step                       # distance in same-side pages
            # a tie goes to the page ahead: a section's head runs forward
            # from its opener (LaMarche scan 23 between the contents, 4 pages
            # back, and chapter I's "L'Épée.", 4 pages on)
            if d < best_dist or (d == best_dist and j > i):
                best_dist = d
                best = root_display.get(k, disp)
        if best is not None and best_dist <= _MAX_GAP:
            _fills[i] = best
    for i, val in _fills.items():
        parsed[i]["header"] = val

    # A big two-line opening title block, printed within the body area on
    # a section's one opening page (Gomard: "HEADER: LISTE DES OUVRAGES",
    # body opens with "CONSULTÉS PAR L'AUTEUR.") -- not a running head,
    # confirmed against the scan.
    #
    # *2026-09-18 regression, caught before it shipped further:* this was
    # first built as a GENERAL rule -- any native header followed by a
    # second, different title-shaped body line demotes. That's far too
    # broad: it's structurally IDENTICAL to the extremely common, totally
    # normal pattern of "book-wide running head, followed by a new
    # chapter's title starting the body" (Prévost: "THÉORIE PRATIQUE DE
    # L'ESCRIME." -- confirmed 69x across the whole book -- wrongly
    # demoted on >=20 pages each time a new chapter opened under it,
    # since the chapter's own title is ALSO short/standalone/uppercase).
    # A genuine compound-title-block page and a genuine running-head-plus-
    # new-chapter page cannot currently be told apart by text shape alone
    # -- both look the same (native header + a second title-shaped body
    # line) without visually checking whether the scan actually shows a
    # small margin header on that specific page. Reverted to a narrow,
    # explicit list of confirmed real instances instead of a general
    # shape rule, matching this file's other narrow, evidence-based
    # repairs (_OCR_PHRASE_FIXES) -- add to this set only after visually
    # confirming a new instance against the actual scan, the same way
    # this one was confirmed.
    _KNOWN_COMPOUND_TITLE_BLOCKS = {
        _norm_head("LISTE DES OUVRAGES"),
    }
    for p in parsed:
        head = (p.get("header") or "").strip()
        if not head or "\t" in head or _key(head) not in _KNOWN_COMPOUND_TITLE_BLOCKS:
            continue
        body = p.get("body") or ""
        b_first, _, _ = body.lstrip("\n").partition("\n")
        b_first = b_first.strip()
        if (b_first and _looks_like_section_title(b_first)
                and _key(b_first) != _key(head)):
            p["body"] = head + "\n\n" + body
            p["header"] = ""

    # The book's own confirmed running head, split across TWO lines at
    # the very top of body instead of appearing as one line (Prévost src
    # 18, page [1] -- the book's actual opening page: body starts
    # "THÉORIE PRATIQUE" / "DE L'ESCRIME" on their own two lines, then a
    # blank line, then the chapter's own title "L'HISTOIRE DE L'ESCRIME"
    # -- native HEADER: none, so this page's header only exists here
    # because gap-fill inherited it from a neighbour). This is the
    # book's grand opening title, not a small margin running head; a
    # header bar here is visually wrong regardless of how it got filled.
    # Only fires when the two lines JOINED exactly reconstruct an
    # independently-confirmed header root -- so it can't misfire on an
    # ordinary two-line chapter title that merely happens to sit under a
    # real running head (that's the extremely common pattern the
    # regression above already guards against).
    def _apos_fold(s):
        # OCR renders the same elided article as either a straight or a
        # curly apostrophe on different pages (Prévost: header "L'ESCRIME"
        # vs. body "L’ESCRIME") -- _norm_head() doesn't fold that, and this
        # check needs an exact reconstruction, so fold it locally rather
        # than widen _norm_head()'s own normalisation for every caller.
        return s.replace("’", "'")

    for p in parsed:
        head = (p.get("header") or "").strip()
        if not head or "\t" in head:
            continue
        body = p.get("body") or ""
        b1, _, brest = body.lstrip("\n").partition("\n")
        b2, _, _ = brest.lstrip("\n").partition("\n")
        b1, b2 = b1.strip(), b2.strip()
        if (b1 and b2 and _looks_like_section_title(b1) and _looks_like_section_title(b2)
                and _apos_fold(_key(f"{b1} {b2}")) == _apos_fold(_key(head))):
            p["header"] = ""

    # A "<Ordinal> PARTIE" division title reported natively in HEADER: is
    # a genuine part title, same class of thing as the other five in this
    # book -- but on THIS one page the model happened to classify it into
    # HEADER: instead of BODY_TEXT: like every other occurrence (Prévost:
    # "TROISIÈME PARTIE" -- vs. PREMIÈRE/DEUXIÈME/QUATRIÈME/CINQUIÈME/
    # SIXIÈME PARTIE, all five natively in BODY_TEXT). Narrow, closed-set
    # shape match (a specific set of French ordinals + "partie", not a
    # general "short uppercase line" rule) -- evidenced 5-to-1 within the
    # same book, not a guess. Demote unconditionally: a part title is
    # body text here regardless of what follows it, per the standing
    # "titles render as plain body text, no tiering" preference.
    _PART_TITLE_HEADER_RE = re.compile(
        r"(?i)^(premi[eè]re|deuxi[eè]me|seconde|troisi[eè]me|quatri[eè]me|"
        r"cinqui[eè]me|sixi[eè]me|septi[eè]me|huiti[eè]me|neuvi[eè]me|"
        r"dixi[eè]me)\s+partie\.?\s*$")
    for p in parsed:
        head = (p.get("header") or "").strip()
        if head and "\t" not in head and _PART_TITLE_HEADER_RE.match(head):
            p["body"] = head + "\n\n" + (p.get("body") or "")
            p["header"] = ""

    # A leaked printed folio glued straight onto the header text, no
    # separator but a colon/period (Gomard "26: INTRODUCTION.", "62:
    # INTRODUCTION:") -- a malformed-VLM-response page (no scaffold at
    # all) whose body's first line is "<folio>: <header>", which then
    # reads as one title-shaped unit and gets lifted/promoted whole,
    # folio prefix and all. Runs last, after every other header-setting
    # step, so it catches the leak regardless of how the header got set.
    # Only strips when what's left IS itself a confirmed header -- so it
    # can't misfire on an ordinary heading that happens to start with a
    # number for some other reason.
    _LEAKED_NUM_PREFIX_RE = re.compile(r"^\s*\d{1,4}\s*[:.]?\s+(?=\S)")
    for p in parsed:
        head = (p.get("header") or "").strip()
        if not head or "\t" in head:
            continue
        stripped = _LEAKED_NUM_PREFIX_RE.sub("", head).strip()
        if stripped != head and _key(stripped) in confirmed:
            p["header"] = stripped
