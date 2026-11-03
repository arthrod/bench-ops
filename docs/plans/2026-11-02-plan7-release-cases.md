# Plan 7 — every jubarte release scores 860 conversions + 600 redlines in ndb; ndb emits the cases ("modified B")

Tracking: arthrod/bench-ops#8. Survey: `docs/plans/reviews/survey-b.md` (copy of /tmp/survey-b.md, MiniMax-M3, 2026-11-02).

## Arthur's decisions (2026-11-02)

1. `bench deploy jubarte` scores **only the new jubarte version**. No other tool is re-run.
2. Every release converts and scores the **860** conversion fixtures (`results/site_fixtures_860.csv`).
3. Every release redlines and scores a **varied, representative 600-pair** redline set.
4. jubarte.pro's redline pairs that ndb never published go into the bench script.

## Facts that shape it (measured, survey-b)

- Both sites rasterise with **MuPDF**: ndb's gh-pages `_build` calls `mutool draw` (150 DPI scoring, 180→120 DPI shown, 3 pages, 16-colour lossless WebP); jubarte.pro's `site_fixtures.py render` calls PyMuPDF (`get_pixmap`, width 640, up to 12 pages, WebP). Same rasteriser, different DPI / page cap / encoding.
- ndb's per-release samples are already **fixed**: `sample_redline_<v>.csv` and `sample_conversion_<v>.csv` hash identically for 0.15.0, 0.16.0 and 0.17.0 (seed 20261003). Reusing other tools' scores is sound only because the set does not move.
- The jubarte.pro redline pairs: 234 published (240 drawn, 6 dropped). **111** are in some ndb release group, **123** are not (the "144" I gave earlier came from a stricter id match; 123 is the measured number). 122 of the 123 have every input locally and on HF; jubarte 0.17.0 failed one. Docxodus Word PDFs exist for 87 of the 123 on HF; superdoc for none.
- The 860 conversions are all in `corpus/word:all`; soffice and docxide renders exist for all 860 (the picker required them). No corpus-wide docxodus conversion exists.
- 42 of the 234 redline pairs and 93 of the 860 conversions are in today's 600/600 samples.

## Decisions

### D1. The fixed sets (pinned once, versioned)
- `results/sets/convert_860.csv` = `site_fixtures_860.csv` + `docx_sha256` + `word_pdf_sha256`.
- `results/sets/redline_600.csv` = all **234** jubarte.pro pairs + **366** pairs from today's 0.17.0 sample, chosen by `draw_balanced` to keep the scarce states whole (clean 2, with_comments_clean 3) and spread `tracking_without_comments` / `with_comments_tracking` over document families. Columns as `sample_redline_<v>.csv` + sha256 of base, next, oracle.
- Each set has a version (`set_version` in the CSV header comment and in `scores.json` provenance). Changing a set is a new set version, never an edit, so release-to-release numbers stay comparable.
- Rejected alternative: the 600 sample ∪ the 192 jubarte.pro pairs not in it (792). More work per release than Arthur's 600.

### D2. One-time backfill for the other tools (not per release)
Rule 1 forbids re-running other tools per release, so their lanes on the new pairs must exist once:
- redlines: docxodus for the 36 + any of the 366 lacking a Word PDF (fetched from HF first, as today), superdoc for the pairs it lacks; Word compare oracle already exists for all.
- conversions: soffice + docxide already cover the 860.
Backfilled outputs and scores go to HF `outputs/` with the set version. After that, a release never touches another tool; a pair a tool lacks shows that tool as "not run", never zero.

### D3. `bench deploy jubarte <version>` (bench-ops) = score, publish, then deploy
1. `release_jubarte_info <v> --convert-set convert_860 --redline-set redline_600` in ndb (jubarte only: install from the GitHub release + SHA256SUMS, generate, Word export, measure, convert, score).
2. ndb `bench report` → `scores.json` (+ the per-release tables, the six `release_info` files) and `ledger/cases.py` → `cases/{convert,redline}/release-<v>.json` shards for the 860 + 600, with the other tools' lanes read from the backfill.
3. Render the pages (one renderer, see D4) and upload images to R2 under `release/<v>/`; witness.
4. `bench publish ndb ndb-v<next>` (HF record + ledger + tag).
5. In jubarte-app: `bench bump ndb && bench fetch && release.sh deploy` (the deploy itself still needs Arthur's go each time).
Steps 1–2 refuse to start unless Word answers and PRs #102 / #104's fixes are on main.

### D4. One renderer, ndb's
ndb's `_build` pipeline moves to `main` as `src/neurotic_docx_bench/pages/render.py`: mutool (MuPDF), 150 DPI scoring, shown pages at 180→120 DPI, 16-colour lossless WebP. Page cap raised from 3 to **12** with per-page scores kept, because jubarte.pro shows 12 pages and per-page scores today. `site_fixtures.py stage/upload/render` is deleted from jubarte-site once jubarte.pro renders from the ndb shards.

### D5. Viewer parity before anything is deleted (bench-viewer)
bench-viewer gains what only jubarte.pro has: per-page scores, links to the case's files on HF, up to 12 pages. It also gets random-case-on-open from the old viewer. jubarte.pro's Cases page switches to bench-viewer with the jubarte theme only after a side-by-side check by Arthur.

## Functionality at risk (must be handled before the step that would lose it)

| Risk | Where it lives today | Handling |
|---|---|---|
| Scorer provenance (`site_1029/scorer-source/`, `bin/page-metrics` sha 2213ff31…) | **only** on the gh-pages branch | Push gh-pages to `archive/gh-pages-2026-11-02` and upload `_build/` to HF before the orphan force-push. The approved force-push waits for this. |
| `release_0.15.0/0.16.0/0.17.0_evidence/` (~943 MB) | only Arthur's checkout | upload to HF `outputs/release_<v>_evidence/` (private archive dataset if size argues for it) |
| jubarte 0.15.0/0.16.0 full redline lanes (2 400 files) | only Arthur's checkout | upload to HF `outputs/redlines_0929_full/` |
| jubarte 0.17.0 candidate PDFs (6 427) | Arthur's checkout + ndb-105 | upload to HF |
| `ndb-apryse-keep/apryse_conv_line.jsonl` | only there | upload with PR #109's results |
| Apryse converter + redline lane | open PRs #108/#109 | merge (Arthur's call); not touched by this plan |
| Release-flow fixes | open PRs #102/#104 | merge before D3 runs |
| DOCX→PDF speed section | old gh-pages `/speed/` only; release `speed.json` says not measured | emit it into `speed.json` before gh-pages is replaced |
| jubarte.pro per-page scores, HF file links, 12 pages | jubarte.pro Cases viewer | D4 + D5 |
| Old viewer random-on-open | gh-pages viewer | D5 |
| Re-scoring the 208 docxide fixtures + 1 660 corpus cases | docxide-pdf clone and candidate PDFs are gone | not regenerated; they stay as the pinned legacy snapshot (`inputs/legacy_cases/8b862e45….jsonl`), images on R2. Stated, not hidden. |

Nothing is deleted by this plan. Every upload above is to Arthur's own HF datasets.

## Order (each step its own issue + PR)

0. Preserve: archive the gh-pages branch, upload the local-only results (table above).
1. ndb: `results/sets/convert_860.csv`, `redline_600.csv` + a test that every row's inputs exist (local or HF) and hashes match.
2. ndb: backfill run for docxodus/superdoc on the new pairs; scores to HF.
3. ndb: `release_jubarte_info --convert-set/--redline-set` (replaces the 600/600 draw; old flags kept for the archived evidence).
4. ndb: `pages/render.py` (moved from gh-pages `_build`), 12-page cap, per-page scores; `ledger/cases.py` emits release shards from the run directories.
5. bench-viewer: per-page scores, HF file links, 12 pages, random-on-open.
6. bench-ops: `bench deploy jubarte <v>` = D3.
7. jubarte-app: Cases page from the ndb shards (side-by-side for Arthur), then delete `site_fixtures.py` stage/upload/render and `fixtures.lock`.

## Acceptance
- A dry run `bench deploy jubarte 0.17.0 --no-deploy` re-runs only jubarte (process list shows no docxodus/soffice/superdoc), produces 860 + 600 scored cases, and its 0.17.0 numbers on the 42 / 93 overlapping cases equal today's within the scorer's tolerance.
- jubarte.pro Cases, rendered from ndb shards, shows the same 860 + 234 cases with per-page scores and file links (checked in a real browser next to the live site).
