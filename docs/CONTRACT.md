# The storage contract

Every byte the benches produce has exactly one role per surface. If you cannot name the role, do not write there.

| Surface | Role | Rule |
|---|---|---|
| **Hugging Face** datasets: `arthrod/neurotic_docx_bench` (runs, results, `site/` fixtures, `release/<tag>/`), `arthrod/neurotic_docx_bench-fixtures` (corpus); planned: `arthrod/editors_trial-artifacts` (Plan 6), private `arthrod/neurotic_docx_bench-archive` (bundles, backups, satellites; Plan 2) | **the record** | immutable once a tag's manifest is written; pinned by revision; 320 commits/h, so uploads are batched |
| **R2** `neurotic-docx-bench-pages`, `benchmarks-law-media`, `jubarte-live-bench-artifacts` | **serving cache** | holds only what a site serves (`FileEntry.served`), under `<tag>/`; always rebuildable from HF + manifest; may be wiped |
| **Git** (four repos) | code, configs, **small ledgers**: `release/index.jsonl`, `bench.lock`, `data/facts.jsonl`, REPORT/RESULTS markdown | no file over 2 MB, no binaries under data dirs (`bench hook size-guard`); no history rewrites |
| **Workers / GitHub Pages** | apps and viewers | point at R2; never embed data that is not in a tagged release |
| **Local disk** `~/bench/.work/<repo>/<run>` | scratch | deletable only by the two-witness rule |

## The Hugging Face datasets (surveyed 2026-11-02)

None of these is a copy of another; each holds bytes the others lack (sha256 cross-check of LFS files against the record). Do not "freeze" or delete one as redundant.

| Dataset | Visibility | Holds | LFS files also in the record |
|---|---|---|---|
| `arthrod/neurotic_docx_bench` | public | **the record**: runs, results, `site/` fixtures, `release/<tag>/` (67,715 files, 14.2 GB) | n/a |
| `arthrod/neurotic_docx_bench-fixtures` | public | the corpus (DOCX/PDF oracle tree), `bench fixtures download` | 1,332 of 4,709 |
| `arthrod/neurotic_docx_bench-history` | public | files removed from git history, with `manifest.tsv` | 9 of 1,072 |
| `arthrod/neurotic-docx-bench-artifacts` | public | working evidence that is not corpus (`really_broken/`, `from_jubarte-redlines/`) | 80 of 584 |
| `arthrod/neurotic_docx_bench-git-archive` | private | git bundles of ndb from the 2026-10-25 rewrite, filter-repo maps, D1 exports | 0 of 4 |
| `arthrod/neurotic-docx-backup` | private | the pre-rewrite bundle of 2026-10-22 | 0 of 1 |

## Two witnesses before any delete

A local file may be deleted only when its sha256 is listed in a release manifest **and** that manifest has been verified against Hugging Face (`bench status` witness; `bench gc` in Plan 2), and against R2 too if the file is served. This is the rule that would have kept the grok_run outputs lost in October 2026.

## Releases

- Producers publish `release/<tag>/{manifest.json, scores.json, cases/*.json}` to HF and append one line to `release/index.jsonl` (`LedgerEntry`). Tags: `ndb-vX.Y.Z[.n]`, `et-vX.Y.Z`.
- Every release stays recorded: the ledger only grows, and each tag's manifest stays on HF at its pinned revision.
- Consumers pin tags in `bench.lock` (`<producer>=<tag>` per line) and render `scores.json` through `@jandira-tech/bench-viewer` with their theme. Frontends show the latest score; "latest" means the lock was bumped, never a runtime fetch.

## Hooks

- Hooks come from `bench-ops/lefthook/common.yml` through lefthook `remotes:`; each repo's `lefthook.yml` is the copy of `lefthook/REPO-lefthook.yml`.
- They run locally in seconds, all on pre-commit: size guard, ripsecrets, and `bench status --fail-on-stale`. No CI gate.
- No pre-push or post-* hooks: those belong to git-lfs, and lefthook taking them over makes pushes from branches without this config skip LFS uploads silently.
- Escape hatches: `LEFTHOOK=0` skips all hooks; `BENCH_ALLOW_STALE=1` lets a commit through with a lock behind its producer.
- Requirements on each machine: `uv tool install --editable ~/bench` (or `uv tool install git+https://github.com/arthrod/bench-ops`), `brew install lefthook ripsecrets`.
- Lefthook fails open: if the first fetch of the shared config fails, it installs no jobs. `bench sync` checks for that and exits 1 with `HOOKS NOT ACTIVE`; after one successful fetch the cached copy works offline.
- `ref: main` is deliberate (decision D5): a hook change lands once, here, instead of as four pin bumps. The trust boundary is push access to `arthrod/bench-ops` main.
- `bench status --repo <path>` matches a checkout by configured path, else by origin URL, so clones and worktrees elsewhere get the same release-state check.
