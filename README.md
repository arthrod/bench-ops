# bench-ops

The workspace for neurotic_docx_bench, editors_trial, benchmarks-law and jubarte-app.

    uv tool install --editable .      # gives you `bench` (elsewhere: uv tool install git+https://github.com/arthrod/bench-ops)
    bench sync                        # clones/pulls every repo in repos.toml, installs hooks, exits 1 if a repo's hooks are inactive
    bench status                      # branches, dirt, release ledger vs Hugging Face
    bench publish <producer> <tag>    # release bundle to the producer's HF dataset (creates nothing; `--dry-run` stages only)

Start here: AGENTS.md (which repo does what, the 0-to-100 release order). Roles and rules: docs/CONTRACT.md. Plans: docs/plans/.
