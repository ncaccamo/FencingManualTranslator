#!/usr/bin/env python3
"""Sentence-align a gold pair's src.txt / ref.txt -> aligned.jsonl.

Gale & Church length-based dynamic programming -- no dependencies, no
model. Good enough for literary text where the translation roughly tracks
the original's structure; the alignment-type histogram it produces is
itself the answer to "is this translation close or free" (see gold_report).

    python3 evals/gold_align.py bazancourt_1862_secrets
"""
import json
import math
import re
import sys
from pathlib import Path

GOLD = Path(__file__).resolve().parent / "gold"

# rough multilingual sentence splitter: break after . ! ? … » " when
# followed by space + capital/quote/dash; keep common abbreviations glued.
_ABBR = r"(?<!\bM)(?<!\bMM)(?<!\bMr)(?<!\bMrs)(?<!\bDr)(?<!\bSt)(?<!\bp)(?<!\bpp)(?<!\bfig)(?<!\bvol)(?<!\bno)(?<!\betc)"
_SPLIT = re.compile(_ABBR + r'([.!?…]|[.!?…][»"”\')\]]+)\s+(?=[«"“(\[]?[A-ZÀ-Þ0-9—-])')


def sentences(text: str) -> list[str]:
    out = []
    for para in re.split(r"\n\s*\n", text):
        para = re.sub(r"<!--p \d+-->", "", para)
        para = re.sub(r"\s+", " ", para).strip()
        if not para:
            continue
        parts = _SPLIT.split(para)
        # _SPLIT.split keeps the delimiter group -> stitch back
        buf = ""
        for i, seg in enumerate(parts):
            buf += seg
            if i % 2 == 1:  # just added the punctuation delimiter
                out.append(buf.strip()); buf = ""
        if buf.strip():
            out.append(buf.strip())
    return [s for s in out if s]


# Gale-Church -------------------------------------------------------------
_PRIOR = {(1, 1): 0.89, (1, 0): 0.01, (0, 1): 0.01,
          (2, 1): 0.089, (1, 2): 0.089, (2, 2): 0.011}
_C, _S2 = 1.0, 6.8


def _pnorm(z):
    return 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))


def _cost(sl, tl, kind):
    if sl == 0 and tl == 0:
        return 0.0
    m = (sl + tl / _C) / 2 or 1e-9
    z = (tl - sl * _C) / math.sqrt(_S2 * m)
    try:
        pen = -math.log(_PRIOR[kind] * max(_pnorm(z), 1e-9))
    except ValueError:
        pen = 25.0
    return pen


def align(src: list[str], tgt: list[str]):
    n, m = len(src), len(tgt)
    sl = [len(x) for x in src]
    tl = [len(x) for x in tgt]
    D = [[1e18] * (m + 1) for _ in range(n + 1)]
    B = [[None] * (m + 1) for _ in range(n + 1)]
    D[0][0] = 0.0
    steps = [(1, 1), (1, 0), (0, 1), (2, 1), (1, 2), (2, 2)]
    for i in range(n + 1):
        for j in range(m + 1):
            if D[i][j] >= 1e17:
                continue
            for di, dj in steps:
                if i + di > n or j + dj > m:
                    continue
                s = sum(sl[i:i + di]); t = sum(tl[j:j + dj])
                c = D[i][j] + _cost(s, t, (di, dj))
                if c < D[i + di][j + dj]:
                    D[i + di][j + dj] = c
                    B[i + di][j + dj] = (i, j, di, dj)
    pairs, i, j = [], n, m
    while (i, j) != (0, 0):
        pi, pj, di, dj = B[i][j]
        pairs.append({
            "src": " ".join(src[pi:pi + di]),
            "ref": " ".join(tgt[pj:pj + dj]),
            "type": f"{di}-{dj}",
        })
        i, j = pi, pj
    pairs.reverse()
    return pairs


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    d = GOLD / sys.argv[1]
    src = sentences((d / "src.txt").read_text(encoding="utf-8"))
    ref = sentences((d / "ref.txt").read_text(encoding="utf-8"))
    print(f"{d.name}: {len(src)} src sentences, {len(ref)} ref sentences")
    pairs = align(src, ref)
    (d / "aligned.jsonl").write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in pairs) + "\n",
        encoding="utf-8")
    from collections import Counter
    h = Counter(p["type"] for p in pairs)
    print("alignment types:", dict(h.most_common()))
    print(f"-> {d / 'aligned.jsonl'}  ({len(pairs)} blocks)")


if __name__ == "__main__":
    main()
