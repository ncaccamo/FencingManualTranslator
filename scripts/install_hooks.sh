#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
# Puts the repository's git hooks in place (once per clone):
#   scripts/install_hooks.sh
# pre-commit: the rule tests run before every commit; a failure stops it.
set -e
root=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
for h in "$root"/scripts/git-hooks/*; do
    install -m 755 "$h" "$root/.git/hooks/$(basename "$h")"
    echo "installed: .git/hooks/$(basename "$h")"
done
