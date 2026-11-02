"""One row of `bench status` per linked repo."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bench_ops.config import Repo
from bench_ops.gitx import status as git_status
from bench_ops.gitx import worktrees
from bench_ops.hf import Witness
from bench_ops.models import LedgerEntry, Lock

WitnessFn = Callable[[LedgerEntry], Witness]


@dataclass(frozen=True)
class RepoReport:
    name: str
    role: str
    branch: str
    dirty: int
    stray_worktrees: list[str]
    release: str
    witness: str
    broken: bool = False  # an online witness that is not ok (D16): the record is not intact

    @property
    def stale(self) -> bool:
        return self.witness.startswith("stale")


def _last_release(repo: Repo) -> LedgerEntry | None:
    ledger = repo.local_path / "release" / "index.jsonl"
    return LedgerEntry.last(ledger.read_text()) if ledger.exists() else None


def _torn_ledger(repo: Repo) -> str | None:
    """'torn ledger line N' when the producer's ledger has a line that is not an entry (M4):
    a crash mid-append; `LedgerEntry.last` tolerates it, the report must still say so."""
    ledger = repo.local_path / "release" / "index.jsonl"
    if not ledger.exists():
        return None
    bad = LedgerEntry.bad_line(ledger.read_text())
    return None if bad is None else f"torn ledger line {bad}"


def latest_tags(repos: list[Repo]) -> dict[str, str]:
    """producer name -> tag of its last ledger line (producers without a ledger are absent)."""
    out: dict[str, str] = {}
    for r in repos:
        if r.role == "producer" and (last := _last_release(r)) is not None:
            out[r.name] = last.tag
    return out


def _short(p: Path) -> str:
    home = Path.home()
    return f"~/{p.relative_to(home)}" if p.is_relative_to(home) else str(p)


def _describe(w: Witness) -> str:
    if w.ok:
        return f"verified {w.checked} files on HF"
    parts = []
    if w.missing:
        parts.append(f"{len(w.missing)} missing")
    if w.mismatched:
        parts.append(f"{len(w.mismatched)} mismatched")
    return ", ".join(parts)


def _one_line(e: BaseException) -> str:
    """An exception folded into one table-cell line (M5): 'RemoteEntryNotFoundError: 404 …'."""
    msg = " ".join(str(e).split())
    return f"{type(e).__name__}: {msg}" if msg else type(e).__name__


def report(repo: Repo, latest: dict[str, str], witness: WitnessFn | None) -> RepoReport:
    """witness=None means offline: producers report their tag without asking Hugging Face."""
    g = git_status(repo.local_path)
    stray = [_short(p) for p in worktrees(repo.local_path) if ".worktrees" not in p.parts]
    release, verdict, broken = "", "", False
    if repo.role == "producer":
        last = _last_release(repo)
        torn = _torn_ledger(repo)
        if last is None and torn is None:
            release = "no releases yet"
        else:
            release = last.tag if last is not None else "(unreadable)"
            if torn is not None:
                verdict, broken = torn, True  # the record itself is damaged (M4)
            elif witness is None:
                verdict = "(offline)"
            else:
                try:
                    w = witness(last)
                except Exception as e:  # noqa: BLE001 -- any hub failure is BROKEN, never a crash (M5)
                    verdict, broken = _one_line(e), True
                else:
                    verdict, broken = _describe(w), not w.ok
    else:
        lock_path = repo.local_path / "bench.lock"
        if not lock_path.exists():
            release = "no bench.lock"
        else:
            lock = Lock.parse(lock_path.read_text())
            release = " ".join(f"{k}={v}" for k, v in sorted(lock.tags.items()))
            stale = [
                f"{k} latest is {latest[k]}"
                for k, v in sorted(lock.tags.items())
                if k in latest and latest[k] != v
            ]
            verdict = "stale: " + "; ".join(stale) if stale else "up to date"
    return RepoReport(repo.name, repo.role, g.branch, g.dirty, stray, release, verdict, broken)
