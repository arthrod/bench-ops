# bench-ops

The workspace for neurotic_docx_bench, editors_trial, benchmarks-law and jubarte-app.

    uv tool install --editable .      # gives you `bench` (elsewhere: uv tool install git+https://github.com/arthrod/bench-ops)
    bench sync                        # clones/pulls every repo in repos.toml, installs hooks, exits 1 if a repo's hooks are inactive
    bench status                      # branches, dirt, release ledger vs Hugging Face
    bench publish <producer> <tag>    # release bundle to the producer's HF dataset (creates nothing, checks its visibility; `--dry-run` stages only; every files glob must match, `?glob` is optional)
    bench bump <producer> [--repo .]  # pin the producer's latest release in the consumer's bench.lock: <producer>=<tag> manifest=<sha256> (reads the producer's last ledger line; creates the lock, keeps other producers, prints old -> new)
    bench fetch [--repo .] [--check]  # materialise every lock pin under .bench/<producer>/ (manifest at the HF tag, sha-checked against the lock; every listed file at the manifest's revision, sha-checked; atomic per producer, present files skipped; `--check` is the offline re-hash the hook and `bench deploy` run)
    bench deploy <consumer> [--yes]   # run a consumer's deploy command ({version} from its lock) — only after `bench fetch --check` passes
    bench work [--older-than 14d] [--yes] [--offline]   # list `.work/` worktrees; remove one only when it holds nothing (no untracked or ignored bytes, not locked, both clocks old, merged or pushed after a fresh fetch); `--offline` never removes
    bench archive <dir> <name> [--yes] [--exclude GLOB]      # snapshot a satellite (`.git` included, symlinks in a sidecar) to the private archive dataset — dataset visibility checked, secrets scanned, resumable; `bench gc <dir>` may then delete what that archive lists (provenance), never a git directory

Start here: AGENTS.md (which repo does what, the 0-to-100 release order). Roles and rules: docs/CONTRACT.md. Plans: docs/plans/.
