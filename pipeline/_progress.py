# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Live progress of a run, for a terminal view (scripts/progress.sh) and the
homelab's Homepage tile -- written by steps 1 and 2 after every page.

    processed/_status/progress.json   the run now: replaced whole each time
                                      (write + rename), so a reader never sees
                                      half a file
    processed/_status/history.jsonl   one line per finished book and step: the
                                      measured speed, for time estimates

progress.json:
    {"state": "running" | "waiting for GPU" | "done" | "stopped",
     "step": "OCR" | "translation", "book": stem,
     "done": pages finished this run, "total": pages this run will do,
     "left": ..., "percent": ...,
     "sec_per_page": mean of the last 10 pages, time waiting for the GPU
                     server left out,
     "pages_per_hour": ..., "eta": "HH:MM" local, "eta_minutes": ...,
     "started": "...", "updated": "...", in the system's clock (UTC in the
     container), and as seconds: "started_unix", "updated_unix", "eta_unix"
     -- readers convert these to their own time zone (progress.sh: DISPLAY_TZ),
     "last": the last page's scan number}

A reader should treat a "running" file whose updated_unix is more than a
few minutes old as stalled: the process may have died without saying so.
Writing here can never stop a run: every error is swallowed.
"""
import atexit
import collections
import json
import os
import time

DIR = os.environ.get("FMT_STATUS_DIR", "/data/processed/_status")
_run = None


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _write():
    if _run is None:
        return
    r = _run
    durs = list(r["durs"])
    spp = sum(durs) / len(durs) if durs else None
    left = max(0, r["total"] - r["done"])
    eta_min = round(left * spp / 60) if spp else None
    out = {"state": r["state"], "step": r["step"], "book": r["book"],
           "done": r["done"], "total": r["total"], "left": left,
           "percent": round(100 * r["done"] / r["total"]) if r["total"] else 100,
           "sec_per_page": round(spp, 1) if spp else None,
           "pages_per_hour": round(3600 / spp) if spp else None,
           "eta": time.strftime("%H:%M", time.localtime(time.time() + left * spp)) if spp else None,
           "eta_minutes": eta_min, "started": r["started"], "updated": _now(),
           "started_unix": int(r["t0"]), "updated_unix": int(time.time()),
           "eta_unix": int(time.time() + left * spp) if spp else None, "last": r["last"]}
    try:
        os.makedirs(DIR, exist_ok=True)
        tmp = f"{DIR}/progress.json.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)
        os.replace(tmp, f"{DIR}/progress.json")
    except OSError:
        pass


def start(book: str, step: str, total: int) -> None:
    """A book's step begins: `total` = the pages this run will process."""
    global _run
    _run = {"book": book, "step": step, "total": total, "done": 0, "state": "running",
            "started": _now(), "t0": time.time(), "mark": time.time(), "waited": 0.0, "waited_total": 0.0,
            "durs": collections.deque(maxlen=10), "last": None}
    _write()


def page(scan=None) -> None:
    """One page finished."""
    if _run is None:
        return
    now = time.time()
    _run["durs"].append(max(0.0, now - _run["mark"] - _run["waited"]))
    _run["mark"], _run["waited"] = now, 0.0
    _run["done"] += 1
    _run["last"] = scan
    _write()


def waiting(on: bool, since: float = None) -> None:
    """The GPU server stopped answering (on) / is back (off, with the time
    the wait began, so that time doesn't count against the speed)."""
    if _run is None:
        return
    _run["state"] = "waiting for GPU" if on else "running"
    if not on and since:
        _run["waited"] += time.time() - since
        _run["waited_total"] += time.time() - since
    _write()


def finish(state: str = "done") -> None:
    """The book's step ended. A completed one is added to history.jsonl."""
    global _run
    if _run is None:
        return
    _run["state"] = state
    _write()
    r = _run
    work = time.time() - r["t0"] - r["waited_total"]          # GPU outages left out
    if state == "done" and r["done"]:
        try:
            with open(f"{DIR}/history.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"date": r["started"][:10], "book": r["book"],
                                     "step": r["step"], "pages": r["done"],
                                     "minutes": round(work / 60, 1),
                                     "sec_per_page": round(work / r["done"], 1),
                                     "model": os.environ.get("TRANSLATION_MODEL_NAME" if r["step"] == "translation"
                                                             else "VLM_MODEL_NAME", "")}) + "\n")
        except OSError:
            pass
    _run = None


# a run that ends any other way (an error, Ctrl-C, a kill) says so
atexit.register(lambda: finish("stopped"))
