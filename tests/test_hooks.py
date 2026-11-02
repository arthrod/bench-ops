from pathlib import Path

import pytest
from typer.testing import CliRunner

from bench_ops.cli import app
from bench_ops.hooks import BLOCKED_DIRS, BLOCKED_SUFFIXES, size_guard

runner = CliRunner()
MB2 = 2 * 1024 * 1024


def test_size_guard_blocks_large(tmp_path: Path) -> None:
    big = tmp_path / "big.bin"
    big.write_bytes(b"\0" * (MB2 + 1))
    assert size_guard([big], root=tmp_path, limit=MB2) == ["big.bin: 2.0 MB over the 2.0 MB limit"]


def test_size_guard_blocks_binary_under_results(tmp_path: Path) -> None:
    p = tmp_path / "results" / "run" / "page.png"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    assert size_guard([p], root=tmp_path) == [
        "results/run/page.png: binary under results/ belongs on Hugging Face"
    ]


def test_size_guard_allows_small_ledger(tmp_path: Path) -> None:
    p = tmp_path / "results" / "bench.jsonl"
    p.parent.mkdir()
    p.write_text("{}\n")
    assert size_guard([p], root=tmp_path) == []


def test_size_guard_ignores_deleted_and_relative_paths(tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "fig.pdf").write_bytes(b"%PDF")
    problems = size_guard([Path("gone.txt"), Path("out/fig.pdf")], root=tmp_path)
    assert problems == ["out/fig.pdf: binary under out/ belongs on Hugging Face"]


def test_blocklists_are_what_the_plan_says() -> None:
    assert {"results", "out", "public/fixtures", "public/neurotic-docx-bench"} <= set(BLOCKED_DIRS)
    assert {".png", ".pdf", ".docx", ".webp", ".zip"} <= set(BLOCKED_SUFFIXES)


def test_cli_size_guard_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ok.txt").write_text("fine\n")
    assert runner.invoke(app, ["hook", "size-guard", "ok.txt"]).exit_code == 0
    (tmp_path / "big.bin").write_bytes(b"\0" * (3 * 1024 * 1024))
    r = runner.invoke(app, ["hook", "size-guard", "big.bin"])
    assert r.exit_code == 1 and "big.bin" in r.output


def test_cli_hooks_accept_no_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["hook", "size-guard"]).exit_code == 0
    assert runner.invoke(app, ["hook", "secrets"]).exit_code == 0


def test_cli_secrets_without_tool_fails_with_hint(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))  # no ripsecrets here
    (tmp_path / "a.txt").write_text("x\n")
    r = runner.invoke(app, ["hook", "secrets", "a.txt"])
    assert r.exit_code == 1 and "brew install ripsecrets" in r.output
