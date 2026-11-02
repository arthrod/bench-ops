# Plan 2 — release bundle: publish, ledger hook, gc (bench-ops side)

Issues: arthrod/bench-ops#2 (publish), #3 (gc), #4 (ledger hook); ndb side: jandira-tech/neurotic_docx_bench#113 (scores.json), #114 (results/ cleanup, PR #116). Tracking: arthrod/bench-ops#8.

Revision 2 (2026-11-02) adopts the adversarial review in `docs/plans/reviews/plan2-review.md` (findings D1–D22).

## Decisions

- **The Hub is the only authority.** Every check that gates something (publish refusing a tag, gc allowing a delete) asks Hugging Face, never a local file. Local ledger lines and git tags are conveniences.
- **Publish order** (one state machine, each step verified before the next):
  1. refuse if `release/<tag>/manifest.json` already exists on the Hub's `main` (D6, D15), or the tag is in the local ledger or a git tag;
  2. stage: expand `files` globs inside the repo; refuse symlinks, paths escaping the repo, dot-paths (`.git`, `.env`…), and anything under `release/` (D4, D21);
  3. upload in chunks (at most 1,000 files and 400 MB per commit, D8) under `release/<tag>/<repo-relative path>`; revisions are `CommitInfo.oid` (D10); the last chunk's revision is `R1`;
  4. **verify** every staged file at `R1` with the existing witness (`verify_manifest` on a manifest built with revision `R1`) before writing anything else (D5);
  5. commit `release/<tag>/manifest.json` (canonical bytes, `models.dumps`) → `R2`; download it at `R2` and check its sha256 (D5);
  6. create the immutable Hub tag `<tag>` at `R2` (`create_tag`, D6);
  7. under `release/.publish.lock` (O_CREAT|O_EXCL), append one `LedgerEntry` (revision `R2`, manifest sha256) with a single O_APPEND write, after `check_ledger` on old+new text (D7, D14);
  8. `git commit -m "release: <tag>" release/index.jsonl` and an annotated git tag. Never push; print the push command (D20).
- **R2 fails closed until it has a content witness.** No producer serves release files yet. A `served` glob in `[repos.release]` makes publish refuse ("R2 mirror needs a content witness; see review D2/D3/D11–D13"), and gc never deletes a file any verified manifest marks `served`. The R2 mirror with checksum verification lands with the first producer that serves files (Plan 4 fixtures).
- **Release config** in `repos.toml` under `[repos.release]`: `hf_repo`, `files` (globs). Models use `extra="forbid"` so a typo fails at load (D18).
- **gc authority is unforgeable** (D1, D3): `VerifiedManifest` is only produced by `fetch_verified(entry, hub)`, which downloads the manifest at `entry.hf.revision`, checks its sha256 against the ledger, checks tag and repo match, and runs the witness at `manifest.hf.revision` (ok required). A file is deletable iff its sha256 is listed by at least one verified manifest, by no unverified one, and is `served` in none. gc skips directories and symlinks, never touches anything under `.git/`, and deletes only with `--yes` (D22).
- **Ledger hook** in `lefthook/common.yml`: pre-commit, `glob: "release/index.jsonl"`, runs `bench hook ledger`, which compares the staged blob (`git show :release/index.jsonl`) with `HEAD`'s and refuses a staged deletion (D14).
- **Status**: a producer whose online witness is not ok shows `BROKEN: …`, and `--fail-on-stale` fails on it too (D16). Witness downloads go through the hf_hub cache, so repeated runs do not re-download (D19).

## Interface

```python
# config.py
class ReleaseSpec(BaseModel):          # extra="forbid"
    hf_repo: str
    files: list[str]                   # globs, repo-relative
    served: list[str] = []             # must stay empty until the R2 witness exists
class Repo(BaseModel):                 # extra="forbid"; + release: ReleaseSpec | None = None

# ledger.py
def check_ledger(old: str | None, new: str) -> list[str]
    # problems: "line N: not a ledger entry"; "line N: changed or removed"; "duplicate tag T"

# publish.py
class PublishError(Exception)
@dataclass(frozen=True) class StagedFile: local: Path; rel: str; sha256: str; bytes: int
    # .hub_path -> f"release/{tag}/{rel}" is computed by the caller
def stage(repo_root: Path, globs: list[str]) -> list[StagedFile]          # sorted by rel; raises PublishError
def chunk(files: list[StagedFile], max_files: int = 1000, max_bytes: int = 400_000_000) -> list[list[StagedFile]]
class Hub(Protocol):                   # extends hf.HubApi
    def exists(self, repo: str, path: str, revision: str = "main") -> bool
    def commit(self, repo: str, files: dict[str, Path], message: str) -> str   # returns CommitInfo.oid
    def create_tag(self, repo: str, tag: str, revision: str) -> None
def publish(repo: Repo, tag: str, hub: Hub, now: datetime, dry_run: bool) -> Manifest
    # dry_run: steps 1 (local checks only) and 2, returns the manifest with revision "0"*40; no writes anywhere
def append_ledger(ledger: Path, entry: LedgerEntry) -> None              # lock + check_ledger + O_APPEND

# gc.py
@dataclass(frozen=True) class VerifiedManifest: entry: LedgerEntry; manifest: Manifest   # only from fetch_verified
def fetch_verified(entry: LedgerEntry, hub: HubApi) -> VerifiedManifest | None   # None when any check fails
@dataclass(frozen=True) class Verdict: path: Path; sha256: str; tags: tuple[str, ...]; deletable: bool; reason: str
def verdicts(paths: list[Path], verified: list[VerifiedManifest], unverified_shas: set[str]) -> list[Verdict]
```

CLI: `bench publish <producer> <tag> [--dry-run]`, `bench gc <paths…> [--yes]`, `bench hook ledger`.

## Acceptance

- `bench publish ndb ndb-v0.0.1 --dry-run` prints a Manifest that validates, with no HF or git writes.
- A real publish leaves `bench status` showing `verified N files on HF` for ndb, and the Hub has tag `ndb-v0.0.1`.
- `bench gc` on a published file reports deletable; a tampered local manifest grants nothing; a served file is never deletable; nothing is deleted without `--yes`.
- A rewritten, removed or duplicate ledger line makes the pre-commit hook exit 1.
