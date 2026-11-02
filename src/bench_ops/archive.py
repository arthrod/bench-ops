"""`bench archive` (plan 6 C): a satellite directory snapshotted to the private archive
dataset, through plan 2's machinery — stage → chunked upload → witness → manifest → HF tag
`arc-<name>` — plus one `LedgerEntry` in `<BENCH_ROOT>/archive/index.jsonl`.

The whole tree is staged, not a clean checkout: the working tree with its untracked and
modified files *and* the `.git` directory, so history is preserved (a satellite like
`ndb-105` with seven local changes keeps them). That is why `stage_all` refuses nothing for
dot-paths — unlike `publish.stage`, whose bundle is public — but symlinks are skipped (never
followed, never stored as links): point them at anything outside the tree and the archive
still holds the bytes that matter.

`bench gc <dir>` accepts the resulting manifest as a witness: files whose sha256 it lists
are deletable once it verifies on HF (the two-witness rule is unchanged).
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from datetime import datetime
from pathlib import Path

from bench_ops.hf import sha256_of, verify_manifest
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
    append_ledger,
    chunk,
    validate_tag,
)

# Created private by Arthur; `bench archive` refuses when it does not exist, it never
# creates repos (plan 6 A's rule, applied to the archive too).
ARCHIVE_REPO = "arthrod/neurotic_docx_bench-archive"
ARCHIVE_LEDGER = "archive/index.jsonl"  # under BENCH_ROOT, i.e. the bench-ops repo
NAME = re.compile(r"[a-z0-9-]+")


def stage_all(src: Path) -> list[StagedFile]:
    """Everything under `src`, recursively: working tree, untracked files and `.git`.

    Sorted by rel; symlinks and other non-regular files are skipped (never followed); an
    empty result refuses the archive (there would be nothing to preserve).
    """
    if not src.is_dir():
        raise PublishError(f"{src} is not a directory; nothing to archive")
    root = src.resolve()
    staged: list[StagedFile] = []
    for p in sorted(root.rglob("*")):
        if p.is_symlink() or not p.is_file():
            continue
        staged.append(
            StagedFile(
                local=p,
                rel=p.relative_to(root).as_posix(),
                sha256=sha256_of(p),
                bytes=p.stat().st_size,
            )
        )
    if not staged:
        raise PublishError(f"{src} contains no files; nothing to archive")
    return staged


def archive(
    src: Path,
    name: str,
    hub,  # publish.Hub (WriteApi): kept untyped to avoid a circular import
    now: datetime,
    yes: bool,
    bench_root: Path,
) -> Manifest:
    """Publish `src` under `archive/<name>/` and record it; `yes=False` is a dry run.

    Dry run stages only — no hub traffic, no writes — and returns the manifest (revision
    all zeros) the real run would record. A real run refuses a name outside `[a-z0-9-]+`,
    a missing archive dataset (create it private; bench never creates repos), a manifest
    already on the Hub, or a tag already in the local ledger: a published archive is never
    overwritten.
    """
    if NAME.fullmatch(name) is None:
        raise PublishError(f"archive name {name!r} must match [a-z0-9-]+")
    tag = f"arc-{name}"
    validate_tag(tag)
    prefix = f"archive/{name}/"
    manifest_rel = manifest_path(tag)
    ledger = bench_root / ARCHIVE_LEDGER

    staged = stage_all(src)

    if not yes:  # staging only: the caller prints the summary from the returned manifest
        return _manifest(tag, staged, prefix, now, "0" * 40)

    if not hub.repo_exists(ARCHIVE_REPO):
        raise PublishError(
            f"Hugging Face dataset {ARCHIVE_REPO} does not exist — create it private on the "
            "Hub first; bench archive never creates repos"
        )
    if hub.exists(ARCHIVE_REPO, manifest_rel):
        raise PublishError(
            f"{manifest_rel} is already on the Hub; a published archive is never overwritten"
        )
    for i, entry in _ledger_entries(ledger):
        if entry.tag == tag:
            raise PublishError(f"tag {tag} is already in the local ledger (line {i})")

    manifest = _manifest(tag, staged, prefix, now, "0" * 40)
    chunks = chunk(staged)
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
    print(f"manifest: did — committed {manifest_rel} at {r2}")

    state = _ensure_hf_tag(hub, ARCHIVE_REPO, tag, r2)
    print(f"hugging face tag: {state} — {tag} at {r2}")

    append_ledger(ledger, LedgerEntry(
        tag=tag,
        created=now,
        hf=HfRef(repo=ARCHIVE_REPO, revision=r2),
        manifest_sha256=manifest_sha,
    ))
    print(f"ledger: did — {ledger}")
    return manifest


def _manifest(tag: str, staged: list[StagedFile], prefix: str, now: datetime,
              revision: str) -> Manifest:
    return Manifest(
        tag=tag,
        created=now,
        producer="archive",
        hf=HfRef(repo=ARCHIVE_REPO, revision=revision),
        r2_prefix=prefix,
        files=[
            FileEntry(path=f"{prefix}{s.rel}", sha256=s.sha256, bytes=s.bytes)
            for s in staged
        ],
    )
