#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""The work queue: books and fixes run one after another, unattended, on
the system doing the run. Runs on the host (it drives the container).

    scripts/queue.py add book <stem> [<stem> ...]      a whole book (see below)
    scripts/queue.py add pages <stem> 12,40-42         retranslate these scan pages
    scripts/queue.py add flagged <stem> risposta       retranslate the pages the review
                                                       report flags for that (or kind:split)
    scripts/queue.py add pdfs <stem>                   rebuild PDFs + review report
    scripts/queue.py add captions <stem>               the caption reading + report
    scripts/queue.py add reprocess <stem>              re-derive step 1's text + report
        ... add --front                                put it at the head of the queue
    scripts/queue.py list                              what's queued, running, done
    scripts/queue.py start                             run the queue in the background
    scripts/queue.py pause                             stop after the current job
    scripts/queue.py stop                              stop now (the job stays first and
                                                       resumes: steps 1 and 2 save every page)
    scripts/queue.py move <n> <m>                      move job n to position m (1 = next)
    scripts/queue.py remove <n>

A "book" job: OCR (step 1) for a new book -- or, for a book already read,
its text re-derived with the current code (reprocess_extracted.py; seconds,
no GPU) -- then the caption reading, translation (only pages new or whose
text changed), a second try for pages that failed (OCR errors, untranslated
pages -- up to 2 rounds each), the PDFs and the review report.

The queue is processed/_status/queue.txt -- one job per line, top first;
edit it by hand if you like (lines starting # are ignored). Finished jobs go
to queue_done.txt with the time and result; the runner's output to
queue.log. scripts/progress.sh shows the queue as well as the page progress.

PDFs are built only from committed code (the cover records the pipeline
revision): with uncommitted changes under pipeline/ a job's PDFs are skipped
and logged -- commit, then queue "pdfs <stem>". A book marked finished
([book] final = true) is refused by the steps; its job fails and is logged.
Waits for the GPU system to answer before each GPU step (remote mode).
"""
import json
import os
import re
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATUS = os.path.join(ROOT, "processed", "_status")
QUEUE = os.path.join(STATUS, "queue.txt")
DONE = os.path.join(STATUS, "queue_done.txt")
LOG = os.path.join(STATUS, "queue.log")
STATE = os.path.join(STATUS, "queue_state.json")
PAUSE = os.path.join(STATUS, "queue.pause")
CONTAINER = "fmt-translator"
_stopping = False          # set by SIGTERM (queue.py stop): start nothing new
ACTIONS = {"book": 0, "pages": 1, "flagged": 1, "pdfs": 0, "captions": 0, "reprocess": 0}


# ---- the queue file --------------------------------------------------------

def read_jobs() -> list:
    if not os.path.exists(QUEUE):
        return []
    return [l.strip() for l in open(QUEUE, encoding="utf-8")
            if l.strip() and not l.lstrip().startswith("#")]


def write_jobs(jobs: list) -> None:
    os.makedirs(STATUS, exist_ok=True)
    tmp = QUEUE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("# one job per line, top first -- see scripts/queue.py\n" + "".join(j + "\n" for j in jobs))
    os.replace(tmp, QUEUE)


def state() -> dict:
    try:
        st = json.load(open(STATE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if st.get("pid") and not _alive(st["pid"]):
        return {}
    return st


def _alive(pid) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ---- running one job -------------------------------------------------------

class Runner:
    def __init__(self):
        self.log = open(LOG, "a", encoding="utf-8")
        self.child = None
        self.env = _backend_env()

    def say(self, msg):
        self.log.write(msg + "\n")
        self.log.flush()

    def wait_gpu(self):
        if self.env.get("BACKEND_MODE") != "remote" or not self.env.get("REMOTE_HOST"):
            return
        url = f"http://{self.env['REMOTE_HOST']}:{self.env.get('REMOTE_PORT', '11500')}/health"
        import urllib.request
        said = False
        while True:
            try:
                if urllib.request.urlopen(url, timeout=10).status == 200:
                    return
            except Exception:
                pass
            if not said:
                self.say(f"  {_now()} GPU system not answering -- waiting")
                said = True
            time.sleep(60)

    def run(self, *args, gpu=False) -> int:
        """One pipeline command in the container; output to the log."""
        if _stopping:
            raise SystemExit(0)
        if gpu:
            self.wait_gpu()
        cmd = ["docker", "exec", "-w", "/app", CONTAINER, "python3", *args]
        self.say(f"  $ {' '.join(args)}")
        self.child = subprocess.Popen(cmd, stdout=self.log, stderr=subprocess.STDOUT, start_new_session=True)
        _set_state(current_cmd=" ".join(args), child=self.child.pid)
        rc = self.child.wait()
        self.child = None
        if _stopping:
            raise SystemExit(0)
        return rc

    def bad_pages(self, stem, fname, marker) -> str:
        sys.path.insert(0, os.path.join(ROOT, "pipeline"))
        import _pageio
        p = os.path.join(ROOT, "processed", f"{stem}_translated", "intermediate", fname)
        if not os.path.exists(p):
            return ""
        pages = _pageio.split_pages(open(p, encoding="utf-8").read())
        return ",".join(str(i + 1) for i, t in enumerate(pages) if t.strip().startswith(marker))

    def heal_ocr(self, stem):
        for _ in range(2):
            bad = self.bad_pages(stem, "raw_ocr.txt", "[VLM Error")
            if not bad:
                return
            self.say(f"  OCR failed on scan pages {bad} -- reading them again")
            self.run("reread_pages.py", stem, bad, gpu=True)

    def heal_translation(self, stem):
        for _ in range(2):
            bad = self.bad_pages(stem, "translation.txt", "[translation unavailable")
            if not bad:
                return
            self.say(f"  untranslated scan pages {bad} -- translating them again")
            self.run("2_translate.py", "--pages", stem, bad, gpu=True)

    def pdfs(self, stem) -> bool:
        dirty = subprocess.run(["git", "-C", ROOT, "status", "--porcelain", "--", "pipeline"],
                               capture_output=True, text=True).stdout.strip()
        if dirty:
            self.say(f"  ⊘ pipeline/ has uncommitted changes -- PDFs for {stem} not built "
                     f"(commit, then queue: pdfs {stem})")
            self.run("quality_report.py", stem)
            return True
        ok = self.run("apply_overrides.py", stem) == 0
        ok = self.run("3_english_only_pdf.py", stem) == 0 and ok
        ok = self.run("4_side_by_side_pdf.py", stem) == 0 and ok
        self.run("quality_report.py", stem)
        return ok

    def job(self, line) -> bool:
        parts = line.split()
        action, stem, rest = parts[0], parts[1], parts[2:]
        raw = os.path.join(ROOT, "processed", f"{stem}_translated", "intermediate", "raw_ocr.txt")
        if action == "book":
            if os.path.exists(raw):
                if self.run("reprocess_extracted.py", stem):
                    return False
            elif self.run("1_ocr_and_extract_vlm.py", stem, gpu=True):
                if self.run("1_ocr_and_extract_vlm.py", stem, gpu=True):   # once more (resumes)
                    return False
            self.heal_ocr(stem)
            self.run("verify_captions.py", stem, gpu=True)
            if self.run("2_translate.py", stem, gpu=True) and self.run("2_translate.py", stem, gpu=True):
                return False
            self.heal_translation(stem)
            return self.pdfs(stem)
        if action == "pages":
            if self.run("2_translate.py", "--pages", stem, rest[0], gpu=True):
                return False
            self.heal_translation(stem)
            return self.pdfs(stem)
        if action == "flagged":
            if self.run("2_translate.py", "--flagged", stem, " ".join(rest), gpu=True):
                return False
            self.heal_translation(stem)
            return self.pdfs(stem)
        if action == "pdfs":
            return self.pdfs(stem)
        if action == "captions":
            return self.run("verify_captions.py", stem, gpu=True) == 0 and self.run("quality_report.py", stem) == 0
        if action == "reprocess":
            return self.run("reprocess_extracted.py", stem) == 0 and self.run("quality_report.py", stem) == 0
        self.say(f"  ✗ unknown job: {line}")
        return False


def _backend_env() -> dict:
    env = {}
    try:
        for l in open(os.path.join(ROOT, "backend.env"), encoding="utf-8"):
            l = l.split("#", 1)[0].strip()
            if "=" in l:
                k, v = l.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return env


def _set_state(**kw):
    st = state() or {"pid": os.getpid()}
    st.update(kw)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh, indent=1)
    os.replace(tmp, STATE)


def _ensure_container():
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER], capture_output=True, text=True)
    if r.stdout.strip() != "true":
        subprocess.run(["docker", "compose", "up", "-d"], cwd=ROOT, check=False)


def run_queue():
    os.makedirs(STATUS, exist_ok=True)
    if state():
        sys.exit("✗ the queue is already running (scripts/queue.py list)")
    _set_state(pid=os.getpid(), started=_now(), current=None)
    r = Runner()

    def _on_stop(signum, frame):
        # stop: start nothing new; end the step running in the container
        global _stopping
        _stopping = True
        st = state()
        if st.get("current_cmd"):
            subprocess.run(["docker", "exec", CONTAINER, "pkill", "-f", st["current_cmd"]], check=False)
        r.say(f"######## {_now()} stopped -- {st.get('current')!r} stays first")
    signal.signal(signal.SIGTERM, _on_stop)
    r.say(f"\n######## {_now()} queue started")
    _ensure_container()
    try:
        while True:
            if os.path.exists(PAUSE):
                os.remove(PAUSE)
                r.say(f"######## {_now()} paused")
                break
            jobs = read_jobs()
            if not jobs:
                r.say(f"######## {_now()} queue empty -- done")
                break
            line = jobs[0]
            parts = line.split()
            if not parts or parts[0] not in ACTIONS or len(parts) < 2 + ACTIONS[parts[0]]:
                r.say(f"✗ skipping malformed job: {line!r}")
                write_jobs(jobs[1:])
                continue
            _set_state(current=line, since=_now())
            r.say(f"######## {_now()} {line}")
            ok = r.job(line)
            r.say(f"  -> {'done' if ok else 'FAILED'}")
            with open(DONE, "a", encoding="utf-8") as fh:
                fh.write(f"{_now()}  {'done  ' if ok else 'FAILED'}  {line}\n")
            jobs = read_jobs()                       # the file may have been edited meanwhile
            if jobs and jobs[0] == line:
                write_jobs(jobs[1:])
            elif line in jobs:
                jobs.remove(line)
                write_jobs(jobs)
    finally:
        try:
            os.remove(STATE)
        except OSError:
            pass


# ---- commands --------------------------------------------------------------

def cmd_list():
    st, jobs = state(), read_jobs()
    if st:
        print(f"RUNNING since {st.get('started')}: {st.get('current') or '(starting)'}"
              + (f"\n   now: {st['current_cmd']}" if st.get("current_cmd") else ""))
    else:
        print("not running" + (" (will pause after the current job)" if os.path.exists(PAUSE) else ""))
    print(f"\n{len(jobs)} job(s) queued:" if jobs else "\nqueue empty")
    for n, j in enumerate(jobs, 1):
        mark = "  <- running" if st and st.get("current") == j and n == 1 else ""
        print(f"  {n:>2}. {j}{mark}")
    if os.path.exists(DONE):
        tail = open(DONE, encoding="utf-8").read().strip().split("\n")[-5:]
        print("\nlast finished:\n" + "\n".join("  " + t for t in tail))


def cmd_add(args):
    front = "--front" in args
    args = [a for a in args if a != "--front"]
    if not args or args[0] not in ACTIONS:
        sys.exit(f"✗ job must start with one of: {', '.join(ACTIONS)}")
    action, rest = args[0], args[1:]
    if action == "book":
        new = [f"book {s}" for s in rest]
    else:
        if len(rest) < 1 + ACTIONS[action]:
            sys.exit(f"✗ {action} needs a book{' and pages / words' if ACTIONS[action] else ''}")
        new = [" ".join([action] + rest)]
    for j in new:
        stem = j.split()[1]
        if not (os.path.exists(os.path.join(ROOT, "books", f"{stem}.pdf"))
                or os.path.isdir(os.path.join(ROOT, "processed", f"{stem}_translated"))):
            sys.exit(f"✗ no such book: {stem}")
    jobs = read_jobs()
    st = state()
    if front:
        at = 1 if st and jobs and st.get("current") == jobs[0] else 0   # after the running job
        jobs[at:at] = new
    else:
        jobs += new
    write_jobs(jobs)
    print(f"added {len(new)} job(s){' at the front' if front else ''}")
    cmd_list()


def cmd_move(a, b):
    jobs = read_jobs()
    a, b = int(a), int(b)
    if not (1 <= a <= len(jobs) and 1 <= b <= len(jobs)):
        sys.exit("✗ no such position")
    st = state()
    if st and st.get("current") in (jobs[a - 1], jobs[b - 1]) and (a == 1 or b == 1):
        sys.exit("✗ job 1 is running -- stop the queue to move it")
    jobs.insert(b - 1, jobs.pop(a - 1))
    write_jobs(jobs)
    cmd_list()


def cmd_remove(a):
    jobs = read_jobs()
    a = int(a)
    st = state()
    if st and a == 1 and st.get("current") == jobs[0]:
        sys.exit("✗ job 1 is running -- stop the queue first")
    print(f"removed: {jobs.pop(a - 1)}")
    write_jobs(jobs)


def cmd_start():
    if state():
        sys.exit("✗ already running")
    if not read_jobs():
        sys.exit("✗ nothing queued (scripts/queue.py add ...)")
    if os.path.exists(PAUSE):
        os.remove(PAUSE)
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "run"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    time.sleep(1)
    print(f"started -- watch with scripts/progress.sh, details in {os.path.relpath(LOG, ROOT)}")


def cmd_stop():
    st = state()
    if not st:
        print("not running")
        return
    os.kill(st["pid"], signal.SIGTERM)        # the runner ends its step and starts nothing new
    for _ in range(30):
        if not _alive(st["pid"]):
            break
        time.sleep(1)
    print(f"stopped; {st.get('current')!r} stays first and resumes on the next start")


def main(argv):
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    c, rest = argv[0], argv[1:]
    if c == "list" or c == "status":
        cmd_list()
    elif c == "add":
        cmd_add(rest)
    elif c == "start":
        cmd_start()
    elif c == "run":
        run_queue()
    elif c == "pause":
        open(PAUSE, "w").close()
        print("will pause after the current job" if state() else "not running (the next start runs normally)")
        if not state():
            os.remove(PAUSE)
    elif c == "stop":
        cmd_stop()
    elif c == "move" and len(rest) == 2:
        cmd_move(*rest)
    elif c == "remove" and len(rest) == 1:
        cmd_remove(rest[0])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
