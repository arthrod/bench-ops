"""Clean trees for the commands that leave the machine (plan 8).

`repos.toml` names each repo's *working* checkout, where people and agents keep feature
branches and half-finished edits. On 2026-11-05 `bench publish ndb ndb-v0.0.4` ran in such a
checkout (an old feature branch, 20 modified files) and failed on a stale page; from a branch
whose pages happened to be current it would have published that branch's data. So:

- `release_tree` is where publish works: a worktree of freshly fetched `origin/<branch>` at
  `<BENCH_ROOT>/.work/release-<tag>`, on branch `release/<tag>`. Reused on resume.
- `scratch_tree` is where `bench exec --main` runs: a detached worktree of `origin/<branch>`,
  removed afterwards when the command left nothing in it.
- `checkout_problems` is what `bench deploy` refuses: a consumer checkout that is not on its
  configured branch, has tracked changes, or is behind its origin.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from bench_ops.config import Repo
from bench_ops.gitx import git


class CheckoutError(Exception):
    """A clean tree could not be made or proved; the message says what to fix."""


def _git(repo: Path, *args: str) -> str:
    try:
        return git(repo, *args)
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or str(e)).strip().splitlines()
        raise CheckoutError(
            f"git {' '.join(args[:3])} in {repo} failed: {detail[0] if detail else e}"
        ) from None


def _fetch(repo: Repo) -> str:
    """Fetch the configured branch; return the commit `origin/<branch>` now names."""
    if not (repo.local_path / ".git").exists():
        raise CheckoutError(f"{repo.name}: no checkout at {repo.local_path}; run `bench sync`")
    _git(repo.local_path, "fetch", "-q", "origin", repo.branch)
    return _git(repo.local_path, "rev-parse", f"origin/{repo.branch}").strip()


def _work(repo: Repo) -> Path:
    return repo._root / ".work"


def release_tree(repo: Repo, tag: str) -> Path:
    """The worktree a publish of `tag` runs in; created from origin/<branch>, or reused."""
    branch = f"release/{tag}"
    tree = _work(repo) / f"release-{tag}"
    if tree.exists():
        if not (tree / ".git").exists():
            raise CheckoutError(f"{tree} exists but is not a worktree on {branch}; move it aside")
        current = _git(tree, "rev-parse", "--abbrev-ref", "HEAD").strip()
        if current != branch:
            raise CheckoutError(f"{tree} is on {current}, not {branch}; move it aside")
        return tree  # resume: the previous run's records are in it
    head = _fetch(repo)
    tree.parent.mkdir(parents=True, exist_ok=True)
    if _git(repo.local_path, "branch", "--list", branch).strip():
        # a branch without its worktree (removed by hand): resume on it, never reset it
        _git(repo.local_path, "worktree", "add", "-q", str(tree), branch)
    else:
        _git(repo.local_path, "worktree", "add", "-q", "-b", branch, str(tree), head)
    return tree


@contextmanager
def scratch_tree(repo: Repo) -> Iterator[Path]:
    """A detached worktree of fresh origin/<branch>; removed after use unless it changed."""
    head = _fetch(repo)
    tree = _work(repo) / f"exec-{repo.name}-{uuid4().hex[:8]}"
    tree.parent.mkdir(parents=True, exist_ok=True)
    _git(repo.local_path, "worktree", "add", "-q", "--detach", str(tree), head)
    try:
        yield tree
    finally:
        left = _git(tree, "status", "--porcelain", "--ignored", "--untracked-files=all").strip()
        if left:
            print(f"kept {tree}: the command left files in it")
        else:
            _git(repo.local_path, "worktree", "remove", str(tree))


def checkout_problems(path: Path, branch: str) -> list[str]:
    """Why a checkout must not deploy: wrong branch, tracked changes, behind its origin."""
    problems: list[str] = []
    current = _git(path, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if current != branch:
        problems.append(f"{path} is on {current}, not {branch}")
    changed = _git(path, "status", "--porcelain", "--untracked-files=no").splitlines()
    if changed:
        names = ", ".join(line[3:] for line in changed[:3])
        more = f" and {len(changed) - 3} more" if len(changed) > 3 else ""
        problems.append(f"{path} has tracked changes: {names}{more}")
    _git(path, "fetch", "-q", "origin", branch)
    behind = int(_git(path, "rev-list", "--count", f"HEAD..origin/{branch}").strip() or 0)
    if behind:
        problems.append(f"{path} is {behind} commit(s) behind origin/{branch}")
    return problems
