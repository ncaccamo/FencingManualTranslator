#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
# Live view of the pipeline run on this system: the run (book, step, pages
# done/left, speed, ETA) and its inference backend (local GPU or the remote
# GPU system, per backend.env). Details: scripts/progress.py.
#
#   scripts/progress.sh              refresh every 5 s (Ctrl-C to quit)
#   scripts/progress.sh --once       print once (for scripts / ssh)
#   scripts/progress.sh --every 15   refresh every 15 s
#   scripts/progress.sh --history    the speed of past runs (history.jsonl)
exec python3 "$(dirname "$(readlink -f "$0")")/progress.py" "$@"
