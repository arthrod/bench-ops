"""--fail-on-stale must also fail on a producer whose witness is not ok (plan 2, D16)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import bench_ops.cli
from bench_ops.cli import app
from bench_ops.gitx import clone_or_pull
from bench_ops.hf import Witness
from bench_ops.models import HfRef, LedgerEntry
from tests.test_gitx import make_origin


def test_fail_on_stale_fails_on_a_broken_witness(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    origin = make_origin(tmp_path)
    repo = tmp_path / "ndb"
    clone_or_pull(str(origin), repo, "main")
    (repo / "release").mkdir()
    (repo / "release" / "index.jsonl").write_text(
        LedgerEntry(
            tag="ndb-v1.0.0",
            created=datetime(2026, 11, 3, tzinfo=UTC),
            hf=HfRef(repo="arthrod/neurotic_docx_bench", revision="b" * 40),
            manifest_sha256="a" * 64,
        ).to_line()
    )
    root = tmp_path / "root"
    root.mkdir()
    (root / "repos.toml").write_text(
        f'[[repos]]\nname = "ndb"\nurl = "{origin}"\nrole = "producer"\npath = "{repo}"\n'
    )
    monkeypatch.setenv("BENCH_ROOT", str(root))

    def broken(entry: LedgerEntry) -> Witness:
        return Witness(missing=["site/x.pdf"], checked=2)

    monkeypatch.setattr(bench_ops.cli, "_witness_via_hf", broken)
    result = CliRunner().invoke(app, ["status", "--fail-on-stale"])
    assert result.exit_code == 1, result.output
    assert "BROKEN" in result.output
