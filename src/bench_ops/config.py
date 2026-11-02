"""repos.toml: the linked repositories of the workspace."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

Role = Literal["producer", "consumer"]


class ReleaseSpec(BaseModel):
    """[repos.release]: what `bench publish` uploads. Strict, so a typo fails at load (D18).

    `before` (rev 3): the producer's own commands publish runs first, in its checkout, before
    any hub call — the pages check, the stores upload. bench-ops reimplements nothing they do.

    `served` must stay empty until the R2 mirror has a content witness (review D2/D3/D11–D13):
    publish refuses a non-empty list, so the field exists only to make that refusal explicit.

    `private` (plan 6, H2): the visibility the dataset must have. Checked against the Hub
    before anything is written — fail closed, so the default is private: a spec that does
    not say must be the safer one (ndb's public record dataset says `private = false`).
    """

    model_config = ConfigDict(extra="forbid")

    hf_repo: str
    files: list[str]  # globs, repo-relative; a "?"-prefixed glob may match nothing (M1)
    before: list[str] = []  # run by publish, in order, before any hub call
    served: list[str] = []
    private: bool = True  # the dataset's required visibility, checked before any upload (H2)


class Repo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    url: str
    branch: str = "main"
    role: Role
    path: Path | None = None
    release: ReleaseSpec | None = None
    deploy: str | None = None  # consumers: the outward-facing command `bench deploy` runs
    _root: Path = PrivateAttr(default=Path("."))

    @property
    def local_path(self) -> Path:
        return self.path if self.path is not None else self._root / self.name


class Config(BaseModel):
    root: Path
    repos: list[Repo] = Field(default_factory=list)

    def by_name(self, name: str) -> Repo:
        for r in self.repos:
            if r.name == name:
                return r
        raise KeyError(name)

    def for_checkout(self, path: Path, origin_url: str | None) -> Repo | None:
        """The linked repo a checkout belongs to: by configured path, else by origin URL.

        A clone or worktree elsewhere (another machine, ~/bench/.work) matches by its origin and
        is reported at its own path, so hooks check the checkout actually being pushed.
        """
        path = path.resolve()
        for r in self.repos:
            if r.local_path.resolve() == path:
                return r
        if origin_url:
            for r in self.repos:
                if same_remote(r.url, origin_url):
                    found = r.model_copy(update={"path": path})
                    found._root = r._root
                    return found
        return None

    def by_role(self, role: Role) -> list[Repo]:
        return [r for r in self.repos if r.role == role]


def _canonical_remote(url: str) -> str:
    """host/owner/repo, lowercased, for https, ssh:// and scp-style git@host:owner/repo URLs."""
    u = url.strip().rstrip("/").removesuffix(".git").lower()
    u = re.sub(r"^[a-z+]+://", "", u)  # scheme
    u = re.sub(r"^[^@/]+@", "", u)  # user@
    return u.replace(":", "/", 1) if "/" not in u.split(":", 1)[0] else u


def same_remote(a: str, b: str) -> bool:
    return _canonical_remote(a) == _canonical_remote(b)


def default_root() -> Path:
    return Path(os.environ.get("BENCH_ROOT", Path.home() / "bench")).expanduser()


def load_config(root: Path | None = None) -> Config:
    root = (root or default_root()).resolve()
    data = tomllib.loads((root / "repos.toml").read_text())
    cfg = Config(root=root, repos=data.get("repos", []))
    for r in cfg.repos:
        r._root = root
    return cfg
