"""The plan-2 CLI surface: bench hook ledger, bench publish --dry-run, bench gc, and the hook config."""

import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bench_ops.cli import app
from bench_ops.gitx import clone_or_pull, git
from bench_ops.models import FileEntry, HfRef, LedgerEntry, Manifest, dumps
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


def test_lefthook_common_configures_the_fetch_check_hook() -> None:
    """Plan 4: a changed bench.lock re-verifies .bench/ offline (bench fetch --check).

    Glob-gated on bench.lock: .bench/ itself is git-ignored (and the size guard blocks it
    anyway), so the lock file is the only staged thing that can make the check relevant."""
    text = (Path(__file__).resolve().parent.parent / "lefthook" / "common.yml").read_text()
    assert "bench fetch --check --repo ." in text
    assert 'glob: "bench.lock"' in text


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


# ---- bench publish --dry-run with before scripts (rev 3) ----------------------------------


def test_cli_publish_dry_run_prints_the_before_commands_and_a_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _clone(tmp_path)
    (repo / "out").mkdir()
    (repo / "out" / "a.bin").write_bytes(b"aaa")
    _sh(repo, "add", "out")
    _sh(repo, "commit", "-qm", "out")
    release = (
        f'\n[repos.release]\nhf_repo = "{HF_REPO}"\nfiles = ["out/**"]\n'
        'before = ["uv run bench report --check", "uv run bench results upload"]\n'
    )
    root = _config(tmp_path, repo, release)
    monkeypatch.setenv("BENCH_ROOT", str(root))
    result = runner.invoke(app, ["publish", "ndb", "ndb-v0.0.1", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "uv run bench report --check" in result.output
    assert "uv run bench results upload" in result.output
    assert "out/a.bin" in result.output  # the manifest
    assert not (repo / "release").exists()  # nothing written, nothing run


# ---- bench deploy (rev 3, gated by bench fetch --check since plan 4) ----------------------

DEPLOY = "echo deployed {version} > done.txt"


def _bench_tree(repo: Path, producer: str = "ndb", tag: str = "ndb-v1.0.0",
                files: dict[str, bytes] | None = None) -> str:
    """Write .bench/<producer>/ the way `bench fetch` leaves it; return the manifest sha."""
    files = {"results/scores.json": b"{}"} if files is None else files
    m = Manifest(
        tag=tag, created=CREATED, producer=producer,
        hf=HfRef(repo=HF_REPO, revision="b" * 40), r2_prefix=f"{tag}/",
        files=[
            FileEntry(path=f"release/{tag}/{rel}", sha256=hashlib.sha256(d).hexdigest(),
                      bytes=len(d))
            for rel, d in sorted(files.items())
        ],
    )
    data = dumps(m).encode()
    prod = repo / ".bench" / producer
    (prod / "manifest.json").parent.mkdir(parents=True, exist_ok=True)
    (prod / "manifest.json").write_bytes(data)
    for rel, d in sorted(files.items()):
        p = prod / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(d)
    return hashlib.sha256(data).hexdigest()


def _consumer(tmp_path: Path, deploy: str | None = DEPLOY, lock: str | None = "ndb=ndb-v1.0.0"
              ) -> tuple[Path, Path]:
    """A jubarte-app clone with a pinned, fetched bench.lock and the BENCH_ROOT naming its deploy."""
    repo = _clone(tmp_path, name="jubarte-app")
    if lock is not None:
        sha = _bench_tree(repo)
        (repo / "bench.lock").write_text(
            f"ndb=ndb-v1.0.0 manifest={sha}\n" if lock else ""  # lock="" pins nothing
        )
    root = tmp_path / "croot"
    root.mkdir(exist_ok=True)
    deploy_line = f'deploy = "{deploy}"\n' if deploy is not None else ""
    (root / "repos.toml").write_text(
        f'[[repos]]\nname = "jubarte-app"\nurl = "x"\nbranch = "main"\n'
        f'role = "consumer"\npath = "{repo}"\n{deploy_line}'
    )
    return repo, root


def test_cli_deploy_without_yes_prints_the_command_and_runs_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, root = _consumer(tmp_path)
    monkeypatch.setenv("BENCH_ROOT", str(root))
    result = runner.invoke(app, ["deploy", "jubarte-app"])
    assert result.exit_code == 0, result.output
    assert "echo deployed ndb-v1.0.0 > done.txt" in result.output  # {version} substituted
    assert not (repo / "done.txt").exists()  # printed, not run


def test_cli_deploy_refuses_when_the_fetch_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan 4: deploy proves .bench against bench.lock first; a mismatch refuses it."""
    repo, root = _consumer(tmp_path)
    (repo / ".bench/ndb/results/scores.json").write_bytes(b"tampered")
    monkeypatch.setenv("BENCH_ROOT", str(root))
    result = runner.invoke(app, ["deploy", "jubarte-app", "--yes"])
    assert result.exit_code == 1, result.output
    assert "scores.json" in result.output  # the mismatch is named
    assert not (repo / "done.txt").exists()  # the deploy command never ran


def test_cli_deploy_with_yes_runs_the_command_in_the_consumer_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, root = _consumer(tmp_path)
    monkeypatch.setenv("BENCH_ROOT", str(root))
    result = runner.invoke(app, ["deploy", "jubarte-app", "--yes"])
    assert result.exit_code == 0, result.output
    assert (repo / "done.txt").read_text().strip() == "deployed ndb-v1.0.0"


def test_cli_deploy_propagates_a_nonzero_exit(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    _repo, root = _consumer(tmp_path, deploy="exit 7")
    monkeypatch.setenv("BENCH_ROOT", str(root))
    result = runner.invoke(app, ["deploy", "jubarte-app", "--yes"])
    assert result.exit_code == 7, result.output


def test_cli_deploy_substitutes_the_first_producers_tag(tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    _repo, root = _consumer(tmp_path)
    # two pinned producers, both fetched; the lock's first line wins {version}
    sha_et = _bench_tree(_repo, producer="et", tag="et-v2.0.0")
    lock = _repo / "bench.lock"
    sha_ndb = hashlib.sha256((lock.parent / ".bench/ndb/manifest.json").read_bytes()).hexdigest()
    lock.write_text(f"et=et-v2.0.0 manifest={sha_et}\nndb=ndb-v1.0.0 manifest={sha_ndb}\n")
    monkeypatch.setenv("BENCH_ROOT", str(root))
    result = runner.invoke(app, ["deploy", "jubarte-app"])
    assert result.exit_code == 0, result.output
    assert "deployed et-v2.0.0" in result.output  # the lock's first line wins


def test_cli_deploy_runs_a_command_without_version_placeholder(tmp_path: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    repo, root = _consumer(tmp_path, deploy="echo plain > plain.txt")
    monkeypatch.setenv("BENCH_ROOT", str(root))
    result = runner.invoke(app, ["deploy", "jubarte-app", "--yes"])
    assert result.exit_code == 0, result.output
    assert (repo / "plain.txt").exists()


def test_cli_deploy_refuses_a_producer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _clone(tmp_path)
    root = _config(tmp_path, repo)  # the default helper writes a producer
    monkeypatch.setenv("BENCH_ROOT", str(root))
    result = runner.invoke(app, ["deploy", "ndb"])
    assert result.exit_code == 1, result.output
    assert "producer" in result.output


def test_cli_deploy_refuses_an_unknown_repo(tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    _repo, root = _consumer(tmp_path)
    monkeypatch.setenv("BENCH_ROOT", str(root))
    result = runner.invoke(app, ["deploy", "nope"])
    assert result.exit_code == 1, result.output
    assert "unknown repo" in result.output


def test_cli_deploy_refuses_a_consumer_without_bench_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, root = _consumer(tmp_path, lock=None)
    monkeypatch.setenv("BENCH_ROOT", str(root))
    result = runner.invoke(app, ["deploy", "jubarte-app", "--yes"])
    assert result.exit_code == 1, result.output
    assert "bench.lock" in result.output
    assert not (repo / "done.txt").exists()


def test_cli_deploy_refuses_a_consumer_without_a_deploy_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo, root = _consumer(tmp_path, deploy=None)
    monkeypatch.setenv("BENCH_ROOT", str(root))
    result = runner.invoke(app, ["deploy", "jubarte-app"])
    assert result.exit_code == 1, result.output
    assert "deploy" in result.output


def test_cli_deploy_refuses_version_substitution_when_the_lock_pins_no_producer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, root = _consumer(tmp_path, lock="")  # a bench.lock with no <producer>=<tag> line
    monkeypatch.setenv("BENCH_ROOT", str(root))
    result = runner.invoke(app, ["deploy", "jubarte-app", "--yes"])
    assert result.exit_code == 1, result.output
    assert "version" in result.output
    assert not (repo / "done.txt").exists()
