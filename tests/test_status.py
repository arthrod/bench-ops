from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bench_ops.cli import app
from bench_ops.config import Repo
from bench_ops.gitx import clone_or_pull
from bench_ops.hf import Witness
from bench_ops.models import HfRef, LedgerEntry, Lock
from bench_ops.status import RepoReport, latest_tags, report
from tests.test_gitx import make_origin

SHA = "a" * 64


def _repo(tmp_path: Path, name: str, role: str) -> Repo:
    origin = make_origin(tmp_path / name)
    dest = tmp_path / "root" / name
    clone_or_pull(str(origin), dest, "main")
    return Repo(name=name, url=str(origin), branch="main", role=role, path=dest)


def _ledger(repo: Repo, *tags: str) -> None:
    (repo.local_path / "release").mkdir(exist_ok=True)
    lines = [
        LedgerEntry(tag=t, created=datetime(2026, 11, 2, tzinfo=UTC),
                    hf=HfRef(repo="arthrod/neurotic_docx_bench", revision="b" * 40),
                    manifest_sha256=SHA).to_line()
        for t in tags
    ]
    (repo.local_path / "release" / "index.jsonl").write_text("".join(lines))


def test_producer_without_ledger(tmp_path: Path) -> None:
    r = _repo(tmp_path, "ndb", "producer")
    rep: RepoReport = report(r, latest={}, witness=lambda e: Witness())
    assert rep.branch == "main" and rep.release == "no releases yet"


def test_producer_with_ledger_is_verified(tmp_path: Path) -> None:
    r = _repo(tmp_path, "ndb", "producer")
    _ledger(r, "ndb-v1.0.0")
    seen: list[str] = []

    def witness(entry: LedgerEntry) -> Witness:
        seen.append(entry.tag)
        return Witness(missing=["site/x.pdf"], checked=2)

    rep = report(r, latest={}, witness=witness)
    assert seen == ["ndb-v1.0.0"]
    assert rep.release == "ndb-v1.0.0" and rep.witness == "1 missing"


def test_latest_tags_reads_last_ledger_line(tmp_path: Path) -> None:
    r = _repo(tmp_path, "ndb", "producer")
    _ledger(r, "ndb-v1.0.0", "ndb-v1.0.1")
    c = _repo(tmp_path, "jubarte-app", "consumer")
    assert latest_tags([r, c]) == {"ndb": "ndb-v1.0.1"}


def test_consumer_lock_vs_latest(tmp_path: Path) -> None:
    r = _repo(tmp_path, "jubarte-app", "consumer")
    (r.local_path / "bench.lock").write_text(Lock(tags={"ndb": "ndb-v1.0.0"}).render())
    rep = report(r, latest={"ndb": "ndb-v1.0.1"}, witness=lambda e: Witness())
    assert rep.release == "ndb=ndb-v1.0.0" and rep.witness == "stale: ndb latest is ndb-v1.0.1"
    assert report(r, latest={"ndb": "ndb-v1.0.0"}, witness=lambda e: Witness()).witness == (
        "up to date"
    )


def test_consumer_without_lock(tmp_path: Path) -> None:
    r = _repo(tmp_path, "benchmarks-law", "consumer")
    rep = report(r, latest={}, witness=lambda e: Witness())
    assert rep.release == "no bench.lock"


def _write_toml(root: Path, *repos: tuple[str, str]) -> None:
    root.joinpath("repos.toml").write_text("".join(
        f'[[repos]]\nname = "{n}"\nurl = "x"\nbranch = "main"\nrole = "{role}"\n'
        for n, role in repos
    ))


def test_cli_status_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _repo(tmp_path, "ndb", "producer")
    _write_toml(tmp_path / "root", ("ndb", "producer"))
    monkeypatch.setenv("BENCH_ROOT", str(tmp_path / "root"))
    result = CliRunner().invoke(app, ["status", "--offline"])
    assert result.exit_code == 0, result.output
    assert "ndb" in result.output and "no releases yet" in result.output


def test_cli_fail_on_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _repo(tmp_path, "ndb", "producer")
    _ledger(p, "ndb-v1.0.1")
    c = _repo(tmp_path, "jubarte-app", "consumer")
    (c.local_path / "bench.lock").write_text("ndb=ndb-v1.0.0\n")
    _write_toml(tmp_path / "root", ("ndb", "producer"), ("jubarte-app", "consumer"))
    monkeypatch.setenv("BENCH_ROOT", str(tmp_path / "root"))
    args = ["status", "--offline", "--fail-on-stale", "--repo", str(c.local_path)]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1, result.output
    assert "behind" in result.output
    monkeypatch.setenv("BENCH_ALLOW_STALE", "1")
    assert CliRunner().invoke(app, args).exit_code == 0


def test_cli_repo_outside_workspace_is_a_noop(tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    _repo(tmp_path, "ndb", "producer")
    _write_toml(tmp_path / "root", ("ndb", "producer"))
    monkeypatch.setenv("BENCH_ROOT", str(tmp_path / "root"))
    result = CliRunner().invoke(app, ["status", "--offline", "--repo", str(tmp_path)])
    assert result.exit_code == 0 and "not a linked repo" in result.output


def test_offline_producer_says_offline(tmp_path: Path) -> None:
    r = _repo(tmp_path, "ndb", "producer")
    _ledger(r, "ndb-v1.0.0")
    assert report(r, latest={}, witness=None).witness == "(offline)"
