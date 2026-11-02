import subprocess
from pathlib import Path

from bench_ops.gitx import GitStatus, clone_or_pull, status, worktrees


def make_origin(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", "-q"], cwd=src, check=True)
    (src / "a.txt").write_text("a\n")
    subprocess.run(["git", "add", "a.txt"], cwd=src, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "core.hooksPath=/dev/null",
         "commit", "-qm", "init"],
        cwd=src, check=True,
    )
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], check=True)
    return bare


def test_clone_then_pull(tmp_path: Path) -> None:
    origin = make_origin(tmp_path)
    dest = tmp_path / "dest"
    assert clone_or_pull(str(origin), dest, "main") == "cloned"
    assert (dest / "a.txt").exists()
    assert clone_or_pull(str(origin), dest, "main") == "pulled"


def test_status_clean_and_dirty(tmp_path: Path) -> None:
    origin = make_origin(tmp_path)
    dest = tmp_path / "dest"
    clone_or_pull(str(origin), dest, "main")
    s: GitStatus = status(dest)
    assert s.branch == "main" and s.dirty == 0
    (dest / "b.txt").write_text("b\n")
    assert status(dest).dirty == 1


def test_worktrees_lists_extra(tmp_path: Path) -> None:
    origin = make_origin(tmp_path)
    dest = tmp_path / "dest"
    clone_or_pull(str(origin), dest, "main")
    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "side", str(wt)], cwd=dest, check=True)
    assert [p.name for p in worktrees(dest)] == ["wt"]


def test_status_not_a_repo(tmp_path: Path) -> None:
    assert status(tmp_path / "missing").branch == "(missing)"


def test_pull_never_switches_a_feature_branch(tmp_path: Path) -> None:
    origin = make_origin(tmp_path)
    dest = tmp_path / "dest"
    clone_or_pull(str(origin), dest, "main")
    subprocess.run(["git", "switch", "-q", "-c", "feature"], cwd=dest, check=True)
    assert clone_or_pull(str(origin), dest, "main") == "fetched (on feature)"
    assert status(dest).branch == "feature"


def test_pull_never_touches_a_dirty_tree(tmp_path: Path) -> None:
    origin = make_origin(tmp_path)
    dest = tmp_path / "dest"
    clone_or_pull(str(origin), dest, "main")
    (dest / "a.txt").write_text("local edit\n")
    assert clone_or_pull(str(origin), dest, "main") == "fetched (dirty)"
    assert (dest / "a.txt").read_text() == "local edit\n"


def test_diverged_main_is_reported_not_raised(tmp_path: Path) -> None:
    origin = make_origin(tmp_path)
    dest = tmp_path / "dest"
    clone_or_pull(str(origin), dest, "main")
    other = tmp_path / "other"
    clone_or_pull(str(origin), other, "main")
    for repo, name in ((other, "remote.txt"), (dest, "local.txt")):
        (repo / name).write_text("x\n")
        subprocess.run(["git", "add", name], cwd=repo, check=True)
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "core.hooksPath=/dev/null",
             "commit", "-qm", name], cwd=repo, check=True,
        )
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=other, check=True)
    assert clone_or_pull(str(origin), dest, "main") == "fetched (diverged)"


def test_git_ignores_hook_environment(tmp_path: Path, monkeypatch) -> None:
    """Inside a git hook GIT_DIR/GIT_INDEX_FILE point at the hooked repo, not the one we ask about."""
    origin = make_origin(tmp_path)
    dest = tmp_path / "dest"
    clone_or_pull(str(origin), dest, "main")
    (dest / "b.txt").write_text("b\n")
    other = tmp_path / "other"
    clone_or_pull(str(origin), other, "main")
    monkeypatch.setenv("GIT_DIR", str(other / ".git"))
    monkeypatch.setenv("GIT_INDEX_FILE", str(other / ".git" / "index"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    assert status(dest).dirty == 1
