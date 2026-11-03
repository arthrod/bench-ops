"""The producer ledger hook (issue #4): `bench hook ledger <files>` and the shared ungated `ledger` job in lefthook/common.yml.

The ledger of a producer repo (`release/index.jsonl`) may only grow: every line parses as a
`LedgerEntry`, the staged file starts with HEAD's byte for byte (no rewritten or removed
lines) and no tag appears twice. The check is the shared one (`check_ledger`); this file
proves it through the hook's own surface — the CLI and the lefthook config.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bench_ops.cli import app
from bench_ops.hooks import ledger_problems
from bench_ops.models import HfRef, LedgerEntry

runner = CliRunner()
CREATED = datetime(2026, 11, 3, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def _entry(tag: str, sha: str = "a" * 64) -> LedgerEntry:
    return LedgerEntry(
        tag=tag,
        created=CREATED,
        hf=HfRef(repo="arthrod/neurotic_docx_bench", revision="b" * 40),
        manifest_sha256=sha,
    )


def _line(tag: str, sha: str = "a" * 64) -> str:
    return _entry(tag, sha).to_line()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "core.hooksPath=/dev/null", *args],
        check=True,
        capture_output=True,
    )


@pytest.fixture
def producer(tmp_path: Path) -> Path:
    """A producer repo whose HEAD already carries a one-line release/index.jsonl."""
    repo = tmp_path / "ndb"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "release").mkdir()
    (repo / "release" / "index.jsonl").write_text(_line("ndb-v1.0.0"))
    _git(repo, "add", "release/index.jsonl")
    _git(repo, "commit", "-qm", "release: ndb-v1.0.0")
    return repo


def _stage(repo: Path, text: str) -> None:
    (repo / "release" / "index.jsonl").write_text(text)
    _git(repo, "add", "release/index.jsonl")


# ---- the check itself ---------------------------------------------------------------------


def test_a_pure_append_is_accepted(producer: Path) -> None:
    _stage(producer, _line("ndb-v1.0.0") + _line("ndb-v1.0.1", sha="b" * 64))
    assert ledger_problems(producer) == []
    assert ledger_problems(producer, [Path("release/index.jsonl")]) == []


def test_a_new_ledger_is_accepted(tmp_path: Path) -> None:
    """A ledger absent at HEAD is new: there is nothing it could have rewritten."""
    repo = tmp_path / "fresh"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "release").mkdir()
    (repo / "release" / "index.jsonl").write_text(_line("ndb-v1.0.0"))
    _git(repo, "add", "release/index.jsonl")
    _git(repo, "commit", "-qm", "first")
    assert ledger_problems(repo) == []


def test_a_line_that_does_not_parse_is_refused(producer: Path) -> None:
    _stage(producer, _line("ndb-v1.0.0") + "not a ledger entry\n")
    problems = ledger_problems(producer)
    assert problems
    assert any("not a ledger entry" in p for p in problems)


def test_a_rewritten_line_is_refused(producer: Path) -> None:
    _stage(producer, _line("ndb-v1.0.0", sha="c" * 64))
    problems = ledger_problems(producer)
    assert problems
    assert any("changed or removed" in p for p in problems)


def test_a_removed_line_is_refused(producer: Path) -> None:
    _stage(producer, _line("ndb-v1.0.0") + _line("ndb-v1.0.1", sha="b" * 64))
    _git(producer, "commit", "-qm", "release: ndb-v1.0.1")
    _stage(producer, _line("ndb-v1.0.0"))  # the second line is gone
    problems = ledger_problems(producer)
    assert problems
    assert any("changed or removed" in p for p in problems)


def test_a_duplicate_tag_is_refused(producer: Path) -> None:
    _stage(producer, _line("ndb-v1.0.0") + _line("ndb-v1.0.0", sha="b" * 64))
    problems = ledger_problems(producer)
    assert problems
    assert any("duplicate tag ndb-v1.0.0" in p for p in problems)


def test_a_staged_deletion_is_refused(producer: Path) -> None:
    _git(producer, "rm", "-q", "release/index.jsonl")
    problems = ledger_problems(producer)
    assert problems
    assert any("may only grow" in p for p in problems)


def test_a_staged_deletion_is_refused_even_with_the_glob_files(producer: Path) -> None:
    """lefthook's file lists exclude deletions, so the glob-scoped job never sees them.

    The hook still has to catch it: the staged blob is gone from the index."""
    _git(producer, "rm", "-q", "release/index.jsonl")
    problems = ledger_problems(producer, [Path("release/index.jsonl")])
    assert problems
    assert any("may only grow" in p for p in problems)


def test_an_untouched_ledger_is_not_complained_about(producer: Path) -> None:
    """The glob-scoped job passes the staged files; a commit that touches none of the ledgers
    has nothing to compare — but the ledger at HEAD must not be read as a rewrite."""
    (producer / "README.md").write_text("hello\n")
    _git(producer, "add", "README.md")
    assert ledger_problems(producer, [Path("README.md")]) == []


# ---- the CLI ------------------------------------------------------------------------------


def test_cli_exits_zero_for_an_append(producer: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stage(producer, _line("ndb-v1.0.0") + _line("ndb-v1.0.1", sha="b" * 64))
    monkeypatch.chdir(producer)
    assert runner.invoke(app, ["hook", "ledger"]).exit_code == 0
    assert runner.invoke(app, ["hook", "ledger", "release/index.jsonl"]).exit_code == 0


def test_cli_exits_zero_for_a_new_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "fresh"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "release").mkdir()
    (repo / "release" / "index.jsonl").write_text(_line("ndb-v1.0.0"))
    _git(repo, "add", "release/index.jsonl")
    monkeypatch.chdir(repo)
    assert runner.invoke(app, ["hook", "ledger"]).exit_code == 0
    assert runner.invoke(app, ["hook", "ledger", "release/index.jsonl"]).exit_code == 0


def test_cli_exits_one_for_a_rewrite(producer: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stage(producer, _line("ndb-v1.0.0", sha="c" * 64))
    monkeypatch.chdir(producer)
    assert runner.invoke(app, ["hook", "ledger"]).exit_code == 1
    assert runner.invoke(app, ["hook", "ledger", "release/index.jsonl"]).exit_code == 1


def test_cli_exits_one_for_a_removed_line(producer: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stage(producer, _line("ndb-v1.0.0") + _line("ndb-v1.0.1", sha="b" * 64))
    _git(producer, "commit", "-qm", "release: ndb-v1.0.1")
    _stage(producer, _line("ndb-v1.0.0"))
    monkeypatch.chdir(producer)
    assert runner.invoke(app, ["hook", "ledger"]).exit_code == 1
    assert runner.invoke(app, ["hook", "ledger", "release/index.jsonl"]).exit_code == 1


def test_cli_exits_one_for_a_line_that_does_not_parse(producer: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    _stage(producer, _line("ndb-v1.0.0") + "not a ledger entry\n")
    monkeypatch.chdir(producer)
    assert runner.invoke(app, ["hook", "ledger"]).exit_code == 1
    assert runner.invoke(app, ["hook", "ledger", "release/index.jsonl"]).exit_code == 1


def test_cli_exits_one_for_a_duplicate_tag(producer: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stage(producer, _line("ndb-v1.0.0") + _line("ndb-v1.0.0", sha="b" * 64))
    monkeypatch.chdir(producer)
    assert runner.invoke(app, ["hook", "ledger"]).exit_code == 1
    assert runner.invoke(app, ["hook", "ledger", "release/index.jsonl"]).exit_code == 1


def test_cli_exits_one_for_a_staged_deletion(producer: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _git(producer, "rm", "-q", "release/index.jsonl")
    monkeypatch.chdir(producer)
    assert runner.invoke(app, ["hook", "ledger"]).exit_code == 1
    assert runner.invoke(app, ["hook", "ledger", "release/index.jsonl"]).exit_code == 1


def test_cli_exits_zero_outside_a_git_repository(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """No ledger, no git: nothing to refuse. The hook must not break a plain directory."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["hook", "ledger"])
    assert result.exit_code == 0, result.output


# ---- the lefthook config ------------------------------------------------------------------


def test_the_shared_config_keeps_the_ungated_ledger_job() -> None:
    """lefthook glob lists exclude deletions, so the shared ledger job has no glob on purpose."""
    text = (ROOT / "lefthook" / "common.yml").read_text()
    assert "bench hook ledger" in text
    ledger_job = text.split("- name: ledger")[1].split("- name:")[0]
    assert "glob:" not in ledger_job
