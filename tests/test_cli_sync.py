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
