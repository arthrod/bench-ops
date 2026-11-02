"""`bench work` (plan 6 B): the `.work/` TTL — list the worktree pen, drain what is safe.

Every directory under `<BENCH_ROOT>/.work/` is listed with its branch, its age (the last
commit's committer date), its dirty count, and whether its HEAD is merged into origin's
default branch or its branch was pushed at the same sha. Only a worktree that is all of
*older than the TTL*, *clean* and *merged or pushed* is a removal candidate — and only
`--yes` actually removes it, through `git worktree remove`, never `--force`: a dirty or
locked worktree is git's refusal, not ours. Everything else is listed with the reason it
stays ("dirty 3", "unpushed", "younger than 14d", "not a worktree").

Origin refs are read as the checkout sees them (no fetch here): run `bench sync` first to
refresh them.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from bench_ops.config import Repo
from bench_ops.gitx import git, worktrees

DAY = 86_400  # seconds


@dataclass(frozen=True)
class WorktreeInfo:
    path: Path
    main: Path | None  # the owning checkout; None when the directory is no linked worktree
    branch: str
    last_commit: datetime | None
    dirty: int
    reasons: tuple[str, ...]  # empty == removable: old, clean, merged into origin or pushed

    @property
    def removable(self) -> bool:
        return not self.reasons


def parse_duration(spec: str) -> timedelta:
    """`14d`, `6h` — the TTL spelling `bench work --older-than` accepts."""
    m = re.fullmatch(r"(\d+)([dh])", spec.strip())
    if m is None:
        raise ValueError(f"duration must be like 14d or 6h, not {spec!r}")
    n = int(m.group(1))
    return timedelta(days=n) if m.group(2) == "d" else timedelta(hours=n)


def format_duration(d: timedelta) -> str:
    """The parser's inverse (whole days, else whole hours), so reason strings stay readable."""
    total = int(d.total_seconds())
    if total >= DAY and total % DAY == 0:
        return f"{total // DAY}d"
    return f"{total // 3600}h"


def _git_opt(repo: Path, *args: str) -> str | None:
    """git as a yes/no question: None on any nonzero exit (no commits, no remote ref…)."""
    try:
        return git(repo, *args)
    except subprocess.CalledProcessError:
        return None


def _origin_default(wt: Path, fallback_branch: str) -> str | None:
    """origin's default branch as this checkout sees it: origin/HEAD, else the linked branch."""
    out = _git_opt(wt, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if out and out.strip():
        return out.strip()
    return f"origin/{fallback_branch}" if fallback_branch else None


def _merged_or_pushed(wt: Path, branch: str, fallback_branch: str) -> bool:
    """HEAD reachable from origin's default branch, or origin holds this branch at HEAD.

    A detached HEAD has no branch to compare, so only reachability applies to it.
    """
    head = (_git_opt(wt, "rev-parse", "HEAD") or "").strip()
    if not head:
        return False
    default = _origin_default(wt, fallback_branch)
    if (
        default is not None
        and _git_opt(wt, "merge-base", "--is-ancestor", head, default) is not None
    ):
        return True
    if branch not in ("HEAD", "(detached)", "?"):
        pushed = (
            _git_opt(wt, "rev-parse", "--verify", f"refs/remotes/origin/{branch}") or ""
        ).strip()
        if pushed == head:
            return True
    return False


def _inspect(wt: Path, repo: Repo, ttl: timedelta, now: datetime) -> WorktreeInfo:
    branch = (_git_opt(wt, "rev-parse", "--abbrev-ref", "HEAD") or "?").strip()
    porcelain = _git_opt(wt, "status", "--porcelain") or ""
    dirty = len([ln for ln in porcelain.splitlines() if ln.strip()])
    iso = (_git_opt(wt, "log", "-1", "--format=%cI") or "").strip()
    last: datetime | None = None
    if iso:
        try:
            last = datetime.fromisoformat(iso)
        except ValueError:
            last = None
    reasons: list[str] = []
    if dirty:
        reasons.append(f"dirty {dirty}")
    if not _merged_or_pushed(wt, branch, repo.branch):
        reasons.append("unpushed")
    if last is None:
        reasons.append("no commits")
    elif now - last <= ttl:
        reasons.append(f"younger than {format_duration(ttl)}")
    return WorktreeInfo(wt, repo.local_path, branch, last, dirty, tuple(reasons))


def scan(
    work_root: Path, repos: list[Repo], ttl: timedelta, now: datetime
) -> list[WorktreeInfo]:
    """One WorktreeInfo per directory under `work_root`, sorted by name.

    A directory that is no linked repo's worktree is reported with "not a worktree" and is
    never a candidate (bench cannot judge its merged state).
    """
    if not work_root.is_dir():
        return []
    owners: dict[Path, Repo] = {}
    for repo in repos:
        for wt in worktrees(repo.local_path):
            owners[wt.resolve()] = repo
    out: list[WorktreeInfo] = []
    for p in sorted(work_root.iterdir()):
        if not p.is_dir():
            continue
        repo = owners.get(p.resolve())
        if repo is None:
            out.append(WorktreeInfo(p, None, "-", None, 0, ("not a worktree",)))
        else:
            out.append(_inspect(p, repo, ttl, now))
    return out


def remove(info: WorktreeInfo) -> None:
    """`git worktree remove` from the owning checkout — never `--force`.

    Raises subprocess.CalledProcessError when git refuses (dirty, untracked files, locked):
    that refusal is the safety net working, so it is reported, not bypassed.
    """
    if info.main is None:
        raise ValueError(f"{info.path} is not a worktree of a linked repo")
    git(info.main, "worktree", "remove", str(info.path))
