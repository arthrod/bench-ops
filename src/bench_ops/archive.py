"""`bench archive` (plan 6 C): a satellite directory snapshotted to the private archive
dataset, through plan 2's machinery — stage → secrets scan → chunked upload → witness →
manifest → HF tag `arc-<name>` — plus one `LedgerEntry` in `<BENCH_ROOT>/archive/index.jsonl`
and a local copy of the manifest under `<BENCH_ROOT>/archive/<name>/` (gc's fail-closed
blocklist, L2).

The whole tree is staged, not a clean checkout: the working tree with its untracked and
modified files *and* the `.git` directory, so history is preserved (a satellite like
`ndb-105` with seven local changes keeps them). That is why `stage_all` refuses nothing for
dot-paths — unlike `publish.stage`, whose bundle is public — but the dataset's privacy is
verified before anything is written (H2) and the pre-commit hook's own secret scanner runs
over the staged set, refusing on a hit: satellites are exactly where `.env` files live.
`--exclude GLOB` (repeatable) drops paths from the stage and is recorded in the manifest.

Symlinks are never followed, but no longer silently dropped (M6): each one is recorded in
the `archive/<name>/symlinks.json` sidecar (path -> target), which is uploaded and listed in
the manifest like any other file, so the archive describes the whole tree it snapshotted.
The manifest also records the archived root (absolute, resolved): `bench gc` deletes a file
outside the linked repos only when that very tree's archive lists it at the same relative
path (M2, provenance — bytes matching some other manifest is not a witness).

Resumable and idempotent (H1), exactly like `bench publish`: when the manifest is already on
the Hub — a previous run that died between the manifest commit and the tag — the archive is
adopted (verified, compared with a fresh stage) and only the missing records are created.
A published archive is never overwritten; a rerun against a changed tree is refused.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from bench_ops.hf import sha256_of, verify_manifest
from bench_ops.hooks import secrets_command
from bench_ops.models import (
    FileEntry,
    HfRef,
    LedgerEntry,
    Manifest,
    dumps,
    manifest_path,
)
from bench_ops.publish import (
    PublishError,
    StagedFile,
    _ensure_hf_tag,
    _ledger_entries,
    _validate_revision,
    append_ledger,
    chunk,
    validate_tag,
)

# Created private by Arthur; `bench archive` refuses when it does not exist *or is not
# private*, it never creates repos or changes visibility (plan 6 A's rule, applied to the
# archive too).
ARCHIVE_REPO = "arthrod/neurotic_docx_bench-archive"
ARCHIVE_LEDGER = "archive/index.jsonl"  # under BENCH_ROOT, i.e. the bench-ops repo
NAME = re.compile(r"[a-z0-9-]+")
SIDECAR = "symlinks.json"


def _excluded(rel: str, excludes: tuple[str, ...]) -> bool:
    """Whether a repo-relative posix path matches one of the --exclude globs (H2)."""
    return any(Path(rel).full_match(g) for g in excludes)


def stage_all(src: Path, excludes: tuple[str, ...] = ()) -> list[StagedFile]:
    """Everything under `src`, recursively: working tree, untracked files and `.git`.

    Sorted by rel; symlinks and other non-regular files are skipped (never followed — they
    are recorded separately by `symlink_map`); paths matching an exclude glob are dropped;
    an empty result refuses the archive (there would be nothing to preserve).
    """
    if not src.is_dir():
        raise PublishError(f"{src} is not a directory; nothing to archive")
    root = src.resolve()
    staged: list[StagedFile] = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if excludes and _excluded(rel, excludes):
            continue
        if p.is_symlink() or not p.is_file():
            continue
        try:  # git's own bookkeeping (objects/maintenance.lock) can vanish mid-walk
            staged.append(
                StagedFile(
                    local=p,
                    rel=rel,
                    sha256=sha256_of(p),
                    bytes=p.stat().st_size,
                )
            )
        except OSError:
            continue
    if not staged:
        raise PublishError(f"{src} contains no files; nothing to archive")
    return staged


def symlink_map(src: Path, excludes: tuple[str, ...] = ()) -> dict[str, str]:
    """Every symlink under `src` (rel -> target, never followed), for the sidecar (M6)."""
    root = src.resolve()
    links: dict[str, str] = {}
    if not root.is_dir():
        return links
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if excludes and _excluded(rel, excludes):
            continue
        if p.is_symlink():
            try:
                links[rel] = os.readlink(p)
            except OSError:  # vanished between the walk and the readlink
                continue
    return links


def _sidecar_bytes(links: dict[str, str]) -> bytes:
    return (json.dumps(links, sort_keys=True, indent=2) + "\n").encode()


def _scan_secrets(staged: list[StagedFile]) -> None:
    """H2: the pre-commit hook's own scanner (ripsecrets) over the staged set. A hit is a
    hard refusal naming the files (never the secrets); without the tool there is no scan,
    so there is no archive either."""
    argv = secrets_command([s.local for s in staged])
    if argv is None:
        raise PublishError(
            "ripsecrets is not installed (brew install ripsecrets); refusing to archive "
            "files no secrets scan has seen"
        )
    proc = subprocess.run(argv, check=False, capture_output=True, text=True)
    if proc.returncode == 0:
        return
    rels = {s.local: s.rel for s in staged}
    hits = sorted(
        {rels.get(Path(ln.split(":", 1)[0]), ln.split(":", 1)[0])
         for ln in proc.stdout.splitlines() if ln.strip()}
    )
    raise PublishError(
        f"{len(hits)} staged file(s) look like they hold secrets ({', '.join(hits)}); "
        "scrub them or drop them with --exclude"
    )


def _adopt_hub_archive(
    hub, tag: str, staged: list[StagedFile], sidecar: StagedFile, prefix: str
) -> tuple[Manifest, str, str, bytes]:
    """H1: take over an archive whose manifest is already on the Hub — the same adoption
    publish uses (C3): verified with the witness at its own revision, then compared with a
    fresh stage; any mismatch is refused — a published archive is never overwritten."""
    path = manifest_path(tag)
    r2 = hub.revision_of(ARCHIVE_REPO, path)
    if r2 is None:
        raise PublishError(
            f"{path} exists on the Hub but its revision cannot be located; refusing to touch it"
        )
    _validate_revision(r2, f"{path}'s revision")
    data = hub.download(ARCHIVE_REPO, r2, path).read_bytes()
    try:
        m = Manifest.model_validate_json(data)
    except Exception as e:  # unparsable bytes are one message, never a traceback
        raise PublishError(f"{path} at {r2} is not a manifest; refusing to touch it") from e
    if m.tag != tag or m.producer != "archive":
        raise PublishError(f"{path} at {r2} records {m.producer}'s {m.tag}, not archive {tag}")
    w = verify_manifest(m, hub)  # the witness, at the manifest's own revision (R1)
    if not w.ok:
        detail = ", ".join(w.missing + w.mismatched)
        raise PublishError(f"witness failed at {m.hf.revision}: {len(w.missing)} missing, "
                           f"{len(w.mismatched)} mismatched ({detail})")
    on_hub = {f.path: f.sha256 for f in m.files}
    on_disk = {f"{prefix}{s.rel}": s.sha256 for s in [*staged, sidecar]}
    if on_hub != on_disk:
        raise PublishError(
            f"the archive on the Hub does not match the current tree ({len(on_hub)} files "
            f"there, {len(on_disk)} staged here, or their sha256 differ); a published "
            "archive is never overwritten — archive the tree under a new name"
        )
    return m, r2, hashlib.sha256(data).hexdigest(), data


def _ensure_local_manifest(bench_root: Path, name: str, manifest_bytes: bytes) -> str:
    """L2: `<BENCH_ROOT>/archive/<name>/manifest.json`, exactly the Hub's bytes — what gc's
    fail-closed blocklist reads for an archive whose manifest it cannot verify."""
    local = bench_root / "archive" / name / "manifest.json"
    if local.exists():
        if local.read_bytes() == manifest_bytes:
            return "done"
        raise PublishError(
            f"{local} differs from the Hub's manifest for arc-{name}; never overwriting it"
        )
    local.parent.mkdir(parents=True, exist_ok=True)
    try:
        local.write_bytes(manifest_bytes)
    except OSError as e:
        raise PublishError(f"cannot write {local}: {e}") from None
    return "did"


def _manifest(
    tag: str,
    files: list[StagedFile],
    prefix: str,
    now: datetime,
    revision: str,
    root: Path,
    excludes: tuple[str, ...],
) -> Manifest:
    return Manifest(
        tag=tag,
        created=now,
        producer="archive",
        hf=HfRef(repo=ARCHIVE_REPO, revision=revision),
        r2_prefix=prefix,
        files=[
            FileEntry(path=f"{prefix}{s.rel}", sha256=s.sha256, bytes=s.bytes)
            for s in files
        ],
        archived_root=str(root.resolve()),
        excludes=list(excludes),
    )


def archive(
    src: Path,
    name: str,
    hub,  # publish.Hub (WriteApi): kept untyped to avoid a circular import
    now: datetime,
    yes: bool,
    bench_root: Path,
    excludes: tuple[str, ...] = (),
) -> Manifest:
    """Publish `src` under `archive/<name>/` and record it; `yes=False` is a dry run.

    Dry run stages and scans only — no hub traffic, no writes — and returns the manifest
    (revision all zeros) the real run would record. A real run refuses a name outside
    `[a-z0-9-]+`, a missing or public archive dataset, a staged file the secrets scan
    flags, a tag already in the local ledger, or an archive on the Hub that does not match
    this tree: a published archive is never overwritten. A run that died after the manifest
    commit resumes (H1): the Hub's archive is adopted and only the missing records — tag,
    ledger line, local manifest copy — are created.
    """
    if NAME.fullmatch(name) is None:
        raise PublishError(f"archive name {name!r} must match [a-z0-9-]+")
    tag = f"arc-{name}"
    validate_tag(tag, kind="archive")  # L1: arc-* is this command's namespace, only
    prefix = f"archive/{name}/"
    manifest_rel = manifest_path(tag)
    ledger = bench_root / ARCHIVE_LEDGER

    staged = stage_all(src, excludes=excludes)
    _scan_secrets(staged)  # H2: both paths, so a dry run refuses what a real run would
    links = symlink_map(src, excludes=excludes)
    sidecar_bytes = _sidecar_bytes(links)
    sidecar = StagedFile(
        local=Path("/dev/null"),  # replaced by a real temp file for the upload
        rel=SIDECAR,
        sha256=hashlib.sha256(sidecar_bytes).hexdigest(),
        bytes=len(sidecar_bytes),
    )
    upload = [*staged, sidecar]

    if not yes:  # staging only: the caller prints the summary from the returned manifest
        return _manifest(tag, upload, prefix, now, "0" * 40, src, excludes)

    if not hub.repo_exists(ARCHIVE_REPO):
        raise PublishError(
            f"Hugging Face dataset {ARCHIVE_REPO} does not exist — create it private on the "
            "Hub first; bench archive never creates repos"
        )
    if not hub.repo_is_private(ARCHIVE_REPO):  # H2: the whole tree goes there
        raise PublishError(
            f"Hugging Face dataset {ARCHIVE_REPO} is public; the archive holds whole "
            "satellites — make the dataset private on the Hub before archiving"
        )

    if hub.exists(ARCHIVE_REPO, manifest_rel):  # a previous run got as far as the hub (H1)
        m, r2, manifest_sha, manifest_bytes = _adopt_hub_archive(
            hub, tag, staged, sidecar, prefix
        )
        print(f"manifest: done — adopted {manifest_rel} at {r2}")
    else:
        for i, entry in _ledger_entries(ledger):
            if entry.tag == tag:
                raise PublishError(f"tag {tag} is already in the local ledger (line {i})")

        manifest = _manifest(tag, upload, prefix, now, "0" * 40, src, excludes)
        with tempfile.TemporaryDirectory(prefix="bench-archive-") as td:
            sidecar_file = Path(td) / SIDECAR
            sidecar_file.write_bytes(sidecar_bytes)
            upload = [
                *staged,
                StagedFile(
                    local=sidecar_file, rel=SIDECAR, sha256=sidecar.sha256,
                    bytes=sidecar.bytes,
                ),
            ]
            chunks = chunk(upload)
            r1 = "0" * 40
            for i, group in enumerate(chunks, 1):
                r1 = hub.commit(
                    ARCHIVE_REPO,
                    {f"{prefix}{s.rel}": s.local for s in group},
                    f"archive {tag}: files {i}/{len(chunks)}",
                )
        manifest = manifest.model_copy(update={"hf": HfRef(repo=ARCHIVE_REPO, revision=r1)})

        w = verify_manifest(manifest, hub)  # verify R1 before writing anything else (D5)
        if not w.ok:
            detail = ", ".join(w.missing + w.mismatched)
            raise PublishError(f"witness failed at {r1}: {len(w.missing)} missing, "
                               f"{len(w.mismatched)} mismatched ({detail})")

        manifest_bytes = dumps(manifest).encode()
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        with tempfile.TemporaryDirectory(prefix="bench-archive-") as td:
            manifest_file = Path(td) / "manifest.json"
            manifest_file.write_bytes(manifest_bytes)
            r2 = hub.commit(
                ARCHIVE_REPO, {manifest_rel: manifest_file}, f"archive {tag}: manifest"
            )
            if sha256_of(hub.download(ARCHIVE_REPO, r2, manifest_rel)) != manifest_sha:
                raise PublishError(f"manifest bytes at {r2} differ from what was committed")
        m = manifest
        print(f"manifest: did — committed {manifest_rel} at {r2}")

    state = _ensure_hf_tag(hub, ARCHIVE_REPO, tag, r2)
    print(f"hugging face tag: {state} — {tag} at {r2}")

    state = _ensure_ledger_line(ledger, m, r2, manifest_sha)
    print(f"ledger: {state} — {ARCHIVE_LEDGER}")

    state = _ensure_local_manifest(bench_root, name, manifest_bytes)
    print(f"bench manifest: {state} — archive/{name}/manifest.json")
    return m


def _ensure_ledger_line(
    ledger: Path, manifest: Manifest, r2: str, manifest_sha: str
) -> str:
    """`done`/`did`; an existing line for the tag with other values is never overwritten
    (the same rule publish's _ensure_ledger_line enforces for releases)."""
    entry = LedgerEntry(
        tag=manifest.tag,
        created=manifest.created,
        hf=HfRef(repo=ARCHIVE_REPO, revision=r2),
        manifest_sha256=manifest_sha,
    )
    for i, found in _ledger_entries(ledger):
        if found.tag != entry.tag:
            continue
        if (found.hf.revision, found.manifest_sha256) == (
            entry.hf.revision, entry.manifest_sha256
        ):
            return "done"
        raise PublishError(
            f"ledger line {i} records {entry.tag} at {found.hf.revision} "
            f"({found.manifest_sha256[:12]}…), the Hub says {entry.hf.revision}; "
            "never overwriting a ledger line"
        )
    try:
        append_ledger(ledger, entry)
    except OSError as e:  # disk full, permissions: a failed append is a message (M6)
        raise PublishError(f"ledger append failed: {e}") from None
    return "did"
