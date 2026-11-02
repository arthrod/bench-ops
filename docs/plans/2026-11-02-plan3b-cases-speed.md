# Plan 3b — cases and speed in the ndb release; every viewer renders from it

Issue: jandira-tech/neurotic_docx_bench#115 (follow-up to 3a, PR #117). Survey: `docs/plans/reviews/plan3b-survey.md` (MiniMax-M3, 2026-11-02). Tracking: arthrod/bench-ops#8.

## Decisions

- **Ownership.** The ndb release owns `cases` (conversion + redline) and `speed` (the 10k-input page). editors_trial's release owns the editors scorecard. The home page is a consumer template fed by counts from both releases, not a release artifact.
- **Contract.** bench-ops adds `Case`, `Cases`, `Engine`, `MetricDiagnostics`, `SpeedTable`, `Speed` to `models.py`, as proposed in the survey §2 (strict, frozen). Nulls in `scores` are kept (a metric that could not be computed); a failure keeps its engine in `scores` with its error.
- **Size.** Cases are ~22 MB compact. They are **not** committed: `bench report` writes them to `results/release/` (git-ignored), sharded one file per group (`cases/<kind>/<group>.json` plus `cases/index.json` listing groups, counts and engines), compact JSON (`models.dumps_compact`, sorted keys, no indent, `exclude_none` for optional metadata only). The release bundle (Plan 2 `files`) lists them; HF is their record.
- **Images.** Page WebPs and PDFs stay on R2 where they are. Cases store paths relative to one `assets_base` (public R2 URL) in `cases/index.json`. Publishing does not upload images; the witness checks that every referenced key exists on R2 (HEAD, batched) — a missing image makes publish refuse. Sha256 of R2 objects comes with the R2 content witness (`upload_r2.py` change, Plan 2 rev 3).
- **One canonical source.** ndb gains `ledger/cases.py` emitting cases from its stores and run directories (not by parsing published HTML). Until it reproduces the published `DATA`, a one-time adapter imports today's inline `DATA` (the authoritative published copy; `site/redlines/cases.json` is stale), and a test diffs emitter vs adapter; the adapter is deleted when they agree.
- **Speed.** `speed.json` carries the 10k page's view (per-pair raw rows aggregated), named as such; `results/speed.jsonl`'s leaderboard view stays in RESULTS.md. Notes are plain text (no HTML).
- **bench-viewer.** Ports the case browser, redline browser, case strip and speed tables from the hardened benchmarks.law variant (it already escapes data strings), as framework-free modules that fetch `cases/index.json` and one shard at a time. Every data string escaped. Mobile behavior: benchmarks.law's.
- **gh-pages.** `build:pages` builds the whole site (leaderboard, cases, redlines, speed) from a release bundle into a directory; Arthur approved the orphan force-push after he sees it locally.

## Acceptance

- Emitter output equals the adapter output for every case (test).
- `bench publish ndb <tag> --dry-run` lists scores.json, speed.json and the case shards.
- The built site, served locally, shows the same cases, scores and images as today's gh-pages (checked in a real browser next to the live site).
