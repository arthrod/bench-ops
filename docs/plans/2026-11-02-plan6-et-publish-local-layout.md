# Plan 6 — editors_trial publish, `.work` TTL, satellite archive

Issues: arthrod/editors_trial#74, arthrod/bench-ops#7. Depends on Plan 2 (`bench publish`, `bench gc`). Tracking: arthrod/bench-ops#8.

## Part A — `bench publish et <tag>`

No new publishing code: editors_trial gets a `[repos.release]` section and goes through Plan 2's `bench publish`.

```toml
[repos.release]          # under the editors_trial entry in repos.toml
hf_repo = "arthrod/editors_trial-artifacts"
files = ["out/facts.json", "out/release.json", "reports/*.md", "reports/*.docx", "reports/figures/**"]
```

- The HF dataset `arthrod/editors_trial-artifacts` is created **private** by Arthur (outward-facing; `--private` is ignored on an existing repo, so check visibility first). Publishing does not create repos.
- `served` stays empty: benchmarks.law reads the bundle at build time (`bench fetch`), so nothing is served from R2 yet.
- RELEASING.md §7 gains one step after the GitHub release: `bench publish editors_trial et-vX.Y.Z`, then push the ledger commit and tag it prints. The site step becomes `bench bump editors_trial` in benchmarks-law, then the sync commands.
- Test (bench-ops): `stage()` over an editors_trial-shaped tree with the globs above picks up exactly the expected files and refuses `.env`.

## Part B — `.work` TTL

`bench work [--older-than 14d] [--yes]` lists worktrees under `~/bench/.work/`, each with age (last commit), dirty count, and whether its branch is merged into, or pushed to, origin. It removes (with `git worktree remove`, never `--force`) only those that are older than the TTL, clean, and merged or pushed, and only with `--yes`. Everything else is listed with the reason it stays.

## Part C — satellite archive

Satellites are the loose copies beside the checkouts (`ndb-105`, `ndb-apryse`, `ndb_*_2026102x`, `colab_bench`, root `*.log`).

- `bench archive <dir> <name> [--yes]` publishes a directory to the private `arthrod/neurotic_docx_bench-archive` dataset under `archive/<name>/` with Plan 2's machinery (stage → chunked upload → witness → manifest → HF tag `arc-<name>`), and appends the entry to `~/bench/archive/index.jsonl` (bench-ops repo). The `Tag` pattern gains `arc-[a-z0-9-]+`.
- Then `bench gc <dir>` deletes only files whose sha256 is in a verified archive manifest, with `--yes`.
- A dirty git satellite (ndb-105 and ndb-apryse have 7 changes each) is archived with its working tree; nothing is deleted before Arthur has seen the `bench gc` verdict output and said yes.

## Acceptance

- `bench publish editors_trial et-v0.9.3 --dry-run` lists the report bundle and refuses nothing unexpected.
- `bench status` shows both producers verified on HF after the first real publishes.
- `bench work` on today's `.work/` lists every worktree with a reason, removes nothing without `--yes`.
- Satellites leave `~/temp/T` only after the two witnesses and Arthur's yes.
