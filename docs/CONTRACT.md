# The storage contract

Every byte the benches produce has exactly one role per surface. If you cannot name the role, do not write there.

| Surface | Role | Rule |
|---|---|---|
| **Hugging Face** datasets: `arthrod/neurotic_docx_bench` (runs, results, `site/` fixtures, `release/<tag>/`), `arthrod/neurotic_docx_bench-fixtures` (corpus); planned: `arthrod/editors_trial-artifacts` (Plan 6), private `arthrod/neurotic_docx_bench-archive` (bundles, backups, satellites; Plan 2) | **the record** | immutable once a tag's manifest is written; pinned by revision; 320 commits/h, so uploads are batched |
| **R2** `neurotic-docx-bench-pages`, `benchmarks-law-media`, `jubarte-live-bench-artifacts` | **serving cache** | holds only what a site serves (`FileEntry.served`), under `<tag>/`; always rebuildable from HF + manifest; may be wiped |
| **Git** (four repos) | code, configs, **small ledgers**: `release/index.jsonl`, `bench.lock`, `data/facts.jsonl`, REPORT/RESULTS markdown | no file over 2 MB, no binaries under data dirs (`bench hook size-guard`); no history rewrites |
| **Workers / GitHub Pages** | apps and viewers | point at R2; never embed data that is not in a tagged release |
| **Local disk** `~/bench/.work/<repo>/<run>` | scratch | deletable only by the two-witness rule |

## Two witnesses before any delete

A local file may be deleted only when its sha256 is listed in a release manifest **and** that manifest has been verified against Hugging Face (`bench status` witness; `bench gc` in Plan 2), and against R2 too if the file is served. This is the rule that would have kept the grok_run outputs lost in October 2026.

## Releases

- Producers publish `release/<tag>/{manifest.json, scores.json, cases/*.json}` to HF and append one line to `release/index.jsonl` (`LedgerEntry`). Tags: `ndb-vX.Y.Z[.n]`, `et-vX.Y.Z`.
- Every release stays recorded: the ledger only grows, and each tag's manifest stays on HF at its pinned revision.
- Consumers pin tags in `bench.lock` (`<producer>=<tag>` per line) and render `scores.json` through `@jandira-tech/bench-viewer` with their theme. Frontends show the latest score; "latest" means the lock was bumped, never a runtime fetch.

## Hooks

- Hooks come from `bench-ops/lefthook/common.yml` through lefthook `remotes:`; each repo's `lefthook.yml` is the copy of `lefthook/REPO-lefthook.yml`.
- They run locally in seconds: size guard and ripsecrets on commit, `bench status --fail-on-stale` on push. No CI gate.
- Escape hatches: `LEFTHOOK=0` skips all hooks; `BENCH_ALLOW_STALE=1` lets a push through with a lock behind its producer.
- Requirements on each machine: `uv tool install --editable ~/bench` (or `uv tool install git+https://github.com/arthrod/bench-ops`), `brew install lefthook ripsecrets`.
- Lefthook fails open: if the first fetch of the shared config fails, it installs no jobs. `bench sync` checks for that and exits 1 with `HOOKS NOT ACTIVE`; after one successful fetch the cached copy works offline.
- `ref: main` is deliberate (decision D5): a hook change lands once, here, instead of as four pin bumps. The trust boundary is push access to `arthrod/bench-ops` main.
- `bench status --repo <path>` matches a checkout by configured path, else by origin URL, so clones and worktrees elsewhere get the same pre-push check.
