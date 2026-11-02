"""The plan-2 CLI surface: bench hook ledger, bench publish --dry-run, bench gc, and the hook config."""

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bench_ops.cli import app
from bench_ops.gitx import clone_or_pull, git
from bench_ops.models import HfRef, LedgerEntry
from tests.test_gitx import make_origin

runner = CliRunner()
CREATED = datetime(2026, 11, 3, tzinfo=UTC)
HF_REPO = "arthrod/neurotic_docx_bench"


def _entry(tag: str, sha: str = "a" * 64) -> LedgerEntry:
    return LedgerEntry(
        tag=tag, created=CREATED, hf=HfRef(repo=HF_REPO, revision="b" * 40), manifest_sha256=sha
    )


def _sh(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "core.hooksPath=/dev/null", *args],
        check=True,
        capture_output=True,
    )


def _clone(tmp_path: Path, name: str = "ndb") -> Path:
    origin = make_origin(tmp_path)
    repo = tmp_path / name
    clone_or_pull(str(origin), repo, "main")
    return repo


def _config(tmp_path: Path, repo: Path, release: str = "") -> Path:
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    (root / "repos.toml").write_text(
        f'[[repos]]\nname = "ndb"\nurl = "x"\nbranch = "main"\nrole = "producer"\n'
        f'path = "{repo}"\n{release}'
    )
    return root


@pytest.fixture
def producer(tmp_path: Path) -> Path:
    """A clone whose HEAD already carries a one-line release/index.jsonl."""
    repo = _clone(tmp_path)
    (repo / "release").mkdir()
    (repo / "release" / "index.jsonl").write_text(_entry("ndb-v1.0.0").to_line())
    _sh(repo, "add", "release/index.jsonl")
    _sh(repo, "commit", "-qm", "release: ndb-v1.0.0")
    return repo


# ---- bench hook ledger -------------------------------------------------------------------


def test_hook_ledger_allows_a_pure_append(producer: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    (producer / "release" / "index.jsonl").write_text(
        _entry("ndb-v1.0.0").to_line() + _entry("ndb-v1.0.1", sha="b" * 64).to_line()
    )
    _sh(producer, "add", "release/index.jsonl")
    monkeypatch.chdir(producer)
    result = runner.invoke(app, ["hook", "ledger"])
    assert result.exit_code == 0, result.output


def test_hook_ledger_refuses_a_staged_rewrite(producer: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    (producer / "release" / "index.jsonl").write_text(_entry("ndb-v1.0.0", sha="c" * 64).to_line())
    _sh(producer, "add", "release/index.jsonl")
    monkeypatch.chdir(producer)
    result = runner.invoke(app, ["hook", "ledger"])
    assert result.exit_code == 1, result.output


def test_hook_ledger_refuses_a_staged_deletion(producer: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    _sh(producer, "rm", "-q", "release/index.jsonl")
    monkeypatch.chdir(producer)
    result = runner.invoke(app, ["hook", "ledger"])
    assert result.exit_code == 1, result.output


def test_lefthook_common_configures_the_ledger_hook() -> None:
    """The hook must run in every repo: it lives in the shared lefthook config (D14)."""
    text = (Path(__file__).resolve().parent.parent / "lefthook" / "common.yml").read_text()
    assert "bench hook ledger" in text
    assert "release/index.jsonl" in text


# ---- bench publish -----------------------------------------------------------------------


def test_cli_publish_dry_run_prints_a_manifest(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _clone(tmp_path)
    (repo / "out").mkdir()
    (repo / "out" / "a.bin").write_bytes(b"aaa")
    _sh(repo, "add", "out")
    _sh(repo, "commit", "-qm", "out")
    release = (
        f'\n[repos.release]\nhf_repo = "{HF_REPO}"\nfiles = ["out/**"]\n'
    )
    root = _config(tmp_path, repo, release)
    monkeypatch.setenv("BENCH_ROOT", str(root))
    result = runner.invoke(app, ["publish", "ndb", "ndb-v0.0.1", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "ndb-v0.0.1" in result.output
    assert "out/a.bin" in result.output
    assert not (repo / "release").exists()  # nothing written to the producer
    assert git(repo, "tag").strip() == ""


# ---- bench gc ----------------------------------------------------------------------------


def test_cli_gc_without_yes_deletes_nothing(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _clone(tmp_path)
    target = repo / "out" / "x.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"xxx")
    root = _config(tmp_path, repo)
    monkeypatch.setenv("BENCH_ROOT", str(root))
    result = runner.invoke(app, ["gc", str(target)])
    assert result.exit_code == 0, result.output
    assert target.exists()
