# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Nicholas A. Caccamo
import sys
sys.path.insert(0, "/app")
exec(compile(open("/app/1_ocr_and_extract_vlm.py").read().split("def preprocess_for_ocr")[0], "vlm", "exec"))
print("Resolved mmproj:", VLM_MMPROJ_PATH)
