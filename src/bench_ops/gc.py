"""`bench gc`: delete a local file only when the Hub says its bytes are recorded (D1/D3/D22).

Authority is unforgeable: `VerifiedManifest` exists only as the return value of
`fetch_verified`, which downloads the manifest from the Hub at the ledger's pinned revision
(never from disk), checks its sha256 against the ledger, checks tag and repo, and runs the
content witness at the manifest's own revision. A local file is deletable iff its sha256 is
listed by at least one verified manifest, by no unverified one, and is `served` in none —
and it is not a file git tracks, not empty, and older than every manifest that lists it
(M3). The delete itself re-stats the file and refuses anything that changed since it was
hashed (M1), and candidates are resolved first: nothing inside a `.git` directory and
nothing outside the linked repos is ever a candidate (M2).
"""

from __future__ import annotations

import hashlib
import os
import stat as stat_m
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from bench_ops.gitx import git
from bench_ops.hf import HubApi, verify_manifest
from bench_ops.models import LedgerEntry, Manifest

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

# What was hashed, from the fstat of the very fd the bytes were read from (M1).
Stat = tuple[int, int, int, int]  # (st_dev, st_ino, st_size, st_mtime_ns)


@dataclass(frozen=True)
class VerifiedManifest:
    """A manifest the Hub vouches for; only `fetch_verified` constructs this."""

    entry: LedgerEntry
    manifest: Manifest


def fetch_verified(entry: LedgerEntry, hub: HubApi) -> VerifiedManifest | None:
    """None when any check fails — always before any gc decision is made from it."""
    path = f"release/{entry.tag}/manifest.json"
    try:
        local = hub.download(entry.hf.repo, entry.hf.revision, path)
    except Exception:  # noqa: BLE001 -- any hub failure (missing revision, network) fails closed
        return None
    data = local.read_bytes()
    if hashlib.sha256(data).hexdigest() != entry.manifest_sha256:  # the bytes we parse (N3)
        return None
    try:
        m = Manifest.model_validate_json(data)
    except ValidationError:
        return None
    if m.tag != entry.tag or m.hf.repo != entry.hf.repo:
        return None
    if not verify_manifest(m, hub).ok:  # the witness, at m.hf.revision
        return None
    return VerifiedManifest(entry=entry, manifest=m)


@dataclass(frozen=True)
class Verdict:
    path: Path
    sha256: str
    tags: tuple[str, ...]  # the verified manifests that list this sha256
    deletable: bool
    reason: str
    stat: Stat | None = None  # set iff the file was hashed; pins what was verified (M1)

    def current(self) -> bool:
        """True while the path is still exactly the file that was hashed (M1): same
        dev/inode (not replaced) and same size/mtime_ns (not rewritten in place)."""
        if self.stat is None:
            return False
        try:
            st = self.path.lstat()
        except OSError:
            return False
        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns) == self.stat


def tracked_in(roots: list[Path]) -> Callable[[Path], bool]:
    """M3: `git ls-files --error-unmatch` in the linked repo that contains the path."""
    resolved = [r.resolve() for r in roots]

    def tracked(p: Path) -> bool:
        rp = p.resolve()
        for root in resolved:
            if not rp.is_relative_to(root):
                continue
            try:
                git(root, "ls-files", "--error-unmatch", "--", str(p))
            except subprocess.CalledProcessError:
                return False
            return True
        return False

    return tracked


def _sha256_fd(fd: int) -> str:
    """Hash the open file, by the descriptor, without touching the path again (M1)."""
    h = hashlib.sha256()
    offset = 0
    while chunk := os.pread(fd, 1 << 20, offset):
        h.update(chunk)
        offset += len(chunk)
    return h.hexdigest()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def verdicts(
    paths: list[Path],
    verified: list[VerifiedManifest],
    unverified_shas: set[str],
    roots: list[Path] | None = None,
    tracked: Callable[[Path], bool] | None = None,
) -> list[Verdict]:
    """One Verdict per path. Directories, symlinks and other non-regular files get a keep
    verdict with their reason (N7); nothing under .git/ or outside `roots` is a candidate
    (M2, resolved paths); empty, git-tracked and fresher-than-the-manifest files are never
    collected (M3)."""
    listing: dict[str, list[str]] = {}
    served: dict[str, list[str]] = {}
    created: dict[str, datetime] = {}
    for vm in verified:
        for f in vm.manifest.files:
            listing.setdefault(f.sha256, []).append(vm.manifest.tag)
            if f.served:
                served.setdefault(f.sha256, []).append(vm.manifest.tag)
            if f.sha256 not in created or vm.manifest.created > created[f.sha256]:
                created[f.sha256] = vm.manifest.created

    resolved_roots = [r.resolve() for r in roots] if roots is not None else None

    def keep(p: Path, reason: str, sha: str = "", tags: tuple[str, ...] = (),
             ident: Stat | None = None) -> Verdict:
        return Verdict(p, sha, tags, False, reason, ident)

    out: list[Verdict] = []
    for p in paths:
        if p.is_symlink():  # never followed, not even for hashing
            out.append(keep(p, "symlink — skipped"))
            continue
        resolved = p.resolve()  # through symlinked directories, before any test (M2)
        if resolved_roots is not None and not any(
            resolved.is_relative_to(r) for r in resolved_roots
        ):
            out.append(keep(p, "outside the linked repos; never collected"))
            continue
        if ".git" in resolved.parts:
            out.append(keep(p, "under .git; never collected"))
            continue
        try:  # O_NONBLOCK: a FIFO must not hang the open; O_NOFOLLOW: the final component
            fd = os.open(p, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            out.append(keep(p, "not found — skipped"))
            continue
        except OSError as e:
            out.append(keep(p, f"cannot open ({e.strerror}); skipped"))
            continue
        try:
            st = os.fstat(fd)
            if not stat_m.S_ISREG(st.st_mode):
                out.append(keep(p, "not a regular file — skipped"))
                continue
            sha = _sha256_fd(fd)
        finally:
            os.close(fd)
        ident: Stat = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
        tags = tuple(dict.fromkeys(listing.get(sha, [])))
        if st.st_size == 0 or sha == EMPTY_SHA256:  # one empty entry would empty every file
            out.append(keep(p, "0 bytes; never collected", sha, tags, ident))
        elif not tags:
            out.append(keep(p, "not listed by any verified manifest", sha, tags, ident))
        elif sha in served:
            out.append(keep(p, f"served by R2 ({', '.join(served[sha])})", sha, tags, ident))
        elif sha in unverified_shas:
            out.append(keep(p, "also listed by an unverified manifest", sha, tags, ident))
        elif tracked is not None and tracked(p):
            out.append(keep(p, "tracked by git; never collected", sha, tags, ident))
        elif datetime.fromtimestamp(st.st_mtime, tz=UTC) > _aware(created[sha]):
            out.append(keep(p, f"newer than the {tags[-1]} manifest; a run may still be "
                            "writing it", sha, tags, ident))
        else:
            out.append(Verdict(p, sha, tags, True, f"verified on HF ({', '.join(tags)})", ident))
    return out
