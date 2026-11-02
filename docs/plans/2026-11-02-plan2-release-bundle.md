# Plan 2 — release bundle: publish, ledger hook, gc (bench-ops side)

Issues: arthrod/bench-ops#2 (publish), #3 (gc), #4 (ledger hook); ndb side: jandira-tech/neurotic_docx_bench#113 (scores.json), #114 (heavy results to HF). Tracking: arthrod/bench-ops#8.

## Decisions

- **Two HF commits per publish.** Commit 1 uploads every file of the bundle under `release/<tag>/` (one commit, batched; the hub allows 320 commits/h). Its revision `R1` goes into `Manifest.hf.revision`. Commit 2 uploads `release/<tag>/manifest.json` (canonical bytes, `models.dumps`). The ledger line records `R2` and the manifest's sha256. The witness downloads the manifest at `R2`, checks its sha256 against the ledger, then checks every file at `R1`. Both revisions are immutable.
- **Release config lives in `repos.toml`**, per producer, under `[repos.release]`: `hf_repo`, `files` (globs, relative to the repo root), `served` (globs, subset mirrored to R2), `r2_prefix_base` (public base URL used by the R2 witness), `r2_upload` (the producer's uploader script, relative to the repo root). A producer without `[repos.release]` cannot publish.
- **Ledger hook goes into `lefthook/common.yml`** as a pre-commit job with `glob: "release/index.jsonl"`, not a separate `producer.yml`: consumers never stage that file, and no repo needs another `lefthook.yml` change.
- **`bench publish` never pushes git.** It appends the ledger line, commits `release/index.jsonl` with message `release: <tag>`, creates the annotated tag, and prints the push command. Pushing is the operator's step.
- **`bench gc` deletes nothing by default.** It prints, per file, the witness verdict; `--yes` deletes only files with both witnesses (HF manifest verified; R2 object present with the same size when the file is served).

## Interface

```python
# config.py
class ReleaseSpec(BaseModel):
    hf_repo: str
    files: list[str]            # globs, repo-relative
    served: list[str] = []      # globs, subset of files mirrored to R2
    r2_public: str | None = None   # e.g. https://pub-….r2.dev
    r2_upload: str | None = None   # repo-relative uploader script
class Repo(BaseModel):  # + release: ReleaseSpec | None = None

# ledger.py
def check_ledger(old: str | None, new: str) -> list[str]
    # problems: unparsable line N; line N changed or removed (new must start with old); duplicate tag T

# publish.py
@dataclass(frozen=True) class StagedFile: local: Path; path: str; sha256: str; bytes: int; served: bool
def stage(repo: Repo, tag: str) -> list[StagedFile]          # expands globs; refuses empty; paths "release/<tag>/<rel>"
def build_manifest(repo: Repo, tag: str, files: list[StagedFile], revision: str, now: datetime) -> Manifest
class Uploader(Protocol):
    def commit(self, repo: str, files: dict[str, Path], message: str) -> str   # returns the new revision
def publish(repo: Repo, tag: str, uploader: Uploader, witness: Callable[[LedgerEntry], Witness],
            mirror: Callable[[list[StagedFile], str], None], now: datetime, dry_run: bool) -> PublishResult
    # dry_run: stage + manifest with revision "0"*40, no writes anywhere
    # else: refuse a tag already in the ledger or as a git tag; commit 1; manifest; commit 2;
    #       witness must be ok; mirror served files (skipped when none); append ledger; git commit + tag

# gc.py
@dataclass(frozen=True) class Verdict: path: Path; sha256: str; tag: str | None; hf: bool; r2: bool | None; deletable: bool; reason: str
def verdicts(paths: list[Path], manifests: list[tuple[LedgerEntry, Manifest, Witness]], r2_head: Callable[[str], int | None]) -> list[Verdict]
```

CLI: `bench publish <producer> <tag> [--dry-run]`, `bench gc <paths…> [--yes]`, `bench hook ledger` (reads the staged `release/index.jsonl` and `git show HEAD:release/index.jsonl`).

## Acceptance

- `bench publish ndb ndb-v0.0.1 --dry-run` prints a Manifest that validates, with no HF, R2 or git writes.
- A real publish leaves `bench status` showing `verified N files on HF` for ndb.
- `bench gc` on a published file reports `deletable`; on an unpublished file `no manifest lists this sha256`; nothing is deleted without `--yes`.
- Rewriting or removing a ledger line, or a duplicate tag, makes the pre-commit hook exit 1.
