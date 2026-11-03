"""#21: bump reads the ledger from origin/<branch> as well as the working tree, and takes the longer.

The ledger only grows, so one copy must be a prefix of the other; anything else is refused.
"""

import subprocess
from pathlib import Path

import pytest

from bench_ops.fetch import FetchError, bump
from bench_ops.models import HfRef, LedgerEntry
from tests.test_fetch import CREATED, HF_REPO, _cfg

A, B = "a" * 64, "b" * 64
LEDGER = "release/index.jsonl"


def line(tag: str, sha: str) -> str:
    return LedgerEntry(
        tag=tag, created=CREATED, hf=HfRef(repo=HF_REPO, revision="c" * 40), manifest_sha256=sha
    ).to_line()


def sh(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-C", str(repo), *args],
        check=True, capture_output=True,
    )


def published(tmp_path: Path, text: str) -> tuple[Path, Path]:
    """An origin whose main carries `text` as the ledger, and a clone of it."""
    src = tmp_path / "src"
    (src / "release").mkdir(parents=True)
    (src / LEDGER).write_text(text)
    sh(src, "init", "-q", "-b", "main")
    sh(src, "add", "-A")
    sh(src, "commit", "-qm", "ledger")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(origin)], check=True)
    clone = tmp_path / "ndb"
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    return origin, clone


def consumer(tmp_path: Path) -> Path:
    c = tmp_path / "app"
    c.mkdir()
    return c


def test_a_checkout_on_another_branch_bumps_from_origin(tmp_path: Path) -> None:
    _origin, clone = published(tmp_path, line("ndb-v0.0.2", B))
    sh(clone, "switch", "-q", "--orphan", "results/other")  # no ledger in the working tree
    _n, _old, new = bump("ndb", _cfg(clone), consumer(tmp_path))
    assert new == f"ndb-v0.0.2 manifest={B}"


def test_a_checkout_behind_origin_bumps_to_origins_newer_tag(tmp_path: Path) -> None:
    origin, clone = published(tmp_path, line("ndb-v0.0.1", A))
    pusher = tmp_path / "pusher"
    subprocess.run(["git", "clone", "-q", str(origin), str(pusher)], check=True)
    (pusher / LEDGER).write_text(line("ndb-v0.0.1", A) + line("ndb-v0.0.2", B))
    sh(pusher, "commit", "-qam", "next")
    sh(pusher, "push", "-q", "origin", "main")
    _n, _old, new = bump("ndb", _cfg(clone), consumer(tmp_path))
    assert new.startswith("ndb-v0.0.2")


def test_an_unpushed_local_publish_wins_over_origin(tmp_path: Path) -> None:
    """bench deploy jubarte: publish commits the ledger locally, the next step bumps from it."""
    _origin, clone = published(tmp_path, line("ndb-v0.0.1", A))
    (clone / LEDGER).write_text(line("ndb-v0.0.1", A) + line("ndb-v0.0.2", B))
    sh(clone, "commit", "-qam", "publish")
    _n, _old, new = bump("ndb", _cfg(clone), consumer(tmp_path))
    assert new.startswith("ndb-v0.0.2")


def test_diverged_ledgers_are_refused(tmp_path: Path) -> None:
    _origin, clone = published(tmp_path, line("ndb-v0.0.1", A))
    (clone / LEDGER).write_text(line("ndb-v0.0.9", B))
    app = consumer(tmp_path)
    with pytest.raises(FetchError, match="diverge"):
        bump("ndb", _cfg(clone), app)
    assert not (app / "bench.lock").exists()
