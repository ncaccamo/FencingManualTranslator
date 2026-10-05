#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
# Live view of the pipeline run on this system: book, step, pages done/left,
# speed, ETA (from processed/_status/progress.json, written by steps 1 and 2
# after every page -- pipeline/_progress.py). Ctrl-C to quit.
#
#   scripts/progress.sh            refresh every 5 s
#   scripts/progress.sh --once     print once (for scripts / ssh)
#   scripts/progress.sh --history  the speed of past runs (history.jsonl)
WORKDIR=~/FencingManualTranslator
S="${FMT_STATUS_DIR:-$WORKDIR/processed/_status}"

show() {
python3 - "$S/progress.json" <<'PY'
import json, sys, time
try:
    p = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    print("No run recorded yet (processed/_status/progress.json)."); sys.exit()
age = time.time() - p.get("updated_unix", 0)
state = p["state"]
if state == "running" and age > 15 * 60:
    state = f"STALLED? no page finished for {age / 60:.0f} min"
bar_w = 40
fill = round(bar_w * p["percent"] / 100)
print(f"{p['book']}  —  {p['step']}  ({state})")
print(f"[{'#' * fill}{'.' * (bar_w - fill)}] {p['percent']}%")
print(f"pages   {p['done']} done, {p['left']} left of {p['total']}"
      + (f"   (last: scan {p['last']})" if p.get("last") else ""))
if p.get("sec_per_page"):
    print(f"speed   {p['sec_per_page']} s/page  ({p['pages_per_hour']} pages/h, last 10 pages)")
if p.get("eta") and p["state"] in ("running", "waiting for GPU"):
    h, m = divmod(p["eta_minutes"], 60)
    print(f"ETA     {p['eta']}  (in {h} h {m} min)" if h else f"ETA     {p['eta']}  (in {m} min)")
print(f"started {p['started']}   updated {p['updated']}")
PY
}

case "$1" in
  --once) show ;;
  --history)
    [ -f "$S/history.jsonl" ] || { echo "No finished runs recorded yet."; exit 0; }
    python3 - "$S/history.jsonl" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
print(f"{'date':10}  {'step':11}  {'pages':>5}  {'s/page':>6}  {'minutes':>7}  book")
for r in rows[-30:]:
    print(f"{r['date']:10}  {r['step']:11}  {r['pages']:>5}  {r['sec_per_page']:>6}  {r['minutes']:>7}  {r['book']}")
PY
    ;;
  *) while true; do clear; show; echo; echo "(refreshing every 5 s -- Ctrl-C to quit)"; sleep 5; done ;;
esac
