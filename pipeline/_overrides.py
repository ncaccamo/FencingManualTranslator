"""Per-book human decisions: books/<stem>.overrides.toml.

Everything a reviewer decides about a book lives here, not in the generated
files under processed/ -- those get regenerated (reprocess_extracted.py,
2_translate.py) and a hand edit there is silently lost. The pipeline
re-applies this file on every run, so a decision is made once.

Pages are SCAN pages: the 1-based page number in the source scan PDF. (The
side-by-side PDF's own numbering shifts whenever a page overflows onto a
continuation sheet, so it can't key anything durable.)

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
"""
import os
import re
import tomllib

_PAGE_KEYS = {"page_number", "header", "retranslate", "note", "caption", "caption_en", "figure"}
_TOP_KEYS = {"headers", "terms", "pages", "fix"}


class OverridesError(ValueError):
    pass


def path_for(stem: str, books_dir: str = "/data/books") -> str:
    return os.path.join(books_dir, f"{stem}.overrides.toml")


def load(stem: str, books_dir: str = "/data/books") -> dict:
    """Parse and validate. Missing file -> empty overrides. A malformed
    file raises OverridesError naming the problem -- a silently ignored
    decision is the failure this file exists to prevent."""
    p = path_for(stem, books_dir)
    if not os.path.exists(p):
        return {"headers": {}, "terms": {}, "pages": {}, "fix": []}
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
        pages[int(k)] = v
    fixes = raw.get("fix") or []
    for n, f in enumerate(fixes, 1):
        if not isinstance(f.get("scan"), int) or f["scan"] < 1:
            raise OverridesError(f"{p}: [[fix]] #{n} needs scan = <page number>")
        has_prepend, has_find = "prepend" in f, "find" in f
        if has_prepend == has_find or (has_find and "replace" not in f):
            raise OverridesError(f"{p}: [[fix]] #{n} (scan {f['scan']}) needs either "
                                 f"prepend, or find + replace")
    for sec in ("headers", "terms"):
        for k, v in (raw.get(sec) or {}).items():
            if not isinstance(v, str):
                raise OverridesError(f"{p}: [{sec}] {k!r} must be a string")
    return {"headers": dict(raw.get("headers") or {}), "terms": dict(raw.get("terms") or {}),
            "pages": pages, "fix": list(fixes)}


def _check_range(ov: dict, n_pages: int, where: str) -> None:
    for s in list(ov["pages"]) + [f["scan"] for f in ov["fix"]]:
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


def apply_fixes(tr_pages: list, ov: dict) -> tuple:
    """translation.txt pages (in place). Returns (applied, not_found) --
    a find that no longer matches (the page was retranslated since) is
    reported, not an error: the fix may simply be obsolete."""
    _check_range(ov, len(tr_pages), "overrides [[fix]]")
    applied, missing = 0, []
    for f in ov["fix"]:
        i = f["scan"] - 1
        t = tr_pages[i]
        if "prepend" in f:
            # already there, in any case/punctuation (a retranslation may
            # have produced "PLATE V" for a prepended "Plate V.")
            norm = lambda s: re.sub(r"[\W_]+", " ", re.sub(r"</?i>", "", s)).strip().lower()
            first = next((l for l in t.split("\n") if l.strip()), "")
            if norm(first).startswith(norm(f["prepend"])):
                continue
            lead = t[:len(t) - len(t.lstrip())]
            tr_pages[i] = f"{lead}{f['prepend']}\n\n{t.lstrip()}"
            applied += 1
        else:
            if f["find"] not in t:
                if f["replace"] not in t:
                    missing.append(f["scan"])
                continue
            tr_pages[i] = t.replace(f["find"], f["replace"])
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


def retranslate_pages(ov: dict) -> list:
    """Scan pages marked retranslate = true, ascending."""
    return sorted(s for s, v in ov["pages"].items() if v.get("retranslate"))
