# Plan 8 — publish and deploy from a clean tree, runnable from bench-ops; jubarte.pro without local fixtures

Date: 2026-11-05. Trigger: `bench publish ndb ndb-v0.0.4` failed with `stale: RESULTS_DETAILED.md`.

## What went wrong (evidence)

1. **publish runs in whatever the shared checkout happens to be.** `repos.toml` points `ndb` at
   `~/temp/T/neurotic_docx_bench`, which was on `results/v1-docx-to-pdf-89` (an old feature branch,
   20 modified and 121 untracked files, no `scripts/release_views.py`). `publish()` runs `before`,
   stages and commits the ledger in `repo.local_path` regardless of branch or dirt. The stale
   check failing was the lucky outcome: from a branch whose pages happened to be current it would
   have published that branch's data and committed the ledger onto it.
2. **The release bundle is not reproducible from git.** `results/release/` is git-ignored. Whether a
   publish ships the versioned 0.17.0 shards or the legacy bundle depends only on which command last
   wrote that folder in that particular directory. In a clean `origin/main` worktree,
   `release_views.py --check` reports every shard stale; after `release_views.py` (no version) it
   says current but ships the *legacy* bundle, not 0.17.0's.
3. **No way to run a repo's command from bench-ops.** In `~/T/bench`, `uv run bench report` is
   bench-ops' own `bench` (no `report` command). Every step needs a `cd` into the right checkout,
   and nothing says which checkout that is.
4. **repos.toml points at a path that no longer exists.** `benchmarks-law` →
   `~/temp/T/key-digest-runner/.scratch/briefs_agreements/benchmarks-law` is gone (no checkout of it
   anywhere on the machine). `bench bump/fetch/deploy benchmarks-law` cannot run at all.
5. **Consumer deploy does not check the checkout.** `bench deploy <consumer>` runs from the
   configured path on any branch, dirty or behind origin.
6. **jubarte.pro ships 8,183 local fixture files (727 MB)** in `jubarte-site/public/fixtures` as
   Worker assets (legacy Cases page, home strip). The bench-release pages (`/use-cases/bench*`) already
   draw their images from the ndb release on R2. Two Cases sources, one of them a local copy.

## Design

### bench-ops (one PR, TDD)

| # | Change | Test first |
|---|---|---|
| P1 | `bench publish` never works in the configured checkout. It fetches `origin`, then creates (or reuses, on resume) the worktree `<BENCH_ROOT>/.work/release-<tag>` on branch `release/<tag>` from `origin/<branch>`, and runs `before`, staging, the ledger commit and the git tag there. The shared checkout's branch and dirt no longer matter. | a publish against a checkout on a dirty feature branch stages origin/main's file and commits on `release/<tag>`; the feature checkout is untouched |
| P2 | `--version X.Y.Z` fills `{version}` in `before` commands. A `before` command naming `{version}` without `--version` is refused before anything runs; unknown placeholders are refused at config load. | refusal without `--version`; substitution; config rejects `{foo}` |
| P3 | After `before`, a tracked change in the release worktree refuses the publish: the bundle is only published when the pages it is built from are the committed ones. | `before` that edits a tracked file → PublishError |
| P4 | `--push`: push `release/<tag>` and the tag, then `gh pr create --base <branch>`. Without it the push and PR commands are printed (unchanged behaviour). | runner records push + pr commands; without `--push` none run |
| P5 | `bench exec <repo> <command…>` runs any command in a repo's checkout from anywhere (`bench exec ndb uv run bench report --check`), with the clean environment pipelines use. `--main` runs it in a fresh detached `origin/<branch>` worktree under `.work/` (removed afterwards when nothing in it changed). | cwd and env of the runner; unknown repo refused |
| P6 | `bench deploy <consumer>` refuses a checkout that is not on its configured branch, has tracked changes, or is behind `origin/<branch>`. | three refusals + the happy path |
| P7 | `repos.toml`: ndb `before` = sync, `bench report --check`, `release_views.py --release-version {version}`, `release_views.py --check`, `bench results upload`; benchmarks-law loses its dead path override (bench sync clones it under BENCH_ROOT); the jubarte pipeline's publish step passes `--version {version} --push`. | config loads; pipeline plan prints the new command |
| P8 | AGENTS.md: the order of operations from bench-ops, `bench exec`, the release worktree. | — |

### Run (after the PR merges)

`bench publish ndb ndb-v0.0.4 --version 0.17.0 --push` → release PR on ndb → merge.

### jubarte-app (issue + PR)

- `bench bump ndb && bench fetch` → `bench.lock` at ndb-v0.0.4.
- Remove the local fixtures: `/use-cases` becomes the bench-viewer Cases from the pinned release (the
  route `/use-cases/bench*` already exists with the jubarte skin); the home strip and any other reader of
  `public/data/cases-*.json` read the release shard instead, with page images from the release's
  R2 `assets_base`. Drop `site_fixtures.py`, `fixtures.lock`, the `fixtures:*` scripts and the
  `/fixtures/*` asset rule. `public/fixtures` is deleted after the build no longer needs it.
- Test: the build fails if anything is written under `public/fixtures`; vitest + `pnpm bench:check`.
- `bench deploy jubarte-app --yes`; check jubarte.pro in Chrome (home, /use-cases, /benchmark).

### benchmarks-law

`bench sync` (clones it under BENCH_ROOT), `bench bump ndb && bench fetch`, PR, merge,
`bench deploy benchmarks-law --yes`, Chrome check.

### Cleanup

Remove `.work/ndb-pubtest`, the release worktree after its PR merges, ndb-accept/root-accept once
v0.0.4 is published, and the jr worktrees once their PRs merge.

## Not changed

The publish state machine (stage → upload → witness → manifest → tag → ledger) and its resume
semantics; the release contract models; the R2 `served` refusal.
