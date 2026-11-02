from pathlib import Path

import pytest
from typer.testing import CliRunner

from bench_ops.cli import app
from tests.test_gitx import make_origin

runner = CliRunner()


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    origin = make_origin(tmp_path)
    (tmp_path / "repos.toml").write_text(
        f'[[repos]]\nname = "ndb"\nurl = "{origin}"\nbranch = "main"\nrole = "producer"\n'
    )
    monkeypatch.setenv("BENCH_ROOT", str(tmp_path))
    monkeypatch.setenv("BENCH_NO_LEFTHOOK", "1")  # tests never shell out to lefthook
    return tmp_path


def test_sync_clones_and_creates_dirs(root: Path) -> None:
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output
    assert (root / "ndb" / "a.txt").exists()
    assert (root / ".work").is_dir() and (root / ".logs").is_dir()
    assert "ndb" in result.output and "cloned" in result.output


def test_sync_twice_pulls(root: Path) -> None:
    runner.invoke(app, ["sync"])
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "pulled" in result.output


def test_sync_only_unknown_repo(root: Path) -> None:
    result = runner.invoke(app, ["sync", "--only", "nope"])
    assert result.exit_code == 1
    assert "nope" in result.output


def test_missing_repos_toml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BENCH_ROOT", str(tmp_path))
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 2
    assert "repos.toml" in result.output


def _fake_lefthook(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dump: str) -> None:
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    exe = bin_dir / "lefthook"
    exe.write_text(f"#!/bin/sh\nif [ \"$1\" = dump ]; then printf '%s\\n' '{dump}'; fi\nexit 0\n")
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")
    monkeypatch.delenv("BENCH_NO_LEFTHOOK", raising=False)


def test_install_hooks_confirms_shared_jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bench_ops.cli import _install_hooks

    _fake_lefthook(tmp_path, monkeypatch, "pre-commit:\n  jobs:\n    - name: size guard")
    (tmp_path / "lefthook.yml").write_text("remotes: []\n")
    assert _install_hooks(tmp_path) == "hooks installed"


def test_install_hooks_detects_unfetched_remote(tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """lefthook continues when the remote can't be fetched and installs no jobs: fail loudly."""
    from bench_ops.cli import _install_hooks

    _fake_lefthook(tmp_path, monkeypatch, "remotes:\n  - git_url: x")
    (tmp_path / "lefthook.yml").write_text("remotes: []\n")
    assert _install_hooks(tmp_path).startswith("HOOKS NOT ACTIVE")


def test_sync_exits_1_when_hooks_inactive(root: Path, tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    runner.invoke(app, ["sync"])  # clone first (BENCH_NO_LEFTHOOK=1)
    (root / "ndb" / "lefthook.yml").write_text("remotes: []\n")
    _fake_lefthook(tmp_path, monkeypatch, "remotes:\n  - git_url: x")
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 1, result.output
    assert "HOOKS NOT ACTIVE" in result.output
