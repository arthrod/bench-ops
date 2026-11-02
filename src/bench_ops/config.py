"""repos.toml: the linked repositories of the workspace."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr

Role = Literal["producer", "consumer"]


class Repo(BaseModel):
    name: str
    url: str
    branch: str = "main"
    role: Role
    path: Path | None = None
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
