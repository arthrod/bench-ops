"""Hugging Face as the record: is every manifest file there, with the bytes the manifest says?

HF reports a sha256 only for LFS-stored files. Small git-stored files (the JSON ledgers) have only
a git blob id, so those are downloaded and hashed. The witness never trusts a size or a name.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from bench_ops.models import Manifest


class HubApi(Protocol):
    def paths_info(self, repo: str, revision: str, paths: list[str]) -> dict[str, str | None]:
        """path -> LFS sha256, or None when the file exists but is git-stored; absent if missing."""
        ...

    def download(self, repo: str, revision: str, path: str) -> Path: ...


class HfHub:
    """The real HubApi over huggingface_hub (dataset repos only)."""

    BATCH = 500  # paths per get_paths_info request

    def __init__(self) -> None:
        from huggingface_hub import HfApi

        self.api = HfApi()

    def paths_info(self, repo: str, revision: str, paths: list[str]) -> dict[str, str | None]:
        from huggingface_hub.hf_api import RepoFile

        out: dict[str, str | None] = {}
        for i in range(0, len(paths), self.BATCH):
            infos = self.api.get_paths_info(
                repo, paths[i : i + self.BATCH], revision=revision, repo_type="dataset"
            )
            for info in infos:
                if isinstance(info, RepoFile):
                    out[info.path] = info.lfs.sha256 if info.lfs else None
        return out

    def download(self, repo: str, revision: str, path: str) -> Path:
        from huggingface_hub import hf_hub_download

        return Path(hf_hub_download(repo, path, revision=revision, repo_type="dataset"))

    # ---- the write side, used by `bench publish` (plan 2) ---------------------------------

    def exists(self, repo: str, path: str, revision: str = "main") -> bool:
        return self.api.file_exists(repo, path, revision=revision, repo_type="dataset")

    def commit(self, repo: str, files: dict[str, Path], message: str) -> str:
        """One commit adding `files` (path in repo -> local path); returns CommitInfo.oid (D10)."""
        from huggingface_hub import CommitOperationAdd

        operations = [
            CommitOperationAdd(path_in_repo=path, path_or_fileobj=local)
            for path, local in files.items()
        ]
        info = self.api.create_commit(
            repo, operations, commit_message=message, repo_type="dataset"
        )
        return info.oid  # str(info) is the commit URL, not the revision

    def create_tag(self, repo: str, tag: str, revision: str) -> None:
        self.api.create_tag(repo, tag=tag, revision=revision, repo_type="dataset")


@dataclass
class Witness:
    missing: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.missing and not self.mismatched


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_manifest(m: Manifest, api: HubApi) -> Witness:
    w = Witness()
    info = api.paths_info(m.hf.repo, m.hf.revision, [f.path for f in m.files])
    for f in m.files:
        w.checked += 1
        if f.path not in info:
            w.missing.append(f.path)
            continue
        remote = info[f.path]
        if remote is None:  # git-stored: fetch the bytes
            remote = sha256_of(api.download(m.hf.repo, m.hf.revision, f.path))
        if remote != f.sha256:
            w.mismatched.append(f.path)
    return w
