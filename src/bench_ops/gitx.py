"""Thin git wrapper. Every call is a subprocess; nothing here rewrites history."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

# Set by git for hooks; inherited, they would redirect every call to the hooked repository.
# GIT_INDEX_FILE is the exception: during a path-limited commit's pre-commit hook it names
# the temporary index git is about to commit — the ledger hook must read exactly that one
# (C2), so hook calls keep it (hook_env); every other call strips it (clean_env).
REDIRECT_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_PREFIX", "GIT_COMMON_DIR")
HOOK_ENV = ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX", "GIT_COMMON_DIR")


def clean_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in HOOK_ENV}


def hook_env() -> dict[str, str]:
    """clean_env, but GIT_INDEX_FILE survives: `git show :<path>` inside a pre-commit hook
    must read the index that commit will commit, not .git/index (C2)."""
    return {k: v for k, v in os.environ.items() if k not in REDIRECT_ENV}


def git(repo: Path, *args: str, hook: bool = False) -> str:
    out = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True, env=hook_env() if hook else clean_env(),
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


@dataclass(frozen=True)
class Worktree:
    """One entry of `git worktree list --porcelain` (L6): the locked/prunable lines git
    prints are parsed, not ignored — a locked worktree is never a removal candidate."""

    path: Path
    locked: bool = False
    prunable: bool = False


def worktree_list(repo: Path) -> list[Worktree]:
    """Every registered worktree, the main checkout first (as git lists it)."""
    if not (repo / ".git").exists():
        return []
    out: list[Worktree] = []
    cur: Worktree | None = None
    for ln in git(repo, "worktree", "list", "--porcelain").splitlines():
        if ln.startswith("worktree "):
            if cur is not None:
                out.append(cur)
            cur = Worktree(path=Path(ln.split(" ", 1)[1]))
        elif cur is None:
            continue
        elif ln.startswith("locked"):
            cur = replace(cur, locked=True)
        elif ln.startswith("prunable"):
            cur = replace(cur, prunable=True)
    if cur is not None:
        out.append(cur)
    return out


def worktrees(repo: Path) -> list[Path]:
    """Worktree paths other than the main checkout.

    git lists the main worktree first. Comparing paths instead would misfire for submodules,
    whose main worktree git reports as the module's git dir (.git/modules/<name>).
    """
    return [w.path for w in worktree_list(repo)[1:]]


def origin_url(repo: Path) -> str | None:
    try:
        return git(repo, "config", "--get", "remote.origin.url").strip() or None
    except subprocess.CalledProcessError:
        return None
