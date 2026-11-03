# P7 — Vendor survey: how the bench runs each vendor today, and what it would take to run the NEWEST version

Read-only survey. Nothing was modified, installed, committed, pushed or deleted. Only this file was written.

## 0. Repos surveyed

| Path | State | Notes |
|---|---|---|
| `~/bench/.work/ndb-main` | clean `origin/main` checkout of `neurotic_docx_bench` | branch `release/ndb-v0.0.1`, HEAD `6eea68ef`. **No `node_modules`** (clean), `.venv` present (Python 3.14). |
| `~/temp/T/neurotic_docx_bench` | Arthur's working checkout — corpus + results | `node_modules` installed (`docxodus@12.6.5`, `superdoc@2.18.0`, `@superdoc-dev/sdk@1.21.3`), `.venv` has `apryse-sdk==12.2.1`, `.env` holds `APRYSE_API_KEY` (value not read/printed). Has uncommitted local edits (`pyproject.toml` adds `apryse-sdk>=12.2.1` to the `dev` extra; `results/converters.jsonl`, `uv.lock`). |
| `~/temp/T/ndb-apryse` | branch `bench/apryse-12.2.1` (PRs #108/#109) | HEAD `429a84ea` "Results: Apryse SDK 12.2.1 on the bench (#100)". `.venv`/`.env` are symlinks to Arthur's checkout. Carries `src/neurotic_docx_bench/apryse_gen.py`, `apryse_stamp.py`, `utils/apryse/apryse-convert`, `out/apryse/*` run scripts. |

## 1. The two bench paths (what "the bench" actually is)

### A. Converter path — DOCX→PDF vs Word's oracle

* CLI: `bench docx-to-pdf` → `src/neurotic_docx_bench/cli.py:827` (`docx_to_pdf_eval`), and the second lens `bench docxide-metrics` → `cli.py:1007` (`docxide_metrics_eval`).
* Engine: `src/neurotic_docx_bench/docx_to_pdf.py`
  * `convert_command()` — **`:328`** — the per-vendor argv
  * `resolve_tool_binary()` — **`:375`**
  * `try_convert_fixtures()` — **`:507`**
  * `tool_version()` — **`:644`** (+ `_version_from_cargo_install_list` `:616`, `_docxodus_export_version` `:668`)
  * `run_eval()` — **`:717`**
* Oracle: Microsoft Word's own PDFs in `corpus/word/<state>/pdf`; SHA-pinned for the legacy `--track` sets under `src/neurotic_docx_bench/docx_to_pdf_pins/*.tsv`.
* Scoring: `pipeline.score_folders_plain` (`src/neurotic_docx_bench/pipeline.py`), headline = `pipeline.overall_from_result`. Rasters at 144 DPI; the docxide lens at 150 DPI.
* Outputs: report JSON at `--json results/<name>.json`; one append-only store line per (report, tool) into **`results/converters.jsonl`** via `ledger/converters.py` (`_append_converter_lines`, `cli.py:716`).

### B. Generator path — redlines vs Word's compare oracle

* CLI: `bench run` → `cli.py:2632`; per-run driver `_execute_run` → `cli.py:1744`. Work dir `runs/<name>_<timestamp>`; the `generate:` command is expanded once per declared corpus pool (`config.expand_generate_commands`, `src/neurotic_docx_bench/config.py:135`).
* Redline generators:
  * `scripts/generate-native-redlines.ts` — `parseArgs` **`:1150`**, entry `:1180`. Methods: `docxodus` **`:243`**, `superdoc-ts` **`:453`**, `docxodus-csharp` / `docxodus-csharp-inproc` **`:564`**, `superdoc-native` `:1064`, plus `folio`, `stemma`, `jubarte-*`, `safe-docx`.
  * `src/neurotic_docx_bench/superdoc_gen.py` (PyPI `superdoc-sdk`) — `parse_manifest` **`:61`**, `generate_one` **`:92`**, `main` **`:181`**.
  * `src/neurotic_docx_bench/apryse_gen.py` (ndb-apryse only) — `api_key` **`:39`**, `version` **`:58`**, `convert` **`:78`**, `compare` **`:97`**, `batch` **`:133`**, `main` **`:170`**.
* Rendering the candidate redline to PDF: `render/auto.py` → Word (`scripts/word_pdf.py`, `main` at `:3104`) or LibreOffice (`render/soffice.py`: `find_soffice` `:35`, `convert_one` `:71`).
* Store: **`results/bench.jsonl`**; `bench report` → `results/scores.json`, `RESULTS.md`, `RESULTS_DETAILED.md`.
* The Word-compare-oracle lane (`results/redlines_0929_full/`, the one the prompt describes: base+next → redline → **Word** exports the redline to PDF → measure vs Word's compare):
  1. generate → 2. `scripts/word_pdf.py --src <tool>/docx --out <tool>/pdf_by_word` → 3. `uv run python results/redlines_0929_full/measure.py <tool>` → `scores_<tool>.json` → 4. `uv run python results/redlines_0929_full/to_scores_jsonl.py` → `scores.jsonl` → 5. `uv run bench report`.
  * Versions are pinned in **`results/redlines_0929_full/versions.json`** (read by `ledger/tables.py:980`).

## 2. Does the bench support running a tool on an explicit list? — YES

| Flag | Where | What it takes |
|---|---|---|
| `bench docx-to-pdf --origin list --files-list <path>…` | `cli.py:866-871` (option), `word_pdf_source.select_corpus_word_pdfs` `src/neurotic_docx_bench/word_pdf_source.py:69` | Repeatable. `--origin list` **requires** `--files-list` and `--files-list` requires `--origin list` (else `ValueError`). Entries must be `<state>/docx/<stem>.docx` or `<state>/pdf/<stem>.pdf` **under `word_pdf.root`** (default `corpus/word`). |
| `bench docxide-metrics --origin list --files-list <path>…` | `cli.py:1034` | Same semantics. |
| `scripts/convert_candidates.py --engine docxide\|soffice --out DIR --files-list <txt>` | `scripts/convert_candidates.py:30` | A **text file** of corpus docx paths (one per line); converts only the listed fixtures that lack a render. This is the LibreOffice-as-converter path. |
| `--manifest <pairs.csv> --source-dir <dir>` | every generator (`generate-native-redlines.ts:1168-1169`, `superdoc_gen.py:185-186`, `apryse_gen.py` `batch`) | The pairs CSV is the explicit list. Header seen in `corpus/word/pools/word_based_pairs.csv`: `key,base,next,base_name,next_name,docx,pdf,state`. `config.py:224` `_DRIVER_OWNED_FLAGS = ("--manifest","--source-dir")` — the driver owns them for `bench run`, so a yaml `generate:` may **not** hardcode them. |
| `bench run --only <run-name>` / `--limit N` | `cli.py:2635-2638` | One yaml run; cap documents per run. |
| `bench try run --fixture <pair_stem>` / `--random --seed N` | `cli.py:3783` | One fixture at a time from the tryout set. |
| `bench try remote --tool X --seed N [--with-sot]` | `cli.py:3934` | Random picks from the HF `superdoc-dev/docx-corpus`. |
| `--score-only --location-to-score <dir|PDF>` | `cli.py:880-886`, `word_pdf_source.py:_score_only` | Score PDFs a tool already produced (the “render elsewhere, score here” path). |

**No** `--files-list` exists on `bench run` itself; for redlines the explicit list is the `--manifest` CSV.

## 3. docxodus

### How the bench runs it today

| Task | Entry point (file:line) | Exact command line used last time |
|---|---|---|
| Redline (npm/WASM lane, the `bench.yaml` run) | `scripts/generate-native-redlines.ts:243` (`--method=docxodus`) | `node --import tsx scripts/generate-native-redlines.ts --method=docxodus --out=$RUN_DIR/docx --run-dir=$RUN_DIR --tool=docxodus` (`bench.yaml:136-146`; the driver appends `--manifest=<pool>.csv --source-dir=corpus/word` per pool) |
| Redline (C# lane — **this is the scored “Docxodus 12.6.5 (C#)”**) | `scripts/generate-native-redlines.ts:564-620` (`--method=docxodus-csharp-inproc --dist …/bin/Release/net10.0`) | `WORKER_REPLY_TIMEOUT_MS=120000 node --import tsx scripts/generate-native-redlines.ts --method docxodus-csharp-inproc --dist src/neurotic_docx_bench/utils/docxodus/docxodus-csharp-inproc/bin/Release/net10.0 --tool docxodus --manifest $R/gen_pairs.csv --source-dir corpus/word --out $R/docxodus/docx --run-dir $R/docxodus > $R/docxodus.generate.log 2>&1` (`results/redlines_0929_full/generate.sh`) |
| DOCX→PDF | `docx_to_pdf.py:328` `convert_command()` → the `docxodus` branch; binary `src/neurotic_docx_bench/utils/docxodus-export/node_modules/.bin/docxodus` (`docx_to_pdf.py:62-63`, resolution `:390-396`) | `docxodus convert <src> --to pdf --output <dest> --review-profile markup --markup-presentation word --comments margin --comment-column-placement balloons --timeout 180000 --font-directory ~/.cache/neurotic-docx-bench/docxodus-fonts` |

* C# worker project: `src/neurotic_docx_bench/utils/docxodus/docxodus-csharp-inproc/DocxodusInproc.csproj` (net10.0; `Docxodus.dll` via `HintPath ../ooxmlsdk/Docxodus/tools/redline/bin/Release/net10.0/Docxodus.dll`). The DLL is byte-identical to `~/temp/T/ooxmlsdk/Docxodus/tools/redline` — the JSv4/Docxodus git checkout at `19b40bd7` (tag `v12.6.5`), built with `~/.dotnet/dotnet` (SDK 10.0.403).
* **Where outputs go:** redlines → `<out>/docx/<key>_<tool>.docx` + `generate_failures.json` / `generate_timings.json` beside it; Word exports → `<tool>/pdf_by_word/<key>_<tool>.pdf`; DOCX→PDF candidates → `<work-dir>/docxodus/candidate/<state>__<stem>.pdf`.
* **Scoring command / scores file:**
  * DOCX→PDF: `uv run bench docx-to-pdf --tool docxodus … --json results/docx_to_pdf_docxodus.json` → `results/converters.jsonl`.
  * Redlines: `uv run python results/redlines_0929_full/measure.py docxodus` → `results/redlines_0929_full/scores_docxodus.json`; then `to_scores_jsonl.py` → `scores.jsonl`.
  * Canonical yaml path: `uv run bench run --only docxodus --rerun` → `results/bench.jsonl`.
* **How the version is recorded:** `tool_updater.resolve_tool_version` (`src/neurotic_docx_bench/tool_updater.py:205`; npm path `update_npm_package` `:142`): npm → `pnpm add --exact docxodus@12.6.5 --workspace-root` then read `node_modules/docxodus/package.json`; C# lane → `resolve_local_version` content hash; DOCX→PDF lane → `docx_to_pdf.py:668 _docxodus_export_version()` → `"@docxodus/export 14.2.0 (docxodus 14.2.0)"`; redline lane → `results/redlines_0929_full/versions.json` → `"docxodus": "Docxodus 12.6.5 (C#)"`.
* **HF:** `uv run python results/redlines_0929_full/hub_upload.py [--dry-run|--prune]` → `outputs/redlines_0929_full/` on `arthrod/neurotic_docx_bench` (`hub.RESULTS_REPO`, `src/neurotic_docx_bench/hub.py:34`), one `upload_folder` commit per subfolder, then `hub.verify_uploaded` (`:563`) + `MANIFEST.sha256.json`. Generic alternative: `uv run bench results upload [--with-outputs] [--prune-local] [--dry-run]` (`cli.py:3606`). Download: `uv run bench hub get <preset>` (`cli.py:3680`).

### Newest version

* npm `docxodus` → **15.0.0** (registry metadata in `~/.npm/_cacache`, published 2026-11-01). npm `@docxodus/export` → **15.0.0** (same date). The pnpm store already holds them offline: `~/Library/pnpm/store/v11/links/@/docxodus/{12.6.5,14.2.0,15.0.0}` and `~/Library/pnpm/store/v11/links/@docxodus/export/15.0.0`.
* The C#/NuGet line: the local `~/temp/T/ooxmlsdk/Docxodus` checkout’s tags stop at **v12.6.5** (v12.6.0…v12.6.5). A 15.0.0 NuGet package is not in `~/.nuget/packages` (no `docxodus` entry at all) and would need a network fetch of the upstream repo.

### Ready-to-run, newest version

```sh
# DOCX→PDF  (15.0.0)
# 1) bump src/neurotic_docx_bench/utils/docxodus-export/package.json to
#    "@docxodus/export": "15.0.0", "docxodus": "15.0.0"
cd src/neurotic_docx_bench/utils/docxodus-export && npm ci && cd -
F=(); while read -r l; do F+=(--files-list "$l"); done < my_docs.txt   # corpus docx paths
uv run bench docx-to-pdf --tool docxodus --origin list "${F[@]}" \
  --json results/docx_to_pdf_docxodus_15.0.0.json \
  --work-dir out/docxodus_15.0.0 --jobs 8 --convert-workers 8

# Redline (npm/WASM lane, 15.0.0)
pnpm add --exact docxodus@15.0.0 --workspace-root
node --import tsx scripts/generate-native-redlines.ts --method=docxodus \
  --out out/docxodus-15.0.0/docx --run-dir out/docxodus-15.0.0 --tool=docxodus-15.0.0 \
  --manifest my_pairs.csv --source-dir corpus/word

# Redline (C# lane, 15.0.0) — needs the upstream source at 15.0.0 + a rebuild
PATH="$HOME/.dotnet:$PATH" dotnet build \
  src/neurotic_docx_bench/utils/docxodus/docxodus-csharp-inproc/DocxodusInproc.csproj -c Release
WORKER_REPLY_TIMEOUT_MS=120000 node --import tsx scripts/generate-native-redlines.ts \
  --method docxodus-csharp-inproc \
  --dist src/neurotic_docx_bench/utils/docxodus/docxodus-csharp-inproc/bin/Release/net10.0 \
  --tool docxodus-15.0.0 --manifest my_pairs.csv --source-dir corpus/word \
  --out out/docxodus-15.0.0/docx --run-dir out/docxodus-15.0.0
```

### Blockers

* `dotnet` is **not on PATH** (`command -v dotnet` → nothing) but SDK **10.0.403** is installed at `~/.dotnet/dotnet` (runtimes 8.0.33, 10.0.14). Usable via `PATH="$HOME/.dotnet:$PATH"`.
* `src/neurotic_docx_bench/utils/docxodus-export/node_modules` is **not installed** in either checkout → `npm ci` there (network) is required before the DOCX→PDF lane can run at all.
* Docxodus 15.0.0’s C# sources are not on disk; only the npm/WASM tarball is cached locally.

## 4. superdoc

### Which package the bench uses (and the rename)

The bench uses **three** superdoc packages, pinned independently (`bench.yaml:90-94`, enforced by `tests/test_superdoc_pins.py`):

| bench.yaml run | Package | Pin today | Role |
|---|---|---|---|
| `superdoc` (`bench.yaml:287`) | **PyPI `superdoc-sdk`** (`python_package:`) | `superdoc-sdk==2.16.0` | **The redline generator** (scored lane) |
| `superdoc-ts` (`bench.yaml:350`) | npm `@superdoc-dev/sdk` (`package:`) | `1.21.3` | TS redline lane (`generate-native-redlines.ts:453`) |
| `superdoc-playwright-*` (`bench.yaml:377/392/401`) | npm `superdoc` (the browser **editor**) | `2.18.0` | `visual_*` benchmarks only — it renders in a browser and **exports no PDF** |

**Was it renamed? Yes — on npm.** From the cached npm registry metadata (`~/.npm/_cacache`):

| Package | `latest` dist-tag | newest published | note |
|---|---|---|---|
| `superdoc` (editor/engine) | **2.28.0** | 2.28.0 | bench pins 2.18.0 → bumpable; not a PDF exporter |
| `@superdoc-dev/sdk` | **1.21.3** | **1.23.2** (dist-tag `legacy`) | bench pins 1.21.3; 1.22.0/1.22.1/1.23.0/1.23.2 published after it |
| `@superdoc/sdk` | **2.25.0** | 2.25.0 | **the current Node SDK** — versioned in lockstep with the Python `superdoc-sdk` (2.25.0). The npm scope moved from `@superdoc-dev/sdk` (1.x) to `@superdoc/sdk` (2.x) |
| `@harbour-enterprises/superdoc` | 1.47.0 (`legacy` tag) | 1.47.0 | the old scope |

**On PyPI the name did not change**: `superdoc-sdk` still exists; the uv simple-index cache (`~/.cache/uv/simple-v26/pypi/superdoc-sdk.rkyv`) tops out at **2.25.0**. So for the scored `superdoc` lane the newest is **`superdoc-sdk==2.25.0`** (2.16.0 → 2.25.0), and the npm `@superdoc-dev/sdk` lane’s newest is **1.23.2**.

### How the bench runs it today

* Entry point: `src/neurotic_docx_bench/superdoc_gen.py:181 main()`; the compare itself is `generate_one` `:92` (`client.open` → `diff.capture` → `diff.compare` → `diff.apply {changeMode:'tracked'}` → `save`), 120 s per-pair timeout, session ids carrying the pid.
* Exact command (bench.yaml `generate:`): `uv run python -m neurotic_docx_bench.superdoc_gen --out=$RUN_DIR/docx --tool=superdoc` (+ driver-appended `--manifest=… --source-dir=corpus/word`).
* Standalone, on an explicit list: `uv run python -m neurotic_docx_bench.superdoc_gen --out out/superdoc/docx --tool=superdoc --manifest my_pairs.csv --source-dir corpus/word [--limit N] [--force]`.
* Outputs: `<out>/docx/<key>_<tool>.docx`; failures in `generate_failures.json` (beside `<out>/docx`), timings in `generate_timings.json`.
* Scoring: `uv run bench run --only superdoc --rerun` → `results/bench.jsonl` → `uv run bench report` → `results/scores.json`. (Legacy lane: `measure.py superdoc` → `scores_superdoc.json`.)
* Version recording: `tool_updater.installed_python_version("superdoc-sdk")` (importlib.metadata) → `tool_version`; `results/redlines_0929_full/versions.json` → `"superdoc": "superdoc-sdk 2.16.0"`.
* HF: `results/redlines_0929_full/hub_upload.py`; the 2.20.0 fresh-cohort artifacts went under `outputs/wordcraft-20261014` (preset `wordcraft-20261014`).

### Ready-to-run, newest version

```sh
uv venv --python 3.14 .venv-superdoc-2.25.0
uv pip install --python .venv-superdoc-2.25.0/bin/python "superdoc-sdk==2.25.0"
PYTHONPATH=src .venv-superdoc-2.25.0/bin/python -m neurotic_docx_bench.superdoc_gen \
  --out out/superdoc-2.25.0/docx --tool=superdoc-2.25.0 \
  --manifest my_pairs.csv --source-dir corpus/word
# then, to score it in the canonical bench:
#   edit bench.yaml: python_package: "superdoc-sdk==2.25.0"
#   uv run bench run --only superdoc --rerun
```

### Blockers

* `superdoc-sdk==2.25.0` is **not in the local uv cache** (only 2.16.0 is) → needs network. The fresh-cohort precedent is an isolated venv: `results/wordcraft_20261014_fresh/bin/superdoc-venv` with `superdoc-sdk==2.20.0` (`add_tools.py`).
* Coverage collapse, not just a score shift: the 2.16.0 lane refused most Word-truth pairs (“tracked header/footer slot or physical topology changes are not supported”, “tracked shared definition replacement is not supported”, “numbering replay is unsafe” — `results/redlines_0929_full/RUN.md`), 192/2611 pairs redlined; the 2.20.0 lane scored **36/400** on the fresh cohort. A version bump will mostly move *coverage*.
* `superdoc` (npm, the editor) 2.28.0 is only relevant to the `visual_*` benchmarks; it cannot produce a PDF for the DOCX→PDF track.

## 5. Apryse SDK

### How the bench runs it today (branch `bench/apryse-12.2.1`, PRs #108/#109)

* **Install:** `apryse-sdk` is a **PyPI wheel installed in the repo's `.venv`**. Arthur's `neurotic_docx_bench/pyproject.toml` dev extra carries `"apryse-sdk>=12.2.1"` (an **uncommitted local change**); the `ndb-apryse` branch's `pyproject.toml` has **no** apryse dependency (`git diff origin/main...HEAD -- pyproject.toml` is empty), so `uv sync` on that branch alone will **not** install it. Installed dist-info: `apryse_sdk 12.2.1` (“Apryse SDK Wrapper”).
* **License key env var: `APRYSE_API_KEY`** — read from the environment first, else the repo’s `.env` (`src/neurotic_docx_bench/apryse_gen.py:39 api_key()`). A key starting with `demo:` makes Apryse stamp its logo on every page (`is_demo()`, and `version()` says so). The value was **not** printed.
* **Newest available offline from the installed metadata: 12.2.1.** The uv simple-index cache (`~/.cache/uv/simple-v26/index/8aad97b46579249a/apryse-sdk.rkyv`) lists 11.9.0 … **12.2.1** and nothing newer; the installed wheel is 12.2.1. No newer Apryse release is discoverable offline.
* **Entry points:**
  * DOCX→PDF — `apryse_gen.py:78 convert()` → `a.Convert.OfficeToPDF(doc, src, opts)` with `opts.SetDisplayChangeTracking(True)` and `opts.SetDisplayComments(True)`. The bench reaches it through the shim **`src/neurotic_docx_bench/utils/apryse/apryse-convert SRC DEST`** (bash → `$root/.venv/bin/python -m neurotic_docx_bench.apryse_gen convert "$@"`; `--version` prints the version string). Registered in `docx_to_pdf.py` (ndb-apryse): `APRYSE_CONVERTER` `:62`, `_TOOL_BINARIES["apryse"]` `:93`, `convert_command` branch `:363` → `[binary, src, dest]`, `resolve_tool_binary` branch `:396`, version `:661`.
  * Redline — `apryse_gen.py:97 compare()` → `a.DOCXCompare.Compare(base, next, out, a.DOCXCompareOptions())`; batch driver `apryse_gen.py:133 batch()` (one child process per pair, 180 s timeout) and `main()` `:170` with `batch --manifest M --source-dir S --out DIR --tool T --jobs N --timeout S --limit N --force`.
* **Exact command lines used last time** (`~/temp/T/ndb-apryse/out/apryse/run_d2p_full.sh`, `run_d2p_stamped.sh`):
  ```sh
  F=(); while read -r l; do F+=(--files-list "$l"); done < out/apryse/v1_list_abs.txt
  BENCH_DEVICE=mps PYTHONPATH=src .venv/bin/python -c "from neurotic_docx_bench.cli import app; app()" docx-to-pdf \
    --config out/apryse/bench.apryse.yaml --tool apryse --origin list $F \
    --json results/docx_to_pdf_v1_apryse/full.json --work-dir out/apryse/full_d2p --jobs 8 --convert-workers 8
  ```
  and the stamp-matched re-score:
  ```sh
  bench docx-to-pdf --config out/apryse/bench.stamped.yaml --tool apryse --origin list $F --score-only \
    --location-to-score out/apryse/stamped_cands \
    --tool-version "apryse-sdk 12.2.1 (PDFNet 12.2.0-079a1c0268, demo key: output watermarked); oracle stamp-matched" \
    --no-store --json results/docx_to_pdf_v1_apryse/stamp_matched.json --work-dir out/apryse/stamped_work --jobs 8
  ```
  Redline batch (RUN.md): `python -m neurotic_docx_bench.apryse_gen batch --manifest results/redlines_0929_full/gen_pairs.csv --source-dir corpus/word --out results/redlines_0929_full/apryse/docx --tool apryse` → 2599 redlines, 12 failures.
  Word export: `scripts/word_pdf.py --src results/redlines_0929_full/apryse/docx --out results/redlines_0929_full/apryse/pdf_by_word --crash-limit 1 --nudge-after 5 --nudge-wait 1 --empty-limit 10` → 1816 of 2599 exported (783 refusals in `apryse/word_refused.csv`).
* **Where outputs go:** DOCX→PDF candidates → `out/apryse/full_d2p/apryse/candidate/<state>__<stem>.pdf`; report → `results/docx_to_pdf_v1_apryse/full.json` (+ `stamp_matched.json`). Redlines → `results/redlines_0929_full/apryse/docx/`; Word exports → `…/apryse/pdf_by_word/` (+ `manifest.jsonl`).
* **Scoring command / scores file:** `bench docx-to-pdf` (above) → `results/docx_to_pdf_v1_apryse/full.json` → one line in `results/converters.jsonl` (tool `apryse`, track `corpus/word:list`, mean 75.4569 / median 79.3426 over 2925 docs). Redline lane: `uv run python results/redlines_0929_full/measure.py apryse` → `scores_apryse.json`; stamp-matched → `apryse/measure_stamp.py` → `scores_apryse_stamp_matched.json`.
* **How the version is recorded:** `apryse_gen.py:58 version()` → `"apryse-sdk 12.2.1 (PDFNet 12.2.0-079a1c0268, demo key: output watermarked)"`; in `results/redlines_0929_full/versions.json` under `"apryse"`; in `results/converters.jsonl` under `version`.
* **HF:** `results/docx_to_pdf_v1_apryse/hub_upload.py --pdf-dir <dir> [--dry-run]` → `outputs/docx_to_pdf_v1_apryse/` (`pdf/`, `full.json`, `stamp_matched.json`, `MANIFEST.sha256.json`), uploaded as one `upload_folder` commit with `parent_commit` = the head read before staging, then `hub.verify_uploaded`. The redline outputs went up with `results/redlines_0929_full/hub_upload.py`. Download preset: `uv run bench hub get docx-to-pdf-v1-apryse` (`src/neurotic_docx_bench/hub_get.py:170`).

### Blockers

* **Licence:** the only key present is a **demo key** → Apryse stamps a translucent blue logo image in every header. Every score made with it carries that stamp (the “stamp-matched” normalisation exists precisely for this). A production key is required for a clean number.
* `apryse-sdk` is not a dependency of the `ndb-apryse` branch → `uv sync` will not install it; `uv pip install apryse-sdk==12.2.1` is needed (wheel is in the local uv cache).
* Apryse’s own redlines are frequently unreadable by Word: Word refused **745 of 2599** and crashed on 38 (`apryse/word_refused.csv`). That is a vendor limitation, not a pipeline bug — but it means the ITT denominator is mostly zeros.
* The Word export step needs Word + AppleScript automation and hours of machine time (2599 docs, 1816 PDFs).

## 6. LibreOffice / soffice

### How the bench runs it today

* Installed: `/opt/homebrew/bin/soffice` and `/Applications/LibreOffice.app/Contents/MacOS/soffice` → **`LibreOffice 26.8.2.2 9dea406ab7695df6325b11b0e19fdca8db4b7276`**. Scored: **26.8.0.3** (store lines carry `LibreOffice 26.8.0.3 bce0998afefdbc355585ca324285661a2170ba77`; a later line has `26.8.1.1 bf6ef266…`).
* Two distinct roles:
  1. **The bench’s own renderer** (`render/soffice.py`) — `find_soffice()` `:35` (order: `$SOFFICE` → `shutil.which("soffice")` → the macOS app path); `convert_one()` `:71` → `soffice -env:UserInstallation=file://<tmp profile> --headless --norestore --nofirststartwizard --convert-to pdf --outdir <dir> <docx>`, then verify the PDF exists (soffice can exit 0 without writing).
  2. **A converter-under-test on the DOCX→PDF track** — `bench docx-to-pdf --tool soffice` is **refused** by the CLI except with `--score-only` (`cli.py:918-926`: the allowed set is `WORD_PDF_TOOLS ∪ {jubarte}`, and `soffice` only when `score_only`). The documented two-step path:
     ```sh
     # convert
     uv run python scripts/convert_candidates.py --engine soffice \
       --out results/soffice_26.8.2.2_work [--files-list list.txt]
     # → results/soffice_26.8.2.2_work/candidate/<state>__<stem>.pdf
     # score
     uv run bench docx-to-pdf --tool soffice --score-only \
       --location-to-score results/soffice_26.8.2.2_work/candidate \
       --tool-version "LibreOffice 26.8.2.2 9dea406ab7695df6325b11b0e19fdca8db4b7276" \
       --json results/docx_to_pdf_soffice_26.8.2.2.json
     ```
     The older scored lane used the same shape: `scripts/build_converter_reports.py --name soffice_26.8.0.3 --tool soffice --version "LibreOffice 26.8.0.3 bce0998…" --candidate results/soffice_26.8.0.3_work/candidate --docxide-ckpt … --convert-log … --engine soffice` → `results/docx_to_pdf_soffice_26.8.0.3_corpus-all.json` (track `corpus/word:all`, n=6427, mean 61.9488 / median 63.4334, 68 generate failures), then `uv run bench ingest-converter-reports`.
* **Outputs:** candidates in `<work-dir>/candidate/`; the report JSON at `--json`; the store line appended to `results/converters.jsonl` (tool `soffice`).
* **Scoring command / scores file:** `bench docx-to-pdf` (pixel lens, 144 DPI) and `bench docxide-metrics` (Jaccard/text-boundary, 150 DPI); both write a report JSON and a `results/converters.jsonl` line. The headline table comes from `uv run bench report` → `results/scores.json`.
* **Version recording:** the renderer fingerprint (`canary.py:43 parse_soffice_version`, `:48 current_soffice_version`) and, for the converter lane, the explicit `--tool-version` string. `results/converters.jsonl` keeps it under `version`.
* **HF:** `uv run bench results upload` (whole results tree) or the lane `hub_upload.py` scripts; the fresh cohort went up under preset `wordcraft-20261014`.

### Ready-to-run, 26.8.2.2

```sh
# full corpus
uv run python scripts/convert_candidates.py --engine soffice --out results/soffice_26.8.2.2_work
uv run bench docx-to-pdf --tool soffice --score-only \
  --location-to-score results/soffice_26.8.2.2_work/candidate \
  --tool-version "LibreOffice 26.8.2.2 9dea406ab7695df6325b11b0e19fdca8db4b7276" \
  --json results/docx_to_pdf_soffice_26.8.2.2.json
uv run bench docxide-metrics --tool soffice --score-only \
  --location-to-score results/soffice_26.8.2.2_work/candidate \
  --tool-version "LibreOffice 26.8.2.2 9dea406ab7695df6325b11b0e19fdca8db4b7276" \
  --json results/docxide_metrics_soffice_26.8.2.2.json
```

### Blockers

* None for the tool (installed and working). But the **renderer canary** will trip: `bench run --canary` verifies the LibreOffice fingerprint and exits 2 on drift (`canary.py:128-144`) — a LO bump requires re-freezing the expectation.
* Conversion is slow (~1.9 s/doc median on the fresh cohort) and the corpus is 6427 docs; the 26.8.0.3 run produced 68 hard failures (“soffice killed by signal 6”, `impl_store failed`) that a new build may or may not still produce.
* LO is also the *fallback renderer* for redline candidates on machines without Word (`render/auto.py`); a LO bump silently moves every redline score on such a machine.

## 7. docxide-pdf

### How the bench runs it today

* Scored lanes: **0.17.1** (store lines tool `docxide-pdf`, version `docxide-pdf v0.17.1`, reports `results/docx_to_pdf_docxide_0.17.1{,_corpus-all}.json` + `results/docxide_metrics_docxide-pdf_0.17.1{,_corpus-all}.json`) and a newer **0.18.4** lane (pin `docxide-pdf 0.18.4@503ea7e085ae9acd16a4f263c7802325cad254eedeff4cb2509e88714019e00b`, 400-doc Wordcraft cohort).
* **How it was built:** a **cargo** crate. `cargo install --locked docxide-pdf --version 0.18.4` into a per-tool root — `results/wordcraft_20261014_fresh/bin/cargo/docxide-pdf-0.18.4/bin/docxide-pdf` — recorded in `tools.json` with its sha256 (which *is* the pin hash). The 0.17.1 lane is the plain `cargo install`ed binary at `~/.cargo/bin/docxide-pdf` (`cargo install --list` → `docxide-pdf v0.17.1`). The crate `docxide-pdf-0.18.4.crate` is already in `~/.cargo/registry/cache/`. The binary **has no `--version` flag** (it errors: `unexpected argument '--version' found`) — the version comes from `docx_to_pdf.py:616 _version_from_cargo_install_list()` (parses `cargo install --list`) or from the tool’s own JSON.
* Upstream: `sverrejb/docxide-pdf` has **no GitHub releases**; crates.io tops out at **0.18.4** (the cargo index cache lists 0.17.0 … 0.18.4, nothing newer). The local source checkout `~/temp/T/docxide-pdf` (referenced by `scripts/build-renderer-corpus.py:161` as `ROOT.parent / "docxide-pdf"`) is currently **absent** — a dangling symlink target from `~/temp/T/docxide_compare/docxide-pdf`.
* Entry point: `docx_to_pdf.py:328 convert_command()` → docxide-pdf branch: `[binary, src, dest]` (positional). Binary resolution `resolve_tool_binary` `:375` → `shutil.which("docxide-pdf")` then `~/.cargo/bin/docxide-pdf` (`_TOOL_BINARIES` `:88`).
* Second lens: `bench docxide-metrics --tool docxide-pdf` — the metrics are a **Python port** of docxide-pdf’s own `tests/common/` (`src/neurotic_docx_bench/docxide_metrics.py` + `page_metrics.py`; the Rust reference crate lives in `src/neurotic_docx_bench/utils/docxide-metrics/`, held to upstream’s frozen numbers by `tests/test_page_metrics.py`).
* Outputs: candidates → `<work-dir>/docxide-pdf/candidate/<state>__<stem>.pdf`; report → `--json`; store line → `results/converters.jsonl`.

### Ready-to-run, 0.18.4

```sh
cargo install --locked --offline docxide-pdf --version 0.18.4 --root out/cargo/docxide-pdf-0.18.4
F=(); while read -r l; do F+=(--files-list "$l"); done < my_docs.txt
uv run bench docx-to-pdf --tool docxide-pdf --origin list "${F[@]}" \
  --json results/docx_to_pdf_docxide_0.18.4.json --work-dir out/docxide_0.18.4 --jobs 8 --convert-workers 8
uv run bench docxide-metrics --tool docxide-pdf --origin list "${F[@]}" \
  --json results/docxide_metrics_docxide_0.18.4.json --work-dir out/docxide_metrics_0.18.4
```

### Blockers

* **None material.** The crate is in the local cargo cache (`cargo install --offline` works), and 0.18.4 is already the newest on crates.io — i.e. **the newest version is already the one that was scored** on the fresh cohort. To go past 0.18.4 you must build from upstream `main` (`0d97e9b0`), which needs the source tree that is currently missing locally.
* The `--version` flag is absent, so version capture depends on `cargo install --list` (or the `dist:`-style content-hash pin used for the 0.18.4 lane).

## 8. Toolchain check (`command -v` / `--version` only — nothing installed)

| Tool | Result |
|---|---|
| `dotnet` | **not on PATH**; SDK at `~/.dotnet/dotnet` → `10.0.403`; runtimes 8.0.33 / 10.0.14 (AspNetCore 10.0.14) |
| `node` | v26.13.0 (`~/.nvm/versions/node/v26.13.0/bin/node`) — repo wants `>=26`, `.nvmrc` = 26 |
| `pnpm` | 12.16.1 (`~/Library/pnpm/bin/pnpm`); repo pins `packageManager: pnpm@12.11.2` |
| `npm` | 11.20.0 (used only for the `utils/docxodus-export` folder, which is **not** a pnpm workspace member) |
| `python3` | 3.15.2 system; the bench venv is 3.14 (`uv 0.13.0`) |
| `soffice` | `/opt/homebrew/bin/soffice` → `LibreOffice 26.8.2.2 9dea406ab7695df6325b11b0e19fdca8db4b7276` |
| `cargo` | present (`cargo install --list` → `docxide-pdf v0.17.1`, `doxx v0.1.4`, …) |
| `docxide-pdf` | `~/.cargo/bin/docxide-pdf` (v0.17.1); **no `--version` flag** |
| `apryse-sdk` | 12.2.1 installed in the repo `.venv`; `APRYSE_API_KEY` present in the repo `.env` (value not printed) |
| Microsoft Word | present (`/Applications/Microsoft Word.app`) — required for the Word export step of the redline lane and for the Word oracle |
| `node_modules` | installed in Arthur’s checkout only (`docxodus@12.6.5`, `superdoc@2.18.0`, `@superdoc-dev/sdk@1.21.3`); **absent** in `~/bench/.work/ndb-main` and in `utils/docxodus-export/` |

Offline version discovery (no network used):
* npm: `~/.npm/_cacache` registry metadata → `docxodus` 15.0.0, `@docxodus/export` 15.0.0, `superdoc` 2.28.0, `@superdoc-dev/sdk` 1.23.2, `@superdoc/sdk` 2.25.0, `@harbour-enterprises/superdoc` 1.47.0.
* pnpm store: `docxodus@15.0.0` and `@docxodus/export@15.0.0` tarballs already local.
* PyPI: uv simple-index cache → `superdoc-sdk` ≤ **2.25.0**, `apryse-sdk` ≤ **12.2.1**.
* crates.io: cargo index cache → `docxide-pdf` ≤ **0.18.4**.
* NuGet: `~/.nuget/packages` has **no `docxodus`** entry → no offline NuGet version discovery; the local JSv4/Docxodus git checkout stops at tag **v12.6.5**.

## 9. Vendor × task summary

| Vendor | Task | Scored today | Newest known (offline) | Ready? | Blocker |
|---|---|---|---|---|---|
| docxodus | redline (npm/WASM) | `docxodus@12.6.5` | **15.0.0** (npm; tarball in pnpm store) | ✅ yes | bump the pin in `bench.yaml` + `utils/docxodus/package.json` + root `package.json` together (bench.yaml:123-135 says so) |
| docxodus | redline (C#) | `Docxodus 12.6.5 (C#)` (git `19b40bd7`, tag v12.6.5) | **15.0.0** (not on disk) | ⚠️ needs source | upstream fetch; `dotnet` not on PATH (use `~/.dotnet/dotnet`) |
| docxodus | DOCX→PDF | `@docxodus/export 14.2.0 (docxodus 14.2.0)` | **15.0.0** both packages | ⚠️ needs `npm ci` | `utils/docxodus-export/node_modules` not installed (network) |
| superdoc (PyPI `superdoc-sdk`) | redline | 2.16.0 | **2.25.0** | ⚠️ needs install | wheel not cached → network; engine refuses most Word-truth pairs (coverage, not score) |
| superdoc-ts (npm `@superdoc-dev/sdk`) | redline | 1.21.3 | **1.23.2** (`legacy` tag) | ⚠️ needs install | npm install; note the scope renamed to `@superdoc/sdk` 2.25.0 |
| superdoc (npm editor) | DOCX→PDF / visual | 2.18.0 | **2.28.0** | ❌ n/a | browser editor — exports no PDF; `visual_*` only |
| Apryse SDK | DOCX→PDF | 12.2.1 (demo key, watermarked) | **12.2.1** (no newer offline) | ⚠️ licence | production `APRYSE_API_KEY`; demo key stamps every page; `apryse-sdk` not in the branch’s pyproject |
| Apryse SDK | redline | 12.2.1 | **12.2.1** | ⚠️ licence + Word | Word refused 745/2599 of its redlines |
| LibreOffice / soffice | DOCX→PDF | 26.8.0.3 (also 26.8.1.1) | **26.8.2.2** installed | ✅ yes | renderer canary fingerprint must be re-frozen; `--tool soffice` needs `--score-only` |
| LibreOffice / soffice | redline | (renderer only) | 26.8.2.2 | ✅ yes | none (it is the fallback renderer) |
| docxide-pdf | DOCX→PDF | 0.17.1 (and a 0.18.4 cohort lane) | **0.18.4** (crates.io max) | ✅ yes | none — crate cached; beyond 0.18.4 needs upstream `main` source (missing locally) |

DONE
