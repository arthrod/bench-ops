"""`bench gc`: delete a local file only when the Hub says its bytes are recorded (D1/D3/D22).

Authority is unforgeable: `VerifiedManifest` exists only as the return value of
`fetch_verified`, which downloads the manifest from the Hub at the ledger's pinned revision
(never from disk), checks its sha256 against the ledger, checks tag and repo, and runs the
content witness at the manifest's own revision. A local file is deletable iff its sha256 is
listed by at least one verified manifest, by no unverified one, and is `served` in none.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from bench_ops.hf import HubApi, sha256_of, verify_manifest
from bench_ops.models import LedgerEntry, Manifest, Sha256


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
    if sha256_of(local) != entry.manifest_sha256:  # fail closed before any witness work
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


def verdicts(
    paths: list[Path],
    verified: list[VerifiedManifest],
    unverified_shas: set[Sha256],
) -> list[Verdict]:
    """One Verdict per eligible path; directories and symlinks are skipped, .git is protected."""
    listing: dict[str, list[str]] = {}
    served: dict[str, list[str]] = {}
    for vm in verified:
        for f in vm.manifest.files:
            listing.setdefault(f.sha256, []).append(vm.manifest.tag)
            if f.served:
                served.setdefault(f.sha256, []).append(vm.manifest.tag)

    out: list[Verdict] = []
    for p in paths:
        if p.is_symlink() or not p.is_file():
            continue
        sha = sha256_of(p)
        tags = tuple(dict.fromkeys(listing.get(sha, [])))
        if ".git" in p.parts:
            out.append(Verdict(p, sha, tags, False, "under .git; never collected"))
        elif not tags:
            out.append(Verdict(p, sha, tags, False, "not listed by any verified manifest"))
        elif sha in served:
            out.append(Verdict(p, sha, tags, False, f"served by R2 ({', '.join(served[sha])})"))
        elif sha in unverified_shas:
            out.append(Verdict(p, sha, tags, False, "also listed by an unverified manifest"))
        else:
            out.append(Verdict(p, sha, tags, True, f"verified on HF ({', '.join(tags)})"))
    return out
