# User Manual — Section III: Using the Pipeline

Day-to-day operation, once Section II's host setup is done and the image
is built. Section I explains what each step does and why.

Throughout, the container is `booktranslator-translator-1` and the project
root is `~/BookTranslator`. Helper scripts live in
`~/BookTranslator/scripts/`.

---

## III.1 — Quick start

```bash
cd ~/BookTranslator

# one-time, if you haven't:
cp backend.env.example backend.env      # defaults: local / vulkan
docker compose build                    # ~20-30 min, first time only

# every run:
cp "/path/to/My Fencing Book (1875).pdf" books/
scripts/run_full_pipeline.sh
```

When it finishes, the two PDFs are in
`processed/My Fencing Book (1875)_translated/`.

`run_full_pipeline.sh` recreates the container, then runs steps 1→2→3→4 in
order, then prints a summary and waits for Enter. If step 1 fails it stops
before translating.

## III.2 — The four steps, run individually

The full-pipeline script is just these four `docker exec` calls with
framing. Run them one at a time when you're iterating.

```bash
docker compose up -d       # container must be up; comes up idle

docker exec booktranslator-translator-1 python3 /app/1_ocr_and_extract_vlm.py
docker exec booktranslator-translator-1 python3 /app/2_translate.py
docker exec booktranslator-translator-1 python3 /app/3_english_only_pdf.py
docker exec booktranslator-translator-1 python3 /app/4_side_by_side_pdf.py
```

Each step decides for itself which books to act on:

| Step | Acts on a book when… | To force a re-run |
|---|---|---|
| 1 — OCR | `processed/<stem>_translated/` does **not** exist yet | `rm -rf processed/<stem>_translated/` (loses everything), or see III.5 to re-parse without re-OCR |
| 2 — translate | `translation.txt` is missing or older than `extracted.txt` | `rm processed/<stem>_translated/translation.txt`, or `touch` its `extracted.txt` |
| 3 — English-only PDF | `translation.txt` exists | just re-run it — it overwrites |
| 4 — side-by-side PDF | `translation.txt` and `<stem>_OCR.pdf` exist | just re-run it — it overwrites |

So the normal iteration loops are:

- **Tune the translation** — edit `pipeline/translation_prompt.txt` or
  `pipeline/footnote_glossary.txt`, `rm translation.txt`, re-run steps
  2, 3, 4.
- **Tune a PDF's layout** — edit `pipeline/3_…` or `pipeline/4_…`, re-run
  just that step. `pipeline/` is mounted read-only into the container at
  `/app`, so your host edits are picked up immediately with no rebuild.
- **Tune the OCR prompt** — edit `pipeline/vlm_ocr_prompt.txt`, then see
  III.5 (you usually don't need to re-run the vision model).

## III.3 — Preparing a source PDF

- **Format:** a PDF, scanned or born-digital. Any page count. Spaces and
  punctuation in the filename are fine — the filename stem becomes the
  output folder name.
- **Quality:** clean flatbed / library scans work best. 300 DPI or better
  in the source. Colour or greyscale both fine. Phone photos of a book
  have not been tested and will probably align poorly in step 4.
- **Put it in `books/`.** That directory is mounted read-only into the
  container; the pipeline never writes there.

### Per-book overrides

Drop a sidecar file next to the PDF in `books/`, named with the same stem:

| File | Content | Effect |
|---|---|---|
| `<stem>.crop` | `left,top,right,bottom` — fractions, e.g. `0.06,0.04,0.06,0.08` | Trims that fraction off each page image before OCR. Use it to cut a dark scanner-bed border or a gutter shadow that the vision model keeps transcribing as noise. |
| `<stem>.lang` | Tesseract language codes, e.g. `fra+lat` | Only used by the legacy non-VLM `1_ocr_and_extract.py`. The default VLM path ignores it. |

## III.4 — What lands in `processed/<stem>_translated/`

| File | From step | What it is |
|---|---|---|
| `<stem>_OCR.pdf` | 1 | Verbatim copy of the source, for step 4 to draw the left column from |
| `raw_ocr.txt` | 1 | The vision model's untouched per-page responses. The recovery point — never deleted by any step |
| `extracted.txt` | 1 | Canonical `PAGE_NUMBER / HEADER / BODY_TEXT` scaffold per page |
| `page_numbers.txt` | 1 | One reconciled folio per line; `[n]` = inferred sheet ordinal, not a printed number |
| `translation.txt` | 2 | English, one page per `\n---\n` block, aligned with `extracted.txt` |
| `… — English Only (Portrait).pdf` | 3 | Reading copy |
| `… — Side by Side (Landscape).pdf` | 4 | Comparison copy |

Because the container runs as your user, these are owned by you. (On a
fresh checkout where `processed/` was created by an older root container,
run `sudo chown -R "$(id -u):$(id -g)" processed/` once —
`run_full_pipeline.sh` also does this automatically.)

## III.5 — Re-parsing OCR without re-running the vision model

`raw_ocr.txt` holds the model's raw output. The page-number reconciliation
and the repetition-loop cleanup that produce `extracted.txt` and
`page_numbers.txt` run *on that file*. If you improve that logic — or just
want the latest cleanup applied to a book OCR'd before it existed — rebuild
the two derived files without paying for OCR again:

```bash
# every book that has a raw_ocr.txt:
docker exec booktranslator-translator-1 python3 /app/reprocess_extracted.py

# or just some:
docker exec booktranslator-translator-1 python3 /app/reprocess_extracted.py \
  /data/processed/<stem>_translated
```

Then re-run steps 2→4. This does **not** re-apply the OCR *prompt* —
changing `vlm_ocr_prompt.txt` genuinely needs the vision model, which
means `rm -rf` the book's folder and re-running step 1.

## III.6 — Processing several books at once

Put them all in `books/` and run the pipeline once. Each step walks every
PDF / every `*_translated` folder that it hasn't already handled, so a
second book added later doesn't disturb the first.

To translate one book while leaving others untouched, use the
missing/older rule from III.2 — e.g. `touch` the other books'
`extracted.txt` files so step 2 sees their `translation.txt` as current
and skips them.

## III.7 — Long runs: watching progress

A 350-page book is a few hours, most of it in step 1. Both steps print
per-page progress to the `docker exec` stdout. In separate terminals:

```bash
# live GPU: util should spike 50-90%, SCLK 1500-2500 MHz, VRAM 60-80%
scripts/monitor_gpu.sh

# the running server's own log
docker exec booktranslator-translator-1 tail -f /tmp/vlm_server.log
docker exec booktranslator-translator-1 tail -f /tmp/translation_server.log
```

Step 2 prints a running count of pages, marks footnoted pages with
`(N fn)`, and flags anything it had to keep as low-confidence.

## III.8 — Helper scripts

All in `scripts/`, run from anywhere.

| Script | Purpose |
|---|---|
| `run_full_pipeline.sh` | Recreate container, run 1→2→3→4, print summary. The normal entry point. |
| `startup_docker.sh` | `systemctl start docker` + `docker compose up -d`. Start-of-session. |
| `shutdown_docker.sh` | Stop the container and the Docker service — frees all VRAM/RAM for other use (e.g. gaming). Keeps images, volumes, data. |
| `monitor_gpu.sh` | `watch`-based live GPU + container-resource display. Ctrl-C to stop. |
| `check_gpu_usage.sh` | One-shot: container up? GPU visible? server healthy? single test inference. Run after setup or a rebuild. |
| `benchmark_backends.sh` | Runs `llama-bench` for each compiled backend against both models. Use when deciding vulkan vs rocm7. |
| `clean_rebuild.sh` | Stop, remove *this project's* image, clear build cache, rebuild, restart. Models are untouched (they live in `~/llm_models`). ~15-20 min. Use after a Dockerfile change. |
| `compact_docker_storage.sh` | Remove dangling images and unused build cache. Monthly housekeeping. Frees 2-10 GB. |
| `full_cleanup.sh` | Nuclear: every container, image, volume, and the overlay2 tree. Requires typing `YES`. Keeps `~/llm_models`, `books/`, `processed/`. Rebuild is ~20 min afterward (no model re-download). |

> Note: `debug_vlm.sh` and `test_translation.sh` still reference the old
> Qwen2-VL model path and are stale — use `check_gpu_usage.sh` and the
> server logs instead.

## III.9 — Switching inference backend

Edit `backend.env`, then `docker compose up -d` (recreate, **no rebuild**):

```bash
BACKEND_MODE=local
LLAMA_BACKEND=rocm7        # or: vulkan (default)
```

`vulkan` and `rocm7` are both compiled into the image, so the switch is
~10 seconds. `rocm714` is wired but not built and will fail fast with a
pointer to `BackendBuildstage`.

For `BACKEND_MODE=remote` (inference on another GPU box on your LAN), see
`docs/remote-gpu.md` — you set `REMOTE_HOST` / `REMOTE_PORT` /
`REMOTE_API_KEY`, start one `llama-server` on that box, and swap the model
there between step 1 and step 2.

## III.10 — Troubleshooting

### The vision server won't start / step 1 exits immediately

```bash
docker exec booktranslator-translator-1 tail -50 /tmp/vlm_server.log
```

- **`libllama-server-impl.so: cannot open shared object file`** — a
  `docker exec` shell that starts a server without the backend lib dir on
  `LD_LIBRARY_PATH`. The pipeline scripts handle this themselves; if you
  see it, you're launching `llama-server` by hand — prepend
  `LD_LIBRARY_PATH=/usr/local/lib/llama-<vulkan|rocm|rocm714>`.
- **Model file not found** — check `~/llm_models` on the host is mounted
  and holds `allenai_olmOCR-2-7B-1025-Q6_K.gguf` +
  `mmproj-allenai_olmOCR-2-7B-1025-f16.gguf`. In `local` mode the
  entrypoint downloads missing models on `up`; watch
  `docker logs booktranslator-translator-1`.
- **RADV / device-lost on load** — transient. The wait loop now notices a
  dead server and reports it instead of hanging; re-run the step. If it
  repeats, try `LLAMA_BACKEND=rocm7` (III.9).

### `[VLM Error: …]` on some pages in `extracted.txt`

```bash
grep -n "VLM Error" processed/<stem>_translated/extracted.txt
```

Step 1 already retries a failed page up to 3 times, restarting the server
and dropping to a smaller image on the last try. Pages that still fail are
genuinely hard (enormous resolution, mangled scan). Options: add a
`.crop` to trim a noisy border, or accept it — step 2 will carry the error
line through as `[translation unavailable — original text follows]` and
the page count stays correct.

### A page reads `[low-confidence translation — review]` or `[translation unavailable …]`

Step 2 never drops a page. These markers mean 3 attempts failed the
wrong-script / too-much-non-English / too-short checks. Usually the OCR
for that page is garbled — look at the same page in `extracted.txt`. If
OCR is clean and it's a translation miss, adjust `translation_prompt.txt`,
`rm translation.txt`, re-run step 2.

### One page in the output is split in two / pages misalign after some point

The `\n---\n` page separator is content-fragile — a stray Markdown `---`
from the translation model once split one page into two, shifting every
later page against its source. Step 2 now rewrites `^---+$` → `* * *` and
`translation_prompt.txt` forbids Markdown, so this should be fixed; if you
see it, check `translation.txt` for a lone `---` line and delete it, then
re-run steps 3-4.

### Translation step never starts / server timeout

```bash
docker exec booktranslator-translator-1 pkill -f llama-server
docker compose restart && sleep 20
docker exec booktranslator-translator-1 python3 /app/2_translate.py
```

Step 2 resumes — it skips books whose `translation.txt` is already current
and picks up where it stopped.

### Container won't start

- **`Unable to find group render` / GPU device errors** — the
  `RENDER_GID` / `VIDEO_GID` in `.env` don't match this host. Re-run the
  Section II group-check (`getent group render video`) and fix `.env`.
- **`permission denied … docker daemon`** — `sudo usermod -aG docker
  $USER`, then log out and back in.
- **Crash loop right after `up`** — the container is *meant* to crash
  visibly rather than restart (`restart: "no"`). Read
  `docker logs booktranslator-translator-1`; don't add a restart policy
  to paper over it.

### Processing hangs mid-book

```bash
df -h                                          # need ≥10 GB free
docker ps                                      # container still up?
docker logs booktranslator-translator-1 | tail -50
docker compose restart && sleep 20
# then re-run the step it stopped on — each step resumes
```

## III.11 — Ending a session

```bash
scripts/shutdown_docker.sh
```

Stops the container and Docker, releasing all VRAM and RAM. Images,
models, `books/`, and `processed/` are all preserved. Restart later with
`scripts/startup_docker.sh`.

## III.12 — Housekeeping

- Each book costs roughly 2–3× its source PDF size in `processed/`.
  Archive finished books elsewhere and delete the folder.
- Run `scripts/compact_docker_storage.sh` monthly, or after a failed
  build.
- Models live in `~/llm_models` (host), not in the image — a rebuild or
  even `full_cleanup.sh` never re-downloads them.
- Keep an untouched copy of every source PDF outside `books/` — the
  pipeline treats `books/` as disposable input.
