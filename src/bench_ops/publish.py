"""`bench publish`: stage a producer's release files, upload them, verify, then record (D4–D8).

One state machine, each step verified before the next (plan 2):

1. refuse a tag that already exists — on the Hub (the authority), in the local ledger, or as a
   local git tag;
2. stage: expand the `files` globs inside the repo; refuse symlinks, paths escaping the repo,
   dot-paths (`.git`, `.env` …), anything under `release/`, and an empty result;
3. upload in chunks (at most 1,000 files / 400 MB per commit); revisions are commit oids; the
   last chunk's revision is `R1`;
4. verify every staged file at `R1` with the witness — before anything else is written;
5. commit `release/<tag>/manifest.json` (canonical `models.dumps` bytes) → `R2`; download it at
   `R2` and check its sha256;
6. create the immutable Hub tag `<tag>` at `R2`;
7. append one `LedgerEntry` to `release/index.jsonl` under `release/.publish.lock`;
8. `git commit release/index.jsonl` and an annotated git tag. Never push; the caller prints the
   push command.

`served` globs are refused until R2 has a content witness (review D2/D3/D11–D13): no producer
serves release files yet, so a manifest that claims "served" without a mirror would let gc
delete a file the site still needs.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from bench_ops.config import Repo
from bench_ops.gitx import git
from bench_ops.hf import HubApi, sha256_of, verify_manifest
from bench_ops.ledger import check_ledger
from bench_ops.models import (
    FileEntry,
    HfRef,
    LedgerEntry,
    Manifest,
    Tag,
    dumps,
)

MAX_FILES_PER_COMMIT = 1000
MAX_BYTES_PER_COMMIT = 400_000_000


class PublishError(Exception):
    """A publish was refused: nothing was written that the step before had not verified."""


@dataclass(frozen=True)
class StagedFile:
    local: Path
    rel: str  # repo-relative, POSIX separators
    sha256: str
    bytes: int
    # The Hub path is f"release/{tag}/{rel}"; the caller knows the tag, stage() does not.


def stage(repo_root: Path, globs: list[str]) -> list[StagedFile]:
    """Expand `globs` inside the repo into StagedFiles sorted by `rel`; refuse anything risky.

    The bundle lands on a public dataset, so staging fails closed: symlinks, paths outside the
    repo, dot-paths and the release tree abort the whole publish (a surprise here is a leak,
    not a false negative). Directories are skipped.
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

    A single file larger than max_bytes still ships, alone: create_commit's payload limits are
    about the commit, not the file (LFS objects are uploaded separately).
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


class Hub(HubApi, Protocol):
    """The HubApi of hf.py plus the write side publish needs. Implementations never push."""

    def exists(self, repo: str, path: str, revision: str = "main") -> bool: ...
    def commit(self, repo: str, files: dict[str, Path], message: str) -> str:
        """Returns the new revision: CommitInfo.oid, never str(info) (that is the URL, D10)."""
        ...
    def create_tag(self, repo: str, tag: str, revision: str) -> None: ...


def _refuse_used_tag(root: Path, ledger: Path, tag: str) -> None:
    lines = [ln for ln in ledger.read_text().splitlines() if ln.strip()] if ledger.exists() else []
    for i, line in enumerate(lines, 1):
        try:
            found = LedgerEntry.from_line(line).tag
        except ValidationError:
            raise PublishError(f"local ledger line {i} is not a ledger entry") from None
        if found == tag:
            raise PublishError(f"tag {tag} is already in the local ledger (line {i})")
    try:
        if git(root, "tag", "-l", tag).strip():
            raise PublishError(f"tag {tag} already exists as a local git tag")
    except subprocess.CalledProcessError:
        pass  # no commits yet: there are no tags


def publish(repo: Repo, tag: Tag, hub: Hub, now: datetime, dry_run: bool) -> Manifest:
    """Run the state machine; returns the manifest as committed (`hf.revision` is R1)."""
    spec = repo.release
    if spec is None:
        raise PublishError(f"{repo.name} has no [repos.release] section; it cannot publish")
    if spec.served:
        raise PublishError("R2 mirror needs a content witness; see review D2/D3/D11–D13")

    root = repo.local_path
    ledger = root / "release" / "index.jsonl"
    _refuse_used_tag(root, ledger, tag)

    if not dry_run and hub.exists(spec.hf_repo, f"release/{tag}/manifest.json"):
        raise PublishError(
            f"release/{tag}/manifest.json already exists on the Hub; a tag is immutable"
        )

    staged = stage(root, spec.files)
    files = [
        FileEntry(path=f"release/{tag}/{s.rel}", sha256=s.sha256, bytes=s.bytes) for s in staged
    ]
    manifest = Manifest(
        tag=tag,
        created=now,
        producer=repo.name,
        hf=HfRef(repo=spec.hf_repo, revision="0" * 40),
        r2_prefix=f"{tag}/",
        files=files,
    )
    if dry_run:
        return manifest  # steps 1 (local checks) and 2 only: no hub traffic, no writes

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

    with tempfile.TemporaryDirectory(prefix="bench-publish-") as td:
        manifest_file = Path(td) / "manifest.json"
        manifest_file.write_text(dumps(manifest), encoding="utf-8")
        manifest_sha = sha256_of(manifest_file)
        r2 = hub.commit(
            spec.hf_repo,
            {f"release/{tag}/manifest.json": manifest_file},
            f"release {tag}: manifest",
        )
        downloaded = hub.download(spec.hf_repo, r2, f"release/{tag}/manifest.json")
        if sha256_of(downloaded) != manifest_sha:
            raise PublishError(f"manifest bytes at {r2} differ from what was committed")

    hub.create_tag(spec.hf_repo, tag, r2)

    append_ledger(
        ledger,
        LedgerEntry(
            tag=tag,
            created=now,
            hf=HfRef(repo=spec.hf_repo, revision=r2),
            manifest_sha256=manifest_sha,
        ),
    )
    _git_release_commit(root, tag)
    return manifest


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


def _git_release_commit(root: Path, tag: str) -> None:
    """Commit the ledger line and tag it. Never pushes; the caller prints the push command."""
    id_args = _git_identity(root)
    git(root, *id_args, "add", "--", "release/index.jsonl")
    git(root, *id_args, "commit", "-m", f"release: {tag}", "--", "release/index.jsonl")
    git(root, *id_args, "tag", "-a", tag, "-m", f"release {tag}")
