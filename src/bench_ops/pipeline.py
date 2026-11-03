"""The config-driven `bench deploy <pipeline> <version>` release pipeline (plan 7 D3).

The workspace config owns the ordered commands. This module resolves their placeholders,
computes the next ndb release tag, proves the release preconditions, and runs one step at
a time. Every external effect goes through injected callables, so the tests never launch
Word, publish, upload, or deploy anything.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from bench_ops.config import Config, PipelineSpec, Repo
from bench_ops.gitx import clean_env, git
from bench_ops.models import LedgerEntry

WORD_COMMAND = 'osascript -e \'tell application "Microsoft Word" to get name\''
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
NDB_TAG = re.compile(r"^ndb-v\d+\.\d+\.\d+$")

CommandRunner = Callable[[str, Path], int]
WordProbe = Callable[[str, int], int]


class PipelineError(Exception):
    """A pipeline was refused before it could run the next step."""


@dataclass(frozen=True)
class ResolvedStep:
    """One config step with its placeholders and checkout resolved."""

    number: int
    name: str
    repo: str
    repo_path: Path
    command: str


@dataclass(frozen=True)
class PipelinePlan:
    """The numbered plan printed before any step runs."""

    name: str
    version: str
    tag: str
    steps: tuple[ResolvedStep, ...]
    deploy_step: int


@dataclass(frozen=True)
class ExecutionResult:
    """The first failed step and its exit code, or a clean result."""

    exit_code: int
    failed_step: ResolvedStep | None = None


def validate_version(version: str) -> str:
    """A release version is three numeric components; it also names the log file."""
    if VERSION.fullmatch(version) is None:
        raise PipelineError(f"version must be X.Y.Z, not {version!r}")
    return version


def validate_tag(tag: str) -> str:
    """Only ndb's release-tag namespace may be published by this pipeline."""
    if NDB_TAG.fullmatch(tag) is None:
        raise PipelineError(f"tag must be ndb-vX.Y.Z, not {tag!r}")
    return tag


def _ledger_text(repo: Repo) -> str:
    """The producer ledger from the checkout, or from its configured branch when the
    working tree does not carry it yet (the dry plan must remain read-only)."""
    path = repo.local_path / "release" / "index.jsonl"
    if path.is_file():
        return path.read_text()
    try:
        return git(repo.local_path, "show", f"{repo.branch}:release/index.jsonl")
    except subprocess.CalledProcessError:
        raise PipelineError(
            f"no release ledger at {path}; ndb has published nothing (pass --tag to override)"
        ) from None


def _latest_ledger_entry(repo: Repo) -> LedgerEntry:
    text = _ledger_text(repo)
    torn = LedgerEntry.bad_line(text)
    if torn is not None:
        raise PipelineError(
            f"{repo.local_path / 'release' / 'index.jsonl'} has a torn line ({torn}); "
            "repair the record before computing the next tag"
        )
    last = LedgerEntry.last(text)
    if last is None:
        raise PipelineError(
            f"{repo.local_path / 'release' / 'index.jsonl'} holds no release; "
            "pass --tag to override"
        )
    return last


def latest_ndb_tag(repo: Repo) -> str:
    """The latest ndb release tag recorded in the ledger."""
    last = _latest_ledger_entry(repo)
    if NDB_TAG.fullmatch(last.tag) is None:
        raise PipelineError(f"the ndb ledger's latest tag is {last.tag!r}, not ndb-vX.Y.Z")
    return last.tag


def next_ndb_tag(repo: Repo) -> str:
    """The next ndb patch tag after the ledger's latest release."""
    last = _latest_ledger_entry(repo)
    match = re.fullmatch(r"ndb-v(\d+)\.(\d+)\.(\d+)", last.tag)
    if match is None:
        raise PipelineError(f"the ndb ledger's latest tag is {last.tag!r}, not ndb-vX.Y.Z")
    major, minor, patch = (int(part) for part in match.groups())
    return f"ndb-v{major}.{minor}.{patch + 1}"


def resolve_command(command: str, version: str, tag: str) -> str:
    """Resolve the two placeholders a pipeline command may use."""
    return command.replace("{version}", version).replace("{tag}", tag)


def build_plan(
    cfg: Config,
    pipeline_name: str,
    version: str,
    tag_override: str | None = None,
    from_step: int = 1,
) -> PipelinePlan:
    """Resolve a configured pipeline into the exact commands and repos it will run."""
    validate_version(version)
    try:
        spec = cfg.pipelines[pipeline_name]
    except KeyError:
        raise PipelineError(f"unknown pipeline {pipeline_name!r}") from None

    if tag_override is None:
        try:
            ndb = cfg.by_name("ndb")
        except KeyError:
            raise PipelineError("the workspace config has no ndb producer") from None
        if spec.publish_step and from_step > spec.publish_step:
            # A resume after the publish step must keep the tag that was published;
            # computing another patch here would silently publish the wrong release.
            tag = latest_ndb_tag(ndb)
        else:
            tag = next_ndb_tag(ndb)
    else:
        tag = validate_tag(tag_override)

    steps = tuple(
        ResolvedStep(
            number=number,
            name=step.name,
            repo=step.repo,
            repo_path=cfg.by_name(step.repo).local_path,
            command=resolve_command(step.run, version, tag),
        )
        for number, step in enumerate(spec.steps, start=1)
    )
    return PipelinePlan(
        name=pipeline_name,
        version=version,
        tag=tag,
        steps=steps,
        deploy_step=spec.deploy_step,
    )


def word_probe(command: str, timeout_seconds: int) -> int:
    """Ask Word for its name, with a hard timeout. The command is configuration-independent."""
    return subprocess.run(
        command,
        shell=True,
        check=False,
        timeout=timeout_seconds,
    ).returncode


def preflight(
    cfg: Config,
    spec: PipelineSpec,
    probe: WordProbe,
) -> None:
    """Prove the ndb checkout, the required fixes, and Word before any step runs."""
    try:
        repo = cfg.by_name(spec.preflight_repo)
    except KeyError:
        raise PipelineError(
            f"preflight repo {spec.preflight_repo!r} is not in repos.toml"
        ) from None

    try:
        branch = git(repo.local_path, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except subprocess.CalledProcessError:
        raise PipelineError(f"cannot read the branch of {repo.local_path}") from None
    if branch != repo.branch:
        raise PipelineError(
            f"{repo.name} is on branch {branch!r}, not {repo.branch!r}; "
            "the release pipeline runs from its configured branch"
        )

    try:
        dirty = git(
            repo.local_path,
            "status",
            "--porcelain",
            "--untracked-files=all",
        ).strip()
    except subprocess.CalledProcessError:
        raise PipelineError(f"cannot read the status of {repo.local_path}") from None
    if dirty:
        first = dirty.splitlines()[0]
        raise PipelineError(
            f"{repo.name} is not clean on {repo.branch}; commit or remove {first}"
        )

    for sha in spec.required_ancestors:
        result = subprocess.run(
            ["git", "-C", str(repo.local_path), "merge-base", "--is-ancestor", sha, "HEAD"],
            check=False,
            env=clean_env(),
        )
        if result.returncode != 0:
            raise PipelineError(
                f"required commit {sha} is not an ancestor of {repo.name}'s HEAD; "
                "merge the release fixes before running the pipeline"
            )

    try:
        code = probe(WORD_COMMAND, spec.word_timeout_seconds)
    except subprocess.TimeoutExpired:
        raise PipelineError(
            f"Microsoft Word probe timed out after {spec.word_timeout_seconds}s"
        ) from None
    if code != 0:
        raise PipelineError(
            f"Microsoft Word did not answer (probe exited {code}); "
            "the release pipeline requires Word"
        )


def log_path(root: Path, pipeline_name: str, version: str, now: datetime) -> Path:
    """The per-run log below BENCH_ROOT/.logs."""
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return root / ".logs" / f"deploy-{pipeline_name}-{version}-{stamp}.log"


def execute_plan(
    plan: PipelinePlan,
    run: CommandRunner,
    log: Path,
    *,
    from_step: int = 1,
    deploy: bool = False,
) -> ExecutionResult:
    """Run selected steps in order, stopping at the first nonzero exit code."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as stream:
        for step in plan.steps[from_step - 1 :]:
            if step.number == plan.deploy_step and not deploy:
                stream.write(
                    f"step {step.number} {step.name}: skipped (requires --deploy)\n"
                )
                continue
            stream.write(f"step {step.number} {step.name}\n")
            stream.write(f"repo: {step.repo_path}\n")
            stream.write(f"command: {step.command}\n")
            stream.flush()
            code = run(step.command, step.repo_path)
            stream.write(f"exit {code}\n")
            stream.flush()
            if code != 0:
                return ExecutionResult(exit_code=code, failed_step=step)
    return ExecutionResult(exit_code=0)
