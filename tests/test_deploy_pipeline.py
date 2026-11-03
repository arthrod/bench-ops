"""`bench deploy jubarte <version>`: the config-driven D3 pipeline (issue #14).

Every execution is offline: the command runner and the Word probe are injected fakes.
The only real subprocesses are local git checks in tiny temporary repositories.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bench_ops.cli import app
from bench_ops.models import HfRef, LedgerEntry
from bench_ops.pipeline import log_path

runner = CliRunner()
CREATED = datetime(2026, 11, 4, tzinfo=UTC)
WORD_COMMAND = 'osascript -e \'tell application "Microsoft Word" to get name\''


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repo(path: Path, branch: str = "main") -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-b", branch)
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    return path


def _ndb_with_ledger(root: Path, tag: str = "ndb-v1.2.3") -> tuple[Path, str]:
    ndb = _repo(root / "ndb")
    ledger = ndb / "release" / "index.jsonl"
    ledger.parent.mkdir()
    ledger.write_text(
        LedgerEntry(
            tag=tag,
            created=CREATED,
            hf=HfRef(repo="arthrod/test", revision="b" * 40),
            manifest_sha256="a" * 64,
        ).to_line()
    )
    _git(ndb, "add", "release/index.jsonl")
    _git(ndb, "commit", "-m", "release")
    return ndb, _git(ndb, "rev-parse", "HEAD")


def _config(
    root: Path,
    ndb: Path,
    ancestor: str,
    *,
    pipeline_name: str = "jubarte",
    repo_name: str = "jubarte-app",
    deploy_step: int = 6,
) -> Path:
    app_repo = root / repo_name
    app_repo.mkdir()
    steps = [
        ("release-info", "ndb", "first {version}"),
        ("report", "ndb", "second"),
        ("pages", "ndb", "third"),
        ("publish", "ndb", "publish {tag}"),
        ("bump-fetch", repo_name, "bump && fetch"),
        ("deploy", repo_name, "deploy"),
    ]
    step_toml = "\n".join(
        f'\n[[pipelines.{pipeline_name}.steps]]\n'
        f'name = "{name}"\nrepo = "{repo}"\nrun = "{run}"\n'
        for name, repo, run in steps
    )
    (root / "repos.toml").write_text(
        f'''[[repos]]
name = "ndb"
url = "x"
role = "producer"
path = "{ndb}"

[[repos]]
name = "{repo_name}"
url = "x"
role = "consumer"
path = "{app_repo}"

[pipelines.{pipeline_name}]
preflight_repo = "ndb"
publish_step = 4
deploy_step = {deploy_step}
word_timeout_seconds = 5
required_ancestors = ["{ancestor}"]
{step_toml}'''
    )
    return root


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "root"
    root.mkdir()
    ndb, ancestor = _ndb_with_ledger(root)
    _config(root, ndb, ancestor)
    return root, ndb, root / "jubarte-app"


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fail_at: str | None = None,
    fail_code: int = 7,
    word_code: int = 0,
    word_timeout: bool = False,
) -> tuple[list[tuple[str, Path]], list[tuple[str, int]]]:
    calls: list[tuple[str, Path]] = []
    probes: list[tuple[str, int]] = []

    def run(command: str, cwd: Path) -> int:
        calls.append((command, cwd))
        if fail_at is not None and command == fail_at:
            return fail_code
        return 0

    def probe(command: str, timeout_seconds: int) -> int:
        probes.append((command, timeout_seconds))
        if word_timeout:
            raise subprocess.TimeoutExpired(command, timeout_seconds)
        return word_code

    monkeypatch.setattr("bench_ops.cli.run_script", run)
    monkeypatch.setattr("bench_ops.cli._word_probe", probe)
    return calls, probes


def test_pipeline_log_name_has_a_unique_timestamp(tmp_path: Path) -> None:
    now = datetime(2026, 11, 4, 5, 6, 7, 123456, tzinfo=UTC)

    path = log_path(tmp_path, "jubarte", "0.18.0", now)

    assert path.name == "deploy-jubarte-0.18.0-20261104T050607123456Z.log"


def test_pipeline_dry_run_prints_the_resolved_plan_and_runs_nothing(
    workspace: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ndb, _app = workspace
    calls, probes = _install_fakes(monkeypatch)
    monkeypatch.setenv("BENCH_ROOT", str(root))

    result = runner.invoke(app, ["deploy", "jubarte", "0.18.0"])

    assert result.exit_code == 0, result.output
    assert "first 0.18.0" in result.output
    assert "publish ndb-v1.2.4" in result.output
    assert "ndb" in result.output and "jubarte-app" in result.output
    assert calls == []
    assert probes == []
    assert not (root / ".logs").exists()


def test_pipeline_yes_runs_steps_one_to_five_and_needs_deploy_for_step_six(
    workspace: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, ndb, app_repo = workspace
    calls, probes = _install_fakes(monkeypatch)
    monkeypatch.setenv("BENCH_ROOT", str(root))

    result = runner.invoke(app, ["deploy", "jubarte", "0.18.0", "--yes"])

    assert result.exit_code == 0, result.output
    assert [command for command, _cwd in calls] == [
        "first 0.18.0",
        "second",
        "third",
        "publish ndb-v1.2.4",
        "bump && fetch",
    ]
    assert [cwd for _command, cwd in calls] == [ndb, ndb, ndb, ndb, app_repo]
    assert probes == [(WORD_COMMAND, 5)]
    logs = list((root / ".logs").glob("deploy-jubarte-0.18.0-*.log"))
    assert len(logs) == 1
    text = logs[0].read_text()
    assert "first 0.18.0" in text
    assert "publish ndb-v1.2.4" in text
    assert "exit 0" in text


def test_pipeline_deploy_flag_runs_the_gated_final_step(
    workspace: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ndb, app_repo = workspace
    calls, _probes = _install_fakes(monkeypatch)
    monkeypatch.setenv("BENCH_ROOT", str(root))

    result = runner.invoke(app, ["deploy", "jubarte", "0.18.0", "--yes", "--deploy"])

    assert result.exit_code == 0, result.output
    assert [command for command, _cwd in calls][-1] == "deploy"
    assert calls[-1][1] == app_repo


def test_pipeline_no_deploy_is_an_explicit_no_op_alias(
    workspace: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ndb, _app = workspace
    calls, _probes = _install_fakes(monkeypatch)
    monkeypatch.setenv("BENCH_ROOT", str(root))

    result = runner.invoke(app, ["deploy", "jubarte", "0.18.0", "--yes", "--no-deploy"])

    assert result.exit_code == 0, result.output
    assert [command for command, _cwd in calls] == [
        "first 0.18.0",
        "second",
        "third",
        "publish ndb-v1.2.4",
        "bump && fetch",
    ]


def test_pipeline_stops_at_the_first_failing_step_with_its_exit_code(
    workspace: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ndb, _app = workspace
    calls, _probes = _install_fakes(monkeypatch, fail_at="second", fail_code=7)
    monkeypatch.setenv("BENCH_ROOT", str(root))

    result = runner.invoke(app, ["deploy", "jubarte", "0.18.0", "--yes"])

    assert result.exit_code == 7, result.output
    assert [command for command, _cwd in calls] == ["first 0.18.0", "second"]
    assert "step 2" in result.output
    assert "exited 7" in result.output
    log = next((root / ".logs").glob("deploy-jubarte-0.18.0-*.log"))
    assert "exit 7" in log.read_text()


def test_pipeline_from_resumes_at_the_selected_step(
    workspace: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ndb, _app = workspace
    calls, _probes = _install_fakes(monkeypatch)
    monkeypatch.setenv("BENCH_ROOT", str(root))

    result = runner.invoke(
        app, ["deploy", "jubarte", "0.18.0", "--yes", "--from", "3"]
    )

    assert result.exit_code == 0, result.output
    assert [command for command, _cwd in calls] == [
        "third",
        "publish ndb-v1.2.4",
        "bump && fetch",
    ]


def test_pipeline_dry_run_from_the_deploy_step_still_prints_the_plan(
    workspace: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ndb, _app = workspace
    calls, probes = _install_fakes(monkeypatch)
    monkeypatch.setenv("BENCH_ROOT", str(root))

    result = runner.invoke(app, ["deploy", "jubarte", "0.18.0", "--from", "6"])

    assert result.exit_code == 0, result.output
    assert "requires --deploy" in result.output
    assert calls == []
    assert probes == []


def test_pipeline_resume_after_publish_keeps_the_published_ledger_tag(
    workspace: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, ndb, _app = workspace
    _install_fakes(monkeypatch)
    ledger = ndb / "release" / "index.jsonl"
    ledger.write_text(
        LedgerEntry(
            tag="ndb-v1.2.4",
            created=CREATED,
            hf=HfRef(repo="arthrod/test", revision="c" * 40),
            manifest_sha256="b" * 64,
        ).to_line()
    )
    _git(ndb, "add", "release/index.jsonl")
    _git(ndb, "commit", "-m", "published")
    monkeypatch.setenv("BENCH_ROOT", str(root))

    result = runner.invoke(app, ["deploy", "jubarte", "0.18.0", "--from", "5"])

    assert result.exit_code == 0, result.output
    assert "publish ndb-v1.2.4" in result.output
    assert "publish ndb-v1.2.5" not in result.output


def test_pipeline_tag_override_is_used_in_the_plan(
    workspace: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ndb, _app = workspace
    _install_fakes(monkeypatch)
    monkeypatch.setenv("BENCH_ROOT", str(root))

    result = runner.invoke(
        app, ["deploy", "jubarte", "0.18.0", "--tag", "ndb-v9.8.7"]
    )

    assert result.exit_code == 0, result.output
    assert "publish ndb-v9.8.7" in result.output
    assert "publish ndb-v1.2.4" not in result.output


def test_pipeline_preflight_refuses_a_dirty_ndb_before_any_step(
    workspace: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, ndb, _app = workspace
    calls, probes = _install_fakes(monkeypatch)
    (ndb / "dirty.txt").write_text("dirty")
    _git(ndb, "add", "dirty.txt")
    _git(ndb, "commit", "-m", "dirty")
    (ndb / "dirty.txt").write_text("changed")
    monkeypatch.setenv("BENCH_ROOT", str(root))

    result = runner.invoke(app, ["deploy", "jubarte", "0.18.0", "--yes"])

    assert result.exit_code == 1, result.output
    assert "clean" in result.output
    assert calls == []
    assert probes == []


def test_pipeline_preflight_refuses_ndb_on_the_wrong_branch(
    workspace: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, ndb, _app = workspace
    calls, _probes = _install_fakes(monkeypatch)
    _git(ndb, "checkout", "-b", "feature")
    monkeypatch.setenv("BENCH_ROOT", str(root))

    result = runner.invoke(app, ["deploy", "jubarte", "0.18.0", "--yes"])

    assert result.exit_code == 1, result.output
    assert "main" in result.output and "branch" in result.output
    assert calls == []


def test_pipeline_preflight_refuses_a_missing_required_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    ndb, _ancestor = _ndb_with_ledger(root)
    _config(root, ndb, "f" * 40)
    calls, probes = _install_fakes(monkeypatch)
    monkeypatch.setenv("BENCH_ROOT", str(root))

    result = runner.invoke(app, ["deploy", "jubarte", "0.18.0", "--yes"])

    assert result.exit_code == 1, result.output
    assert "f" * 40 in result.output
    assert calls == []
    assert probes == []


def test_pipeline_preflight_refuses_when_word_does_not_answer(
    workspace: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ndb, _app = workspace
    calls, probes = _install_fakes(monkeypatch, word_code=1)
    monkeypatch.setenv("BENCH_ROOT", str(root))

    result = runner.invoke(app, ["deploy", "jubarte", "0.18.0", "--yes"])

    assert result.exit_code == 1, result.output
    assert "Word" in result.output
    assert calls == []
    assert probes == [(WORD_COMMAND, 5)]


def test_pipeline_preflight_refuses_when_the_word_probe_times_out(
    workspace: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ndb, _app = workspace
    calls, _probes = _install_fakes(monkeypatch, word_timeout=True)
    monkeypatch.setenv("BENCH_ROOT", str(root))

    result = runner.invoke(app, ["deploy", "jubarte", "0.18.0", "--yes"])

    assert result.exit_code == 1, result.output
    assert "timed out" in result.output
    assert calls == []


def test_pipeline_requires_a_version(
    workspace: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ndb, _app = workspace
    _install_fakes(monkeypatch)
    monkeypatch.setenv("BENCH_ROOT", str(root))

    result = runner.invoke(app, ["deploy", "jubarte"])

    assert result.exit_code == 2, result.output
    assert "version" in result.output


def test_pipeline_refuses_a_version_for_the_legacy_consumer(
    workspace: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ndb, _app = workspace
    _install_fakes(monkeypatch)
    monkeypatch.setenv("BENCH_ROOT", str(root))

    result = runner.invoke(app, ["deploy", "jubarte-app", "0.18.0"])

    assert result.exit_code == 2, result.output
    assert "version" in result.output


def test_pipeline_refuses_an_unknown_name(
    workspace: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ndb, _app = workspace
    _install_fakes(monkeypatch)
    monkeypatch.setenv("BENCH_ROOT", str(root))

    result = runner.invoke(app, ["deploy", "neither"])

    assert result.exit_code == 1, result.output
    assert "neither" in result.output
    assert "pipeline" in result.output


def test_pipeline_refuses_a_name_that_is_both_a_pipeline_and_a_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    ndb, ancestor = _ndb_with_ledger(root)
    _config(root, ndb, ancestor, pipeline_name="jubarte-app")
    _install_fakes(monkeypatch)
    monkeypatch.setenv("BENCH_ROOT", str(root))

    result = runner.invoke(app, ["deploy", "jubarte-app", "0.18.0"])

    assert result.exit_code == 2, result.output
    assert "both" in result.output
    assert "pipeline" in result.output and "repo" in result.output
