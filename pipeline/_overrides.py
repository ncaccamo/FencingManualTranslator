# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Per-book human decisions: books/<stem>.overrides.toml.

Everything a reviewer decides about a book lives here, not in the generated
files under processed/ -- those get regenerated (reprocess_extracted.py,
2_translate.py) and a hand edit there is silently lost. The pipeline
re-applies this file on every run, so a decision is made once.

Pages are SCAN pages: the 1-based page number in the source scan PDF. (The
side-by-side PDF's own numbering shifts whenever a page overflows onto a
continuation sheet, so it can't key anything durable.)

    [book]               book-wide settings:
        running_heads    false -> the book prints no running heads: the line
                         the layout detector takes for one is the page
                         number (Florio, Bruno, Lambertini). Only the
                         OCR's own HEADER counts; no head is carried onto
                         a page from its neighbours.
        paragraphs       "indented" (default) -> each paragraph opens with
                         a first-line indent and no blank line between
                         paragraphs, as most of these books print them;
                         "spaced" -> a blank line between paragraphs, no
                         indent. (The OCR marks paragraphs at random, so the
                         PDFs set one style per book -- _render.layout.)
        page_numbers     where the book prints its page numbers, for a book
                         whose scans the layout detector can't read for it
                         (Jacob-André): "outer" (odd right, even left),
                         "inner", "centre", or "bottom-..." of these. The
                         running head is then centred. Unset: learned from
                         the scans (_layout.folio_plan).
        bold_headings    true -> the book sets its section headings in bold
                         (Masiello 1887 «§ 4. — Posizione normale.»): a
                         heading that is not a part or chapter is set in
                         bold, a leading number label («§ 4. —») in roman
                         as printed (_render.bold_title).
        final            true -> the book is finished: scripts that would
                         change it skip it unless run with --unlock
                         (_books.py). The review report still runs.
    [headers]            running head (French, exactly as in extracted.txt)
                         -> English, overriding the model's translation
    [terms]              book-specific glossary appended to the translation
                         prompt ("coup" = "thrust")
    [pages.N]            per-scan-page settings:
        page_number      folio to show ("" = none)
        header           French running head ("" = none)
        retranslate      true -> retranslated by `2_translate.py --pages <stem>`
                         (once per version of the page's source text)
        caption          the printed caption of the page's plate/figure,
                         corrected (source language) -- for engraved or
                         damaged captions the OCR can't read. Step 2
                         translates it.
        caption_en       your own English for that caption (optional; wins
                         over the model's translation)
        figure           "plate" (default) or "inset": the kind of figure to
                         record when the OCR missed the figure altogether --
                         "inset" for a small picture on a text page
        note             free text, ignored by the pipeline
    [[fix]]              direct edit of the English translation, applied
                         after every translation run. scan = N, then either
                         prepend = "..." (added as the first line unless the
                         page already starts with it) or find = "..." +
                         replace = "..." (a no-op if find isn't there).
    [[book_fix]]         a find + replace edit applied to EVERY page of the
                         English, before the pages' own [[fix]] edits: one
                         line for a book-wide wording change (Gomard's
                         «coup» -> thrust, 2026-11-09) instead of a fix per
                         page. find, replace; no scan. The body text only
                         (not tables, captions or running heads). Never
                         marks a page as edited; reported only when it
                         matches nowhere.
    [[table_fix]]        the same find + replace edit for the English of a
                         page's TABLES (intermediate/tables.json, which the
                         [[fix]] edits never reach): scan = N, find, replace;
                         applied after every table translation run.

Hand edits of whole pages live beside it, in books/<stem>.edits/: one file
per page, scan-0091.txt, made by edit_page.py. Everything below the file's
"---" line replaces the page's English after every translation run (after
the [[fix]] edits). The header records the source text's fingerprint when
the edit was made, so the review report can say when the page's source has
changed since. Delete the file to go back to the machine translation.
"""
import os
import re
import tomllib

_PAGE_KEYS = {"page_number", "header", "retranslate", "note", "caption", "caption_en", "figure", "leave_out"}
_TOP_KEYS = {"book", "headers", "terms", "pages", "fix", "book_fix", "table_fix"}
_BOOK_KEYS = {"running_heads": bool, "final": bool, "paragraphs": str, "page_numbers": str,
              "bold_headings": bool}
_PARAGRAPHS = ("indented", "spaced")
_PAGE_NUMBERS = ("outer", "inner", "centre", "bottom-outer", "bottom-inner", "bottom-centre")


class OverridesError(ValueError):
    pass


def _here_or_skip(books_dir: str, name: str) -> str:
    """books/<name>, or books/_skip/<name> when only that exists: a book
    moved to _skip after its run (an English translation exists; it is kept
    as an evaluation pair -- La Boëssière, Rossi) keeps its decisions."""
    p = os.path.join(books_dir, name)
    skip = os.path.join(books_dir, "_skip", name)
    return skip if not os.path.exists(p) and os.path.exists(skip) else p


def path_for(stem: str, books_dir: str = "/data/books") -> str:
    return _here_or_skip(books_dir, f"{stem}.overrides.toml")


def load(stem: str, books_dir: str = "/data/books") -> dict:
    """Parse and validate. Missing file -> empty overrides. A malformed
    file raises OverridesError naming the problem -- a silently ignored
    decision is the failure this file exists to prevent."""
    p = path_for(stem, books_dir)
    if not os.path.exists(p):
        return {"book": {}, "headers": {}, "terms": {}, "pages": {}, "fix": [], "table_fix": []}
    try:
        with open(p, "rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:
        raise OverridesError(f"{p}: {e}") from None
    bad = set(raw) - _TOP_KEYS
    if bad:
        raise OverridesError(f"{p}: unknown section(s) {sorted(bad)}")
    pages = {}
    for k, v in (raw.get("pages") or {}).items():
        if not str(k).isdigit() or int(k) < 1:
            raise OverridesError(f"{p}: [pages.{k}] -- page must be a scan page number >= 1")
        if not isinstance(v, dict) or set(v) - _PAGE_KEYS:
            raise OverridesError(f"{p}: [pages.{k}] unknown key(s) "
                                 f"{sorted(set(v) - _PAGE_KEYS) if isinstance(v, dict) else v}")
        if "leave_out" in v and not isinstance(v["leave_out"], bool):
            raise OverridesError(f"{p}: [pages.{k}] leave_out must be true or false")
        pages[int(k)] = v
    fixes = raw.get("fix") or []
    for n, f in enumerate(fixes, 1):
        if not isinstance(f.get("scan"), int) or f["scan"] < 1:
            raise OverridesError(f"{p}: [[fix]] #{n} needs scan = <page number>")
        has_prepend, has_find = "prepend" in f, "find" in f
        if has_prepend == has_find or (has_find and "replace" not in f):
            raise OverridesError(f"{p}: [[fix]] #{n} (scan {f['scan']}) needs either "
                                 f"prepend, or find + replace")
    book_fixes = raw.get("book_fix") or []
    for n, f in enumerate(book_fixes, 1):
        if set(f) - {"find", "replace", "note"} or not isinstance(f.get("find"), str) or not f.get("find") \
                or not isinstance(f.get("replace"), str):
            raise OverridesError(f"{p}: [[book_fix]] #{n} needs exactly find and replace (no scan: "
                                 f"it applies to every page)")
        if f["find"] in f["replace"]:
            # applied on every run to every page: it would add its text again each time
            raise OverridesError(f"{p}: [[book_fix]] #{n}: the replace contains the find -- "
                                 f"write a [[fix]] per page instead")
    table_fixes = raw.get("table_fix") or []
    for n, f in enumerate(table_fixes, 1):
        if not isinstance(f.get("scan"), int) or f["scan"] < 1:
            raise OverridesError(f"{p}: [[table_fix]] #{n} needs scan = <page number>")
        if set(f) - {"scan", "find", "replace"} or not isinstance(f.get("find"), str) \
                or not isinstance(f.get("replace"), str):
            raise OverridesError(f"{p}: [[table_fix]] #{n} (scan {f['scan']}) needs exactly "
                                 f"scan, find and replace")
    book = raw.get("book") or {}
    for k, v in book.items():
        if k not in _BOOK_KEYS:
            raise OverridesError(f"{p}: [book] unknown key {k!r}")
        if not isinstance(v, _BOOK_KEYS[k]):
            raise OverridesError(f"{p}: [book] {k} must be {_BOOK_KEYS[k].__name__}")
        if k == "paragraphs" and v not in _PARAGRAPHS:
            raise OverridesError(f"{p}: [book] paragraphs must be one of {_PARAGRAPHS}")
        if k == "page_numbers" and v not in _PAGE_NUMBERS:
            raise OverridesError(f"{p}: [book] page_numbers must be one of {_PAGE_NUMBERS}")
    for sec in ("headers", "terms"):
        for k, v in (raw.get(sec) or {}).items():
            if not isinstance(v, str):
                raise OverridesError(f"{p}: [{sec}] {k!r} must be a string")
    return {"book": dict(book),
            "headers": dict(raw.get("headers") or {}), "terms": dict(raw.get("terms") or {}),
            "pages": pages, "fix": list(fixes), "table_fix": list(table_fixes),
            "book_fix": list(book_fixes)}


def _check_range(ov: dict, n_pages: int, where: str) -> None:
    for s in list(ov["pages"]) + [f["scan"] for f in ov["fix"] + ov.get("table_fix", [])]:
        if s > n_pages:
            raise OverridesError(f"{where}: scan page {s} is past the end of the book "
                                 f"({n_pages} pages)")


def apply_extraction(parsed: list, nums: list, ov: dict) -> list:
    """Step 1 / reprocess: impose page_number and header decisions on the
    parsed pages (in place) and the reconciled folio list (returned).
    Runs AFTER all automatic reconciliation, so a decision always wins."""
    _check_range(ov, len(parsed), "overrides")
    nums = list(nums)
    for scan, v in ov["pages"].items():
        i = scan - 1
        if "page_number" in v:
            nums[i] = str(v["page_number"])
        if "header" in v:
            parsed[i]["header"] = v["header"]
        if "caption" in v:
            set_caption(parsed[i].setdefault("figures", []), v["caption"], v.get("figure", "plate"))
    return nums


def set_caption(figs: list, caption: str, kind: str = "plate") -> bool:
    """The page's (first) figure gets the decided caption; a page with no
    figure recorded gets one (a plate). True if anything changed."""
    if not figs:
        figs.append({"kind": kind, "caption": caption, "decided": True})
        return True
    if figs[0].get("caption") == caption:
        figs[0]["decided"] = True
        return False
    figs[0]["caption"] = caption
    figs[0]["decided"] = True             # step 2's caption clean-up leaves it as is
    figs[0].pop("caption_en", None)       # the old English was for the old caption
    return True


def apply_captions(fig_map: dict, ov: dict) -> tuple:
    """figures.json ({0-based page: [figure, ...]}): impose caption and
    caption_en decisions. Returns (changed, pages still needing a
    translation of their caption)."""
    changed, need = 0, []
    for scan, v in sorted(ov["pages"].items()):
        if "caption" not in v and "caption_en" not in v:
            continue
        figs = fig_map.setdefault(str(scan - 1), [])
        if "caption" in v and set_caption(figs, v["caption"], v.get("figure", "plate")):
            changed += 1
        if "caption_en" in v:
            if not figs:
                figs.append({"kind": "plate", "caption": ""})
            if figs[0].get("caption_en") != v["caption_en"]:
                figs[0]["caption_en"] = v["caption_en"]
                changed += 1
        elif figs and not figs[0].get("caption_en"):
            need.append(scan)
    return changed, need


def terms_addendum(ov: dict) -> str:
    """Book-specific glossary, appended after the prompt's own term lists."""
    if not ov["terms"]:
        return ""
    lines = "\n".join(f"   - {k} → {v}" for k, v in ov["terms"].items())
    return ("\n\nBOOK-SPECIFIC TERMS -- for this book these override the lists above:\n"
            + lines)


def apply_headers(hmap: dict, ov: dict) -> int:
    """headers.json: a decided English rendering replaces the model's."""
    n = 0
    for k, v in ov["headers"].items():
        if hmap.get(k) != v:
            hmap[k] = v
            n += 1
    return n


def _q(s: str) -> str:
    """«» as the English quotes the house rule writes (_tx_terms.english_quotes)."""
    return re.sub(r"\s*»", "\u201d", re.sub(r"«\s*", "\u201c", s))


def book_worded(fixes: list, book_fixes: list) -> list:
    """Page fixes with their text passed through the book-wide edits, so
    they speak the wording the page has after them."""
    def w(v):
        for g in book_fixes:
            v = v.replace(g["find"], g["replace"])
        return v
    return [{k: (w(v) if k in ("find", "replace", "prepend") else v) for k, v in f.items()} for f in fixes]


def apply_fixes(tr_pages: list, ov: dict) -> tuple:
    """translation.txt pages (in place). Returns (applied, not_found) --
    a find that no longer matches (the page was retranslated since) is
    reported, not an error: the fix may simply be obsolete."""
    _check_range(ov, len(tr_pages), "overrides [[fix]]")
    applied, missing = 0, []
    # book-wide edits first, on every page; the pages' own fixes are then
    # read in the same wording, so a fix written before a [[book_fix]]
    # («<i>coup</i>» in its find, Gomard) still finds its text. Missing
    # (reported as page 0) only when a book fix matches nowhere.
    bf = ov.get("book_fix", [])
    for f in bf:
        hit = False
        for i, t in enumerate(tr_pages):
            if f["find"] in t:
                tr_pages[i] = t.replace(f["find"], f["replace"])
                applied += 1
                hit = True
        if not hit and not any(f["replace"] in t for t in tr_pages):
            missing.append(0)
    for f in (book_worded(ov["fix"], bf) if bf else ov["fix"]):
        i = f["scan"] - 1
        t = tr_pages[i]
        if "prepend" in f:
            # already there, in any case/punctuation (a retranslation may
            # have produced "PLATE V" for a prepended "Plate V."). A
            # multi-line prepend is compared over as many leading lines as
            # it has -- comparing only the first line never matched, so
            # every run added another copy (Jacob scan 49, six copies of
            # "THE GAME OF THE ÉPÉE / INTRODUCTION"). Copies already
            # stacked up are removed, keeping one.
            norm = lambda s: re.sub(r"[\W_]+", " ", re.sub(r"</?i>", "", s)).strip().lower()
            want = norm(f["prepend"])
            k = len([l for l in f["prepend"].split("\n") if l.strip()])
            lead, body = t[:len(t) - len(t.lstrip())], t.lstrip()
            first, copies = None, 0
            while True:                         # peel off leading copies
                lines = body.split("\n")
                idx = [j for j, l in enumerate(lines) if l.strip()][:k]
                if len(idx) < k or not norm(" ".join(lines[j] for j in idx)).startswith(want):
                    break
                copies += 1
                first = first or "\n".join(lines[:idx[-1] + 1])
                body = "\n".join(lines[idx[-1] + 1:]).lstrip()
            if copies == 1:
                continue
            tr_pages[i] = f"{lead}{first if copies else f['prepend']}\n\n{body}"
            applied += 1
        else:
            if f["find"] in f["replace"] and f["find"] != f["replace"]:
                # a replace that contains its own find (an addition: "words"
                # -> "words:") matched again on every run and grew one more
                # copy each time (Kirchhoffer scan 80 "from back to back to
                # back to", LaMarche scan 4 "THE THE ... ÉPÉE", 2026-11-05).
                # Stacked copies collapse to one; applied once = nothing to do.
                pre, post = f["replace"].split(f["find"], 1)
                pat = ((f"(?:{re.escape(pre)})+" if pre else "") + re.escape(f["find"])
                       + (f"(?:{re.escape(post)})+" if post else ""))
                healed = re.sub(pat, lambda m: f["replace"], t)
                if f["replace"] in healed:
                    if healed != t:
                        tr_pages[i] = healed
                        applied += 1
                    continue
            find, repl = f["find"], f["replace"]
            if find not in t and _q(find) in t:
                # a fix written with «guillemets» the house rule has since
                # turned into English quotes (a half pair it cannot convert)
                find, repl = _q(find), _q(repl)
            if find not in t:
                if repl not in t and _q(repl) not in t:
                    missing.append(f["scan"])
                continue
            tr_pages[i] = t.replace(find, repl)
            applied += 1
    return applied, missing


def apply_table_fixes(tmap: dict, ov: dict) -> tuple:
    """tables.json ({0-based page: {"src": [...], "en": [html or None]}}):
    find -> replace in the English of the page's tables. Returns (applied,
    not_found) like apply_fixes: a find already replaced is silent, a find
    gone after the table was retranslated is reported."""
    applied, missing = 0, []
    for f in ov.get("table_fix", []):
        en = (tmap.get(str(f["scan"] - 1)) or {}).get("en") or []
        hit = [k for k, h in enumerate(en) if h and f["find"] in h]
        if not hit:
            if not any(h and f["replace"] in h for h in en):
                missing.append(f["scan"])
            continue
        for k in hit:
            en[k] = en[k].replace(f["find"], f["replace"])
        applied += 1
    return applied, missing


def parse_page_spec(spec: str) -> list:
    """"12,40-42" -> [12, 40, 41, 42] (scan pages, sorted, de-duplicated)."""
    out = []
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        a, dash, b = part.partition("-")
        if not a.isdigit() or (dash and not b.isdigit()) or (b and int(b) < int(a)):
            raise OverridesError(f"bad page list {spec!r} -- use e.g. 12,40-42")
        out += list(range(int(a), int(b or a) + 1))
    return sorted(set(out))


def left_out_pages(ov: dict) -> list:
    """Scan pages marked leave_out = true, ascending: library or digitiser
    text (a bookplate, a date-due slip, Google's notice) whose English is
    left out of both PDFs; the side-by-side still shows the scan."""
    return sorted(s for s, v in ov["pages"].items() if v.get("leave_out"))


def retranslate_pages(ov: dict) -> list:
    """Scan pages marked retranslate = true, ascending."""
    return sorted(s for s, v in ov["pages"].items() if v.get("retranslate"))


# ---- hand edits of whole pages (books/<stem>.edits/scan-NNNN.txt) ---------

EDIT_RULE = "---"


def edits_dir(stem: str, books_dir: str = "/data/books") -> str:
    """books/<stem>.edits -- or under books/_skip/ for a book whose scan
    is there (_here_or_skip)."""
    d = _here_or_skip(books_dir, f"{stem}.edits")
    if not os.path.exists(d) and os.path.exists(os.path.join(books_dir, "_skip", f"{stem}.pdf")):
        d = os.path.join(books_dir, "_skip", f"{stem}.edits")
    return d


def edit_file_text(stem: str, scan: int, source_fp: str, english: str,
                   original: str = "") -> str:
    """A new edit file: a short header (with the page's original text, for
    reference), the rule, then the English to edit."""
    ref = "".join(f"#   {l}\n" if l.strip() else "#\n" for l in original.strip().split("\n")) \
        if original.strip() else ""
    return (f"# Hand edit of {stem}, scan page {scan}.\n"
            f"# Everything below the {EDIT_RULE} line replaces this page's English on every\n"
            f"# run (keep <i>, <fn> and other markup as it is). Delete the file to undo.\n"
            f"# source: {source_fp}\n"
            + (f"#\n# The original, for reference:\n{ref}" if ref else "")
            + f"{EDIT_RULE}\n{english.strip()}\n")


def load_edits(stem: str, books_dir: str = "/data/books") -> dict:
    """{scan: {"text": English, "source": fingerprint or ""}}. A file with
    no rule line is an error: its header would otherwise end up printed."""
    d = edits_dir(stem, books_dir)
    out = {}
    if not os.path.isdir(d):
        return out
    for name in sorted(os.listdir(d)):
        m = re.fullmatch(r"scan-0*(\d+)\.txt", name)
        if not m:
            continue
        raw = open(os.path.join(d, name), encoding="utf-8").read()
        head, rule, text = raw.partition(f"\n{EDIT_RULE}\n")
        if not rule:
            raise OverridesError(f"{d}/{name}: no '{EDIT_RULE}' line between the header "
                                 f"and the English")
        src = re.search(r"(?m)^# source: *(\S*)", head)
        out[int(m.group(1))] = {"text": text.strip(), "source": src.group(1) if src else ""}
    return out


def apply_edits(tr_pages: list, edits: dict) -> int:
    """translation.txt pages (in place): each edited page takes its edit.
    Returns how many pages changed."""
    n = 0
    for scan, e in edits.items():
        if scan > len(tr_pages):
            raise OverridesError(f"hand edit scan-{scan}: past the end of the book "
                                 f"({len(tr_pages)} pages)")
        if tr_pages[scan - 1].strip() != e["text"]:
            tr_pages[scan - 1] = e["text"]
            n += 1
    return n


def hand_corrected_pages(stem: str, books_dir: str = "/data/books") -> list:
    """Scan pages whose English a person changed: a [[fix]] or a page edit
    (for the cover's disclosure line)."""
    try:
        ov = load(stem, books_dir)
        edits = load_edits(stem, books_dir)
    except OverridesError:
        return []
    return sorted({f["scan"] for f in ov["fix"]} | set(edits))


def load_review(stem: str, books_dir: str = "/data/books") -> dict:
    """books/<stem>.review (the review page's answers) -> {card id: answer
    dict}. Missing file -> {}. A malformed file raises OverridesError."""
    p = _here_or_skip(books_dir, f"{stem}.review")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:
        raise OverridesError(f"{p}: {e}") from None
    out = {}
    for n, c in enumerate(raw.get("card") or [], 1):
        if not isinstance(c.get("id"), str) or c.get("answer") not in ("fix", "fine", "later"):
            raise OverridesError(f"{p}: [[card]] #{n} needs id and answer = "
                                 "\"fix\", \"fine\" or \"later\"")
        out[c["id"]] = c
    return out


def review_fixes_missing(answers: dict, ov: dict) -> list:
    """Answers "fix" with a find/replace that has no [[fix]] in the overrides
    file yet: [(scan, card id, find)] -- run apply_review.py."""
    have = {(f["scan"], f.get("find"), f.get("replace")) for f in ov["fix"]}
    return [(c.get("scan"), k, c["find"]) for k, c in answers.items()
            if c["answer"] == "fix" and c.get("find") and isinstance(c.get("scan"), int)
            and (c["scan"], c["find"], c.get("replace", "")) not in have]
