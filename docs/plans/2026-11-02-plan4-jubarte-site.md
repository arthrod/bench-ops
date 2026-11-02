# Plan 4 — jubarte.pro consumes the ndb release

Issue: arthrod/jubarte-app#162. Depends on Plan 2 (a published `ndb-v*` tag with `scores.json`) and Plan 3 (bench-viewer). Tracking: arthrod/bench-ops#8.

## Revision 2 (Arthur, 2026-11-02): one source of truth

The engine's `release_info/` files are written by ndb (`neurotic_docx_bench.jubarte_release_info … --engine-dir`), so ndb is the only producer. ndb keeps a copy of the six files in `results/release_<v>_evidence/release_info/` and `scores.json` carries the sample tables, headlines, home groups, method and the per-table states. jubarte reads only the ndb release: `sync-bench.ts` has one mode (`--scores`), the release_info mode and its proof move to ndb (`bench report` refuses a scores.json whose sample tables differ from the newest evidence). `fixtures.lock` is folded into `bench.lock`: the Cases fixtures come from the ndb release bundle (`cases.json` plus the fixture files it lists). `bench deploy jubarte` is restored (the single command Arthur asked for): `bench fetch --check`, then `scripts/release.sh deploy`.

The sections below are revision 1; where they disagree with this paragraph, this paragraph wins.

## What the survey found (revision 1)

- jubarte.pro's numbers have **two** producers, not one. The sample tables (`conversion-sample`, `redlines-sample`) and the headlines come from the **engine** release (`jubarte-redlines/release_info/`, `sync-bench.ts`, proved by `check-bench.ts compareRelease`). Only the full-corpus tables, the states and `bench.version/generated` come from ndb (today parsed out of `RESULTS.md`). ndb's `scores.json` therefore replaces the `RESULTS.md` half only; the `release_info` half stays.
- `fixtures.lock` is **not** an ndb release pin. It pins the dataset revision of the Cases fixtures jubarte-site stages and uploads itself (`site_fixtures.py stage/upload`). Folding it into `bench.lock` would mix a consumer's own artifact with a producer's release. It stays as it is.
- `bench deploy jubarte` adds nothing over `scripts/release.sh deploy` (which already refuses a dirty tree, proves and checks the live page). Dropped.

## Decisions

- `bench.lock` at the jubarte-app root (where `bench status` looks): `ndb=<tag> manifest=<sha256>`. The manifest sha pins content, so a moved HF tag cannot change what jubarte renders. `Lock` gains `shas: dict[str, str]`; `Lock.tags` keeps tags so the staleness check is unchanged; a line without `manifest=` stays valid.
- bench-ops gains `bench bump <producer> [--repo .]` (writes the producer's last ledger tag and manifest sha into `bench.lock`; needs the producer checkout) and `bench fetch [--repo .] [--check]`:
  - fetch: for each lock line, download `release/<tag>/manifest.json` at the HF tag, check its sha256 against the lock, download every file under `release/<tag>/` the manifest lists, check each sha256, write them under `.bench/<producer>/` (git-ignored) plus `.bench/<producer>/manifest.json`.
  - `--check` (offline, used by the hook): `.bench/<producer>/manifest.json` hashes to the lock's sha and every file in `.bench/` hashes to its manifest entry.
- `lefthook/common.yml` gains a pre-commit job `bench fetch --check`, `glob: "bench.lock"`.
- `sync-bench.ts --scores <path>` (new mode, default `.bench/ndb/scores.json`): writes `bench.version`, `bench.generated`, `bench.repo`, `bench.viewer`, `bench.states` and the non-sample `bench.tables` through `scripts/facts.py`, keeping the sample tables first, as today. The `release_info` mode is unchanged. Two mappings live here, not in the contract: ndb table ids → jubarte's existing ids (a fixed table in `sync-bench.ts`, an unknown ndb id fails), and `tool` gets ` †` appended when `ours` is true, because jubarte's facts carry the dagger in the name and the contract carries it in `ours`.
- `check-bench.ts`: the `RESULTS.md` proof is replaced by a `scores.json` proof (every non-sample table, row and state in `facts.jsonl` equals `scores.json`, which is equal by construction; version and generated equal). `--results` and `parseResults` are deleted. The `release_info` proof is unchanged.
- `benchmark.ts` and `home.ts` render through `@jandira-tech/bench-viewer` (git dependency until it is on npm) with `themes/jubarte.css`. Page sorting (redlines first) stays in jubarte's page code. The header line gains the badge `bench <tag> · generated <date>`.
- `release.sh bench` step: `bench bump ndb && bench fetch && node scripts/sync-bench.ts --scores` replaces the `RESULTS.md` read.

## Acceptance

- In a fresh jubarte-app clone with no ndb checkout: `bench fetch && pnpm build && pnpm test && pnpm bench:check` pass.
- Changing one number in `.bench/ndb/scores.json` makes `bench fetch --check` and `pnpm bench:check` exit 1.
- The built `benchmark.html` equals today's byte for byte, apart from the badge line (diffed in the PR).
- jubarte.pro/benchmark checked in a real browser after deploy (deploy is Arthur's call).
