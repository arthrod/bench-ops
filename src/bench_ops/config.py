"""repos.toml: the linked repositories of the workspace."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, StringConstraints, model_validator

Role = Literal["producer", "consumer"]
GitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
NonEmpty = Annotated[str, StringConstraints(min_length=1)]


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


class PipelineStep(BaseModel):
    """One ordered command in a release pipeline.

    The command is configuration, not code: ndb can rename or add a release tool without
    changing bench-ops. ``{version}`` is the jubarte release version and ``{tag}`` is the
    ndb release tag. Unknown placeholders are rejected when the workspace config loads.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: NonEmpty
    repo: NonEmpty
    run: NonEmpty


class PipelineSpec(BaseModel):
    """A named release pipeline and the safety checks that guard it.

    ``deploy_step`` is the one-based number of the outward-facing deploy command; it runs
    only with the pipeline's explicit deploy approval. ``required_ancestors`` are git SHAs
    that must already be ancestors of the preflight repository's HEAD.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    steps: list[PipelineStep]
    preflight_repo: NonEmpty = "ndb"
    publish_step: int = Field(default=0, ge=0)
    deploy_step: int = Field(default=0, ge=0)
    word_timeout_seconds: int = Field(default=30, gt=0)
    required_ancestors: list[GitSha] = Field(default_factory=list)


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
    pipelines: dict[str, PipelineSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_pipelines(self) -> Config:
        repo_names = [repo.name for repo in self.repos]
        duplicates = sorted({name for name in repo_names if repo_names.count(name) > 1})
        if duplicates:
            raise ValueError(f"duplicate repo names: {duplicates}")

        for name, pipeline in self.pipelines.items():
            if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", name) is None:
                raise ValueError(
                    f"pipeline name {name!r} must be a lowercase slug "
                    "(letters, digits, and hyphens)"
                )
            if name in repo_names:
                raise ValueError(
                    f"{name!r} names both a pipeline and a repo; pipeline and repo names "
                    "must be distinct"
                )
            if pipeline.preflight_repo not in repo_names:
                raise ValueError(
                    f"pipeline {name!r} has unknown preflight repo {pipeline.preflight_repo!r}"
                )
            if pipeline.publish_step and pipeline.publish_step > len(pipeline.steps):
                raise ValueError(
                    f"pipeline {name!r} publish_step {pipeline.publish_step} has no matching step"
                )
            if pipeline.deploy_step and pipeline.deploy_step > len(pipeline.steps):
                raise ValueError(
                    f"pipeline {name!r} deploy_step {pipeline.deploy_step} has no matching step"
                )
            step_names = [step.name for step in pipeline.steps]
            duplicate_steps = sorted(
                {step_name for step_name in step_names if step_names.count(step_name) > 1}
            )
            if duplicate_steps:
                raise ValueError(f"pipeline {name!r} has duplicate step names: {duplicate_steps}")
            for step in pipeline.steps:
                if step.repo not in repo_names:
                    raise ValueError(
                        f"pipeline {name!r} step {step.name!r} names unknown repo {step.repo!r}"
                    )
                for placeholder in re.findall(r"\{([^{}]+)\}", step.run):
                    if placeholder not in {"version", "tag"}:
                        raise ValueError(
                            f"pipeline {name!r} step {step.name!r} has unknown placeholder "
                            f"{{{placeholder}}}; use {{version}} or {{tag}}"
                        )
        return self

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
    cfg = Config(
        root=root,
        repos=data.get("repos", []),
        pipelines=data.get("pipelines", {}),
    )
    for r in cfg.repos:
        r._root = root
    return cfg
