# Survey B — neurotic_docx_bench (ndb) · gh-pages pipeline, per-release flow, corpus overlap, feature parity, work at risk, newest runs

Read-only survey. Nothing was modified, committed, pushed, uploaded or deleted. Only
`/tmp/survey-b.md` and `/tmp/survey-b/*.py` were written.

Measured on 2026-11-02 (local clock) against:
`~/temp/T/neurotic_docx_bench` (Arthur's checkout, branch `results/v1-docx-to-pdf-89`),
`~/temp/T/ndb-105` (detached `eaf92271`), `~/temp/T/ndb-apryse` (`bench/apryse-12.2.1`),
`~/temp/T/ndb-apryse-keep` (not a git repo), `~/bench/.work/ndb-main` (clean `origin/main`),
`~/bench/.work/ndb-cases` (`feat/cases-speed`), `~/bench/.work/ndb-viewer-cases` (`feat/viewer-cases`),
`~/temp/T/1_jubarte-redlines/jubarte-app/jubarte-site` (jubarte.pro),
`~/temp/T/briefs_agreements/benchmarks-law/public/neurotic-docx-bench` (benchmarks.law copy),
and the read-only HF dataset `arthrod/neurotic_docx_bench` (+ `arthrod/neurotic_docx_bench-fixtures`).

---

## 0. Orientation — what exists where

| Artifact | Path | State |
|---|---|---|
| gh-pages worktree | `~/temp/T/neurotic_docx_bench/site` | worktree of branch `gh-pages` @ `610bc447`, 7 commits on top of `origin/main` |
| gh-pages build scripts | `~/temp/T/neurotic_docx_bench/site/_build/` | tracked on `gh-pages`: `build_site.py`, `add_jubarte.py`, `add_pymupdf.py`, `score_corpus.py`, `redlines/*`, `viewer_features.py`, `viewer_theme.py`, `speed/*`, `site_1011/`, `site_1028/`, `site_1029/`, `site_1030/` |
| New release contract | `~/bench/src/bench_ops/models.py` | `Case`/`CaseShard`/`CasesIndex`/`ShardRef`; `GROUP_KINDS` at `:288`; `shard_path()` at `:315` |
| Release case bundle (adapter snapshot) | `~/bench/.work/ndb-cases/results/release/cases/` | 21 shards + `index.json` + `legacy_cases.jsonl` (22.9 MB) |
| New viewer | `~/bench/.work/ndb-viewer-cases/packages/bench-viewer/src/` | `cases.ts` (1978 L), `speed.ts` (399 L), `leaderboard.ts` (181 L), `render.ts`, `parse.ts` |
| jubarte.pro | `~/temp/T/1_jubarte-redlines/jubarte-app/jubarte-site` | `public/data/cases-convert.json` (860), `cases-redline.json` (234), `home.json`; `scripts/site_fixtures.py`; `site/pages/cases.ts` + `site/static/js/cases.js` |
| benchmarks.law | `~/temp/T/briefs_agreements/benchmarks-law/public/neurotic-docx-bench/` | `index.html` (41 KB) + `data.json` (13.6 MB, 5193 cases); `redlines/{index.html,data.json}` (2229 cases); `editors/index.html`; `home/index.html`; `speed/index.html` |

---

## 1. The gh-pages build pipeline — the 21 published case groups

### 1.1 The pipeline, script by script

| Script | file:line | What it does | Inputs | Outputs |
|---|---|---|---|---|
| `add_jubarte.py` | `site/_build/add_jubarte.py:30,35,36,89` | Converts the 208 **docxide-pdf** fixtures with jubarte 0.9.3 (`--revisions word --compress`) and scores them with docxide's own `page-metrics` | `docxide-pdf/tests/fixtures/{cases,scraped,new,samples}/<case>/{input.docx,reference.pdf}` (clone, now gone) | `local_scores.json`; WebP into `site/<group>/<case>/jubarte/` |
| `add_pymupdf.py` | `site/_build/add_pymupdf.py:38-43` | PyMuPDF Pro 1.28.2 **unlicensed → first 3 pages only**; runs only where Word's `reference.pdf` has ≤ 3 pages | same fixtures | `local_pymupdf.json`, `work/<g>/<c>/pymupdf.pdf` |
| `score_corpus.py` | `site/_build/score_corpus.py:24-33` | Scores **every** `corpus/word` document, every engine vs Word | `results/jubarte_0.9.3_docx_to_pdf_work/{oracle,jubarte/candidate}`, `results/docxide_0.17.1_work/candidate`, `results/soffice_26.8.0.3_work/candidate`, `results/pymupdf-pro_1.28.2_work/candidate` | `corpus_scores.json` (3554 rows) |
| `build_site.py` | `site/_build/build_site.py:47-53,141-158,196-206` | Picks the **600-doc** stratified corpus sample (`SAMPLE_N=600`, `SEED=20260928`), converts the 3 "extra" engines (minipdf, rdocx, office2pdf) locally, rasterises, writes `index.html` | `corpus_scores.json`, `corpus_extra_scores.json`, `sample.json`, `local_scores.json`, `local_pymupdf.json`, the published docxide `site/index.html` | `jubarte_site/index.html` (208 fixture cases + 600 corpus cases) |
| `redlines/build_redlines_site.py` | `site/_build/redlines/build_redlines_site.py:48,67-70` | Builds the `/redlines/` page from `results/redlines_0929_full` (2611 pairs) + accepted/rejected selections; `REDLINE_N=30`/state, `ACCEPTED_N=20`/state, `SEED=20260928` | `results/redlines_0929_full/{gen_pairs.csv,pool_pairs.csv,scores_*.json}`, `results/compare_regen/out`, HF `outputs/redlines_0929_full/` | `redlines_site/index.html`, `redlines/cases.json` |
| `speed/build_speed_site.py` + `publish_speed.sh` | `site/_build/speed/` | Renders `results/speed_10k/{redlines,pdf}/SUMMARY.md` (headings/paragraphs/lists/pipe tables only) | `results/speed_10k/*/SUMMARY.md` | `speed/index.html` |
| `site_1011/` | `site/_build/site_1011/README.md` | The 2026-10-11 renewal: `inventory.py`+`sample.py` (seed 20261011) → `sample_docx_to_pdf.csv` (**800** docs) and `sample_redlines.csv` (**1000** pairs); `run_tools.py`/`run_docxodus.py`; Word compare/PDF; `replace.py` (57 pairs Word refused); `process.py`; `upload_r2.py`/`upload_worker.py`; `patch_site.py` | bench `corpus/word`, HF `arthrod/neurotic_docx_bench-fixtures` | `cases_*.json`; R2 `d2p/`, `redlines/` |
| `site_1028/` | `site/_build/site_1028/README.md` | Phase 1 (2026-10-28): the **v1** cohort (2 925 cases), the **Wordcraft 2026-10-14** cohort (400 compare + 400 convert); upstream page-metrics `a72d91bc…`; `bench fixtures get v1` pinned snapshot `984e64d8…`; the 800-pair accept/reject split was **blocked** (source gap documented in the README) | `ndb-v1/out/dx89/{full_work,soffice_work}` (now missing), HF fixture snapshot | `build/{d2p_v1,redlines_0929ar,wordcraft1014}/`; R2 `d2p_v1/`, `wordcraft1014/` |
| `site_1029/` | `site/_build/site_1029/README.md` | Phase 3a: re-scored **1 276** cases at **jubarte 0.14.0** (76 fixtures + 800 older corpus + 400 Wordcraft convert). Pixel = `pipeline.score_pdf_pair` at **144 DPI** (bench.yaml); J/SSIM/TB from upstream page-metrics at **150 DPI** | phase-1 artifacts in `~/temp/T/renew_1028` (3.0 GB, present) | `build/renew1028/d2p/`; R2 `renew1028/` |
| `site_1030/` | `site/_build/site_1030/README.md` | Phase 3b: redlines renewal — 1 400 selected compare cases re-run (jubarte 0.14.0, docxodus 14.2.0, superdoc-sdk 2.16.0) + **800 appended** accept/reject pairs (`ar/accept_all`, `ar/reject_all`) | `~/temp/T/renew_1028/MANIFEST.sha256.txt` + phase-1 `bench.jsonl` | `build/renew1030/{redlines,ar}/`; R2 `renew1030/` |

### 1.2 Rasterisation — library, DPI, page cap, format

| Build | Raster | Scoring raster | Shown pages | Encoding | PDF |
|---|---|---|---|---|---|
| `build_site.py` / `add_jubarte.py` | **mutool draw -F png -r 150** (`build_site.py:152`, `add_jubarte.py:47`) | 150 DPI (`add_jubarte.py:35`) | **first 3 pages** (`SHOW_PAGES = 3`, `build_site.py:47`) | Pillow: 150→100 DPI (×2/3, LANCZOS) → `quantize(colors=16, MEDIANCUT)` → **lossless WebP, method 4** (`build_site.py:141-147`) | none |
| `site_1011/process.py` | mutool, 150 DPI scoring (`:47`), **180 DPI** shown pages (`:107`) | 150 DPI, every page | 3 for docxide fixtures, "the same page numbers for every engine" for the corpus | 180→120 DPI (×2/3) → 16 colours → lossless WebP (`:67-72`) | `mutool clean -gggz` → `pages.pdf` (first 3) and `all.pdf` (whole document) |
| `site_1028/process.py` | mutool, 150 DPI every page (`:33`), shown pages at **180 DPI** | 150 DPI + upstream `page-metrics` `a72d91bc…` | **first 3 Word pages**, capped at Word's own page count | ×2/3 LANCZOS → 16-colour median-cut → lossless WebP method 4 (`:54-60`) | `mutool clean -gggz` → `pages.pdf` (`:102-103`) |
| `site_1029` / `site_1030` | identical to 1028 (`reference_process_1028.py` is a byte copy) | 150 DPI upstream + 144 DPI pixel | same | same | same |

**Uniform answer: mutool for rasterisation, WebP (16-colour, lossless) for the page strips, `mutool clean -gggz` for the shown-page PDF, page cap 3 (except the v1 cohort's per-case `shown` list).**

### 1.3 R2 prefixes (bucket `neurotic-docx-bench-pages`, base `https://pub-92c80eda1059428394970066c8500eda.r2.dev/`)

Measured from the published pages' own URLs:

| Prefix | Producer | Groups it serves |
|---|---|---|
| `gh-pages-tree/docxide/` | gh-pages commit `80cd187e` ("one commit, every page image on R2", was 1.07 GB of history) | `cases`, `new`, `samples`, `scraped` |
| `gh-pages-tree/corpus/` | same | `corpus/*` (the 600-doc `build_site.py` sample, plus the 2026-10-11 renewal's `d2p/`) |
| `gh-pages-tree/redlines/` | same | `accepted/*`, `rejected/*`, and the pre-renewal `redlines/*` rows |
| `d2p/cases/`, `d2p/corpus/` | `site_1011` | `cases` (libreoffice), `corpus/*` |
| `redlines/redlines/` | `site_1011` | `redlines/*` |
| `d2p_v1/v1/` | `site_1028` | `v1/*` (all four states) |
| `wordcraft1014/wordcraft1014/` | `site_1028` | `wordcraft1014/convert`, `wordcraft1014/compare` |
| `renew1028/d2p/` | `site_1029` (jubarte 0.14.0) | `cases`, `corpus/*`, `wordcraft1014/convert` (jubarte lane only) |
| `renew1030/redlines/`, `renew1030/ar/` | `site_1030` | `redlines/*`, `ar/accept_all`, `ar/reject_all`, `wordcraft1014/compare` |

### 1.4 The 21 groups: producer, inputs, availability

| # | Group | Kind | n | Produced by | From which inputs | R2 prefix(es) | Inputs still here? |
|---|---|---|---|---|---|---|---|
| 1 | `cases` | conversion | 76 | `build_site.py` + `site_1029` (jubarte lane) | docxide-pdf fixtures (208 docs) | `gh-pages-tree/docxide`, `d2p/cases`, `renew1028/d2p` | **No** — `~/temp/T/docxide-pdf` is gone (dangling symlink at `~/temp/T/docxide_compare/docxide-pdf`). Re-cloneable from the public `sverrejb/docxide-pdf` repo. The **scorer binary survives**: `site/_build/site_1029/bin/page-metrics` and `site_1030/bin/page-metrics` (2 917 536 B each, SHA256 `2213ff31…`) |
| 2 | `corpus/clean` | conversion | 977 | `build_site.py` (380) + `site_1011` (597) | `corpus/word` + bench runs | `gh-pages-tree/corpus`, `d2p/corpus`, `renew1028/d2p` | **Partly** — `corpus/word` ✓ (2480 docx); `results/jubarte_0.9.3_docx_to_pdf_work` ✗, `results/docxide_0.17.1_work` ✗, `results/pymupdf-pro_1.28.2_work` ✗; `results/soffice_26.8.0.3_work` ✓ (83 MB) |
| 3 | `corpus/tracking_without_comments` | conversion | 476 | same | same | same | same as #2 |
| 4 | `corpus/with_comments_clean` | conversion | 62 | same | same | same | same as #2 |
| 5 | `corpus/with_comments_tracking` | conversion | 145 | same | same | same | same as #2 |
| 6 | `new` | conversion | 74 | `build_site.py` | docxide-pdf fixtures | `gh-pages-tree/docxide` | No (clone gone) |
| 7 | `samples` | conversion | 5 | `build_site.py` | docxide-pdf fixtures | `gh-pages-tree/docxide` | No (clone gone) |
| 8 | `scraped` | conversion | 53 | `build_site.py` | docxide-pdf fixtures | `gh-pages-tree/docxide` | No (clone gone) |
| 9 | `v1/clean` | conversion | 2478 | `site_1028` phase 1 | bench fixture set `v1` (pinned snapshot `984e64d8…`, in-repo `src/neurotic_docx_bench/data/fixture_sets/v1.csv.gz`) + `ndb-v1/out/dx89/{full_work,soffice_work}` | `d2p_v1/v1` | **Scores ✓ on main** (`results/docx_to_pdf_v1_89/full.json`, n=2925, tools docxodus+jubarte; `soffice_full.json` n=2925). **Candidate PDFs ✗** (`~/temp/T/ndb-v1` absent). Re-derivable from the HF fixture set + a re-conversion |
| 10 | `v1/tracking_without_comments` | conversion | 237 | `site_1028` | same | `d2p_v1/v1` | same as #9 |
| 11 | `v1/with_comments_clean` | conversion | 112 | `site_1028` | same | `d2p_v1/v1` | same as #9 |
| 12 | `v1/with_comments_tracking` | conversion | 98 | `site_1028` | same | `d2p_v1/v1` | same as #9 |
| 13 | `wordcraft1014/convert` | conversion | 400 | `site_1028` (cohort) + `site_1029` (jubarte lane) | Wordcraft 2026-10-14 metadata + PDFs (HF `r2-mirror/wordcraft-20261014`, manifest TSV 2.4 MB) | `wordcraft1014/wordcraft1014`, `renew1028/d2p` | **Yes** — HF mirror + `fetch_wordcraft.py` |
| 14 | `accepted/tracking_without_comments` | redline | 5 | `redlines/build_redlines_site.py` | `results/redlines_0929_full/accept_selection.csv` | `gh-pages-tree/redlines` | Yes (CSV tracked on main; `oracle_pdf/` present locally) |
| 15 | `rejected/tracking_without_comments` | redline | 8 | `redlines/build_redlines_site.py` | `reject_selection.csv` | `gh-pages-tree/redlines` | Yes |
| 16 | `redlines/tracking_without_comments` | redline | 902 | `build_redlines_site.py` + `site_1030` | `redlines_0929_full` (2611 pairs) + `compare_regen/out` | `redlines/redlines`, `renew1030/redlines`, `gh-pages-tree/redlines` | **Mostly** — `results/compare_regen` ✓ (159 MB, `out/` + `regen_list.csv`); `oracle_pdf/` ✓ (3502 PDFs); tool lanes mostly pruned locally |
| 17 | `redlines/with_comments_clean` | redline | 2 | `build_redlines_site.py` + `site_1030` | same | `redlines/redlines`, `renew1030/redlines` | same as #16 |
| 18 | `redlines/with_comments_tracking` | redline | 112 | `build_redlines_site.py` + `site_1030` | same | same | same as #16 |
| 19 | `ar/accept_all` | redline | 400 | `site_1030` phase 3b (Set B) | the 400 accept pairs of the split | `renew1030/ar` | Yes via `~/temp/T/renew_1028` (3.0 GB, MANEST verified) |
| 20 | `ar/reject_all` | redline | 400 | `site_1030` phase 3b (Set B) | the 400 reject pairs | `renew1030/ar` | Yes, same |
| 21 | `wordcraft1014/compare` | redline | 400 | `site_1028` (cohort) + `site_1030` (jubarte/docxodus lanes) | Wordcraft compare cohort | `wordcraft1014/wordcraft1014`, `renew1030/redlines` | Yes (HF mirror) |

Counts cross-check: conversion shards 76+977+476+62+145+74+5+53+2478+237+112+98+400 = **5193** (equals the gh-pages `index.html` DATA length and the benchmarks.law `data.json` length). Redline shards 5+400+400+902+2+112+8+400 = **2229** (equals the gh-pages `redlines/index.html` DATA length). Total **7422** cases in the release.

### 1.5 Can each group be regenerated today?

| Group | Regenerable today? | Missing |
|---|---|---|
| `cases`, `new`, `samples`, `scraped` (208) | **Yes, with network** | the docxide-pdf clone (`~/temp/T/docxide-pdf`) is deleted; re-clone from `github.com/sverrejb/docxide-pdf` @ `261618a9` + rebuild `page-metrics`. The *published* page images are still on R2 (`gh-pages-tree/…`) so the page can be re-hosted without re-rendering |
| `corpus/*` (1660) | **Partly** | `corpus/word` ✓; but `results/jubarte_0.9.3_docx_to_pdf_work`, `results/docxide_0.17.1_work`, `results/pymupdf-pro_1.28.2_work` are gone → the `generated` (docxide), `reference` (Word) and `pymupdf` lanes cannot be re-scored; `minipdf`/`rdocx`/`office2pdf` binaries also gone (`site/_build/tools/bin`). `soffice_26.8.0.3_work` survives (83 MB). `renew1028/` (jubarte 0.14.0) images are on R2 and the phase-1 inputs survive in `renew_1028` |
| `v1/*` (2925) | **Yes** | needs the candidate PDFs (`ndb-v1/out/dx89/…`, absent) → re-convert with the pinned tools, or re-derive from the in-repo fixture set `v1.csv.gz` + `bench.yaml` pins. The *scores* are on `origin/main` |
| `wordcraft1014/convert` (400) | **Yes** | `fetch_wordcraft.py` + HF `r2-mirror/wordcraft-20261014` |
| `accepted/*`, `rejected/*` (13) | **Partly** | the selections are tracked; the tool lanes' PDFs were pruned locally, but the images survive on R2 (`gh-pages-tree/redlines`) |
| `redlines/*` (1016) | **Yes for the `renew1030` lanes** | `renew_1028` inputs ✓, `compare_regen` ✓, `oracle_pdf` ✓, `page-metrics` binary ✓; the pre-renewal `redlines/redlines` lane needs the `redlines_0929_full` tool PDFs, which exist only for `jubarte-0.17.0` (2611 docx / 2566 Word PDFs) and `docxodus` (586) locally |
| `ar/accept_all`, `ar/reject_all` (800) | **Yes** | `renew_1028` + `site_1030` are self-contained |
| `wordcraft1014/compare` (400) | **Yes** | HF mirror + `site_1030` |

**Bottom line:** every group can be re-*published* from R2 + the pinned inputs, but the **docxide-fixture groups** (208) and the **corpus groups** (1660) cannot be re-*scored* from this machine — their candidate PDFs and the docxide clone are gone. The v1 cohort needs a re-conversion; everything else is reproducible.

### 1.6 Where the new release's case data comes from

`~/bench/.work/ndb-cases/src/neurotic_docx_bench/ledger/cases_adapter.py` is the **one-time adapter** that reads the published pages (`const ENGINES`/`const DATA` from `gh-pages/index.html` + `redlines/index.html`, or the benchmarks.law `data.json`) and rewrites absolute R2 URLs to `~/`-relative paths against the discovered `assets_base` (`cases_adapter.py:70-78`, `:104-137`). It is the only piece of the release that reads the pages; the emitter `ledger/cases.py` reads stores. The adapter's output was snapshotted to `results/release/legacy_cases.jsonl` (22.9 MB) and uploaded to HF as `inputs/legacy_cases/8b862e4519757a01fbfe3f1157d60614be2522da24758fbe6e521bc3c5001455.jsonl`.

---

## 2. The per-release flow in ndb

Two distinct flows exist; they are not the same thing.

### 2.1 `jubarte_bench_release.py` (CLI: `scripts/release_jubarte.py <version>`) — the **full-corpus** flow

`src/neurotic_docx_bench/jubarte_bench_release.py:56` — `STAGES = ('install','convert','metrics','redlines','report','publish')`.

| Stage | What it runs | Where it writes |
|---|---|---|
| `install` | downloads the **GitHub release binary** of that version, checks it against the release's `SHA256SUMS.txt`, checks `--version` == the release (`jubarte_bench_release.py:216-224`) | `~/.cache/…/<version>/…/jubarte` |
| `convert` | `bench docx-to-pdf --converter <binary> --origin all --work-dir results/jubarte_<v>_docx_to_pdf_work --json results/docx_to_pdf_jubarte_<v>.json` — **all of `corpus/word`** (6427 docs) | `results/docx_to_pdf_jubarte_<v>.json`, `results/converters.jsonl` |
| `metrics` | `bench docxide-metrics --tool jubarte --converter <binary> --origin all` | `results/docxide_metrics_jubarte_<v>.json` |
| `redlines` | preflights: Word answers osascript; `results/compare_regen/out` + `regen_list.csv` are complete. Then `node --import tsx scripts/generate-native-redlines.ts --method jubarte-rust --tool jubarte-<v> --manifest results/redlines_0929_full/gen_pairs.csv`; Word export via `scripts/word_pdf.py --do-not-close` (batch) then one osascript per document; `results/redlines_0929_full/measure.py --fresh results/compare_regen/out --regen-list … --device mps jubarte-<v>`; `versions.json`; `to_scores_jsonl.py`; `hub_upload.py` | `results/redlines_0929_full/jubarte-<v>/{docx,pdf_by_word}`, `scores_jubarte-<v>.json`, `scores.jsonl`, `versions.json` |
| `report` | `bench report` and `bench report --check` | `RESULTS.md`, `RESULTS_DETAILED.md` |
| `publish` | `git switch -c bench/jubarte-<v>`, stage, commit, push, `gh pr create` | branch `bench/jubarte-<v>` |

**Only jubarte is run.** No docxodus, no superdoc, no soffice in this flow.

### 2.2 `jubarte_release_info.py` — the **sample-based** flow (the one the site uses)

`src/neurotic_docx_bench/jubarte_release_info.py:105` — `STAGES = ('install','sample','generate','export','measure','convert','score','write')`.

The two samples (both `n = 600`, `DEFAULT_N = 600` at `:114`, seed `DEFAULT_SEED = 20261003` at `:113`):

| Sample | Size | Corpus | Stratification | Where |
|---|---|---|---|---|
| conversion | **600 docs** | `corpus/word` (4 states) | `CONVERSION_QUOTAS` (`:107-111`) = clean **150**, tracking_without_comments **150**, with_comments_clean **117**, with_comments_tracking **183** (scaled to `n` by largest remainder, `conversion_quotas()`); among DOCX that **have a Word PDF**; seeded shuffles (`draw_conversion()`) | `results/release_<v>_evidence/sample_conversion_<v>.csv` (`state,stem,docx,docx_sha256,word_pdf,word_pdf_sha256`) |
| redline | **600 compares = 600 pairs** | `results/redlines_0929_full/{gen_pairs.csv,pool_pairs.csv}` | `draw_balanced()`: every **scarce** state (≠ `tracking_without_comments`) whole first, docxodus-covered pairs first, then filled with `tracking_without_comments` compares spread over document families; **one compare per pair**; seeded | `results/release_<v>_evidence/sample_redline_<v>.csv` (pool columns + sha256) and `gen_pairs_sample.csv` |

Measured from the 0.17.0 evidence (`results/release_0.17.0_evidence/sample_meta.json`):

```
conversion: n 600, seed 20261003, date 2026-11-01,
  by_state clean 150 / tracking_without_comments 150 / with_comments_clean 117 / with_comments_tracking 183
redline:    n 600, pairs 600, seed 20261003, date 2026-11-01,
  by_state clean 2 / tracking_without_comments 421 / with_comments_clean 3 / with_comments_tracking 174,
  docxodus_covered_pairs 548 (local PDFs + cached Hub listing)
```

**Which tools are actually run per release:**

| Tool | Run? | Why |
|---|---|---|
| **jubarte** | **Yes** — the only tool re-run | `generate` (redlines of the sampled pairs, `--force`), `export` (Word opens each redline), `measure` (score vs Word's compare), `convert` (`bench docx-to-pdf --origin list` with one `--files-list` per fixture — it converts *and* scores) |
| docxodus | **Only for pairs missing its Word PDF** | `generate` fetches the missing `outputs/redlines_0929_full/docxodus/pdf_by_word/*.pdf` from HF first; only the remainder is redlined by Docxodus |
| soffice | **Only for fixtures lacking a render** | `results/soffice_26.8.0.3_work/candidate` is reused; `scripts/convert_candidates.py` renders the missing ones, which are then staged into `soffice_sample/` and **re-scored** with `bench docx-to-pdf --score-only --tool soffice` |
| superdoc / others | **No** | never in this flow |

Outputs of the `write` stage: **six files** per version, written to the engine's `release_info/` *and* to `results/release_<v>_evidence/release_info/` with identical bytes (`write_six()`, `:1093-1152`):
`sample_redline_<v>_<mm-dd-yy_hh-mm>.csv`, `sample_conversion_<v>_….csv`, `results_redline_<v>_….json`, `results_conversion_<v>_….json`, `website_data_<v>_….jsonl`, `app_data_<v>_….jsonl`.

`origin/main` holds `results/release_0.17.0_evidence/release_info/` with all six (stamp `11-01-26_01-06`); `results/release_0.14.0_evidence/` holds 20 tracked files; `release_0.15.0_evidence/` and `release_0.16.0_evidence/` are **not on main** (local only).

### 2.3 How `results/site_fixtures_860.csv` was picked

`scripts/pick_site_fixtures.py` (on `origin/main`):

* pool = documents scored by jubarte **and** converted by every other engine on the site (`docxide_0.17.1_work`, `soffice_26.8.0.3_work`), read from `results/jubarte_0.10.1_docx_to_pdf_work/jubarte/scores.checkpoint.jsonl` (`:20-24`, `:46-57`);
* strata = corpus state × page bin (`PAGE_BINS = 1, 2, 3, 4-6, 7-12, 13-30, 31+`) × jubarte score band (`SCORE_BINS = 0-50, 50-70, 70-90, 90-100`) (`:22-23`);
* allocation flattens the distribution (cell weight `size**0.5` with a floor) so rare document kinds survive (`:1-6`);
* `--n 800 --seed 20261001` produced `results/site_fixtures_800.csv` (commit `2a1523d3`);
* commit `8dc64856` ("Site fixtures: --exclude for the picker, 60 extra fixtures (860 total) under the Pages size cap") added `--exclude` and `results/site_fixtures_extra60.csv` → **`results/site_fixtures_860.csv`, 860 rows**, on `origin/main` (blob `cf84a526…`).

Measured composition of the 860: clean 380, tracking_without_comments 375, with_comments_tracking 83, with_comments_clean 22; page counts 1…12+.

jubarte.pro consumes it in `jubarte-site/scripts/site_fixtures.py:167` (reads `results/site_fixtures_860.csv` row by row) and stages `source.docx` + `word/jubarte/soffice/docxide` PDFs, with pixel scores from the bench checkpoint files and Jaccard/text-boundary from `results/docxide_metrics_*.json` (`:152-160`).

### 2.4 How the 240 redlines were picked (jubarte.pro)

`jubarte-site/scripts/site_fixtures.py:212-235` (`pick_redlines`):

* pool = `results/redlines_0929_full/scores_<tool>.json` rows with `oracle == "corpus"`, present in `pool_pairs.csv`, and with their files actually available (local or HF);
* strata = (corpus state) × (score band of `overall_score`, bins `SCORE_BINS = (0,50),(50,70),(70,90),(90,100.01)`);
* quota = `min(len(cell), 2)` per cell, then the remainder distributed by `sqrt(len(cell))` weight, seeded with **20261001** (`--redlines 240 --seed 20261001`, `:609-610`);
* **240 requested → 234 published**: 6 pairs were dropped because their files were missing. The published file is `public/data/cases-redline.json` with 234 cases; `public/data/cases-convert.json` has 860.

---

## 3. Overlap: jubarte.pro ↔ ndb

### 3.1 The 234 jubarte.pro redline pairs vs ndb (match on the 10-hex case hash)

jubarte ids are `r-<10hex>`; ndb redline case ids are either a bare 10-hex (`redlines/*`, `accepted/*`, `rejected/*`, `wordcraft1014/compare`) or `<base>__vs__<next>_redline_<10hex>` (`ar/accept_all`, `ar/reject_all`).

| Population | Count | Source |
|---|---|---|
| jubarte.pro redline cases | **234** (234 distinct 10-hex) | `jubarte-site/public/data/cases-redline.json` |
| … found in `results/redlines_0929_full/gen_pairs.csv` (by `id`) | **234 / 234** | the pairs are all real bench pairs |
| … found in `sample_redline_0.17.0.csv` (600 rows) | **42 / 234** | the per-release redline sample |
| … found in **any** ndb release shard (conversion + redline) | **111 / 234** | per shard: `redlines--tracking_without_comments` 76, `wordcraft1014--compare` 19, `ar--accept_all` 18, `ar--reject_all` 11 (overlaps counted once) |
| … **not** in any ndb release shard | **123 / 234** | |

> The brief said “144 missing”. I measure **123 missing** against the release bundle at `~/bench/.work/ndb-cases/results/release/cases/` (`index.json` `generated` 2026-11-02T22:13:53Z), matching on any 10-hex inside the case id. Matching on the **redline shards only** gives the same 111/123 split plus 10 extra accidental hits in the conversion shard `wordcraft1014--convert` (those 10 are the *base* document hashes, not redline keys — the genuine redline-key overlap is 111 and the genuine missing set is 123). The 234/234 against `gen_pairs.csv` and 42/234 against `sample_redline_0.17.0.csv` are unambiguous.

### 3.2 The 860 jubarte.pro conversions vs ndb

| Population | Count |
|---|---|
| jubarte.pro conversion cases | **860** (ids `clean-010cd893de`, `tracking_without_comments-…`) |
| present in `corpus/word` (all 7228 DOCX of the 4 states + accept_all/reject_all/pools) | **860 / 860** |
| present in the `corpus/word:all` document set (**6427** docs = DOCX with a Word PDF) | **860 / 860** (measured: clean 2480 + tracking 3410 + with_comments_clean 117 + with_comments_tracking 420) |
| present in the per-release conversion sample `sample_conversion_0.17.0.csv` (600 rows) | **93 / 860** |
| present in the ndb release conversion shards | the release's `corpus/*` shards use the corpus document hash as case id (`218c9d60d5` style) while `v1/*` shards carry the full `stem`; the 860 stems are corpus stems, so they map onto `corpus/*` + `v1/*` by stem — **not** a 1:1 with the release's 21 shards |

### 3.3 The 123 missing jubarte.pro redline pairs — are the inputs available?

| Input | Local | HF (`arthrod/neurotic_docx_bench`) |
|---|---|---|
| original + modified DOCX (`corpus/word/<state>/docx/…`) | **123 / 123** | ✓ `arthrod/neurotic_docx_bench-fixtures` |
| Word's compare oracle PDF (`results/redlines_0929_full/oracle_pdf/<key>.pdf`) | **123 / 123** | ✓ (`outputs/redlines_0929_full/…`) |
| jubarte 0.17.0 redline DOCX (lane `results/redlines_0929_full/jubarte-0.17.0/docx`) | **122 / 123** | **122 / 123** (`outputs/redlines_0929_full/jubarte-0.17.0/docx`, 2611 files) |
| jubarte 0.17.0 Word PDF of that redline (`…/pdf_by_word`) | **122 / 123** | **122 / 123** (2566 files) |
| docxodus Word PDF | 21 / 123 | **87 / 123** (`outputs/redlines_0929_full/docxodus/pdf_by_word`, 1933 files) |
| superdoc output | 0 / 123 (shards hold only logs) | not in the HF listing |
| **complete set (base+next+oracle+jubarte docx+pdf)** | **122 / 123** | **122 / 123** |
| the jubarte.pro staging itself (`original.docx, modified.docx, word.docx/pdf, jubarte.docx/pdf, docxodus.docx/pdf`) | — | **234 / 234** at `site/redline/r-<10hex>/` (8 files each) |

So: **the 123 missing pairs are fully reproducible** — 122 have every input locally *and* on HF; the one exception is the pair jubarte 0.17.0 itself failed to redline. Nothing is lost; it was simply never selected into a published ndb group.

---

## 4. Feature parity

`site/_build/viewer_features.py` (17 124 B) is the feature spec for the old pages; `site/_build/test_viewer_features.py` (5 275 B, **13 tests**) is its unit spec. `viewer_theme.py` adds the jubarte.pro theme on top.

| Feature | old gh-pages (`site/index.html`, `site/redlines/index.html`) | benchmarks.law copy | jubarte.pro Cases viewer | new bench-viewer (`packages/bench-viewer/src/cases.ts`) |
|---|---|---|---|---|
| Convert ↔ Redline switch | **yes** — `#mode` select (`viewer_features.py:29-31`, `:196-203`) | **yes** — `#mode` (1 occurrence) | **yes** — segmented `#bench-redline` / `#bench-convert` (`site/pages/cases.ts:31-33`) | **yes** — `#mode` select (`cases.ts:1138`) |
| Case filters: group / pages / engine-score ≤ X on a metric / pages differ from Word | **yes** — `FX_HTML` + `passesFx()` (`viewer_features.py:85-93`, `:96-108`) | **yes** — `fxGroup/fxPages/fxEng/fxMetric/fxMax/fxDiffer` present | **partial** — group list, Word-page buckets, **jubarte**-score slider (no metric picker), "pages differ" checkbox (`site/pages/cases.ts:60-70`) | **yes** — same six controls (`cases.ts:1153-1157`) |
| Free-text filter (name, group) | **yes** — `#filter` | **yes** — `#filter` | **no** (group list only) | **yes** — `#filter` (`cases.ts:1152`) |
| “N of M cases” counter | **yes** — `#fxn` | **yes** | **no** | **yes** — `#fxn` |
| Random case (button + `r`) | **yes** — `pickRandom()` | **yes** | **yes** (`#random`) | **yes** |
| Random case on open (when no `#group/case` link) | **yes** — `if (!linked) pickRandom()` | **yes** | **no** (opens on a case) | **partial** (hash-linked case honoured; no random-first) |
| Prev / next / position | **yes** — `#navPrev/#navNext/#navPos`, `step()` | **yes** | **yes** (`#prev/#next/#pos`) | **yes** |
| Hide side panel (`s`) | **yes** — `#sideToggle`, `body.noside` | **yes** | **yes** (`#toggle-side`) | **yes** |
| Legend → accordion + keyboard hints folded in | **yes** — `accordionLegend()` | **yes** | **no** (a static “Keys” list) | **yes** (`cases.ts:1059-1067`) |
| Per-engine toggles + saved state in `localStorage` | **yes** — `'ec.'` / `'ec.redline.'` keys (`viewer_features.py:207-208`) | **yes** | **partial** (engine list, no persistence) | **yes** — `'ec.cases.<kind>'` (`cases.ts:1239`, `:1249`) |
| Zoom slider | **yes** | **yes** | **yes** | **yes** (`#zoom`) |
| Overlay (side/under/difference/multiply) | **yes** (`#ovl`, `#ovlA/#ovlB`, `#blend`) | **yes** | **yes** (4 modes) | **yes** (3 blend modes + opacity) |
| Scores table view (`t`) | **yes** — `#viewToggle` | **yes** | **yes** (`#toggle-scores`) | **yes** |
| Sort the score table by a column | **yes** (9 `sort` hits) | **yes** (8 hits) | **no** | **yes** — “click a column to sort” (`cases.ts:941-945`) |
| “More pages” strip beyond the cap | **yes** — `#more` | **yes** | **yes** (`#toggle-more`, `#more-strip`) | **yes** — `#more` |
| Per-page scores | **no** (only whole-document scores) | **no** | **yes** — `page_scores[]` per case | **no** (whole-document only) |
| Failure tagging (0 = failed, PERFECT / ≥90 / <50) | **yes** (ITT zero + `dropped` lanes) | **yes** | **yes** — `FAILED · scores 0`, `PERFECT`, `≥ 90`, `< 50` (`site/static/js/cases.js:104-110`) | **yes** — `LANE_LABEL` (`cases.ts:311-340`) |
| Download / link to the source files | **no** (PDF only) | **no** | **yes** — “files on Hugging Face ↗” + per-engine file links | **partial** — `pdfs{}` in the contract, not surfaced |
| Permalink to the case | **yes** (`#group/case` hash) | **yes** | **yes** (`#permalink`) | **yes** (hash) |
| Mobile layout (drawer / sheet / small screens) | **no** | **yes** — `#mMenu`, `#mLayout`, `#msheet`, `renderMobile()`, `closeDrawer()` | **yes** — mobile CSS, embed mode | **yes** — `#mbar`, `#msheet` sheet with 3 layouts (`cases.ts:1134`, `:1162-1167`) |
| Data source | inline `const DATA` in an 18.2 MB HTML | `data.json` (13.6 MB) fetched at runtime | `public/data/cases-*.json` (860/234) | `cases/index.json` + one shard per group fetched on demand |
| Assets | R2 `pub-92c80eda1059428394970066c8500eda.r2.dev/` | same R2 base | HF dataset `site/convert`, `site/redline` | `assets_base` from `cases/index.json` (same R2) |
| Page cap | first 3 (or the case's `shown` list) | same | up to 12 pages drawn per document (`MAX_PAGES = 12`) | per-case `shown` list, `#more` for the rest |
| Extra pages the old pages have that the others lack | — | — | per-page scores, HF file links | **editors/** page, **home/** score page, **leaderboard** page — three pages that exist in benchmarks.law (`editors/index.html`, `home/index.html`) and in the new viewer (`leaderboard.ts`), but have **no equivalent** on jubarte.pro’s Cases viewer or in the old gh-pages pair |

**Parity summary:** the new `bench-viewer` is a *superset* of the old gh-pages viewer (same filters, same nav, same theme, plus per-group lazy loading, a mobile sheet and a leaderboard page). `benchmarks.law` is a faithful re-implementation of the old viewer with the same feature set plus a mobile layout and the `editors/` and `home/` pages. jubarte.pro’s Cases viewer is an independent implementation over a different, much smaller data contract (860 + 234 cases, 4 engines each) — it adds per-page scores and file links but loses the metric picker, the “N of M” counter, persisted state and the score-table sort.

---

## 5. Unmerged work at risk

### 5.1 Open PRs (`gh pr list -R jandira-tech/neurotic_docx_bench --state open`)

| # | Title | Branch | Opened |
|---|---|---|---|
| 109 | Results: Apryse SDK 12.2.1 (#100) | `bench/apryse-12.2.1` | 2026-11-02T04:48:40Z |
| 108 | feat(apryse): Apryse SDK as converter and redline generator; word_pdf timing flags (#100, #107) | `feat/apryse-100` | 2026-11-02T04:48:27Z |
| 104 | fix(hub_upload): a folder split into “(part N)” commits is ours (#103) | `fix/hub-upload-part-commits` | 2026-11-01T18:30:20Z |
| 102 | fix(release_jubarte): a Word export that refuses to start stops the run; check node first (#101) | `fix/word-export-refusal-fatal` | 2026-11-01T17:47:32Z |

### 5.2 Local branches with commits not on `origin/main`

| Branch | Commits ahead | Commit list |
|---|---|---|
| `bench/apryse-12.2.1` | 4 | `429a84ea` Results: Apryse SDK 12.2.1 (#100) · `227a894d` feat(hub_get): docx-to-pdf-v1-apryse preset · `9759f4ca` feat(word_pdf): --nudge-after/--nudge-wait/--empty-limit (#107) · `dd0f145c` feat(apryse): DOCX→PDF converter, redline generator, stamp-matched normalization (#100) |
| `feat/apryse-100` | 2 | `9759f4ca`, `dd0f145c` |
| `feat/viewer-cases` | 2 | `b6dbbd50` bench-viewer: lanes of the page's kind, display order, scroll-into-view, optional fonts · `afbfdf33` bench-viewer: the case browsers and the speed page, from the release |
| `fix/hub-upload-part-commits` | 2 | `a5a26f9b` refactor(hub_upload): upload_folder is the only check · `0b263000` fix(hub_upload) (#103) |
| `fix/word-export-refusal-fatal` | 2 | `04c5b3ef` fix(release_jubarte): batch refusal only; same rule in the release_info flow (#101) · `110f6139` fix(release_jubarte) (#101) |
| `gh-pages` | 7 | `610bc447` Pages: the redline viewer carries its own mode drop-down and saved state · `f6d98c53` Pages: drop five unused engine toggles · `c7b24368` Pages: jubarte 0.14.0 and docxodus 14.2.0; 800 accept/reject pairs (#99, #95) · `3476e755` Pages: untrack the node_modules symlink · `2e399a0c` Pages: jubarte 0.14.0, 1 276 cases rescored (#99) · `93dcd1cb` Pages: the v1 measurement (#94) and the Wordcraft cohort (#96) · `80cd187e` Pages: one commit, every page image on R2 |
| `results/v1-docx-to-pdf-89` (HEAD) | **0** | already an ancestor of `origin/main` (`git merge-base --is-ancestor` → true). `origin/main` is at `15aa9ecb` (merge of #125) |

Also unmerged in the bench/.work worktrees: `feat/cases-speed` (`9ba725f8`, #119 — **already merged** as `origin/main` `15aa9ecb`), `feat/viewer-leaderboard` (`453f8a0b`, merged in #121), `feat/scores-json` (`23f680de`), `chore/drop-lo-profiles` (`0c40b508`), `fix/verify-no-expand` (`7a52775a`, merged in #123), `release/ndb-v0.0.1` (`6eea68ef`, merged in #124).

### 5.3 Arthur’s checkout: 114 untracked + 11 modified

`git status --porcelain` in `~/temp/T/neurotic_docx_bench`: **114 untracked entries, 11 modified** (measured).

**11 modified** (all tracked on `origin/main` — the working tree is simply behind the branch):

| Path | Nature |
|---|---|
| `corpus/word_unindexed/…/centralized_mapping_randomized.csv` | corpus data |
| `pyproject.toml`, `uv.lock` | dependency pins |
| `results/converters.jsonl` | **run result** (append-only ledger) |
| `results/hub_redlines_0929_full/docxodus/meta/generate_failures.json` | run result |
| `results/hub_redlines_0929_full/run/MANIFEST.sha256.json` | run result |
| `results/hub_redlines_0929_full/run/hub_upload.py` | script |
| `results/hub_redlines_0929_full/run/measure.py` | script |
| `results/hub_redlines_0929_full/run/scores_jubarte-0.10.1.json` | run result |
| `results/hub_redlines_0929_full/run/versions.json` | run result |
| `results/redlines_0929_full/docxodus/pdf_by_word/manifest.jsonl` | run result |

**114 untracked**, grouped (7 of them are already present on `origin/main`; 107 are not):

| Group | Entries | What it is | On HF? |
|---|---|---|---|
| **Run results — jubarte 0.17.0 full lane** | `results/redlines_0929_full/jubarte-0.17.0/` (5 184 files, 1.2 GB), `results/hub_redlines_0929_full/jubarte-0.17.0/` (5 180 files, 1.2 GB), `results/docx_to_pdf_jubarte_0.17.0.json` (6 427), `results/docxide_metrics_jubarte_0.17.0.json` (6 427) + `.log`, `results/jubarte_0.17.0_docx_to_pdf_work`, `results/jubarte_0.17.0_docx_to_pdf.log`, `results/redlines_0929_full/scores_jubarte-0.17.0.json` (3 454) | the newest jubarte measurement on this machine | ✔ `outputs/redlines_0929_full/jubarte-0.17.0/{docx,pdf_by_word}` (2611/2566) on HF; the conversion JSONs are on `origin/main` |
| **Run results — sample redline scores 0.15/0.16/0.17** | `scores_jubarte-0.1{5,6,7}.0_sample_redline_0.1{5,6,7}.0.json` (593 rows each), `scores_docxodus_sample_redline_0.1{5,6,7}.0.json` (575 each), `scores_jubarte-0.17.0_stamp_matched_sample300_seed0.json` (300), `sample_redline_0.1{5,6,7}.0_*.csv` (×6) | per-release sample scores | ✔ all on HF under `outputs/redlines_0929_full/run/` |
| **Run results — Apryse** | `results/redlines_0929_full/apryse/` (8 666 files, 975 MB), `scores_apryse.json` (2 524), `scores_apryse_stamp_matched.json` (2 509), `apryse.generate.log`, `apryse.word_pdf.stage1.log` | the newest redline lane | ✔ HF `outputs/redlines_0929_full/apryse` + `run/scores_apryse*.json` |
| **Run results — release evidence 0.15/0.16/0.17** | `results/release_0.1{5,6,7}.0_evidence/` (3 621 / 3 019 / 3 019 files; 372 / 285 / 286 MB) | per-release sample + convert + score evidence | ✗ **not on HF** (HF has only `outputs/release_0.13.0_evidence`); `release_0.17.0_evidence/release_info/` (the six files) **is** on `origin/main` |
| **Run results — local lanes 0.15/0.16** | `results/redlines_0929_full/jubarte-0.15.0/`, `jubarte-0.16.0/` (1 200 files each, ~227 MB) | full-corpus redline lanes | ✗ not on HF (HF has `jubarte-0.10.1`, `-0.13.0`, `-0.14.0`, `-0.17.0`, `-093`, `-rust`) |
| **Run results — Word export logs / measure logs / hub upload logs** | `results/redlines_0929_full/{jubarte-0.17.0.*.log, measure_jubarte-0.17.0.log, hub_upload_jubarte-0.17.0*.log, docxodus/word_pdf.sample_0.1{5,6,7}.0.*}` | evidence of the Word passes | partly on HF (`run/` has `word_pdf.py`, `measure.py`, `hub_upload.py`) |
| **Scripts** | `results/hub_redlines_0929_full/run/{font_session_split,identity_0112_full,paired_0101_0112,rebuild_manifest,stage_0112_full}.py`, `run/NOTES_0112_full.md` | run tooling | ✔ all on HF `outputs/redlines_0929_full/run/` |
| **Mirror of the HF run dir** | `results/hub_redlines_0929_full/run/sample*`, `run/scores_*.json` (≈50 files), `run/docxodus/pdf_by_word/` (586 files, 125 MB), `docxodus/meta/word_refused.csv` | local copy of the HF upload | ✔ mirrored on HF |
| **Locks / scratch** | `results/redlines_0929_full/.scores_*.lock` (8), `.tmp/` (4 files) | scratch | n/a |

### 5.4 `ndb-apryse`, `ndb-apryse-keep`, `ndb-105`

| Checkout | State | Risk |
|---|---|---|
| `~/temp/T/ndb-apryse` (`bench/apryse-12.2.1`, HEAD `429a84ea`) | 7 untracked entries: `.venv` and `corpus/word/{accept_all,clean,reject_all,tracking_without_comments,with_comments_clean,with_comments_tracking}` — **symlinks or copies of the corpus**, 4 commits ahead of `origin/main` | the 4 commits are exactly PR #108/#109's content. The corpus dirs are re-fetchable (HF `arthrod/neurotic_docx_bench-fixtures`). **What would be lost:** nothing that is not in the PR; but if PR #109/#108 were closed instead of merged, the Apryse converter + stamp-matched normalisation + the `word_pdf` nudge flags would vanish from the repo |
| `~/temp/T/ndb-apryse-keep` | **not a git repo**; only `apryse_conv_line.jsonl` (240 800 B) + `hub_upload_apryse.log` (1 312 B) | the per-document Apryse conversion line and the upload log — **the only local copies** of that record. On HF? `outputs/docx_to_pdf_v1_apryse` exists but the *conversion line* for the corpus run is not in the HF listing I walked |
| `~/temp/T/ndb-105` (detached `eaf92271` = merge of #105 `bench/jubarte-0.17.0`) | 7 untracked: the six `corpus/word/*` state dirs + `results/jubarte_0.17.0_docx_to_pdf_work` | the work dir of the jubarte 0.17.0 full-corpus conversion — **the candidate PDFs for the 6 427-document run**. Its *scores* are on `origin/main`; the *PDFs* are only here and in `results/jubarte_0.17.0_docx_to_pdf_work` in Arthur’s checkout |

### 5.5 What moving case generation and per-release scoring onto `main` would bypass / lose

| If moved onto `main` | Bypassed | Lost |
|---|---|---|
| Case generation (the adapter + emitter) | the one-time adapter that reads the published pages (`ledger/cases_adapter.py`) and the `results/release/legacy_cases.jsonl` snapshot (already on HF at `inputs/legacy_cases/8b862e45….jsonl`) | nothing — the snapshot is pinned and uploaded |
| The per-release `release_info` flow onto `main` | `scripts/release_jubarte.py` (full corpus) and `jubarte_release_info.py` (sample-based) as *ad-hoc* side flows; `scripts/release_jubarte.py` stays needed for the corpus run | the `results/release_<v>_evidence/` folders for 0.15/0.16/0.17 (**not on HF, not on main** — 966 MB, the only copy) and the `release_info/` six files for 0.15/0.16 (**not on main, not on HF**) |
| The redline lanes onto `main` | the local `jubarte-0.15.0` / `jubarte-0.16.0` lanes (not on HF) | those two full-corpus lanes (2 400 files) |
| The site pipeline (`site/_build/*`) onto `main` | `site_1011/…site_1030/` already tracked on the `gh-pages` branch, not on `main` | the `gh-pages` branch’s 7 commits, incl. the `renew1028`/`renew1030` **scorer snapshot** (`scorer-source/` = bench main `3121c498…`) and `bin/page-metrics` — the `gh-pages` branch is the only place the *scorer provenance* is recorded |
| The `feat/viewer-cases` / `feat/cases-speed` branches onto `main` | `b6dbbd50` + `afbfdf33` (bench-viewer case browsers) — the viewer branch is **not on `main`** even though the data branch `feat/cases-speed` (`9ba725f8`) is merged as #125 | without them, `packages/bench-viewer` has no page implementation on `main` |

---

## 6. Newest runs on this machine (jubarte 0.14 → 0.17 × tool)

### 6.1 Conversion (DOCX → PDF vs Word)

| Version / tool | Newest result file | Rows | Date in file | Where |
|---|---|---|---|---|
| **jubarte 0.17.0** | `results/docx_to_pdf_jubarte_0.17.0.json` | 6 427 (`corpus/word:all`) | `generated_at 2026-11-01T11:03:17Z` | **on `origin/main`** ✔ |
| jubarte 0.17.0 (docxide metrics) | `results/docxide_metrics_jubarte_0.17.0.json` | 6 427 | `2026-11-01T13:28:36Z` | **on `origin/main`** ✔ |
| jubarte 0.17.0 (per-release sample) | `results/release_0.17.0_evidence/docx_to_pdf_jubarte_0.17.0_sample.json` | 600 | `2026-11-01T05:22:31Z` | **local only** (evidence folder untracked; `release_info/` six files on `origin/main`) |
| jubarte 0.17.0 (PDFs) | `results/jubarte_0.17.0_docx_to_pdf_work` | — | — | local only (`ndb-105` too); scores on main, **PDFs on neither main nor HF** |
| **jubarte 0.16.0** | `results/release_0.16.0_evidence/docx_to_pdf_jubarte_0.16.0_sample.json` | 600 | `2026-10-31T04:12:19Z` | **local only** |
| **jubarte 0.15.0** | `results/release_0.15.0_evidence/docx_to_pdf_jubarte_0.15.0_sample.json` | 600 | `2026-10-30T03:42:57Z` | **local only** |
| **jubarte 0.14.0** | `results/docx_to_pdf_jubarte_0.14.0.json` | 6 427 | `2026-10-25T18:45:50Z` | **on `origin/main`** ✔ |
| jubarte 0.14.0 (docxide metrics) | `results/docxide_metrics_jubarte_0.14.0.json` | 6 427 | `2026-10-25T20:01:53Z` | **on `origin/main`** ✔ |
| jubarte 0.14.0 (per-release sample) | `results/release_0.14.0_evidence/docx_to_pdf_jubarte_0.14.0_sample.json` | 600 | `2026-10-25T04:52:01Z` | on `origin/main` ✔ (20 files) |
| docxodus (v1 cohort) | `results/docx_to_pdf_v1_89/full.json` | 2 925 (`corpus/word:list`) | `2026-10-28T06:10:44Z` | **on `origin/main`** ✔ |
| soffice (v1 cohort) | `results/docx_to_pdf_v1_89/soffice_full.json` | 2 925 | `2026-10-28T08:51:17Z` | **on `origin/main`** ✔ |
| soffice (corpus-wide) | `results/docx_to_pdf_soffice_26.8.0.3_corpus-all.json` | 6 427 | `2026-10-01T08:32:03Z` | **on `origin/main`** ✔ |
| docxide-pdf (corpus-wide) | `results/docx_to_pdf_docxide_0.17.1_corpus-all.json` | 6 427 | `2026-10-01T08:32:03Z` | **on `origin/main`** ✔ |
| docxodus (corpus-wide) | — | — | — | **no corpus-wide docxodus run exists** (only the v1 cohort and the redline lane) |
| Apryse (corpus-wide) | `~/temp/T/ndb-apryse-keep/apryse_conv_line.jsonl` | per-document lines | — | **local only** (HF `outputs/docx_to_pdf_v1_apryse` = the v1 cohort only) |

### 6.2 Redlines (vs Word's compare)

| Version / tool | Newest score file | Rows | Where |
|---|---|---|---|
| **jubarte 0.17.0 (full lane)** | `results/redlines_0929_full/scores_jubarte-0.17.0.json` | **3 454** | **on `origin/main`** ✔ (HF copy 4 207 923 B) |
| jubarte 0.17.0 (sample) | `results/redlines_0929_full/scores_jubarte-0.17.0_sample_redline_0.17.0.json` | **593** | **local only**, but ✔ on HF `outputs/redlines_0929_full/run/` |
| jubarte 0.17.0 (outputs) | `results/redlines_0929_full/jubarte-0.17.0/{docx,pdf_by_word}` | 2 611 / 2 566 files | **local only**, ✔ on HF |
| jubarte 0.17.0 (stamp-matched) | `results/redlines_0929_full/scores_jubarte-0.17.0_stamp_matched_sample300_seed0.json` | 300 | local only, ✔ on HF |
| **jubarte 0.16.0 (sample)** | `results/redlines_0929_full/scores_jubarte-0.16.0_sample_redline_0.16.0.json` | **593** | **local only**, ✔ on HF (no full lane, no `origin/main`) |
| **jubarte 0.15.0 (sample)** | `results/redlines_0929_full/scores_jubarte-0.15.0_sample_redline_0.15.0.json` | **593** | **local only**, ✔ on HF (no full lane) |
| **jubarte 0.14.0 (full lane)** | `results/redlines_0929_full/scores_jubarte-0.14.0.json` | **3 414** | **on `origin/main`** ✔ |
| jubarte 0.14.0 (sample) | `results/redlines_0929_full/scores_jubarte-0.14.0_sample_redline_0.14.0.json` | 577 | **on `origin/main`** ✔ |
| docxodus (sample 0.17.0) | `results/redlines_0929_full/scores_docxodus_sample_redline_0.17.0.json` | 575 | local only, ✔ on HF |
| docxodus (full lane) | `results/redlines_0929_full/scores_docxodus.json` | 2 612 | **on `origin/main`** ✔ |
| superdoc (full lane) | `results/redlines_0929_full/scores_superdoc.json` | 380 | **on `origin/main`** ✔ (locally pruned to shards with logs only) |
| **Apryse (full lane)** | `results/redlines_0929_full/scores_apryse.json` | **2 524** | **local only**, ✔ on HF `run/scores_apryse.json` |
| Apryse (stamp-matched) | `results/redlines_0929_full/scores_apryse_stamp_matched.json` | 2 509 | local only, ✔ on HF |
| Apryse (outputs) | `results/redlines_0929_full/apryse/` | 8 666 files | **local only** (HF `outputs/redlines_0929_full/apryse` exists) |
| docxodus 0.17.0 (Word PDFs) | `results/hub_redlines_0929_full/docxodus/pdf_by_word/` | 586 files | local only, ✔ on HF (1 933 files) |

> **Note on the “593”.** The *sample list* `sample_redline_0.17.0.csv` has **600 rows**; the *score file* has **593** rows — 7 sampled pairs produced no score (ITT-zero failures). The same 600/593 split holds for 0.15.0 and 0.16.0 (593 scored). Earlier releases scored 575–600 of 600 (`scores_*_sample_redline_0.14.0.json` = 577).

### 6.3 The published-site side (newest)

| Artifact | Value |
|---|---|
| `cases/index.json` `generated` | `2026-11-02T22:13:53.191414Z` |
| `speed.json` `generated` | `2026-11-02T22:13:53.764231Z` |
| Release | `release/ndb-v0.0.1` (`6eea68ef`), `release/ndb-v0.0.1/results/scores.json` on HF |
| `speed.json` tasks | `redlines` **available** (10 000/10 000 for docxodus, jubarte-rust, jubarte-rust-inproc, jubarte-wasm; superdoc 798/10 000; docxodus-csharp-inproc 1 159/10 000), `pdf` **not measured yet** |
| Old gh-pages `/speed/` | both sections present (`<h2>Redline speed, 10,000 planned pairs</h2>`, `<h2>DOCX to PDF</h2>`, 3 tables) — the PDF section is **newer** than the release's `speed.json`, so the release is one task behind the published page |

---

## Facts the plan must respect

1. **The release bundle is a snapshot of the published pages, not a re-measurement.** `ledger/cases_adapter.py` is the only reader of the published HTML; its output is pinned as `results/release/legacy_cases.jsonl` (22.9 MB) and uploaded to HF at `inputs/legacy_cases/8b862e4519757a01fbfe3f1157d60614be2522da24758fbe6e521bc3c5001455.jsonl`. The emitter (`ledger/cases.py`) reads stores, not pages.
2. **21 groups / 7 422 cases** — 13 conversion shards (5 193) + 8 redline shards (2 229). The kind of every group is *policy*, registered in `bench_ops/models.py:288` (`GROUP_KINDS`), not inferred from the name.
3. **The docxide-pdf clone is gone** (`~/temp/T/docxide_compare/docxide-pdf` is a dangling symlink). The 208 fixture cases and the 1 660 corpus cases cannot be re-scored here; only the *scorer binary* survives (`site/_build/site_1029/bin/page-metrics`, SHA256 `2213ff317059873a15e9605825387a6f41e1514a21a1c9c7e4b7187a7f37925c`).
4. **Rasterisation is uniform**: mutool, 150 DPI for scoring, 180 DPI for shown pages, ×2/3 LANCZOS → 16-colour median-cut → **lossless WebP method 4**; `mutool clean -gggz` → `pages.pdf`; page cap **3** (the v1 cohort uses a per-case `shown` list). Page images live on R2 `pub-92c80eda1059428394970066c8500eda.r2.dev/` under 8 prefixes (`gh-pages-tree/{docxide,corpus,redlines}`, `d2p/{cases,corpus}`, `redlines/redlines`, `d2p_v1/v1`, `wordcraft1014/wordcraft1014`, `renew1028/d2p`, `renew1030/{redlines,ar}`).
5. **Per-release samples are 600 + 600**, seed **20261003**, drawn **2026-11-01** for 0.17.0. Conversion quotas 150/150/117/183 by state; redline 600 compares = 600 pairs (one per pair), scarce states whole, `tracking_without_comments` fills by document family.
6. **Only jubarte is re-run per release.** Docxodus redlines only pairs whose Word PDF is missing (the rest are fetched from HF); soffice converts only fixtures without a render and is then re-scored with `--score-only`. The full-corpus flow (`release_jubarte.py`, stages install→convert→metrics→redlines→report→publish) runs jubarte only.
7. **`results/site_fixtures_860.csv` = 800 (pick_site_fixtures.py, seed 20261001) + 60 extra**; it is tracked on `origin/main`. The 240 redlines are drawn by `jubarte-site/scripts/site_fixtures.py:212` (state × score band, `sqrt` weights, seed 20261001) and **6 of the 240 were dropped** → 234 published.
8. **The 234 jubarte.pro redline pairs are all real bench pairs** (234/234 in `gen_pairs.csv`), 42 of them are in the 0.17.0 sample, and **123 are in no published ndb group**. 122 of those 123 have *every* input locally and on HF (corpus DOCX, Word compare oracle, jubarte 0.17.0 redline DOCX + Word PDF); the 234 staged file sets are on HF at `site/redline/r-<10hex>/`. Nothing is lost.
9. **The 860 conversions are all inside `corpus/word:all` (6 427)** and 93 of them are in the 0.17.0 per-release conversion sample.
10. **`corpus/word:all` = 6 427 documents** (DOCX with a Word PDF; 7 228 DOCX exist in the four states alone; `corpus/word` holds 6 428 in the four states + 800 in `accept_all`/`reject_all`/`pools`).
11. **The 11 modified files are behind-the-branch, not real edits** — `results/v1-docx-to-pdf-89` is an *ancestor* of `origin/main`. 7 of the 114 untracked entries are already on `origin/main`.
12. **Three things exist only locally and nowhere else (main or HF)**: `results/release_0.{15,16,0}_evidence/` (≈ 943 MB), the `jubarte-0.15.0` / `jubarte-0.16.0` full redline lanes (2 400 files), the `results/jubarte_0.17.0_docx_to_pdf_work` candidate PDFs, and `ndb-apryse-keep/apryse_conv_line.jsonl`. Everything else in the untracked set is mirrored on HF.
13. **The `gh-pages` branch (7 commits) holds the only copy of the scorer provenance** (`site_1029/scorer-source/` = bench main `3121c4988d72bf5cd93607c18cb50e3d713ce338`, and the byte-identical `bin/page-metrics`).
14. **The new bench-viewer is a superset of the old viewer**; the only features it does *not* carry are per-page scores and the link-to-source-files affordances, both of which only jubarte.pro has. benchmarks.law is a faithful re-implementation of the old viewer plus a mobile layout plus `editors/` and `home/` pages.
15. **The release’s `speed.json` is behind the published `/speed/` page** — the release says “DOCX to PDF: not measured yet”, the published page has a DOCX-to-PDF section.
16. **The `sample_redline_<v>.csv` has 600 rows; the score file has 593.** Any plan that says “593 rows” is talking about scored rows, not the sample.
17. **The ndb release contract is a hard dependency**: `bench_ops` is now a real dependency of the bench (`2d278c90 build: bench-ops contract as a real dependency`), and the release writes `cases/index.json` + one shard per group, `shard_path()` = `cases/<kind>/<group with "/"→"--">.json`.
18. **Open PRs 102/104/108/109 are unmerged** and PR #101/#103 fix the hub upload and the Word-export refusal path — both are in the *release* flow, so a release run on today’s `main` would still have the bugs they fix.

DONE
