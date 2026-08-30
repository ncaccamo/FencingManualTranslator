#!/usr/bin/env python3
"""Run the pipeline's translation on a gold book's OCR'd original, so its
output can be scored against the published reference.

Runs INSIDE the container. Reuses 2_translate.py's prompt, model, server
lifecycle and per-page retry/quality-gate -- so hyp.txt reflects the real
pipeline, deterministic bits included (apply_always_translate,
enforce_kept_terms, match_boundary_punctuation, deshout).

    docker exec booktranslator-translator-1 \
      python3 /data/evals/translate_gold.py bazancourt_1862_secrets
    ... --model /models/Qwen2.5-14B-Instruct-Q4_K_M.gguf     # A/B a model
"""
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")

_STEP2 = Path("/app/2_translate.py")
if "--model" in sys.argv:
    os.environ["TRANSLATION_MODEL_PATH"] = sys.argv[sys.argv.index("--model") + 1]

_src = _STEP2.read_text()
_ns = {"__name__": "_translate_gold"}
exec(compile(_src.split('\nprint("\\n" + "="*60)')[0], str(_STEP2), "exec"), _ns)

start_server = _ns["start_translation_server"]
translate = _ns["translate_with_llama"]
PROMPT = _ns["PROMPT_TEMPLATE"]
apply_always = _ns["apply_always_translate"]
enforce_terms = _ns["enforce_kept_terms"]
boundary = _ns["match_boundary_punctuation"]
deshout = _ns["deshout"]
shout = _ns["_shout_ratio"]
too_french = _ns["contains_too_much_non_english"]
strip_preamble = _ns["strip_model_preamble"]

GOLD = Path("/data/evals/gold")


def translate_page(fr: str) -> str:
    best = ""
    for att in range(3):
        temp = 0.3 + 0.25 * att
        p = PROMPT + ("" if att == 0 else
                      "\n\nTranslate into fluent English. Do NOT repeat the "
                      "source language. Match the source's capitalisation.")
        out = translate(f"{p}\n\nText to translate:\n{fr}", temperature=temp)
        out = strip_preamble((out or "").strip()).strip()
        if not out:
            continue
        out = re.sub(r"(?m)^\s*-{3,}\s*$", "* * *", out)
        out = enforce_terms(apply_always(out), fr)
        best = best or out
        if too_french(out) or (shout(out) > 0.85 and shout(fr) < 0.6):
            time.sleep(3); continue
        return boundary(out, fr)
    return deshout(boundary(best, fr), fr) if best else "[untranslated]"


def main():
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not pos:
        sys.exit(__doc__)
    d = GOLD / pos[0]
    pages = re.split(r"<!--p \d+-->", (d / "src.txt").read_text(encoding="utf-8"))
    pages = [p.strip() for p in pages if p.strip()]
    print(f"{d.name}: {len(pages)} pages to translate")
    if start_server() is None:
        sys.exit("no translation server")
    try:
        out, t0 = [], time.time()
        for i, fr in enumerate(pages):
            out.append(translate_page(fr))
            if (i + 1) % 10 == 0 or i == len(pages) - 1:
                r = (time.time() - t0) / (i + 1)
                print(f"  {i + 1}/{len(pages)}  ({r:.1f}s/pg, "
                      f"~{r * (len(pages) - i - 1) / 60:.0f} min left)")
                (d / "hyp.txt").write_text("\n\n".join(out) + "\n", encoding="utf-8")
        (d / "hyp.txt").write_text("\n\n".join(out) + "\n", encoding="utf-8")
        print(f"-> {d / 'hyp.txt'}")
    finally:
        _ns["subprocess"].run(["pkill", "-f", "llama-server.*11434"])


if __name__ == "__main__":
    main()
