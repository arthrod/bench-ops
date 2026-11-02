"""Thin git wrapper. Every call is a subprocess; nothing here rewrites history."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Set by git for hooks; inherited, they would redirect every call to the hooked repository.
HOOK_ENV = ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX", "GIT_COMMON_DIR")


def clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in HOOK_ENV}


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True, env=clean_env(),
    )
    return out.stdout


def clone_or_pull(url: str, dest: Path, branch: str) -> str:
    """Clone a missing repo; fast-forward an existing one only when that is safe.

    A checkout on another branch or with local changes is only fetched, never switched or merged:
    these are working checkouts, and sync must not move work out from under anyone.
    """
    if (dest / ".git").exists():
        git(dest, "fetch", "-q", "origin", branch)
        current = git(dest, "rev-parse", "--abbrev-ref", "HEAD").strip()
        if current != branch:
            return f"fetched (on {current})"
        if git(dest, "status", "--porcelain", "--untracked-files=no").strip():
            return "fetched (dirty)"
        try:
            git(dest, "merge", "-q", "--ff-only", f"origin/{branch}")
        except subprocess.CalledProcessError:
            return "fetched (diverged)"
        return "pulled"
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "-q", "--branch", branch, url, str(dest)], check=True, env=clean_env()
    )
    return "cloned"


@dataclass(frozen=True)
class GitStatus:
    branch: str
    dirty: int
    head: str


def status(repo: Path) -> GitStatus:
    if not (repo / ".git").exists():
        return GitStatus(branch="(missing)", dirty=0, head="")
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    head = git(repo, "rev-parse", "--short", "HEAD").strip()
    dirty = len([ln for ln in git(repo, "status", "--porcelain").splitlines() if ln.strip()])
    return GitStatus(branch=branch, dirty=dirty, head=head)


def worktrees(repo: Path) -> list[Path]:
    """Worktrees other than the main checkout."""
    if not (repo / ".git").exists():
        return []
    paths: list[Path] = []
    for line in git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            p = Path(line.split(" ", 1)[1])
            if p.resolve() != repo.resolve():
                paths.append(p)
    return paths
