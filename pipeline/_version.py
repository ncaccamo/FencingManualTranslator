# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
"""The pipeline revision (short git commit hash), for the per-page
translation record (step 2) and the PDF cover (steps 3/4).

Tried in order:
  * PIPELINE_VERSION env (run_full_pipeline.sh exports it);
  * the repo's .git, mounted read-only at /data/git (docker-compose.yml) --
    /app is a mount of pipeline/ alone, so a bare `docker exec` has no repo;
  * the repo this file sits in (running on the host, e.g. the tests).
"" when none is reachable -- the caller says "not recorded", never a guess.
"""
import os
import subprocess
from pathlib import Path

_GIT_DIRS = ("/data/git", str(Path(__file__).resolve().parent.parent / ".git"))


def pipeline_version() -> str:
    v = os.environ.get("PIPELINE_VERSION", "").strip()
    if v:
        return v
    for g in _GIT_DIRS:
        if not os.path.isdir(g):
            continue
        try:
            # safe.directory: the mounted repo is owned by the host user,
            # not the container's, and git refuses it otherwise
            out = subprocess.run(
                ["git", "-c", "safe.directory=*", "--git-dir", g,
                 "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=3)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except Exception:
            pass
    return ""
