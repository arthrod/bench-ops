"""`bench bump` / `bench fetch`: the consumer half of a release pin (plan 4).

bench.lock pins `<producer>=<tag> manifest=<sha256>` — the manifest's own sha256, so a
moved HF tag cannot change what a consumer renders. `bench bump` copies the producer's
last ledger line into the lock (it reads the producer checkout named in repos.toml).
`bench fetch` materialises the pin under `<repo>/.bench/<producer>/`: the manifest is
downloaded at the HF tag and refused unless it hashes to the lock's sha, then every file
it lists is downloaded at the manifest's own pinned revision and verified. The tree is
built in a scratch directory and renamed into place — atomic per producer — and files
already present with the right hash are not downloaded again. `check_tree` is the offline
half (`bench fetch --check`, the pre-commit hook, `bench deploy`): it re-hashes `.bench/`
against the lock, naming the first problem. Private datasets use the user's
huggingface_hub token (the default auth).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from bench_ops.config import Config, Repo
from bench_ops.gitx import git
from bench_ops.hf import HubApi, sha256_of
from bench_ops.models import FileEntry, LedgerEntry, Lock, Manifest, manifest_path

LOCK = "bench.lock"


class FetchError(Exception):
    """A bump or fetch was refused: nothing was written that had not verified."""


def _parse(lock_path: Path) -> Lock:
    try:
        return Lock.parse(lock_path.read_text())
    except ValueError as e:
        raise FetchError(str(e)) from None


def _repo(producer: str, cfg: Config) -> Repo:
    try:
        return cfg.by_name(producer)
    except KeyError:
        known = ", ".join(sorted(r.name for r in cfg.repos))
        raise FetchError(f"unknown repo {producer!r}; known: {known}") from None


def _rel_under(path: str, prefix: str, producer: str) -> Path:
    """The manifest path relative to `prefix`, refusing anything that would escape
    `.bench/<producer>/` (the manifest is remote bytes; its paths are never trusted)."""
    if not path.startswith(prefix):
        raise FetchError(f"{producer}: {path} is not under {prefix}")
    rel = path[len(prefix) :]
    parts = PurePosixPath(rel).parts
    if not parts or any(p in ("", ".", "..") or p.startswith(".") for p in parts):
        raise FetchError(f"{producer}: {path} would escape .bench/{producer}; not fetching it")
    return Path(*parts)


# ---- bench bump --------------------------------------------------------------------------


def _origin_ledger(checkout: Path, branch: str) -> str | None:
    """The ledger on origin/<branch> after a fetch; None when there is no remote copy (#21)."""
    if not (checkout / ".git").exists():
        return None
    rel = "release/index.jsonl"
    try:
        git(checkout, "fetch", "-q", "origin", branch)
        return git(checkout, "show", f"origin/{branch}:{rel}")
    except subprocess.CalledProcessError:
        return None


def _newest_ledger(checkout: Path, branch: str, ledger: Path) -> str | None:
    """The longer of the working-tree ledger and origin/<branch>'s (#21).

    The ledger only grows, so one copy must be a prefix of the other. The working tree may
    be ahead (a publish commits the ledger locally and leaves the push to a human) or behind
    / on another branch (a checkout parked on a results branch); origin covers the latter.
    """
    local = ledger.read_text() if ledger.exists() else None
    remote = _origin_ledger(checkout, branch)
    if local is None or remote is None:
        return local if remote is None else remote
    if local.startswith(remote):
        return local
    if remote.startswith(local):
        return remote
    raise FetchError(
        f"{ledger} and origin/{branch}:release/index.jsonl diverge (neither is a prefix of the "
        "other); the ledger only grows, so one of them was rewritten — repair it before bumping"
    )


def bump(producer: str, cfg: Config, repo_root: Path) -> tuple[str, str, str]:
    """Write the producer's last ledger tag and manifest sha into <repo_root>/bench.lock.

    Creates the lock when missing and keeps every other producer's line. Returns
    (producer, old, new) for the `old -> new` print; old is "(none)" when the producer
    was not pinned before.
    """
    repo = _repo(producer, cfg)
    if repo.role != "producer":
        raise FetchError(f"{producer} is a {repo.role}; bench bump pins a producer's release")
    ledger = repo.local_path / "release" / "index.jsonl"
    text = _newest_ledger(repo.local_path, repo.branch, ledger)
    if text is None:
        raise FetchError(f"no release ledger at {ledger}: {producer} has published nothing")
    torn = LedgerEntry.bad_line(text)
    if torn is not None:
        raise FetchError(f"{ledger} has a torn line ({torn}); repair the record before bumping")
    last = LedgerEntry.last(text)
    if last is None:
        raise FetchError(f"{ledger} holds no ledger entry; nothing to bump to")

    lock_path = repo_root / LOCK
    lock = _parse(lock_path) if lock_path.exists() else Lock(tags={})
    old = lock.tags.get(producer, "(none)")
    lock.tags[producer] = last.tag
    lock.shas[producer] = last.manifest_sha256
    lock_path.write_text(lock.render())
    return producer, old, f"{last.tag} manifest={last.manifest_sha256}"


# ---- bench fetch -------------------------------------------------------------------------


def _swap_into_place(scratch: Path, target: Path) -> None:
    """Rename the scratch tree over the target directory (atomic per producer).

    A reader of `.bench/<producer>/` sees either the previous tree or the complete new
    one. The previous tree is moved aside first and deleted only after the rename; a
    failed rename puts it back."""
    aside: Path | None = None
    if target.exists():
        aside = Path(tempfile.mkdtemp(dir=target.parent, prefix=f".old-{target.name}-"))
        os.replace(target, aside)  # renaming onto the empty dir succeeds
    try:
        os.replace(scratch, target)
    except OSError:
        if aside is not None:
            os.replace(aside, target)  # put the previous tree back
        raise
    if aside is not None:
        shutil.rmtree(aside, ignore_errors=True)


def _fetch_producer(
    repo_root: Path, producer: str, tag: str, sha: str, cfg: Config, hub: HubApi
) -> None:
    repo = _repo(producer, cfg)
    if repo.role != "producer":
        raise FetchError(f"{producer} is a {repo.role}; bench fetch pulls a producer's release")
    if repo.release is None:
        raise FetchError(f"{producer} has no [repos.release] in repos.toml; it publishes nothing")

    mpath = manifest_path(tag)  # release/<tag>/manifest.json
    data = hub.download(repo.release.hf_repo, tag, mpath).read_bytes()
    if hashlib.sha256(data).hexdigest() != sha:
        raise FetchError(
            f"{producer}: manifest at HF tag {tag} hashes to "
            f"{hashlib.sha256(data).hexdigest()}, bench.lock pins {sha}; "
            "the lock and the Hub disagree — never fetch unverified bytes"
        )
    try:
        m = Manifest.model_validate_json(data)
    except ValidationError:
        raise FetchError(f"{producer}: {mpath} at HF tag {tag} is not a manifest") from None
    if m.tag != tag or m.producer != producer or m.hf.repo != repo.release.hf_repo:
        raise FetchError(
            f"{producer}: the manifest at HF tag {tag} records {m.producer}'s {m.tag} "
            f"in {m.hf.repo}; not fetching it"
        )

    prefix = f"release/{tag}/"
    files = [(f, _rel_under(f.path, prefix, producer)) for f in m.files]
    bench = repo_root / ".bench"
    bench.mkdir(parents=True, exist_ok=True)
    prod_dir = bench / producer
    scratch = Path(tempfile.mkdtemp(dir=bench, prefix=f".tmp-{producer}-"))
    try:
        to_download: list[tuple[FileEntry, Path]] = []
        present = 0
        for entry, rel in files:
            dest = scratch / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            have = prod_dir / rel
            if have.is_file() and sha256_of(have) == entry.sha256:
                try:
                    os.link(have, dest)  # same tree (.bench/), so a link is enough
                except OSError:
                    shutil.copy2(have, dest)
                present += 1
            else:
                to_download.append((entry, dest))
        for entry, dest in to_download:
            got = hub.download(m.hf.repo, m.hf.revision, entry.path)
            shutil.copyfile(got, dest)
            if sha256_of(dest) != entry.sha256:
                raise FetchError(
                    f"{producer}: {entry.path} at {m.hf.revision} does not hash to what "
                    f"{mpath} says; the record is not intact"
                )
        (scratch / "manifest.json").write_bytes(data)
        _swap_into_place(scratch, prod_dir)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(
        f"{producer}: {tag} -> {prod_dir} "
        f"({len(to_download)} downloaded, {present} present; {len(files)} files + manifest.json)"
    )


def fetch(repo_root: Path, cfg: Config, hub: HubApi) -> None:
    """Materialise every bench.lock pin under <repo_root>/.bench/<producer>/."""
    lock_path = repo_root / LOCK
    if not lock_path.exists():
        raise FetchError(f"no bench.lock at {lock_path}; run `bench bump <producer>` first")
    lock = _parse(lock_path)
    for producer in sorted(lock.tags):
        tag = lock.tags[producer]
        sha = lock.shas.get(producer)
        if sha is None:
            raise FetchError(
                f"bench.lock pins {producer}={tag} without a manifest sha; "
                f"run `bench bump {producer} --repo {repo_root}`"
            )
        _fetch_producer(repo_root, producer, tag, sha, cfg, hub)


# ---- bench fetch --check (offline) -------------------------------------------------------


def check_tree(repo_root: Path) -> list[str]:
    """Offline problems with `<repo_root>/.bench/` against `<repo_root>/bench.lock`, in
    check order: the lock and its pins, then per producer the manifest's hash, its listed
    files (in manifest order) and anything extra. An empty list means the fetched tree
    is exactly the pinned one; the caller names the first problem."""
    lock_path = repo_root / LOCK
    if not lock_path.exists():
        return [f"no bench.lock at {lock_path}; run `bench bump <producer> --repo {repo_root}`"]
    try:
        lock = _parse(lock_path)
    except FetchError as e:
        return [str(e)]
    problems: list[str] = []
    for producer in sorted(lock.tags):
        tag = lock.tags[producer]
        sha = lock.shas.get(producer)
        if sha is None:
            problems.append(
                f"{producer}: pinned {tag} without a manifest sha; run `bench bump {producer}`"
            )
            continue
        prod = repo_root / ".bench" / producer
        mfile = prod / "manifest.json"
        if not mfile.is_file():
            problems.append(f"{producer}: {mfile} is missing; run `bench fetch --repo {repo_root}`")
            continue
        actual = sha256_of(mfile)
        if actual != sha:
            problems.append(f"{producer}: {mfile} hashes to {actual}, bench.lock pins {sha}")
            continue
        try:
            m = Manifest.model_validate_json(mfile.read_bytes())
        except ValidationError:
            problems.append(f"{producer}: {mfile} is not a manifest")
            continue
        if m.tag != tag:
            problems.append(f"{producer}: {mfile} records tag {m.tag}, bench.lock pins {tag}")
            continue
        prefix = f"release/{tag}/"
        listed = {"manifest.json"}
        for entry in m.files:
            try:
                rel = _rel_under(entry.path, prefix, producer)
            except FetchError as e:
                problems.append(str(e))
                continue
            listed.add(rel.as_posix())
            local = prod / rel
            if not local.is_file():
                problems.append(
                    f"{producer}: {local} is missing; run `bench fetch --repo {repo_root}`"
                )
            elif sha256_of(local) != entry.sha256:
                problems.append(
                    f"{producer}: {local} hashes to {sha256_of(local)}, "
                    f"the manifest says {entry.sha256}"
                )
        extra = sorted(
            p.relative_to(prod).as_posix() for p in prod.rglob("*")
            if p.is_file() and p.relative_to(prod).as_posix() not in listed
        )
        if extra:
            problems.append(
                f"{producer}: {prod / extra[0]} is not in the manifest; "
                f"run `bench fetch --repo {repo_root}` to replace the tree"
            )
    return problems
