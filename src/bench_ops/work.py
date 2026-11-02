"""`bench work` (plan 6 B): the `.work/` TTL — list the worktree pen, drain what is safe.

Every directory under `<BENCH_ROOT>/.work/` is listed with its branch, its age (the newer of
the worktree's creation and its last commit), its dirt, and whether its HEAD is reachable
from origin's default branch or its branch was pushed at the same sha. A worktree leaves
only when *nothing of it can be lost*: no tracked change, no untracked file, no git-ignored
byte (`git status --porcelain --ignored --untracked-files=all` must be empty — a run's
output and `.env` are exactly the files a worktree holds that exist nowhere else), it is not
locked, its path resolves inside the pen, it is not the main checkout, and both its clocks
(creation — the mtime of `.git/worktrees/<id>/gitdir` — and last commit) are older than the
TTL. Only `--yes` removes, through `git worktree remove`, never `--force`.

Before any verdict, each parent repo is fetched once (`git fetch --prune origin`, M5): the
"pushed" test compares HEAD with the *server's* refs, not with a remote-tracking ref that
`bench sync` (which fetches only the configured branch) may have left stale. `--offline`
skips the fetch — and then never removes anything.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from bench_ops.config import Repo
from bench_ops.gitx import Worktree, git, worktree_list

DAY = 86_400  # seconds


@dataclass(frozen=True)
class WorktreeInfo:
    path: Path
    main: Path | None  # the owning checkout; None when the directory is no linked worktree
    branch: str
    last_commit: datetime | None
    dirty: int
    reasons: tuple[str, ...]  # empty == removable: old, clean, merged into origin or pushed
    created: datetime | None = None  # when git created the worktree (C1's second clock)

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


def fetch_origin(repo: Path) -> bool:
    """M5: refresh every origin ref (and prune deleted branches) before any verdict.

    Returns False when the fetch fails: the caller then treats every verdict that would
    rest on those refs as unsafe (a removal needs a server we could actually reach)."""
    return _git_opt(repo, "fetch", "--quiet", "--prune", "origin") is not None


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


def _worktree_created(wt: Path) -> datetime | None:
    """When git created the worktree: the mtime of its administrative `gitdir` file under
    `<common-dir>/worktrees/<id>/` (C1). The last commit's date says how old the *branch*
    is; a worktree created one second ago on an old branch is young."""
    common = _git_opt(wt, "rev-parse", "--git-common-dir")
    if not common or not common.strip():
        return None
    cdir = Path(common.strip())
    if not cdir.is_absolute():
        cdir = wt / cdir
    admin_root = cdir.resolve() / "worktrees"
    if not admin_root.is_dir():
        return None
    target = wt.resolve()
    for entry in sorted(admin_root.iterdir()):
        gitdir = entry / "gitdir"
        if not gitdir.is_file():
            continue
        try:
            pointed = Path(gitdir.read_text().strip()).resolve()
        except OSError:
            continue
        if pointed.parent == target:
            try:
                return datetime.fromtimestamp(gitdir.stat().st_mtime, tz=UTC)
            except OSError:
                return None
    return None


def _inspect(wt: Path, repo: Repo, ttl: timedelta, now: datetime,
             locked: bool, origin_ok: bool = True) -> WorktreeInfo:
    branch = (_git_opt(wt, "rev-parse", "--abbrev-ref", "HEAD") or "?").strip()
    porcelain = _git_opt(
        wt, "status", "--porcelain", "--ignored", "--untracked-files=all"
    ) or ""
    dirty = untracked = ignored = 0
    for ln in porcelain.splitlines():  # C1: ignored bytes are dirt too — git hides them
        if not ln.strip():
            continue
        code = ln[:2]
        if code.startswith("!!"):
            ignored += 1
        elif code.startswith("??"):
            untracked += 1
        else:
            dirty += 1
    iso = (_git_opt(wt, "log", "-1", "--format=%cI") or "").strip()
    last: datetime | None = None
    if iso:
        try:
            last = datetime.fromisoformat(iso)
        except ValueError:
            last = None
    created = _worktree_created(wt)
    reasons: list[str] = []
    if dirty:
        reasons.append(f"dirty {dirty}")
    if untracked:
        reasons.append(f"untracked {untracked}")
    if ignored:
        reasons.append(f"ignored files {ignored}")
    if locked:  # L6
        reasons.append("locked")
    if not origin_ok:  # M5: no verdict that rests on refs we could not refresh
        reasons.append("origin unreachable")
    elif not _merged_or_pushed(wt, branch, repo.branch):
        reasons.append("unpushed")
    if last is None:
        reasons.append("no commits")
    elif now - last <= ttl:
        reasons.append(f"younger than {format_duration(ttl)}")
    if created is None:
        reasons.append("no worktree creation time")
    elif now - created <= ttl:  # C1: the worktree's own clock, not the branch's
        reasons.append(f"younger than {format_duration(ttl)}")
    uniq = tuple(dict.fromkeys(reasons))
    return WorktreeInfo(wt, repo.local_path, branch, last, dirty, uniq, created)


def scan(
    work_root: Path,
    repos: list[Repo],
    ttl: timedelta,
    now: datetime,
    fetch: bool = True,
) -> list[WorktreeInfo]:
    """One WorktreeInfo per directory under `work_root`, sorted by name.

    A directory that is no linked repo's worktree is reported with "not a worktree" and is
    never a candidate (bench cannot judge its merged state); one that resolves outside the
    pen — a symlink to a registered worktree elsewhere — is "outside .work" and never a
    candidate either (L3): the blast radius of `bench work` is the pen.
    """
    if not work_root.is_dir():
        return []
    owners: dict[Path, tuple[Repo, Worktree]] = {}
    for repo in repos:
        for w in worktree_list(repo.local_path)[1:]:
            owners[w.path.resolve()] = (repo, w)
    unreachable: set[Path] = set()
    if fetch:  # M5: once per parent repo, before any verdict
        for path in {r.local_path for r, _ in owners.values()}:
            if not fetch_origin(path):
                unreachable.add(path)
    pen = work_root.resolve()
    out: list[WorktreeInfo] = []
    for p in sorted(work_root.iterdir()):
        if not p.is_dir():  # follows symlinks; a file in the pen is not a worktree
            continue
        resolved = p.resolve()
        found = owners.get(resolved)
        if found is None or resolved == found[0].local_path.resolve():
            out.append(WorktreeInfo(p, None, "-", None, 0, ("not a worktree",)))
            continue
        repo, w = found
        if not resolved.is_relative_to(pen):  # L3: only the pen is ever drained
            out.append(WorktreeInfo(p, None, "-", None, 0, ("outside .work",)))
            continue
        out.append(_inspect(p, repo, ttl, now, w.locked,
                            origin_ok=repo.local_path not in unreachable))
    return out


def remove(info: WorktreeInfo) -> None:
    """`git worktree remove` from the owning checkout — never `--force`.

    Raises subprocess.CalledProcessError when git refuses (dirty, untracked files, locked):
    that refusal is the safety net working, so it is reported, not bypassed.
    """
    if info.main is None:
        raise ValueError(f"{info.path} is not a worktree of a linked repo")
    git(info.main, "worktree", "remove", str(info.path))
