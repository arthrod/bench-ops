"""Plan 8: publish and deploy from a clean tree; every repo command runnable from bench-ops.

`bench publish ndb ndb-v0.0.4` failed on 2026-11-05 because publish ran in the shared ndb
checkout, which sat on an old feature branch. These tests pin the fix: the release is built
in a fresh worktree of origin/<branch>, the bundle version is an argument, the pages must be
the committed ones, and consumers deploy only from an up-to-date, clean configured branch.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bench_ops import config
from bench_ops.checkout import (
    CheckoutError,
    checkout_problems,
    release_tree,
    scratch_tree,
)
from bench_ops.gitx import git
from bench_ops.publish import PublishError, publish, push_release

from .test_publish import FakeHub

CREATED = datetime(2026, 11, 5, tzinfo=UTC)
TAG = "ndb-v0.0.4"
HF_REPO = "arthrod/neurotic_docx_bench"


def _sh(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "core.hooksPath=/dev/null", *args],
        check=True, capture_output=True, text=True,
    ).stdout


def _origin_and_checkout(tmp_path: Path) -> tuple[Path, Path]:
    """A bare origin whose main holds out/a.bin = b"main", and a checkout of it that has
    moved to a dirty feature branch where out/a.bin = b"feature" (the 2026-11-05 state)."""
    seed = tmp_path / "seed"
    seed.mkdir()
    subprocess.run(["git", "init", "-b", "main", "-q"], cwd=seed, check=True)
    (seed / "out").mkdir()
    (seed / "out" / "a.bin").write_bytes(b"main")
    _sh(seed, "add", "-A")
    _sh(seed, "commit", "-qm", "init")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(origin)], check=True)
    checkout = tmp_path / "ndb"
    subprocess.run(["git", "clone", "-q", str(origin), str(checkout)], check=True)
    _sh(checkout, "checkout", "-qb", "results/old-feature")
    (checkout / "out" / "a.bin").write_bytes(b"feature")
    _sh(checkout, "commit", "-qam", "feature")
    (checkout / "out" / "a.bin").write_bytes(b"dirty")
    return origin, checkout


def _repo(checkout: Path, root: Path, before: tuple[str, ...] = ()) -> config.Repo:
    repo = config.Repo(
        name="ndb", url=str(checkout), branch="main", role="producer", path=checkout,
        release=config.ReleaseSpec(hf_repo=HF_REPO, files=["out/**"], before=list(before),
                                   private=True),
    )
    repo._root = root
    return repo


# ---- P1: the release tree --------------------------------------------------------------


def test_release_tree_is_a_fresh_worktree_of_origin_main_whatever_the_checkout_is_on(
    tmp_path: Path,
) -> None:
    _, checkout = _origin_and_checkout(tmp_path)
    root = tmp_path / "bench"
    tree = release_tree(_repo(checkout, root), TAG)

    assert tree == root / ".work" / f"release-{TAG}"
    assert (tree / "out" / "a.bin").read_bytes() == b"main"
    assert git(tree, "rev-parse", "--abbrev-ref", "HEAD").strip() == f"release/{TAG}"
    assert git(tree, "rev-parse", "HEAD") == git(checkout, "rev-parse", "origin/main")
    # the shared checkout is untouched: same branch, same dirt
    assert git(checkout, "rev-parse", "--abbrev-ref", "HEAD").strip() == "results/old-feature"
    assert (checkout / "out" / "a.bin").read_bytes() == b"dirty"


def test_release_tree_fetches_origin_first(tmp_path: Path) -> None:
    origin, checkout = _origin_and_checkout(tmp_path)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    (other / "out" / "a.bin").write_bytes(b"newer main")
    _sh(other, "commit", "-qam", "newer")
    _sh(other, "push", "-q", "origin", "main")

    tree = release_tree(_repo(checkout, tmp_path / "bench"), TAG)
    assert (tree / "out" / "a.bin").read_bytes() == b"newer main"


def test_release_tree_is_reused_on_resume(tmp_path: Path) -> None:
    _, checkout = _origin_and_checkout(tmp_path)
    repo = _repo(checkout, tmp_path / "bench")
    tree = release_tree(repo, TAG)
    (tree / "release").mkdir()
    (tree / "release" / "index.jsonl").write_text("resume marker\n")
    assert release_tree(repo, TAG) == tree
    assert (tree / "release" / "index.jsonl").read_text() == "resume marker\n"


def test_release_tree_refuses_a_path_that_is_not_its_release_branch(tmp_path: Path) -> None:
    _, checkout = _origin_and_checkout(tmp_path)
    root = tmp_path / "bench"
    squatter = root / ".work" / f"release-{TAG}"
    squatter.mkdir(parents=True)
    (squatter / "x").write_text("someone else's\n")
    with pytest.raises(CheckoutError, match=f"release/{TAG}"):
        release_tree(_repo(checkout, root), TAG)


def test_publish_through_the_release_tree_stages_main_and_commits_on_the_release_branch(
    tmp_path: Path,
) -> None:
    _, checkout = _origin_and_checkout(tmp_path)
    repo = _repo(checkout, tmp_path / "bench")
    tree = release_tree(repo, TAG)
    hub = FakeHub(tmp_path)
    m = publish(repo.model_copy(update={"path": tree}), TAG, hub, now=CREATED, dry_run=False)

    assert [f.path for f in m.files] == [f"release/{TAG}/out/a.bin"]
    staged = hub.commits[hub.oids[0]][f"release/{TAG}/out/a.bin"]
    assert staged == b"main"
    assert git(tree, "log", "-1", "--format=%s").strip() == f"release: {TAG}"
    assert git(tree, "tag").split() == [TAG]
    assert not (checkout / "release").exists()


# ---- P2: {version} in before ----------------------------------------------------------


def test_before_commands_get_the_version(tmp_path: Path) -> None:
    _, checkout = _origin_and_checkout(tmp_path)
    repo = _repo(checkout, tmp_path / "bench",
                 before=("views --release-version {version}", "views --check"))
    tree = release_tree(repo, TAG)
    ran: list[str] = []

    def run(command: str, cwd: Path) -> int:
        ran.append(command)
        return 0

    publish(repo.model_copy(update={"path": tree}), TAG, FakeHub(tmp_path), now=CREATED,
            dry_run=False, run=run, version="0.17.0")
    assert ran == ["views --release-version 0.17.0", "views --check"]


@pytest.mark.parametrize("dry_run", [True, False])
def test_a_before_command_naming_version_needs_one(tmp_path: Path, dry_run: bool) -> None:
    _, checkout = _origin_and_checkout(tmp_path)
    repo = _repo(checkout, tmp_path / "bench", before=("views --release-version {version}",))
    tree = release_tree(repo, TAG)
    ran: list[str] = []
    hub = FakeHub(tmp_path)
    with pytest.raises(PublishError, match="--version"):
        publish(repo.model_copy(update={"path": tree}), TAG, hub, now=CREATED,
                dry_run=dry_run, run=lambda c, _: ran.append(c) or 0)
    assert ran == [] and hub.calls == []


def test_a_malformed_version_is_refused(tmp_path: Path) -> None:
    _, checkout = _origin_and_checkout(tmp_path)
    repo = _repo(checkout, tmp_path / "bench", before=("views --release-version {version}",))
    tree = release_tree(repo, TAG)
    with pytest.raises(PublishError, match="X.Y.Z"):
        publish(repo.model_copy(update={"path": tree}), TAG, FakeHub(tmp_path), now=CREATED,
                dry_run=False, run=lambda c, _: 0, version="0.17.0; rm -rf /")


def test_config_refuses_an_unknown_placeholder_in_before() -> None:
    with pytest.raises(ValueError, match=r"\{tag\}"):
        config.ReleaseSpec(hf_repo=HF_REPO, files=["x"], before=["views {tag}"])


# ---- P3: the bundle is built from committed pages --------------------------------------


def test_publish_refuses_when_before_changed_a_tracked_file(tmp_path: Path) -> None:
    _, checkout = _origin_and_checkout(tmp_path)
    repo = _repo(checkout, tmp_path / "bench", before=("regenerate pages",))
    tree = release_tree(repo, TAG)
    hub = FakeHub(tmp_path)

    def run(command: str, cwd: Path) -> int:
        (cwd / "out" / "a.bin").write_bytes(b"regenerated differently")
        return 0

    with pytest.raises(PublishError, match="out/a.bin"):
        publish(repo.model_copy(update={"path": tree}), TAG, hub, now=CREATED,
                dry_run=False, run=run)
    assert not any(c.startswith("commit:") for c in hub.calls)


# ---- P4: push ----------------------------------------------------------------------------


def test_push_release_pushes_the_branch_and_tag_then_opens_the_pr(tmp_path: Path) -> None:
    calls: list[tuple[str, Path]] = []
    push_release(tmp_path, TAG, "main", run=lambda c, cwd: calls.append((c, cwd)) or 0)
    commands = [c for c, _ in calls]
    assert commands[0] == f"git push -u origin release/{TAG}"
    assert commands[1] == f"git push origin {TAG}"
    assert commands[2].startswith(f"gh pr create --base main --head release/{TAG}")
    assert all(cwd == tmp_path for _, cwd in calls)


def test_push_release_stops_at_a_failed_push(tmp_path: Path) -> None:
    calls: list[str] = []

    def run(command: str, _: Path) -> int:
        calls.append(command)
        return 1

    with pytest.raises(PublishError, match="git push"):
        push_release(tmp_path, TAG, "main", run=run)
    assert len(calls) == 1


def test_push_release_accepts_an_existing_pr(tmp_path: Path) -> None:
    """A resumed publish finds the PR already open: `gh pr create` fails, `gh pr view` works."""

    def run(command: str, _: Path) -> int:
        return 1 if command.startswith("gh pr create") else 0

    push_release(tmp_path, TAG, "main", run=run)  # no raise


# ---- P5: exec from anywhere ----------------------------------------------------------------


def test_scratch_tree_is_a_detached_origin_worktree_removed_when_untouched(tmp_path: Path) -> None:
    _, checkout = _origin_and_checkout(tmp_path)
    repo = _repo(checkout, tmp_path / "bench")
    with scratch_tree(repo) as tree:
        assert (tree / "out" / "a.bin").read_bytes() == b"main"
        assert git(tree, "rev-parse", "--abbrev-ref", "HEAD").strip() == "HEAD"
    assert not tree.exists()


def test_scratch_tree_is_kept_when_a_command_changed_it(tmp_path: Path) -> None:
    _, checkout = _origin_and_checkout(tmp_path)
    repo = _repo(checkout, tmp_path / "bench")
    with scratch_tree(repo) as tree:
        (tree / "new.txt").write_text("output\n")
    assert (tree / "new.txt").exists()


# ---- P6: consumer checkouts ------------------------------------------------------------------


def test_checkout_problems_names_branch_dirt_and_lag(tmp_path: Path) -> None:
    origin, checkout = _origin_and_checkout(tmp_path)
    problems = checkout_problems(checkout, "main")
    assert any("results/old-feature" in p for p in problems)
    assert any("out/a.bin" in p for p in problems)

    clean = tmp_path / "clean"
    subprocess.run(["git", "clone", "-q", str(origin), str(clean)], check=True)
    assert checkout_problems(clean, "main") == []

    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(clean.parent / "other")], check=True)
    (other / "out" / "a.bin").write_bytes(b"ahead")
    _sh(other, "commit", "-qam", "ahead")
    _sh(other, "push", "-q", "origin", "main")
    assert any("behind origin/main" in p for p in checkout_problems(clean, "main"))


# ---- the CLI ---------------------------------------------------------------------------------


def _bench_root(tmp_path: Path, checkout: Path) -> Path:
    root = tmp_path / "bench"
    root.mkdir(exist_ok=True)
    (root / "repos.toml").write_text(
        f'[[repos]]\nname = "ndb"\nurl = "{checkout}"\nbranch = "main"\nrole = "producer"\n'
        f'path = "{checkout}"\n'
        f'[repos.release]\nhf_repo = "{HF_REPO}"\nprivate = false\nfiles = ["out/**"]\n'
    )
    return root


def test_cli_exec_runs_a_command_in_the_repo_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bench_ops.cli import app

    _, checkout = _origin_and_checkout(tmp_path)
    monkeypatch.setenv("BENCH_ROOT", str(_bench_root(tmp_path, checkout)))
    result = CliRunner().invoke(app, ["exec", "ndb", "--", "cat", "out/a.bin"])
    assert result.exit_code == 0, result.output


def test_cli_exec_main_runs_in_a_fresh_origin_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bench_ops.cli import app

    _, checkout = _origin_and_checkout(tmp_path)
    monkeypatch.setenv("BENCH_ROOT", str(_bench_root(tmp_path, checkout)))
    out = tmp_path / "seen.txt"
    result = CliRunner().invoke(app, ["exec", "ndb", "--main", "--", "sh", "-c",
                                      f"cat out/a.bin > {out}"])
    assert result.exit_code == 0, result.output
    assert out.read_bytes() == b"main"


def test_cli_exec_propagates_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bench_ops.cli import app

    _, checkout = _origin_and_checkout(tmp_path)
    monkeypatch.setenv("BENCH_ROOT", str(_bench_root(tmp_path, checkout)))
    result = CliRunner().invoke(app, ["exec", "ndb", "--", "sh", "-c", "exit 7"])
    assert result.exit_code == 7


def test_cli_exec_refuses_an_unknown_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bench_ops.cli import app

    _, checkout = _origin_and_checkout(tmp_path)
    monkeypatch.setenv("BENCH_ROOT", str(_bench_root(tmp_path, checkout)))
    result = CliRunner().invoke(app, ["exec", "nope", "--", "true"])
    assert result.exit_code == 1
    assert "unknown repo" in result.output


def test_workspace_publish_steps_use_the_version_and_push() -> None:
    cfg = config.load_config(Path(__file__).resolve().parents[1])
    ndb = cfg.by_name("ndb")
    assert ndb.release is not None
    assert any("{version}" in c for c in ndb.release.before)
    assert ndb.release.before[-1] == "uv run bench results upload"
    step = next(s for s in cfg.pipelines["jubarte"].steps if s.name == "publish-ndb")
    assert step.run == "bench publish ndb {tag} --version {version} --push"
    blaw = cfg.by_name("benchmarks-law")
    assert blaw.path is None  # the dead override is gone; bench sync clones it under BENCH_ROOT


# ---- review round 1 (#31) ----------------------------------------------------------------


def test_a_resumed_publish_with_the_ledger_left_dirty_is_not_refused(tmp_path: Path) -> None:
    """CodeRabbit/Cursor/CodeAnt: a run that appended the ledger and died leaves a tracked
    change of its own; the guard compares before/after the commands, not against HEAD."""
    _, checkout = _origin_and_checkout(tmp_path)
    repo = _repo(checkout, tmp_path / "bench", before=("noop",))
    tree = release_tree(repo, TAG)
    (tree / "release").mkdir()
    (tree / "release" / "index.jsonl").write_text("")
    _sh(tree, "add", "release/index.jsonl")
    _sh(tree, "commit", "-qm", "track the ledger")
    (tree / "release" / "index.jsonl").write_text("left by a run that died\n")
    with pytest.raises(PublishError) as e:
        publish(repo.model_copy(update={"path": tree}), TAG, FakeHub(tmp_path), now=CREATED,
                dry_run=False, run=lambda c, _: 0)
    assert "before commands changed" not in str(e.value)  # refused later, for its own reason


def test_the_guard_runs_after_each_command_so_the_upload_never_follows_a_change(
    tmp_path: Path,
) -> None:
    _, checkout = _origin_and_checkout(tmp_path)
    repo = _repo(checkout, tmp_path / "bench", before=("regenerate", "upload"))
    tree = release_tree(repo, TAG)
    ran: list[str] = []

    def run(command: str, cwd: Path) -> int:
        ran.append(command)
        if command == "regenerate":
            (cwd / "out" / "a.bin").write_bytes(b"moved")
        return 0

    with pytest.raises(PublishError, match="out/a.bin"):
        publish(repo.model_copy(update={"path": tree}), TAG, FakeHub(tmp_path), now=CREATED,
                dry_run=False, run=run)
    assert ran == ["regenerate"]


def test_the_guard_fails_closed_when_git_status_fails(tmp_path: Path) -> None:
    _, checkout = _origin_and_checkout(tmp_path)
    repo = _repo(checkout, tmp_path / "bench", before=("break git",))
    tree = release_tree(repo, TAG)

    def run(command: str, cwd: Path) -> int:
        (cwd / ".git").unlink()  # a worktree's .git file: git status fails afterwards
        return 0

    with pytest.raises(PublishError):
        publish(repo.model_copy(update={"path": tree}), TAG, FakeHub(tmp_path), now=CREATED,
                dry_run=False, run=run)


def test_push_release_refuses_when_only_a_closed_pr_exists(tmp_path: Path) -> None:
    def run(command: str, _: Path) -> int:
        if command.startswith("gh pr create"):
            return 1
        if command.startswith("gh pr view"):
            return 1  # the state query finds no OPEN/MERGED PR
        return 0

    with pytest.raises(PublishError, match="no PR"):
        push_release(tmp_path, TAG, "main", run=run)


def test_push_release_checks_the_pr_state() -> None:
    calls: list[str] = []
    push_release(Path("."), TAG, "main",
                 run=lambda c, _: calls.append(c) or (1 if c.startswith("gh pr create") else 0))
    view = next(c for c in calls if c.startswith("gh pr view"))
    assert "state" in view and "OPEN" in view and "MERGED" in view


def test_config_accepts_shell_expansions_in_before() -> None:
    spec = config.ReleaseSpec(
        hf_repo=HF_REPO, files=["x"],
        before=["echo ${HOME}", "echo ${VAR:-x}", "awk '{print $1}' f", "cp {a,b}.txt d"],
    )
    assert len(spec.before) == 4


def test_a_release_branch_left_without_commits_moves_to_fresh_origin(tmp_path: Path) -> None:
    origin, checkout = _origin_and_checkout(tmp_path)
    repo = _repo(checkout, tmp_path / "bench")
    tree = release_tree(repo, TAG)
    old = git(tree, "rev-parse", "HEAD")
    _sh(checkout, "worktree", "remove", "--force", str(tree))
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    (other / "out" / "a.bin").write_bytes(b"newer")
    _sh(other, "commit", "-qam", "newer")
    _sh(other, "push", "-q", "origin", "main")

    tree = release_tree(repo, TAG)
    assert git(tree, "rev-parse", "HEAD") != old
    assert (tree / "out" / "a.bin").read_bytes() == b"newer"


def test_a_release_branch_with_its_own_commits_is_kept(tmp_path: Path) -> None:
    _, checkout = _origin_and_checkout(tmp_path)
    repo = _repo(checkout, tmp_path / "bench")
    tree = release_tree(repo, TAG)
    (tree / "release.txt").write_text("record\n")
    _sh(tree, "add", "release.txt")
    _sh(tree, "commit", "-qm", "release record")
    mine = git(tree, "rev-parse", "HEAD")
    _sh(checkout, "worktree", "remove", "--force", str(tree))
    tree = release_tree(repo, TAG)
    assert git(tree, "rev-parse", "HEAD") == mine


def test_release_tree_refuses_a_worktree_on_another_branch(tmp_path: Path) -> None:
    _, checkout = _origin_and_checkout(tmp_path)
    root = tmp_path / "bench"
    path = root / ".work" / f"release-{TAG}"
    path.parent.mkdir(parents=True)
    _sh(checkout, "worktree", "add", "-q", "-b", "elsewhere", str(path), "origin/main")
    with pytest.raises(CheckoutError, match=f"release/{TAG}"):
        release_tree(_repo(checkout, root), TAG)


def test_release_tree_refuses_a_worktree_of_another_repository(tmp_path: Path) -> None:
    _, checkout = _origin_and_checkout(tmp_path)
    (tmp_path / "foreign").mkdir()
    _, foreign = _origin_and_checkout(tmp_path / "foreign")
    root = tmp_path / "bench"
    path = root / ".work" / f"release-{TAG}"
    path.parent.mkdir(parents=True)
    _sh(foreign, "worktree", "add", "-q", "-b", f"release/{TAG}", str(path), "origin/main")
    with pytest.raises(CheckoutError, match="another repository"):
        release_tree(_repo(checkout, root), TAG)


def test_bump_sees_a_release_ledger_that_is_only_on_its_release_branch(tmp_path: Path) -> None:
    """Gitar/Cursor: after `publish --push` the new line is on release/<tag> until the PR
    merges; the pipeline's next step (bump) must pin it, not the previous tag."""
    from bench_ops.fetch import _newest_ledger

    _, checkout = _origin_and_checkout(tmp_path)
    repo = _repo(checkout, tmp_path / "bench")
    tree = release_tree(repo, TAG)
    (tree / "release").mkdir()
    (tree / "release" / "index.jsonl").write_text("line one\n")
    _sh(tree, "add", "release/index.jsonl")
    _sh(tree, "commit", "-qm", f"release: {TAG}")
    text = _newest_ledger(checkout, "main", checkout / "release" / "index.jsonl")
    assert text == "line one\n"


def test_checkout_problems_may_allow_the_bumped_lock(tmp_path: Path) -> None:
    """Gitar: the pipeline bumps bench.lock and deploys; that one tracked change is what the
    deploy verifies (.bench/ against the lock), so it may be allowed — nothing else."""
    origin, _ = _origin_and_checkout(tmp_path)
    clean = tmp_path / "clean"
    subprocess.run(["git", "clone", "-q", str(origin), str(clean)], check=True)
    (clean / "bench.lock").write_text("ndb=ndb-v0.0.1 manifest=" + "a" * 64 + "\n")
    _sh(clean, "add", "bench.lock")
    _sh(clean, "commit", "-qm", "lock")
    _sh(clean, "push", "-q", "origin", "HEAD:main")
    (clean / "bench.lock").write_text("ndb=ndb-v0.0.2 manifest=" + "b" * 64 + "\n")
    assert checkout_problems(clean, "main", allow=("bench.lock",)) == []
    assert checkout_problems(clean, "main") != []
    (clean / "out" / "a.bin").write_bytes(b"other")
    assert checkout_problems(clean, "main", allow=("bench.lock",)) != []


def test_a_resumed_release_tree_without_commits_follows_origin(tmp_path: Path) -> None:
    """codex: a worktree left by an aborted run (no release commit yet) must not publish the
    pages origin had then; it is fast-forwarded to the fresh origin/<branch>."""
    origin, checkout = _origin_and_checkout(tmp_path)
    repo = _repo(checkout, tmp_path / "bench")
    tree = release_tree(repo, TAG)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True)
    (other / "out" / "a.bin").write_bytes(b"corrected")
    _sh(other, "commit", "-qam", "corrected")
    _sh(other, "push", "-q", "origin", "main")
    assert release_tree(repo, TAG) == tree
    assert (tree / "out" / "a.bin").read_bytes() == b"corrected"
