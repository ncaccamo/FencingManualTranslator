#!/usr/bin/env python3
"""Report on a gold pair: how the published translation relates to the
original (close vs free), how it handles fencing terms, and -- if a
pipeline hypothesis is present -- chrF against the reference.

    python3 evals/gold_report.py bazancourt_1862_secrets

Reads  evals/gold/<stem>/{src.txt,ref.txt,aligned.jsonl}  and optionally
`hyp.txt` (the pipeline's English for the same src). Writes report.md.
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

GOLD = Path(__file__).resolve().parent / "gold"

TERMS = [
    "prise de fer", "engagement", "dégagement", "coupé", "doublé", "liement",
    "froissement", "coup droit", "coup de temps", "temps d'arrêt", "appel",
    "fente", "développement", "garde", "riposte", "remise", "reprise",
    "parade", "assaut", "tireur", "fer", "quarte", "tierce", "sixte",
    "octave", "prime", "seconde", "septime", "quinte", "botte",
]


# --- chrF (character n-gram F2, + optional word n-grams = chrF++) --------
def _ngrams(seq, n):
    return Counter(tuple(seq[i:i + n]) for i in range(len(seq) - n + 1))


def chrf(hyp: str, ref: str, cn=6, wn=2, beta=2.0) -> float:
    def score(h, r, N, tok):
        tp = fp = fn = 0
        for n in range(1, N + 1):
            hn, rn = _ngrams(tok(h), n), _ngrams(tok(r), n)
            for g, c in hn.items():
                m = min(c, rn.get(g, 0)); tp += m; fp += c - m
            for g, c in rn.items():
                fn += c - min(c, hn.get(g, 0))
        p = tp / (tp + fp) if tp + fp else 0.0
        rc = tp / (tp + fn) if tp + fn else 0.0
        if not (p and rc):
            return 0.0
        b2 = beta * beta
        return (1 + b2) * p * rc / (b2 * p + rc)
    ch = score(hyp.lower(), ref.lower(), cn, list)
    if not wn:
        return 100 * ch
    wd = score(hyp.lower(), ref.lower(), wn, str.split)
    return 100 * (ch + wd) / 2


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    d = GOLD / sys.argv[1]
    src_t = (d / "src.txt").read_text(encoding="utf-8")
    ref_t = (d / "ref.txt").read_text(encoding="utf-8")
    pairs = [json.loads(l) for l in (d / "aligned.jsonl").read_text().splitlines() if l]
    L = [f"# gold report: {d.name}", ""]

    # 1. shape
    sw, rw = len(src_t.split()), len(ref_t.split())
    types = Counter(p["type"] for p in pairs)
    n11 = types.get("1-1", 0)
    splits = sum(v for k, v in types.items() if k in ("1-2", "1-3", "2-3"))
    merges = sum(v for k, v in types.items() if k in ("2-1", "3-1", "3-2"))
    adds = types.get("0-1", 0)
    drops = types.get("1-0", 0)
    L += ["## Shape",
          f"- words: {sw} FR  ->  {rw} EN   (expansion {rw / sw:.2f}x)",
          f"- alignment blocks: {len(pairs)}",
          f"- types: " + ", ".join(f"{k} {v}" for k, v in types.most_common()),
          f"- clean 1-1: {n11} ({100 * n11 // max(len(pairs),1)}%)  |  "
          f"splits {splits}  merges {merges}  EN-only {adds}  FR-only {drops}",
          ""]
    verdict = ("close / mostly sentence-parallel" if n11 / max(len(pairs), 1) > 0.6
               else "free / restructured (splits and additions dominate) — "
                    "use paragraph-level metrics, term mining, and few-shot "
                    "extraction; not tight sentence-level scoring or SFT")
    L += [f"**Read:** {verdict}", ""]

    # 2. term handling
    L += ["## Term handling (how the published translation renders each)", ""]
    for term in TERMS:
        rx = re.compile(r"\b" + re.escape(term) + r"\b", re.I)
        hits = [p for p in pairs if rx.search(p["src"])]
        if not hits:
            continue
        kept = sum(1 for p in hits if rx.search(p["ref"]))
        eg = next((p for p in hits if not rx.search(p["ref"])), hits[0])
        L.append(f"- **{term}** — {len(hits)} occ, kept-French in {kept}")
        L.append(f"    FR: …{_ctx(eg['src'], term)}…")
        L.append(f"    EN: {eg['ref'][:200]}")
    L.append("")

    # 3. chrF vs reference, if a pipeline hypothesis exists
    hyp_f = d / "hyp.txt"
    if hyp_f.exists():
        from gold_align import sentences, align
        hyp_s = sentences(hyp_f.read_text(encoding="utf-8"))
        src_s = sentences(src_t)
        hp = align(src_s, hyp_s)              # map hyp back onto src units
        by_src = {}
        for p in hp:
            by_src.setdefault(p["src"], []).append(p["ref"])
        scores = []
        for p in pairs:
            h = " ".join(by_src.get(p["src"], []))
            if h and p["ref"]:
                scores.append(chrf(h, p["ref"]))
        if scores:
            scores.sort()
            L += ["## Pipeline vs reference (chrF++)",
                  f"- segments scored: {len(scores)}",
                  f"- mean {sum(scores) / len(scores):.1f}  median "
                  f"{scores[len(scores) // 2]:.1f}  "
                  f"p10 {scores[len(scores) // 10]:.1f}  p90 "
                  f"{scores[len(scores) * 9 // 10]:.1f}", ""]

    out = d / "report.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n-> {out}")


def _ctx(s, term, w=60):
    m = re.search(r"\b" + re.escape(term) + r"\b", s, re.I)
    if not m:
        return s[:120]
    a, b = max(0, m.start() - w), min(len(s), m.end() + w)
    return s[a:b]


if __name__ == "__main__":
    main()
