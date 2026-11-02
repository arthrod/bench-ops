"""Plan 6 part B: `bench work` — the `.work/` TTL. Real git repos and worktrees, no network.

A worktree leaves `.work/` only when it is older than the TTL, clean, and its HEAD is merged
into origin's default branch or its branch was pushed at the same sha — and only with `--yes`,
through `git worktree remove`, never `--force`.
"""

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bench_ops.cli import app
from bench_ops.config import Repo
from bench_ops.work import format_duration, parse_duration, remove, scan
from tests.test_gitx import make_origin

runner = CliRunner()
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)  # unit tests pass `now` explicitly
OLD = NOW - timedelta(days=30)
YOUNG = NOW - timedelta(days=2)
TTL = timedelta(days=14)
# The CLI computes `now` itself, so its fixtures anchor to the real clock.
REAL_OLD = datetime.now(UTC) - timedelta(days=30)
REAL_YOUNG = datetime.now(UTC) - timedelta(days=2)


def _run(repo: Path, *args: str, when: datetime | None = None) -> None:
    env = {**os.environ}
    if when is not None:
        stamp = when.strftime("%Y-%m-%d %H:%M:%S +0000")
        env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = stamp
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "core.hooksPath=/dev/null", *args],
        check=True, capture_output=True, env=env,
    )


def _workshop(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """(origin, main clone, bench root, .work/) — the linked repo and its worktree pen."""
    origin = make_origin(tmp_path)
    main = tmp_path / "main"
    subprocess.run(["git", "clone", "-q", str(origin), str(main)], check=True)
    work = tmp_path / "root" / ".work"
    work.mkdir(parents=True)
    return origin, main, tmp_path / "root", work


def _repo(main: Path, origin: Path) -> Repo:
    return Repo(name="ndb", url=str(origin), branch="main", role="producer", path=main)


def _age_creation(main: Path, wt: Path, when: datetime) -> None:
    """Backdate a worktree's creation clock (C1): the admin gitdir file git wrote at `worktree
    add`. Without this, every fixture worktree is seconds old and never removable."""
    for entry in sorted((main / ".git" / "worktrees").iterdir()):
        gitdir = entry / "gitdir"
        if gitdir.is_file() and gitdir.read_text().strip().endswith(f"{wt.name}/.git"):
            os.utime(gitdir, (when.timestamp(), when.timestamp()))


def _worktree(
    main: Path, work: Path, name: str, *, when: datetime,
    branch: str | None = None, detach_at: str | None = None,
    merge: bool = False, push: bool = False, push_branch: bool = False,
    dirty: bool = False,
) -> Path:
    wt = work / name
    if detach_at is not None:
        _run(main, "worktree", "add", "--detach", str(wt), detach_at)
    elif branch is not None:
        _run(main, "worktree", "add", "-b", branch, str(wt))
    else:
        _run(main, "worktree", "add", str(wt))
    _age_creation(main, wt, when)  # C1: the TTL is the worktree's age, not the branch's
    if detach_at is None:
        (wt / f"{name}.txt").write_text(name + "\n")
        _run(wt, "add", "-A")
        _run(wt, "commit", "-qm", name, when=when)
    if merge:
        _run(main, "merge", "-q", "--ff-only", branch or name)
    if push:
        _run(main, "push", "-q", "origin", "main")
    if push_branch:
        _run(main, "push", "-q", "origin", branch or name)
    if dirty:
        (wt / f"{name}-dirty.txt").write_text("uncommitted\n")  # untracked: dirt, blocks removal
    return wt


def _scan(work: Path, main: Path, origin: Path, ttl: timedelta = TTL, now: datetime = NOW):
    return {i.path.name: i for i in scan(work, [_repo(main, origin)], ttl=ttl, now=now)}


# ---- the duration parser -------------------------------------------------------------------


def test_parse_duration_accepts_days_and_hours() -> None:
    assert parse_duration("14d") == timedelta(days=14)
    assert parse_duration("5h") == timedelta(hours=5)
    assert parse_duration("0h") == timedelta()


@pytest.mark.parametrize("bad", ["14", "d14", "14w", "1.5d", "", " ", "-3d", "14dd", "1d4h"])
def test_parse_duration_refuses_anything_else(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_duration(bad)


def test_format_duration_round_trips_the_parser() -> None:
    assert format_duration(timedelta(days=14)) == "14d"
    assert format_duration(timedelta(hours=5)) == "5h"
    for text in ("14d", "5h", "1d", "200h"):
        assert parse_duration(format_duration(parse_duration(text))) == parse_duration(text)


# ---- scan ----------------------------------------------------------------------------------


def test_scan_lists_age_dirty_branch_and_the_reason_each_worktree_stays(tmp_path: Path) -> None:
    origin, main, _root, work = _workshop(tmp_path)
    merged = _worktree(main, work, "old-merged", branch="merged", when=OLD, merge=True, push=True)
    _worktree(main, work, "dirty", branch="dirty", when=OLD, merge=True, push=True, dirty=True)
    _worktree(main, work, "young", branch="young", when=YOUNG, merge=True, push=True)
    _worktree(main, work, "unpushed", branch="unpushed", when=OLD)
    (work / "plain").mkdir()

    infos = _scan(work, main, origin)

    assert infos["old-merged"].removable  # old, clean, merged into origin/main
    assert infos["old-merged"].branch == "merged"
    assert infos["old-merged"].dirty == 0
    assert infos["old-merged"].last_commit is not None
    assert infos["old-merged"].created is not None  # C1: the creation clock is known
    assert NOW - infos["old-merged"].last_commit > TTL

    assert infos["dirty"].reasons == ("untracked 1",)  # C1: untracked bytes are named as such
    assert infos["young"].reasons == ("younger than 14d",)
    assert infos["unpushed"].reasons == ("unpushed",)
    assert infos["plain"].reasons == ("not a worktree",)
    for name in ("dirty", "young", "unpushed", "plain"):
        assert not infos[name].removable
    assert merged.name == "old-merged"  # every path above is still there


def test_scan_treats_a_pushed_branch_as_safe_even_unmerged(tmp_path: Path) -> None:
    origin, main, _root, work = _workshop(tmp_path)
    _worktree(main, work, "feature", branch="feature", when=OLD, push_branch=True)
    infos = _scan(work, main, origin)
    assert infos["feature"].removable  # origin/feature == HEAD


def test_scan_detached_head_stays_unless_reachable_from_origin_default(tmp_path: Path) -> None:
    origin, main, _root, work = _workshop(tmp_path)
    _run(main, "commit", "-q", "--allow-empty", "-m", "old main", when=OLD)
    _run(main, "push", "-q", "origin", "main")
    reach = subprocess.run(["git", "-C", str(main), "rev-parse", "HEAD"],
                           check=True, capture_output=True, text=True).stdout.strip()
    side = _worktree(main, work, "side-src", branch="side-src", when=OLD)
    side_sha = subprocess.run(["git", "-C", str(side), "rev-parse", "HEAD"],
                              check=True, capture_output=True, text=True).stdout.strip()
    _run(main, "worktree", "add", "--detach", str(work / "det-reach"), reach)
    _run(main, "worktree", "add", "--detach", str(work / "det-side"), side_sha)
    _age_creation(main, work / "det-reach", OLD)
    _age_creation(main, work / "det-side", OLD)

    infos = _scan(work, main, origin)
    assert infos["det-reach"].removable  # clean, old, and origin/main contains HEAD
    assert infos["det-reach"].branch in ("HEAD", "(detached)")
    assert infos["det-side"].reasons == ("unpushed",)
    assert infos["side-src"].reasons == ("unpushed",)  # negative control: not detached, not pushed


def test_scan_reports_a_missing_work_root_as_empty(tmp_path: Path) -> None:
    origin, main, root, _work = _workshop(tmp_path)
    assert scan(root / ".nope", [_repo(main, origin)], ttl=TTL, now=NOW) == []


# ---- remove ----------------------------------------------------------------------------------


def test_remove_uses_git_worktree_remove(tmp_path: Path) -> None:
    origin, main, _root, work = _workshop(tmp_path)
    wt = _worktree(main, work, "gone", branch="gone", when=OLD, merge=True, push=True)
    infos = _scan(work, main, origin)
    remove(infos["gone"])
    assert not wt.exists()
    listing = subprocess.run(["git", "-C", str(main), "worktree", "list"],
                             check=True, capture_output=True, text=True).stdout
    assert str(wt) not in listing  # the registration went with it


def test_remove_never_forces_a_dirty_worktree(tmp_path: Path) -> None:
    origin, main, _root, work = _workshop(tmp_path)
    wt = _worktree(main, work, "dirty", branch="dirty", when=OLD, merge=True, push=True,
                   dirty=True)
    infos = _scan(work, main, origin)
    assert not infos["dirty"].removable  # scan already refuses; remove double-checks via git
    with pytest.raises(subprocess.CalledProcessError):
        remove(infos["dirty"])
    assert wt.exists() and (wt / "dirty-dirty.txt").exists()


# ---- the CLI ---------------------------------------------------------------------------------


def _bench_root(bench_root: Path, origin: Path, main: Path) -> None:
    (bench_root / "repos.toml").write_text(
        f'[[repos]]\nname = "ndb"\nurl = "{origin}"\nbranch = "main"\n'
        f'role = "producer"\npath = "{main}"\n'
    )


def test_cli_work_lists_every_worktree_with_its_reason_and_removes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin, main, bench_root, work = _workshop(tmp_path)
    _bench_root(bench_root, origin, main)
    gone = _worktree(main, work, "old-merged", branch="merged", when=REAL_OLD, merge=True,
                      push=True)
    keep_dirty = _worktree(main, work, "dirty", branch="dirty", when=REAL_OLD, merge=True,
                           push=True, dirty=True)
    keep_young = _worktree(main, work, "young", branch="young", when=REAL_YOUNG, merge=True,
                           push=True)
    keep_unpushed = _worktree(main, work, "unpushed", branch="unpushed", when=REAL_OLD)
    plain = work / "plain"
    plain.mkdir()
    monkeypatch.setenv("BENCH_ROOT", str(bench_root))

    result = runner.invoke(app, ["work"])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "old-merged" in out and "would remove" in out
    assert "untracked 1" in out
    assert "younger than 14d" in out
    assert "unpushed" in out
    assert "not a worktree" in out
    assert all(p.exists() for p in (gone, keep_dirty, keep_young, keep_unpushed, plain))


def test_cli_work_with_yes_removes_only_old_clean_merged_worktrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin, main, bench_root, work = _workshop(tmp_path)
    _bench_root(bench_root, origin, main)
    gone = _worktree(main, work, "old-merged", branch="merged", when=REAL_OLD, merge=True,
                      push=True)
    keep_dirty = _worktree(main, work, "dirty", branch="dirty", when=REAL_OLD, merge=True,
                           push=True, dirty=True)
    keep_young = _worktree(main, work, "young", branch="young", when=REAL_YOUNG, merge=True,
                           push=True)
    plain = work / "plain"
    plain.mkdir()
    monkeypatch.setenv("BENCH_ROOT", str(bench_root))

    result = runner.invoke(app, ["work", "--yes"])
    assert result.exit_code == 0, result.output
    assert "removed" in result.output
    assert not gone.exists()
    assert keep_dirty.exists() and keep_young.exists() and plain.exists()


def test_cli_work_refuses_a_duration_it_cannot_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin, main, bench_root, _work = _workshop(tmp_path)
    _bench_root(bench_root, origin, main)
    monkeypatch.setenv("BENCH_ROOT", str(bench_root))
    result = runner.invoke(app, ["work", "--older-than", "2w"])
    assert result.exit_code == 2, result.output
    assert "duration" in result.output


def test_cli_work_accepts_an_hours_ttl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin, main, bench_root, work = _workshop(tmp_path)
    _bench_root(bench_root, origin, main)
    _worktree(main, work, "fresh", branch="fresh", when=datetime.now(UTC), merge=True,
              push=True)
    monkeypatch.setenv("BENCH_ROOT", str(bench_root))
    result = runner.invoke(app, ["work", "--older-than", "1h"])
    assert result.exit_code == 0, result.output
    assert "younger than 1h" in result.output  # seconds old is younger than any hour TTL


def test_cli_work_on_an_empty_work_root_is_quiet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin, main, bench_root, _work = _workshop(tmp_path)
    _bench_root(bench_root, origin, main)
    monkeypatch.setenv("BENCH_ROOT", str(bench_root))
    result = runner.invoke(app, ["work"])
    assert result.exit_code == 0, result.output
    assert "no worktrees" in result.output
