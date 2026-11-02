"""Plan 6 part C: `bench archive` — satellites to the private archive dataset, and gc over
archive manifests. All hub traffic goes through fakes; nothing here touches the real HF."""

import hashlib
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bench_ops.archive import ARCHIVE_REPO, archive, stage_all
from bench_ops.cli import app
from bench_ops.gc import VerifiedManifest, fetch_verified, verdicts
from bench_ops.models import FileEntry, HfRef, LedgerEntry, Manifest
from bench_ops.publish import PublishError
from tests.test_publish import CREATED, FakeHub

runner = CliRunner()
NAME = "ndb-105"
TAG = f"arc-{NAME}"
PREFIX = f"archive/{NAME}/"
OLD_STAMP = datetime(2026, 10, 1, 12, 0, tzinfo=UTC).timestamp()  # older than every manifest


def _age_files(root: Path) -> None:
    """Pin mtimes: verdicts compare a file's mtime with the manifest that lists it."""
    for p in root.rglob("*"):
        if p.is_file():
            os.utime(p, (OLD_STAMP, OLD_STAMP))


def _sh(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "core.hooksPath=/dev/null", *args],
        check=True,
        capture_output=True,
    )


def _satellite(tmp_path: Path) -> Path:
    """A dirty git satellite: one committed file, one modification, one untracked file."""
    sat = tmp_path / NAME
    sat.mkdir()
    subprocess.run(["git", "init", "-b", "main", "-q"], cwd=sat, check=True)
    (sat / "tracked.txt").write_text("committed\n")
    _sh(sat, "add", "-A")
    _sh(sat, "commit", "-qm", "init")
    (sat / "tracked.txt").write_text("modified\n")  # dirty: a change HEAD does not hold
    (sat / "untracked.bin").write_bytes(b"untracked-bytes")
    _age_files(sat)
    return sat


def _bench_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    (root / "repos.toml").write_text("# no linked repos; the archive ledger is the witness\n")
    return root


def _entry_of(ledger: Path) -> LedgerEntry:
    lines = [ln for ln in ledger.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    return LedgerEntry.from_line(lines[0])


# ---- stage_all: the working tree *and* the .git directory -----------------------------------


def test_stage_all_includes_untracked_and_the_git_directory(tmp_path: Path) -> None:
    staged = stage_all(_satellite(tmp_path))
    rels = {s.rel for s in staged}
    assert ".git/HEAD" in rels  # history is preserved, not just the working tree
    assert any(r.startswith(".git/objects/") for r in rels)
    assert "untracked.bin" in rels
    tracked = next(s for s in staged if s.rel == "tracked.txt")
    assert tracked.sha256 == hashlib.sha256(b"modified\n").hexdigest()  # the tree, not HEAD


def test_stage_all_refuses_sources_that_are_not_directories(tmp_path: Path) -> None:
    with pytest.raises(PublishError):
        stage_all(tmp_path / "nope")
    with pytest.raises(PublishError):
        stage_all(_satellite(tmp_path) / "tracked.txt")


def test_stage_all_skips_symlinks(tmp_path: Path) -> None:
    sat = _satellite(tmp_path)
    (sat / "link").symlink_to(sat / "tracked.txt")
    rels = {s.rel for s in stage_all(sat)}
    assert "link" not in rels and "tracked.txt" in rels  # skipped, never followed


def test_stage_all_refuses_an_empty_tree(tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(PublishError):
        stage_all(tmp_path / "empty")


# ---- the name -------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["NDB-105", "ndb_105", "", "ndb.105", "a b", "ndb/105"])
def test_archive_refuses_a_name_outside_the_pattern(tmp_path: Path, bad: str) -> None:
    hub = FakeHub(tmp_path)
    with pytest.raises(PublishError, match="name"):
        archive(_satellite(tmp_path), bad, hub, now=CREATED, yes=False,
                bench_root=_bench_root(tmp_path))
    assert hub.calls == []


# ---- the dry run -----------------------------------------------------------------------------


def test_archive_dry_run_stages_only_no_hub_traffic_no_ledger(tmp_path: Path) -> None:
    sat = _satellite(tmp_path)
    hub = FakeHub(tmp_path)
    bench_root = _bench_root(tmp_path)
    m = archive(sat, NAME, hub, now=CREATED, yes=False, bench_root=bench_root)
    staged = stage_all(sat)
    assert hub.calls == []
    assert m.tag == TAG and m.hf.revision == "0" * 40
    assert len(m.files) == len(staged)
    assert sum(f.bytes for f in m.files) == sum(s.bytes for s in staged)
    assert not (bench_root / "archive" / "index.jsonl").exists()


# ---- the real thing ---------------------------------------------------------------------------


def test_archive_publishes_under_archive_name_and_appends_the_ledger(tmp_path: Path) -> None:
    sat = _satellite(tmp_path)
    hub = FakeHub(tmp_path)
    bench_root = _bench_root(tmp_path)
    m = archive(sat, NAME, hub, now=CREATED, yes=True, bench_root=bench_root)

    assert m.producer == "archive"
    assert all(f.path.startswith(PREFIX) for f in m.files)
    assert not any(f.served for f in m.files)
    assert {f.path: f.sha256 for f in m.files} == {
        f"{PREFIX}{s.rel}": s.sha256 for s in stage_all(sat)
    }

    r2 = hub.oids[-1]  # the manifest commit
    mb = hub.commits[r2][f"{PREFIX}manifest.json"]
    assert Manifest.model_validate_json(mb).tag == TAG
    assert hub.tags == {TAG: r2}

    entry = _entry_of(bench_root / "archive" / "index.jsonl")
    assert entry.tag == TAG and entry.hf.repo == ARCHIVE_REPO
    assert entry.hf.revision == r2
    assert entry.manifest_sha256 == hashlib.sha256(mb).hexdigest()


def test_archive_refuses_a_second_archive_of_the_same_name(tmp_path: Path) -> None:
    sat = _satellite(tmp_path)
    hub = FakeHub(tmp_path)
    bench_root = _bench_root(tmp_path)
    archive(sat, NAME, hub, now=CREATED, yes=True, bench_root=bench_root)
    with pytest.raises(PublishError, match="never overwritten"):
        archive(sat, NAME, hub, now=CREATED, yes=True, bench_root=bench_root)
    ledger = bench_root / "archive" / "index.jsonl"
    assert len(ledger.read_text().splitlines()) == 1  # still exactly one line
    assert len([c for c in hub.calls if c.startswith("commit:")]) == 2  # first run only


def test_archive_refuses_a_tag_already_in_the_local_ledger(tmp_path: Path) -> None:
    sat = _satellite(tmp_path)
    hub = FakeHub(tmp_path)
    bench_root = _bench_root(tmp_path)
    ledger = bench_root / "archive" / "index.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        LedgerEntry(
            tag=TAG, created=CREATED, hf=HfRef(repo=ARCHIVE_REPO, revision="b" * 40),
            manifest_sha256="a" * 64,
        ).to_line()
    )
    with pytest.raises(PublishError, match="ledger"):
        archive(sat, NAME, hub, now=CREATED, yes=True, bench_root=bench_root)
    assert not any(c.startswith("commit:") for c in hub.calls)


def test_archive_refuses_when_the_dataset_does_not_exist(tmp_path: Path) -> None:
    sat = _satellite(tmp_path)
    hub = FakeHub(tmp_path)
    hub.missing_repo = True
    bench_root = _bench_root(tmp_path)
    with pytest.raises(PublishError) as e:
        archive(sat, NAME, hub, now=CREATED, yes=True, bench_root=bench_root)
    msg = str(e.value)
    assert "\n" not in msg
    assert ARCHIVE_REPO in msg and "private" in msg and "create" in msg
    assert not any(c.startswith(("commit:", "tag:")) for c in hub.calls)
    assert not (bench_root / "archive" / "index.jsonl").exists()


# ---- gc over archive manifests -----------------------------------------------------------------


class _ServingHub:
    """Stands in for HfHub() at the CLI: serves one manifest and its LFS sha256s."""

    def __init__(self, tmp: Path, manifest_bytes: bytes, lfs: dict[str, str]) -> None:
        self.manifest = tmp / "served-manifest.json"
        self.manifest.write_bytes(manifest_bytes)
        self.lfs = lfs
        self.downloaded: list[str] = []

    def download(self, repo: str, revision: str, path: str) -> Path:
        self.downloaded.append(path)
        return self.manifest

    def paths_info(self, repo: str, revision: str, paths: list[str]) -> dict[str, str | None]:
        return {p: self.lfs[p] for p in paths if p in self.lfs}


def _archived_satellite(tmp_path: Path) -> tuple[Path, Path, _ServingHub]:
    """(satellite, bench root, hub serving the verified archive manifest of that satellite)."""
    sat = _satellite(tmp_path)
    bench_root = _bench_root(tmp_path)
    hub = FakeHub(tmp_path)
    m = archive(sat, NAME, hub, now=CREATED, yes=True, bench_root=bench_root)
    entry = _entry_of(bench_root / "archive" / "index.jsonl")
    r2 = entry.hf.revision
    serving = _ServingHub(
        tmp_path, hub.commits[r2][f"{PREFIX}manifest.json"],
        {f.path: f.sha256 for f in m.files},
    )
    return sat, bench_root, serving


def test_fetch_verified_reads_the_archive_manifest_path(tmp_path: Path) -> None:
    _sat, _root, hub = _archived_satellite(tmp_path)
    entry = _entry_of(_root / "archive" / "index.jsonl")
    vm = fetch_verified(entry, hub)
    assert vm is not None and vm.manifest.tag == TAG
    assert hub.downloaded == [f"{PREFIX}manifest.json"]  # archive/, not release/
    assert fetch_verified(entry.model_copy(update={"tag": "arc-other"}), hub) is None  # sha ties


def test_verdicts_delete_files_listed_by_a_verified_archive_manifest(tmp_path: Path) -> None:
    m = Manifest(
        tag=TAG,
        created=CREATED,
        producer="archive",
        hf=HfRef(repo=ARCHIVE_REPO, revision="b" * 40),
        r2_prefix=PREFIX,
        files=[
            FileEntry(path=f"{PREFIX}tracked.txt",
                      sha256=hashlib.sha256(b"modified\n").hexdigest(), bytes=9)
        ],
    )
    vm = VerifiedManifest(
        entry=LedgerEntry(
            tag=TAG, created=CREATED, hf=HfRef(repo=ARCHIVE_REPO, revision="c" * 40),
            manifest_sha256="a" * 64,
        ),
        manifest=m,
    )
    local = tmp_path / "tracked.txt"
    local.write_bytes(b"modified\n")
    os.utime(local, (OLD_STAMP, OLD_STAMP))
    out = tmp_path / "out"
    out.mkdir()
    v = verdicts([local], [vm], set(), roots=[tmp_path])[0]
    assert v.deletable and TAG in v.tags and "verified on HF" in v.reason


# ---- the CLI ------------------------------------------------------------------------------------


def test_cli_archive_dry_run_prints_the_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sat = _satellite(tmp_path)
    bench_root = _bench_root(tmp_path)
    monkeypatch.setenv("BENCH_ROOT", str(bench_root))
    result = runner.invoke(app, ["archive", str(sat), NAME])
    assert result.exit_code == 0, result.output
    out = result.output
    assert TAG in out and "files" in out and "bytes" in out and "--yes" in out
    assert not (bench_root / "archive" / "index.jsonl").exists()


def test_cli_archive_refuses_a_bad_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sat = _satellite(tmp_path)
    bench_root = _bench_root(tmp_path)
    monkeypatch.setenv("BENCH_ROOT", str(bench_root))
    result = runner.invoke(app, ["archive", str(sat), "NDB_105"])
    assert result.exit_code == 1, result.output
    assert "name" in result.output and "[a-z0-9-]+" in result.output  # the pattern survives
    assert not (bench_root / "archive" / "index.jsonl").exists()


def test_cli_archive_with_yes_publishes_and_appends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sat = _satellite(tmp_path)
    bench_root = _bench_root(tmp_path)
    hub = FakeHub(tmp_path)
    monkeypatch.setenv("BENCH_ROOT", str(bench_root))
    monkeypatch.setattr("bench_ops.cli.HfHub", lambda: hub)
    result = runner.invoke(app, ["archive", str(sat), NAME, "--yes"])
    assert result.exit_code == 0, result.output
    assert TAG in hub.tags
    entry = _entry_of(bench_root / "archive" / "index.jsonl")
    assert entry.tag == TAG and entry.hf.repo == ARCHIVE_REPO
    assert "archive/index.jsonl" in result.output  # the ledger commit hint


def test_cli_gc_deletes_satellite_files_via_a_verified_archive_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sat, bench_root, serving = _archived_satellite(tmp_path)
    monkeypatch.setenv("BENCH_ROOT", str(bench_root))
    monkeypatch.setattr("bench_ops.cli.HfHub", lambda: serving)

    result = runner.invoke(app, ["gc", str(sat / "tracked.txt")])
    assert result.exit_code == 0, result.output
    assert "deletable" in result.output and (sat / "tracked.txt").exists()  # no --yes: report

    result = runner.invoke(app, ["gc", str(sat / "tracked.txt"), "--yes"])
    assert result.exit_code == 0, result.output
    assert not (sat / "tracked.txt").exists()
    assert "deleted" in result.output

    git_head = sat / ".git" / "HEAD"
    result = runner.invoke(app, ["gc", str(git_head), "--yes"])
    assert result.exit_code == 0, result.output
    assert git_head.exists()  # .git is never collected, even archived
    assert ".git" in result.output


def test_cli_gc_accepts_a_directory_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sat, bench_root, serving = _archived_satellite(tmp_path)
    monkeypatch.setenv("BENCH_ROOT", str(bench_root))
    monkeypatch.setattr("bench_ops.cli.HfHub", lambda: serving)

    result = runner.invoke(app, ["gc", str(sat)])
    assert result.exit_code == 0, result.output
    out = result.output
    assert "tracked.txt" in out and "untracked.bin" in out  # the directory was expanded
    assert "deletable" in out
    assert (sat / "tracked.txt").exists()  # no --yes: only a report
    assert (sat / "untracked.bin").exists()


def test_cli_gc_directory_with_yes_deletes_the_working_tree_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sat, bench_root, serving = _archived_satellite(tmp_path)
    monkeypatch.setenv("BENCH_ROOT", str(bench_root))
    monkeypatch.setattr("bench_ops.cli.HfHub", lambda: serving)

    result = runner.invoke(app, ["gc", str(sat), "--yes"])
    assert result.exit_code == 0, result.output
    assert not (sat / "tracked.txt").exists()
    assert not (sat / "untracked.bin").exists()
    assert (sat / ".git" / "HEAD").exists()  # gc never collects .git; remove the rest by hand
    assert ".git" in result.output
