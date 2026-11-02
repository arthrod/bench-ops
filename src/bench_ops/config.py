"""repos.toml: the linked repositories of the workspace."""

from __future__ import annotations

import os
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

    def by_role(self, role: Role) -> list[Repo]:
        return [r for r in self.repos if r.role == role]


def default_root() -> Path:
    return Path(os.environ.get("BENCH_ROOT", Path.home() / "bench")).expanduser()


def load_config(root: Path | None = None) -> Config:
    root = (root or default_root()).resolve()
    data = tomllib.loads((root / "repos.toml").read_text())
    cfg = Config(root=root, repos=data.get("repos", []))
    for r in cfg.repos:
        r._root = root
    return cfg
