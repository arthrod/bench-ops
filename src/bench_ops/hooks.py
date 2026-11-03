"""Pre-commit checks every linked repo runs through lefthook. Each must finish in seconds."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from bench_ops.gitx import git
from bench_ops.ledger import check_ledger

LIMIT = 2 * 1024 * 1024
# Directories whose binaries are data, not code: they live on Hugging Face (record) and R2 (cache).
BLOCKED_DIRS = ("results", "out", "public/fixtures", "public/neurotic-docx-bench", ".bench")
BLOCKED_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".webp", ".pdf", ".docx", ".zip", ".tar", ".gz", ".parquet", ".bin",
)


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


def _untracked(_rel: str) -> int | None:
    return None


def size_guard(
    files: list[Path],
    root: Path,
    limit: int = LIMIT,
    previous_size: Callable[[str], int | None] = _untracked,
) -> list[str]:
    """Problems with the staged files; paths are relative to the repo root, as lefthook passes them.

    A new file may not exceed the limit. A file already tracked (previous_size gives its size at
    HEAD) may grow, as the append-only ledgers do, but not by more than the limit in one commit.
    """
    problems: list[str] = []
    for f in files:
        path = f if f.is_absolute() else root / f
        if not path.is_file():  # deleted or renamed away
            continue
        rel = path.resolve().relative_to(root.resolve()).as_posix()
        size = path.stat().st_size
        before = previous_size(rel)
        if before is None and size > limit:
            problems.append(f"{rel}: {_mb(size)} over the {_mb(limit)} limit")
            continue
        if before is not None and size - before > limit:
            problems.append(f"{rel}: grew by {_mb(size - before)}, over the {_mb(limit)} limit")
            continue
        top = next((d for d in BLOCKED_DIRS if rel.startswith(d + "/")), None)
        if top and path.suffix.lower() in BLOCKED_SUFFIXES:
            problems.append(f"{rel}: binary under {top}/ belongs on Hugging Face")
    return problems


def secrets_command(files: list[Path]) -> list[str] | None:
    """The ripsecrets argv, or None when the tool is not on PATH."""
    exe = shutil.which("ripsecrets")
    return None if exe is None else [exe, "--strict-ignore", *map(str, files)]


# M4: every ledger the workspace knows — release/index.jsonl in the producers and
# archive/index.jsonl in BENCH_ROOT (the bench-ops repo itself).
LEDGER_PATHS = ("release/index.jsonl", "archive/index.jsonl")


def _git_text(repo: Path, *args: str) -> str | None:
    """The output of a git command, or None when it fails (the path is not there)."""
    try:
        return git(repo, *args, hook=True)
    except subprocess.CalledProcessError:
        return None


def _repo_root(repo: Path) -> Path | None:
    """The top of the work tree, or None when `repo` is not in one."""
    out = _git_text(repo, "rev-parse", "--show-toplevel")
    return Path(out.strip()) if out and out.strip() else None


def _relative(root: Path, path: Path) -> str:
    """`path` relative to the repo root, as a posix path (lefthook passes relative paths)."""
    full = path if path.is_absolute() else root / path
    try:
        return full.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return full.as_posix()


def _staged_removal(root: Path, ledger: str) -> bool:
    """True when the ledger is staged for deletion or rename (it may only grow)."""
    diff = _git_text(root, "diff", "--cached", "--name-status", "-M", "--", ledger) or ""
    return any(ln.split("\t")[0].startswith(("D", "R")) for ln in diff.splitlines())


def ledger_problems(repo: Path, files: list[Path] | None = None) -> list[str]:
    """Problems with the ledgers in the index of `repo`: they may only grow (D7/D14).

    The staged blob (`git show :<ledger>`) is compared with HEAD's (`git show HEAD:<ledger>`);
    a ledger absent at HEAD is new, which is fine. Every line must parse as a `LedgerEntry`
    and no tag may appear twice — `check_ledger` names the offending line.

    `files` are the staged files lefthook passed. When given, only the ledgers among them are
    compared. The deletion and rename check still runs for every known ledger: lefthook's file
    lists exclude deletions, so a glob-scoped job would never see them (C1).

    Outside a git repository there is nothing to check: no problems.
    """
    root = _repo_root(repo)
    if root is None:
        return []
    touched = None if files is None else {_relative(root, f) for f in files}
    problems: list[str] = []
    for ledger in LEDGER_PATHS:
        if _staged_removal(root, ledger):
            problems.append(f"{ledger} may only grow: staged deletions and renames are refused")
            continue
        if touched is not None and ledger not in touched:
            continue
        staged = _git_text(root, "show", f":{ledger}")
        if staged is None:  # not in the index: nothing was staged for it
            continue
        head = _git_text(root, "show", f"HEAD:{ledger}")
        problems.extend(f"{ledger}: {p}" for p in check_ledger(head, staged))
    return problems
