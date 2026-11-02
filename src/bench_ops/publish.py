"""`bench publish`: stage a producer's release files, upload them, verify, then record (D4–D8).

One state machine, each step verified before the next (plan 2), resumable and idempotent
(C3): when `release/<tag>/manifest.json` is already on the Hub — a previous publish that
failed partway — the release is *adopted*: downloaded at its revision, verified with the
witness and against what `stage()` produces now, and the missing records (Hub tag, ledger
line, producer manifest, git commit, git tag) are created. A mismatch is refused: a
published release is never overwritten. Every step prints `did` (this run created it) or
`done` (it already existed), so a rerun shows its progress.

0. run the producer's own `before` scripts in its checkout, stopping at the first failure
   (rev 3: bench-ops reimplements nothing the producer's tooling already does);
1. refuse a git-ignored `release/` tree (the ledger commit could never happen), a malformed
   tag, and — for a fresh release — a tag already in the local ledger or a local git tag;
2. stage: expand the `files` globs inside the repo; refuse symlinks, paths escaping the
   repo, dot-paths (`.git`, `.env` …), anything under `release/`, and an empty result;
3. upload in chunks (at most 1,000 files / 400 MB per commit); revisions are commit oids;
   the last chunk's revision is `R1`;
4. verify every staged file at `R1` with the witness — before anything else is written;
5. commit `release/<tag>/manifest.json` (canonical `models.dumps` bytes) → `R2`; download
   it at `R2` and check its sha256;
6. ensure the immutable Hub tag `<tag>` exists at `R2` — only now that the manifest
   verifies;
7. ensure one `LedgerEntry` in `release/index.jsonl` (under `release/.publish.lock`), the
   producer's copy of the manifest under `release/<tag>/` (gc's fail-closed blocklist,
   N4), the git commit of both, and the annotated git tag — the bytes committed are read
   back and must be the bytes `append_ledger` wrote (N5). Never push; the caller prints
   the push command.

`served` globs are refused until R2 has a content witness (review D2/D3/D11–D13): no
producer serves release files yet, so a manifest that claims "served" without a mirror
would let gc delete a file the site still needs.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from bench_ops.config import ReleaseSpec, Repo
from bench_ops.gitx import git
from bench_ops.hf import WriteApi, sha256_of, verify_manifest
from bench_ops.ledger import check_ledger
from bench_ops.models import (
    FileEntry,
    GitSha,
    HfRef,
    LedgerEntry,
    Manifest,
    Tag,
    dumps,
)

MAX_FILES_PER_COMMIT = 1000
MAX_BYTES_PER_COMMIT = 400_000_000
LEDGER = "release/index.jsonl"

# The Hub as publish needs it: paths_info/download of HubApi plus the write side (C3).
Hub = WriteApi


class PublishError(Exception):
    """A publish was refused: nothing was written that the step before had not verified."""


def run_script(command: str, cwd: Path) -> int:
    """Run one of the producer's own scripts: shell, inherited env, output streamed (rev 3).

    Returns the exit code instead of raising: publish decides what a failure means, and the
    runner is injected so tests can watch the commands without a shell.
    """
    return subprocess.run(command, shell=True, cwd=str(cwd), check=False).returncode


@dataclass(frozen=True)
class StagedFile:
    local: Path
    rel: str  # repo-relative, POSIX separators
    sha256: str
    bytes: int
    # The Hub path is f"release/{tag}/{rel}"; the caller knows the tag, stage() does not.


def stage(repo_root: Path, globs: list[str]) -> list[StagedFile]:
    """Expand `globs` inside the repo into StagedFiles sorted by `rel`; refuse anything risky.

    The bundle lands on a public dataset, so staging fails closed: symlinks, paths outside
    the repo, dot-paths and the release tree abort the whole publish (a surprise here is a
    leak, not a false negative). Directories and other non-regular files (a FIFO would
    block the read forever, N1) are skipped.
    """
    root = repo_root.resolve()
    matches: set[Path] = set()
    for g in globs:
        try:
            matches.update(repo_root.glob(g))
        except NotImplementedError:  # absolute patterns
            raise PublishError(f"glob must be relative to the repo root: {g!r}") from None

    staged: list[StagedFile] = []
    for p in sorted(matches):
        if p.is_symlink():
            raise PublishError(f"{p} is a symlink; a release tree must not contain one")
        try:
            rel = p.resolve().relative_to(root)
        except ValueError:
            raise PublishError(f"{p} escapes the repo root; not publishable") from None
        if p.resolve().is_dir():
            continue  # directories stage nothing (the root itself can appear via ../*)
        if not p.is_file():  # N1: a FIFO or socket must never reach the hashing
            continue
        if not rel.parts:
            raise PublishError("the glob matched the repo root itself; not publishable")
        if rel.parts[0] == "release":
            raise PublishError(f"{rel.as_posix()} is under release/; publish writes that tree")
        if any(part.startswith(".") for part in rel.parts):
            raise PublishError(f"{rel.as_posix()} is a dot-path (.git, .env …); not publishable")
        staged.append(
            StagedFile(
                local=p,
                rel=rel.as_posix(),
                sha256=sha256_of(p),
                bytes=p.stat().st_size,
            )
        )
    if not staged:
        raise PublishError(f"the globs {globs} staged no files")
    return sorted(staged, key=lambda s: s.rel)


def chunk(
    files: list[StagedFile], max_files: int = MAX_FILES_PER_COMMIT,
    max_bytes: int = MAX_BYTES_PER_COMMIT,
) -> list[list[StagedFile]]:
    """Greedy packing: never more than max_files or max_bytes per commit.

    A single file larger than max_bytes still ships, alone: create_commit's payload limits
    are about the commit, not the file (LFS objects are uploaded separately).
    """
    chunks: list[list[StagedFile]] = []
    current: list[StagedFile] = []
    used = 0
    for f in files:
        if current and (len(current) >= max_files or used + f.bytes > max_bytes):
            chunks.append(current)
            current, used = [], 0
        current.append(f)
        used += f.bytes
    if current:
        chunks.append(current)
    return chunks


def validate_tag(tag: str) -> None:
    """N6/M6: the tag reaches Hub URLs; validate it as the first statement, as a
    PublishError, before any hub call sees it."""
    try:
        TypeAdapter(Tag).validate_python(tag)
    except ValidationError:
        raise PublishError(
            f"tag {tag!r} must match (ndb|et)-v<major>.<minor>.<patch> or arc-<name>"
        ) from None


def _validate_revision(sha: str, what: str) -> str:
    try:
        return TypeAdapter(GitSha).validate_python(sha)
    except ValidationError:
        raise PublishError(f"{what} is not a git revision: {sha!r}") from None


def _git(root: Path, *args: str) -> str:
    """git as a publish step: any failure is a PublishError (M6), never a CalledProcessError."""
    try:
        return git(root, *args)
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or str(e)).strip().splitlines()
        raise PublishError(f"git {' '.join(args[:3])}… failed: "
                           f"{detail[0] if detail else 'see the repo'}") from None


def _git_opt(root: Path, *args: str) -> str | None:
    """_git that treats a failure (missing path, no commits yet) as None."""
    try:
        return _git(root, *args)
    except PublishError:
        return None


def _refuse_git_ignored(root: Path) -> None:
    """C3: publish ends with a git commit of the ledger; a git-ignored release/ tree would
    orphan the release (tag on the Hub, nothing in git). Refuse before anything is written."""
    if _git_opt(root, "check-ignore", "-q", "--", LEDGER) is not None:
        raise PublishError(
            f"{LEDGER} is git-ignored (check .gitignore); the release ledger must be committed"
        )


def _ledger_entries(ledger: Path) -> list[tuple[int, LedgerEntry]]:
    """The ledger's entries, or a PublishError naming the first line that is not one."""
    out: list[tuple[int, LedgerEntry]] = []
    if ledger.exists():
        lines = (ln for ln in ledger.read_text().splitlines() if ln.strip())
        for i, line in enumerate(lines, 1):
            try:
                out.append((i, LedgerEntry.from_line(line)))
            except ValidationError:
                raise PublishError(f"local ledger line {i} is not a ledger entry") from None
    return out


def _refuse_local_traces(root: Path, ledger: Path, tag: str) -> None:
    """A tag already recorded locally, with no release on the Hub for it, is a broken record.

    (When the Hub does have the release, publish resumes instead: `_ensure_*` compares.)"""
    for i, entry in _ledger_entries(ledger):
        if entry.tag == tag:
            raise PublishError(f"tag {tag} is already in the local ledger (line {i})")
    if (_git_opt(root, "tag", "-l", tag) or "").strip():
        raise PublishError(f"tag {tag} already exists as a local git tag")


def _run_before(spec: ReleaseSpec, repo_root: Path, dry_run: bool,
                run: Callable[[str, Path], int]) -> None:
    """Step 0 (rev 3): the producer's own scripts, in its checkout, before any hub call.

    dry_run prints the commands and runs none; a real run stops at the first nonzero exit.
    """
    for command in spec.before:
        print(f"before: {command}")
        if dry_run:
            continue
        if run(command, repo_root) != 0:
            raise PublishError(f"before command failed: {command}")


def _adopt_hub_release(hub: Hub, repo: Repo, tag: str,
                       staged: list[StagedFile]) -> tuple[Manifest, str, str, bytes]:
    """C3: take over a release whose manifest is already on the Hub. Verified with the
    witness (at its own revision) and against what `stage()` produces now; any mismatch is
    refused — a published release is never overwritten."""
    spec = repo.release
    assert spec is not None
    path = f"release/{tag}/manifest.json"
    r2 = hub.revision_of(spec.hf_repo, path)
    if r2 is None:
        raise PublishError(f"{path} exists on the Hub but its revision cannot be located; "
                           "refusing to touch it")
    _validate_revision(r2, f"{path}'s revision")
    data = hub.download(spec.hf_repo, r2, path).read_bytes()
    try:
        m = Manifest.model_validate_json(data)
    except ValidationError:
        raise PublishError(f"{path} at {r2} is not a manifest; refusing to touch it") from None
    if m.tag != tag or m.producer != repo.name:
        raise PublishError(f"{path} at {r2} records {m.producer}'s {m.tag}, "
                           f"not {repo.name}'s {tag}")
    w = verify_manifest(m, hub)  # the witness, at the manifest's own revision (R1)
    if not w.ok:
        detail = ", ".join(w.missing + w.mismatched)
        raise PublishError(f"witness failed at {m.hf.revision}: {len(w.missing)} missing, "
                           f"{len(w.mismatched)} mismatched ({detail})")
    on_hub = {f.path: f.sha256 for f in m.files}
    on_disk = {f"release/{tag}/{s.rel}": s.sha256 for s in staged}
    if on_hub != on_disk:
        raise PublishError(f"the release on the Hub does not match the current tree ({len(on_hub)} "
                           f"files there, {len(on_disk)} staged here, or their sha256 differ); "
                           "a published release is never overwritten — publish a new tag")
    return m, r2, hashlib.sha256(data).hexdigest(), data


def _ensure_hf_tag(hub: Hub, hf_repo: str, tag: str, r2: str) -> str:
    """`done`/`did`. Only called after the manifest verified; an existing tag pointing
    anywhere else is a hard refusal (tags are immutable)."""
    at = hub.tag_revision(hf_repo, tag)
    if at == r2:
        return "done"
    if at is not None:
        raise PublishError(f"hub tag {tag} points at {at}, not {r2}; a tag is immutable")
    hub.create_tag(hf_repo, tag, r2)
    return "did"


def _ensure_ledger_line(ledger: Path, entry: LedgerEntry) -> str:
    """`done`/`did`; an existing line for the tag with other values is never overwritten."""
    for i, found in _ledger_entries(ledger):
        if found.tag != entry.tag:
            continue
        if (found.hf.revision, found.manifest_sha256) == (
            entry.hf.revision, entry.manifest_sha256
        ):
            return "done"
        raise PublishError(f"ledger line {i} records {entry.tag} at {found.hf.revision} "
                           f"({found.manifest_sha256[:12]}…), the Hub says {entry.hf.revision}; "
                           "never overwriting a ledger line")
    try:
        append_ledger(ledger, entry)
    except OSError as e:  # disk full, permissions: a failed append is a message (M6)
        raise PublishError(f"ledger append failed: {e}") from None
    return "did"


def _ensure_producer_manifest(root: Path, tag: str, manifest_bytes: bytes) -> str:
    """N4: `release/<tag>/manifest.json` in the producer tree, exactly the Hub's bytes —
    what gc's fail-closed blocklist reads for a manifest it cannot verify."""
    local = root / "release" / tag / "manifest.json"
    if local.exists():
        if local.read_bytes() == manifest_bytes:
            return "done"
        raise PublishError(f"{local} differs from the Hub's manifest for {tag}; "
                           "never overwriting it")
    local.parent.mkdir(parents=True, exist_ok=True)
    try:
        local.write_bytes(manifest_bytes)
    except OSError as e:
        raise PublishError(f"cannot write {local}: {e}") from None
    return "did"


def _git_release_commit(root: Path, tag: str, expected_ledger: bytes, manifest_rel: str) -> None:
    """Commit the ledger line and the producer manifest, then tag it. Never pushes.

    The staged blob is read back before committing and the committed blob after (N5): a
    path-limited commit commits the working tree, so the committed bytes must be checked,
    not assumed.
    """
    id_args = _git_identity(root)
    _git(root, *id_args, "add", "--", LEDGER, manifest_rel)
    staged = _git(root, "show", f":{LEDGER}")
    if staged != expected_ledger.decode():
        raise PublishError(f"the staged {LEDGER} differs from the line append_ledger wrote; "
                           "refusing to commit")
    _git(root, *id_args, "commit", "-m", f"release: {tag}", "--", LEDGER, manifest_rel)
    committed = _git(root, "show", f"HEAD:{LEDGER}")
    if committed != expected_ledger.decode():
        raise PublishError(f"the committed {LEDGER} differs from the line append_ledger wrote; "
                           f"the Hub release {tag} is published — inspect the repo and rerun "
                           "bench publish to finish the remaining records")


def _ensure_git_records(root: Path, tag: str, expected_ledger: bytes, manifest_rel: str,
                        manifest_bytes: bytes) -> tuple[str, str]:
    """(commit state, tag state): create what is missing, never touch what exists."""
    head_ledger = _git_opt(root, "show", f"HEAD:{LEDGER}")
    head_manifest = _git_opt(root, "show", f"HEAD:{manifest_rel}")
    if head_ledger == expected_ledger.decode() and head_manifest == manifest_bytes.decode():
        commit_state = "done"
    else:
        _git_release_commit(root, tag, expected_ledger, manifest_rel)
        commit_state = "did"
    if (_git_opt(root, "tag", "-l", tag) or "").strip():
        return commit_state, "done"
    # Tag the commit that last touched the ledger — after checking it carries our bytes.
    target = _git(root, "log", "-1", "--format=%H", "--", LEDGER).strip()
    if not target or _git_opt(root, "show", f"{target}:{LEDGER}") != expected_ledger.decode():
        raise PublishError(f"no git tag {tag} and the commit that last touched {LEDGER} does "
                           "not carry the release; tag the release commit by hand")
    _git(root, *_git_identity(root), "tag", "-a", tag, "-m", f"release {tag}", target)
    return commit_state, "did"


def publish(
    repo: Repo,
    tag: Tag,
    hub: Hub,
    now: datetime,
    dry_run: bool,
    run: Callable[[str, Path], int] = run_script,
) -> Manifest:
    """Run the state machine; returns the manifest as recorded (`hf.revision` is R1)."""
    spec = repo.release
    if spec is None:
        raise PublishError(f"{repo.name} has no [repos.release] section; it cannot publish")
    if spec.served:
        raise PublishError("R2 mirror needs a content witness; see review D2/D3/D11–D13")
    validate_tag(tag)

    root = repo.local_path
    ledger = root / "release" / "index.jsonl"
    manifest_rel = f"release/{tag}/manifest.json"
    _refuse_git_ignored(root)

    if dry_run:  # local checks and staging only: no hub traffic, no writes
        _refuse_local_traces(root, ledger, tag)
        _run_before(spec, root, dry_run=True, run=run)
        staged = stage(root, spec.files)
        return Manifest(
            tag=tag,
            created=now,
            producer=repo.name,
            hf=HfRef(repo=spec.hf_repo, revision="0" * 40),
            r2_prefix=f"{tag}/",
            files=[
                FileEntry(path=f"release/{tag}/{s.rel}", sha256=s.sha256, bytes=s.bytes)
                for s in staged
            ],
        )

    _run_before(spec, root, dry_run=False, run=run)
    if not hub.repo_exists(spec.hf_repo):  # plan 6 A: publishing never creates repos
        raise PublishError(
            f"Hugging Face dataset {spec.hf_repo} does not exist — create it private on the "
            "Hub first; bench publish never creates repos"
        )
    staged = stage(root, spec.files)

    if hub.exists(spec.hf_repo, manifest_rel):  # a previous publish got as far as the hub
        m, r2, manifest_sha, manifest_bytes = _adopt_hub_release(hub, repo, tag, staged)
        print(f"manifest: done — adopted {manifest_rel} at {r2}")
    else:
        _refuse_local_traces(root, ledger, tag)
        files = [
            FileEntry(path=f"release/{tag}/{s.rel}", sha256=s.sha256, bytes=s.bytes)
            for s in staged
        ]
        manifest = Manifest(
            tag=tag,
            created=now,
            producer=repo.name,
            hf=HfRef(repo=spec.hf_repo, revision="0" * 40),
            r2_prefix=f"{tag}/",
            files=files,
        )
        chunks = chunk(staged)
        r1 = "0" * 40
        for i, group in enumerate(chunks, 1):
            r1 = hub.commit(
                spec.hf_repo,
                {f"release/{tag}/{s.rel}": s.local for s in group},
                f"release {tag}: files {i}/{len(chunks)}",
            )
        manifest = manifest.model_copy(update={"hf": HfRef(repo=spec.hf_repo, revision=r1)})

        w = verify_manifest(manifest, hub)  # verify R1 before writing anything else (D5)
        if not w.ok:
            detail = ", ".join(w.missing + w.mismatched)
            raise PublishError(f"witness failed at {r1}: {len(w.missing)} missing, "
                               f"{len(w.mismatched)} mismatched ({detail})")

        manifest_bytes = dumps(manifest).encode()
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        with tempfile.TemporaryDirectory(prefix="bench-publish-") as td:
            manifest_file = Path(td) / "manifest.json"
            manifest_file.write_bytes(manifest_bytes)
            r2 = hub.commit(
                spec.hf_repo,
                {manifest_rel: manifest_file},
                f"release {tag}: manifest",
            )
            downloaded = hub.download(spec.hf_repo, r2, manifest_rel)
            if sha256_of(downloaded) != manifest_sha:
                raise PublishError(f"manifest bytes at {r2} differ from what was committed")
        m = manifest
        print(f"manifest: did — committed {manifest_rel} at {r2}")

    state = _ensure_hf_tag(hub, spec.hf_repo, tag, r2)
    print(f"hugging face tag: {state} — {tag} at {r2}")

    entry = LedgerEntry(
        tag=tag,
        created=now,
        hf=HfRef(repo=spec.hf_repo, revision=r2),
        manifest_sha256=manifest_sha,
    )
    state = _ensure_ledger_line(ledger, entry)
    print(f"ledger: {state} — {LEDGER}")

    state = _ensure_producer_manifest(root, tag, manifest_bytes)
    print(f"producer manifest: {state} — {manifest_rel}")

    commit_state, tag_state = _ensure_git_records(root, tag, ledger.read_bytes(),
                                                  manifest_rel, manifest_bytes)
    print(f"git commit: {commit_state} — release: {tag}")
    print(f"git tag: {tag_state} — {tag}")
    return m


def append_ledger(ledger: Path, entry: LedgerEntry) -> None:
    """Append one line under release/.publish.lock, after check_ledger on old+new text (D7)."""
    ledger.parent.mkdir(parents=True, exist_ok=True)
    lock = ledger.parent / ".publish.lock"
    try:
        lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        raise PublishError(f"another publish holds {lock}") from None
    try:
        old = ledger.read_text() if ledger.exists() else None
        problems = check_ledger(old, (old or "") + entry.to_line())
        if problems:
            raise PublishError("; ".join(problems))
        fd = os.open(ledger, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, entry.to_line().encode())  # one append write, atomic for a short line
        finally:
            os.close(fd)
    finally:
        os.close(lock_fd)
        lock.unlink(missing_ok=True)


def _git_identity(root: Path) -> list[str]:
    """`-c user.name/-c user.email` for whatever is not configured; nothing when both are (D20)."""
    args: list[str] = []
    for key, fallback in (("user.name", "bench publish"), ("user.email", "bench@jandira.tech")):
        try:
            configured = bool(git(root, "config", key).strip())
        except subprocess.CalledProcessError:
            configured = False
        if not configured:
            args += ["-c", f"{key}={fallback}"]
    return args
