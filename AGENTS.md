# AGENTS.md — bench-ops, the workspace of the docx benches

Read this first when you work on any of the bench repositories. It says which repo does what, which commands to run, and what happens, in order, from a finished bench run to the numbers on the public sites. Each repo's own `AGENTS.md` (or `RELEASING.md`) holds the detail; this file is the map and the order.

## The repositories

| Repo (name in `repos.toml`) | Local checkout | Role | What it does | Its own docs |
|---|---|---|---|---|
| `ndb` = jandira-tech/neurotic_docx_bench | `~/temp/T/neurotic_docx_bench` | producer | The DOCX fidelity bench: renders, rasterises and scores tools against the Microsoft Word oracle. Writes `results/*.jsonl` and the generated `RESULTS.md`; its `gh-pages` branch (worktree `site/`) is the review site. | `AGENTS.md`, `docs/PUBLISHING.md` |
| `editors_trial` = arthrod/editors_trial | `~/temp/T/editors_trial` | producer | The editors trial: measures browser DOCX editors, renders the reports (`out/facts.json` → `reports/`), builds the live demos. | `AGENTS.md`, `RELEASING.md` |
| `benchmarks-law` = arthrod/benchmarks-law | `~/temp/T/briefs_agreements/benchmarks-law` | consumer | benchmarks.law (Astro on Workers, arthur.law design). Shows the editors-trial reports and a copy of the ndb viewer. | `AGENTS.md`, `scripts/sync.ts` header |
| `jubarte-app` = arthrod/jubarte-app | `~/temp/T/1_jubarte-redlines/jubarte-app` | consumer | jubarte.pro (`jubarte-site/`, product-focused). Every number it prints is a record in `data/facts.jsonl`; the benchmark page is proved against the evidence before deploy. | `jubarte-site/scripts/release.sh` header, `scripts/facts.py` |
| jandira-tech/jubarte-redlines (not in `repos.toml`) | `~/temp/T/jubarte-redlines` | engine | The jubarte engine. Its release writes `release_info/website_data_<v>_<stamp>.jsonl` and the sample results JSONs that jubarte.pro's sample tables come from. | `scripts/release.sh` header |

`bench-ops` (this repo, `~/bench`) holds no data. It links the repos (`repos.toml`), defines the release contract (`src/bench_ops/models.py`), and ships the `bench` command and the shared git hooks (`lefthook/common.yml`).

## How data flows

```
ndb  ── bench run ──► results/*.jsonl ── bench report ──► RESULTS.md ──┬─► jubarte.pro full-corpus tables (release.sh bench)
 │                                                                    └─► benchmarks.law viewer copy   (pnpm sync:assets)
 └─ page images / fixtures ──► R2 neurotic-docx-bench-pages, HF arthrod/neurotic_docx_bench ──► gh-pages viewers

jubarte-redlines release ──► release_info/ ──► jubarte.pro sample tables (release.sh figures)

editors_trial ── facts.py / render_reports.py ──► out/, reports/ ──► benchmarks.law (pnpm sync:* + deploy)
```

Storage roles (full text: `docs/CONTRACT.md`): **Hugging Face is the record, R2 is a rebuildable serving cache, git holds code and small ledgers, Workers/GitHub Pages serve.** Nothing local is deleted before it is on HF (and on R2 if a site serves it).

## Machine setup (once)

```bash
git clone https://github.com/arthrod/bench-ops ~/bench
uv tool install --editable ~/bench          # the `bench` command; elsewhere: uv tool install git+https://github.com/arthrod/bench-ops
brew install lefthook ripsecrets
bench sync                                  # clone/fast-forward every repo, install hooks; exits 1 on HOOKS NOT ACTIVE
bench status                                # branch, dirt, stray worktrees, release ledger per repo (--offline skips HF); the `.work/` pen is not "stray"
bench bump <producer> [--repo PATH]         # pin the producer's latest release in a consumer's bench.lock (plan 4): reads the producer's last release/index.jsonl line in its checkout (repos.toml) and writes `<producer>=<tag> manifest=<sha256>`; creates the lock when missing, keeps every other producer's line, prints old -> new
bench fetch [--repo PATH] [--check]         # materialise every lock pin under <repo>/.bench/<producer>/ (plan 4): the manifest comes from the producer's HF dataset at the tag and must hash to the lock's manifest= sha; every listed file comes at the manifest's own pinned revision and must hash to its entry; paths land relative to release/<tag>/ (so .bench/ndb/results/scores.json) plus .bench/<producer>/manifest.json. Atomic per producer (scratch dir, rename); files already present with the right hash are skipped; private datasets need the logged-in hf token. `--check` is offline: it re-hashes .bench/ against the lock, names the first mismatch (extra files included) and exits 1 — the pre-commit hook and `bench deploy` use it
bench work                                  # list `.work/` worktrees; `--yes` removes one only when NOTHING of it can be lost: no tracked change, no untracked file, no git-ignored byte (`git status --porcelain --ignored --untracked-files=all` empty), not locked, inside the pen, and both clocks (worktree creation and last commit) older than the TTL — via `git worktree remove`, never `--force`. Verdicts run after `git fetch --prune origin` (one per parent repo), never on a stale remote-tracking ref; `--offline` skips the fetch and then removes nothing
bench archive <dir> <name>                  # snapshot a satellite (working tree **and** `.git`, untracked included) to the private `arthrod/neurotic_docx_bench-archive` dataset under `archive/<name>/`, tag `arc-<name>`, one line in `archive/index.jsonl` plus a manifest copy in `archive/<name>/`; symlinks are recorded in a `symlinks.json` sidecar. Refuses a public dataset and any file the ripsecrets scan flags (`--exclude GLOB` drops paths, recorded in the manifest). Resumable: a run that died mid-way is finished on rerun, never redone; a changed tree is refused. Then `bench gc <dir> --yes` may delete local files that THIS archive lists at the same relative path once its manifest verifies on HF — provenance, not bytes-that-match-anywhere (the git directory itself, however it is named, stays for hand removal)
```

Per-repo tooling the steps below need: `uv` (Python 3.14), `pnpm`, LibreOffice and Microsoft Word on macOS (ndb oracle and the Word gates), `wrangler` (`bunx wrangler login`, the token lives about an hour), `hf auth login`, `gh auth login`. Secrets stay in each repo's git-ignored `.env`; check their length, never print them.

`bench sync` only fast-forwards a clean checkout that is on its configured branch. A feature branch, a dirty tree or a diverged `main` is fetched and reported, never switched or merged.

## The hooks (every repo, no CI needed)

All five run on **pre-commit**, in about a second, from `bench-ops/lefthook/common.yml` (each repo's `lefthook.yml` only points there):

- `size guard`: no file over 2 MB, no binaries under `results/`, `out/`, `public/fixtures/`, `public/neurotic-docx-bench/`, `.bench/`.
- `secrets`: ripsecrets on the staged files.
- `ledger`: the ledgers may only grow — `release/index.jsonl` in the producers and `archive/index.jsonl` in BENCH_ROOT (bench-ops' own `lefthook.yml` runs the job too) — `bench hook ledger` refuses staged rewrites, deletions and renames of either. No glob: it runs on every commit, because lefthook's glob file lists exclude deletions and would skip the job exactly when it is needed.
- `release state`: `bench status --fail-on-stale` refuses a commit while the repo's `bench.lock` is behind its producer's latest release (no-op until the lock exists).
- `fetch check` (plan 4): when `bench.lock` is staged, `bench fetch --check` re-hashes `.bench/` offline against the lock — manifest.json to the `manifest=` pin, every listed file to its entry, extras refused. `.bench/` itself is git-ignored (and size-guarded), so the lock is the only staged thing that can change the verdict; consumers whose `.bench/` is stale or tampered with cannot commit the lock that claims otherwise. Escape hatch: `LEFTHOOK=0`.

Escape hatches: `LEFTHOOK=0 git commit …`, `BENCH_ALLOW_STALE=1 git commit …`. Change hooks only in `bench-ops/lefthook/common.yml`. Never add pre-push or post-* hooks there: they belong to git-lfs, and lefthook owning them makes pushes from older ndb branches skip LFS uploads (`tests/test_lefthook_config.py` enforces this). A branch created before the hook PRs (2026-11-02) carries the old file; merge `main` into it and run `bench sync`.

## From 0 to 100 after a full new bench

Today these steps are run by hand in each repo; the bench-ops plans turn the marked ones into single `bench` commands (column "later"). Run them in this order. Stop at the first step that fails; do not edit a generated file to make a step pass.

### A. A new neurotic_docx_bench run (in `ndb`)

| # | Step | Command | Done when | Later |
|---|---|---|---|---|
| A1 | Run the bench (all tools, or one) | `uv run bench run` / `uv run bench run --only <tool>`; a jubarte release: `scripts/release_jubarte.py X.Y.Z --plan`, then without `--plan` | one new line per tool in `results/bench.jsonl`; the gate passes (aggregate drop fails) | |
| A2 | Merge a cohort run's lines (cohorts under `results/<run>/` only) | append its `bench.jsonl`/`converters.jsonl`/`speed.jsonl` to `results/*.jsonl`, deduplicated (bench and converter lines by `id_run`, speed by `(tool, task, run_ts)`); map every new tool name in `bench.registry.yaml` | `uv run bench report` does not refuse an unmapped name | |
| A3 | Accept a new baseline when the drop is understood | `uv run bench accept-scores <tool>` | | |
| A4 | Regenerate the pages | `uv run bench report`; check with `uv run bench report --check` | `RESULTS.md`, `RESULTS_DETAILED.md`, README vendor table current; headline tables read | `scores.json` emitted too (Plan 2) |
| A5 | Commit the records | stores, registry, the three pages, any frozen `RESULTS_v<old>.md`; never `out/` or corpus files (the size guard refuses binaries) | PR merged on ndb `main` | |
| A6 | Publish heavy outputs | page images/fixtures: `uv run --with httpx python scripts/publish/upload_r2.py <dir> <prefix> [--via-worker https://ndb-r2-upload.cicero-im.workers.dev]`, then `--verify`; run outputs to HF in batches (320 commits/h limit) | every file on R2/HF with the same size; only then delete local copies | `bench publish ndb <tag>` (Plan 2 rev 3: runs `bench report --check` and `bench results upload` first, then uploads the bundle) |
| A7 | Renew the review site (when cases change) | `cd site && git pull --ff-only`, patch the viewer data (reference: `site/_build/site_1011/{process.py,patch_site.py}`), commit, `git push origin gh-pages` | https://jandira-tech.github.io/neurotic_docx_bench/ shows the new cases (check in a real browser) | built from bench-viewer (Plan 3) |

### B. A new jubarte engine release (in `jubarte-redlines`, then `jubarte-app`)

| # | Step | Command | Done when | Later |
|---|---|---|---|---|
| B1 | Release the engine | `scripts/release.sh X.Y.Z --changelog-summary … --crates-summary … --npm-summary … --pypi-summary … --github-summary … --how-readme-and-other-docs-were-updated …` (all required) | GitHub release, crates, npm, PyPI out; `release_info/website_data_X.Y.Z_<stamp>.jsonl` written. Its last step runs the app's `scripts/release-engine.sh` | |
| B2 | Move the release onto jubarte.pro | in `jubarte-app/jubarte-site`: `scripts/release.sh release X.Y.Z --no-deploy` (`engine` then `figures`: version, changelog, demo pin, sample benchmark tables via `sync-bench.ts`, proved by `check-bench.ts`) | `pnpm bench:check` exits 0; `data/facts.jsonl` changed only in proved keys | |
| B3 | Full-corpus figures (after A has scored X.Y.Z on the full corpus) | move the full-corpus `bench.*` facts with `uv run scripts/facts.py merge FILE`, then `scripts/release.sh bench X.Y.Z [--redline-tool jubarte-X.Y.Z] --no-deploy` (proves tables against the new `RESULTS.md`, restages Cases fixtures, uploads them to HF, renders, pins `fixtures.lock`) | `pnpm bench:check` exits 0 | |
| B4 | Commit and deploy | commit to `main`, then `scripts/release.sh deploy X.Y.Z` (refuses uncommitted changes, proves again, `wrangler deploy`, checks the live page) | https://jubarte.pro/benchmark shows X.Y.Z (check in a real browser) | `bench deploy jubarte-app` (Plan 2 rev 3: checks `bench.lock`, `{version}` from its first producer; fetch verification pending Plan 4) |

Later (Plan 4): `fixtures.lock` becomes `bench.lock`, `bench fetch` pulls the tagged `scores.json`, and the page renders through `@jandira-tech/bench-viewer`.

### C. A new editors_trial release (in `editors_trial`, then `benchmarks-law`)

Follow `editors_trial/RELEASING.md` top to bottom and tick it in the release's tracking issue. The parts that must not be skipped:

| # | Step | Command | Done when | Later |
|---|---|---|---|---|
| C1 | Pins, quiet machine, measurements (each changed result twice) | RELEASING.md §1–3 | measurements reproduced | |
| C2 | Facts and reports | `python3 build/facts.py`; `python3 build/render_reports.py --stale out/facts-v<previous>.json`; `--all-twins`; `--check`; copy `out/facts.json` to `out/facts-vX.Y.Z.json` | every sentence about a moved fact re-read; nothing typed by hand | |
| C3 | Demos whose pin moved | `build/rebuild_demo.sh <slug>` | each demo opened in a real browser | |
| C4 | Gate | `python3 -m unittest discover -s build -p "test_*.py"`; `build/verify_publication.sh --with-word` **twice** | no `WRONG-DOCUMENT`/`ORACLE-NOT-CLEAN`; every `.docx` Word valid | |
| C5 | Publish the trial | CHANGELOG section, PR to `main`, merge, tag `vX.Y.Z`, GitHub release, then `bench publish editors_trial et-vX.Y.Z` (report bundle to the private `arthrod/editors_trial-artifacts` dataset; push the ledger commit and tag it prints) | bundle on HF, tag `et-vX.Y.Z`; `bench status` shows it verified | |
| C6 | Publish the site | in `benchmarks-law` on up-to-date `main`: `pnpm sync:content`, `pnpm sync:vendors`, `pnpm sync:assets`, `pnpm sync:demos` (if a demo was rebuilt), `pnpm run deploy` (needs `EMDASH_TOKEN`; `REPORTS_ROOT` defaults to `../../editors_trial`) | https://benchmarks.law home, `/editors-trial/`, vendors, every report and demo checked in a real browser | `bench deploy benchmarks-law` (Plan 2 rev 3) |
| C7 | Close out | commit the site, PR, merge, tag with the same version; close the issues | | |

Later (Plans 5–6): `bench publish et <tag>` replaces the publishing half of C, and benchmarks.law renders ndb numbers from the tagged `scores.json` instead of patching a copied viewer.

### D. After any of A, B or C

```bash
bench status            # every repo: right branch, no stray worktrees, ledger verified on HF (once ledgers exist)
bench sync              # pull what was merged; hooks still active everywhere
```

Both sites must show the same figures for the same bench version: jubarte.pro (product skin) and benchmarks.law (arthur.law skin). If they differ, one of them skipped a step above.

## Rules that bit before

- Never edit a generated file (`RESULTS*.md`, `out/facts.json`, rendered reports, `data/facts.jsonl` by hand). Change the measurement or the template and regenerate.
- The JSONL stores are append-only; never rewrite or reorder them.
- No binaries in git: heavy outputs go to HF (record) and R2 (served copy). Delete local data only after both are verified. Satellites (`ndb-105`, `ndb-apryse`, root `*.log`) leave `~/temp/T` only through `bench archive` + `bench gc`, after the two witnesses and Arthur's yes.
- `bench gc` deletes outside the linked repos only what an archive of that very tree lists at the same path (the manifest records the archived root); it refuses `/`, `$HOME`, BENCH_ROOT and any parent of a linked repo as a target, and re-checks each file (O_NOFOLLOW + fstat against the pinned stat) right before the unlink.
- `bench work` is allowed to remove a worktree only when it holds no byte that exists nowhere else (ignored and untracked included); a fresh worktree on an old branch is young — its TTL clock starts at its creation, not at the branch's last commit.
- Word validity is checked in Word itself (`open file.docx`): no warning, no repair offer.
- Websites are checked in a real browser, not only with curl.
- One Hugging Face upload batch per many files: the hub allows 320 commits an hour.
