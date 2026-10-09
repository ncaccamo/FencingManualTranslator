#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""Live view of a pipeline run, on the system doing the run: the run itself
(book, step, pages done/left, speed, ETA) and the inference backend it uses,
which depends on BACKEND_MODE in backend.env:

  local   the two llama-servers in the container (OCR :11435, translation
          :11434) and the GPU (busy %, VRAM, temperature, power) read from the
          amdgpu kernel files -- no ROCm needed on the host
  remote  the GPU system's llama-swap: reachable or not, and which model it
          has loaded (needs REMOTE_API_KEY when llama-swap has one)

    scripts/progress.sh                 refresh every 5 s (Ctrl-C to quit)
    scripts/progress.sh --once          print once (scripts, ssh)
    scripts/progress.sh --every 15      refresh every 15 s
    scripts/progress.sh --history       the speed of past runs

Times are shown in DISPLAY_TZ from backend.env when it is set ("14:39
Eastern (18:39 UTC)", with DISPLAY_TZ_LABEL as the zone's name), else UTC.

The run part reads processed/_status/progress.json, written by steps 1 and
2 after every page (pipeline/_progress.py). Standard library only. Every
check is read-only and passive: it never sends a model request, so it can't
load a model or reset llama-swap's idle timer.
"""
import calendar
import glob
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS = os.environ.get("FMT_STATUS_DIR", os.path.join(ROOT, "processed", "_status"))
STALL_MIN = 15
CONTAINER = "fmt-translator"


def backend_env() -> dict:
    env = {}
    try:
        for line in open(os.path.join(ROOT, "backend.env"), encoding="utf-8"):
            line = line.split("#", 1)[0].strip()
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


def get(url: str, key: str = "", timeout: float = 3):
    """(status code or None, body text)."""
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, OSError, TimeoutError):
        return None, ""


# ---- times -----------------------------------------------------------------

def _clock(env: dict):
    """unix seconds -> "14:39 Eastern (18:39 UTC)" in DISPLAY_TZ (plain UTC
    without it); a day other than today gets its weekday."""
    from datetime import datetime, timezone
    zone, label = None, ""
    if env.get("DISPLAY_TZ"):
        try:
            from zoneinfo import ZoneInfo
            zone = ZoneInfo(env["DISPLAY_TZ"])
        except Exception:                      # unknown zone name: stay in UTC
            zone = None
    def fmt(ts):
        if ts is None:
            return "—"
        u = datetime.fromtimestamp(ts, timezone.utc)
        today_u = datetime.now(timezone.utc).date()
        utc = u.strftime("%H:%M UTC") + ("" if u.date() == today_u else u.strftime(" %a"))
        if zone is None:
            return utc
        l = u.astimezone(zone)
        name = env.get("DISPLAY_TZ_LABEL") or l.tzname()
        day = "" if l.date() == datetime.now(zone).date() else l.strftime("%a ")
        return f"{day}{l.strftime('%H:%M')} {name} ({utc})"
    return fmt


def _unix(p: dict, key: str):
    """A time from progress.json as unix seconds (older files have only the
    text, in the container's clock -- UTC)."""
    if p.get(f"{key}_unix") is not None:
        return p[f"{key}_unix"]
    if key == "eta" and p.get("eta_minutes") is not None:
        return p.get("updated_unix", time.time()) + 60 * p["eta_minutes"]
    try:
        return calendar.timegm(time.strptime(p[key], "%Y-%m-%d %H:%M:%S"))
    except (KeyError, ValueError, TypeError):
        return None


# ---- the run ---------------------------------------------------------------

def run_lines() -> list:
    try:
        p = json.load(open(os.path.join(STATUS, "progress.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return ["No run recorded yet (processed/_status/progress.json)."]
    age = time.time() - p.get("updated_unix", 0)
    state = p["state"]
    if state == "running" and age > STALL_MIN * 60:
        state = f"STALLED? no page finished for {age / 60:.0f} min"
    w = 40
    fill = round(w * p["percent"] / 100)
    out = [f"{p['book']}  —  {p['step']}  ({state})",
           f"[{'#' * fill}{'.' * (w - fill)}] {p['percent']}%",
           f"pages    {p['done']} done, {p['left']} left of {p['total']}"
           + (f"   (last: scan {p['last']})" if p.get("last") else "")]
    if p.get("sec_per_page"):
        out.append(f"speed    {p['sec_per_page']} s/page  ({p['pages_per_hour']} pages/h, last 10 pages)")
    clock = _clock(backend_env())
    if p.get("eta") and p["state"] in ("running", "waiting for GPU"):
        h, m = divmod(p["eta_minutes"], 60)
        out.append(f"ETA      {clock(_unix(p, 'eta'))}  (in {h} h {m} min)" if h
                   else f"ETA      {clock(_unix(p, 'eta'))}  (in {m} min)")
    out.append(f"started  {clock(_unix(p, 'started'))}   updated {clock(_unix(p, 'updated'))}")
    return out


# ---- the backend -----------------------------------------------------------

def container_state() -> str:
    try:
        r = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}", CONTAINER],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or "not found"
    except (OSError, subprocess.TimeoutExpired):
        return "docker not available"


def _read(path: str):
    try:
        return open(path).read().strip()
    except OSError:
        return None


def gpu_lines() -> list:
    """The first amdgpu card's busy %, VRAM, temperature and power, from the
    kernel's sysfs files (the in-kernel driver writes them; no ROCm)."""
    for dev in sorted(glob.glob("/sys/class/drm/card*/device")):
        busy = _read(f"{dev}/gpu_busy_percent")
        if busy is None:
            continue
        used, total = _read(f"{dev}/mem_info_vram_used"), _read(f"{dev}/mem_info_vram_total")
        hw = (glob.glob(f"{dev}/hwmon/hwmon*") or [""])[0]
        temp = _read(f"{hw}/temp1_input") if hw else None
        power = (_read(f"{hw}/power1_average") or _read(f"{hw}/power1_input")) if hw else None
        parts = [f"busy {busy}%"]
        if used and total:
            parts.append(f"VRAM {int(used) / 2**30:.1f} / {int(total) / 2**30:.1f} GB")
        if temp:
            parts.append(f"{int(temp) / 1000:.0f} °C")
        if power:
            parts.append(f"{int(power) / 1e6:.0f} W")
        return ["GPU      " + ", ".join(parts)]
    return ["GPU      no amdgpu card found on this system"]


def local_lines(env: dict) -> list:
    out = [f"backend  local ({env.get('LLAMA_BACKEND', 'vulkan')}), container {container_state()}"]
    for name, port in (("OCR model        ", 11435), ("translation model", 11434)):
        code, _ = get(f"http://localhost:{port}/health", timeout=2)
        state = {200: "loaded, ready", 503: "loading"}.get(code, "not running" if code is None else f"HTTP {code}")
        out.append(f"{name} :{port}  {state}")
    return out + gpu_lines()


def remote_lines(env: dict) -> list:
    host, port, key = env.get("REMOTE_HOST", ""), env.get("REMOTE_PORT", "11500"), env.get("REMOTE_API_KEY", "")
    if not host:
        return ["backend  remote, but REMOTE_HOST is not set in backend.env"]
    base = f"http://{host}:{port}"
    code, _ = get(f"{base}/health")
    out = [f"backend  remote: GPU system {host}:{port} "
           + ("reachable" if code == 200 else "NOT ANSWERING" if code is None else f"HTTP {code}")
           + f", container {container_state()}"]
    if code != 200:
        return out
    # llama-swap's own list of loaded models: not proxied to a model, so it
    # neither starts one nor counts as activity for its idle timer
    code, body = get(f"{base}/running", key)
    if code in (401, 403):
        return out + ["models   set REMOTE_API_KEY in backend.env to see which model is loaded"]
    try:
        running = json.loads(body).get("running") or []
    except ValueError:
        running = None
    if running is None:
        return out + ["models   (no model list: not a llama-swap server?)"]
    names = {env.get("VLM_MODEL_NAME", "fmt-ocr"): "OCR", env.get("TRANSLATION_MODEL_NAME", "fmt-translate"): "translation"}
    if not running:
        return out + ["models   none loaded (the next request loads one)"]
    for m in running:
        cmd = m.get("cmd") or ""
        layers = cmd.split("--n-gpu-layers", 1)[1].split()[0] if "--n-gpu-layers" in cmd else None
        out.append(f"model    {m.get('model')} ({names.get(m.get('model'), 'other project')}): {m.get('state')}"
                   + (f", {layers} layers on the GPU" if layers else ""))
    return out


def backend_lines() -> list:
    env = backend_env()
    return remote_lines(env) if env.get("BACKEND_MODE", "local") == "remote" else local_lines(env)


# ---- views -----------------------------------------------------------------

def queue_lines() -> list:
    """The work queue (scripts/queue.py), when one is in use."""
    q = os.path.join(STATUS, "queue.txt")
    if not os.path.exists(q):
        return []
    jobs = [l.strip() for l in open(q, encoding="utf-8") if l.strip() and not l.startswith("#")]
    try:
        st = json.load(open(os.path.join(STATUS, "queue_state.json"), encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    if st.get("current"):
        nxt = [j for j in jobs if j != st["current"]]
        return [f"queue    running: {st['current']}", f"         then {len(nxt)} more"
                + (f": {', '.join(nxt[:3])}{' ...' if len(nxt) > 3 else ''}" if nxt else "")]
    return [f"queue    not running; {len(jobs)} job(s) waiting (scripts/queue.py start)"] if jobs else []


def screen() -> str:
    q = queue_lines()
    return "\n".join(run_lines() + ([""] + q if q else []) + [""] + backend_lines())


def history() -> str:
    p = os.path.join(STATUS, "history.jsonl")
    if not os.path.exists(p):
        return "No finished runs recorded yet."
    rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
    out = [f"{'date':10}  {'step':11}  {'pages':>5}  {'s/page':>6}  {'minutes':>7}  book"]
    out += [f"{r['date']:10}  {r['step']:11}  {r['pages']:>5}  {r['sec_per_page']:>6}  "
            f"{r['minutes']:>7}  {r['book']}" for r in rows[-30:]]
    return "\n".join(out)


def main(argv: list) -> int:
    if "--history" in argv:
        print(history())
        return 0
    if "--once" in argv:
        print(screen())
        return 0
    every = int(argv[argv.index("--every") + 1]) if "--every" in argv else 5
    try:
        while True:
            text = screen()                       # gather first, then redraw: no flicker
            sys.stdout.write("\033[H\033[2J" + text + f"\n\n(refreshing every {every} s -- Ctrl-C to quit)\n")
            sys.stdout.flush()
            time.sleep(every)
    except KeyboardInterrupt:
        print()
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
