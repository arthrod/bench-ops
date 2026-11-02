"""bench bump / bench fetch (plan 4): a consumer pins a release and materialises .bench/.

Offline fakes only: FakeHub stands in for the HubApi read side (download), tests build
releases by hand (files commit → manifest commit → tag), exactly what `bench publish`
would have left behind.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bench_ops import config
from bench_ops.cli import app
from bench_ops.fetch import FetchError, bump, check_tree, fetch
from bench_ops.models import FileEntry, HfRef, LedgerEntry, Manifest, dumps

runner = CliRunner()
CREATED = datetime(2026, 11, 4, tzinfo=UTC)
HF_REPO = "arthrod/test-bench"
TAG = "ndb-v0.0.1"
A64 = "a" * 64
B64 = "b" * 64


# ---- the hub, and releases on it ---------------------------------------------------------


class FakeHub:
    """The read side fetch needs (HubApi.download); commits are cumulative trees."""

    def __init__(self, tmp: Path, repo: str = HF_REPO) -> None:
        self.tmp = tmp
        self.repo = repo
        self.commits: dict[str, dict[str, bytes]] = {}
        self.order: list[str] = []
        self.tags: dict[str, str] = {}
        self.calls: list[str] = []
        self.fail_on: set[str] = set()  # paths whose download blows up
        self._n = 0

    def commit(self, files: dict[str, bytes]) -> str:
        self._n += 1
        oid = f"{self._n:040x}"
        tree = dict(self.commits[self.order[-1]]) if self.order else {}
        tree.update(files)
        self.commits[oid] = tree
        self.order.append(oid)
        return oid

    def tag(self, tag: str, revision: str) -> None:
        self.tags[tag] = revision

    def download(self, repo: str, revision: str, path: str) -> Path:
        self.calls.append(f"download:{path}@{revision}")
        if path in self.fail_on:
            raise OSError(f"hub unreachable for {path}")
        rev = self.tags.get(revision, revision)
        if rev not in self.commits or path not in self.commits[rev]:
            raise LookupError(f"{path} is not at revision {revision} in {repo}")
        self._n += 1
        f = self.tmp / "downloads" / f"{self._n}"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(self.commits[rev][path])
        return f


def _release(
    hub: FakeHub, tag: str, files: dict[str, bytes], producer: str = "ndb"
) -> tuple[str, bytes, str, str]:
    """Publish a release the way `bench publish` leaves it: (manifest sha, bytes, R1, R2)."""
    r1 = hub.commit({f"release/{tag}/{rel}": data for rel, data in sorted(files.items())})
    m = Manifest(
        tag=tag,
        created=CREATED,
        producer=producer,
        hf=HfRef(repo=hub.repo, revision=r1),
        r2_prefix=f"{tag}/",
        files=[
            FileEntry(
                path=f"release/{tag}/{rel}",
                sha256=hashlib.sha256(data).hexdigest(),
                bytes=len(data),
            )
            for rel, data in sorted(files.items())
        ],
    )
    data = dumps(m).encode()
    r2 = hub.commit({f"release/{tag}/manifest.json": data})
    hub.tag(tag, r2)
    return hashlib.sha256(data).hexdigest(), data, r1, r2


def _cfg(producer_path: Path) -> config.Config:
    return config.Config(
        root=producer_path.parent / "root",
        repos=[
            config.Repo(
                name="ndb",
                url="x",
                branch="main",
                role="producer",
                path=producer_path,
                release=config.ReleaseSpec(hf_repo=HF_REPO, files=["out/**"]),
            )
        ],
    )


@pytest.fixture
def consumer(tmp_path: Path) -> Path:
    repo = tmp_path / "jubarte-app"
    repo.mkdir()
    return repo


def _pin(consumer: Path, line: str) -> None:
    (consumer / "bench.lock").write_text(line + "\n")


# ---- bump --------------------------------------------------------------------------------


def _ledger(producer: Path, tag: str = TAG, sha: str = A64) -> Path:
    producer.mkdir(parents=True, exist_ok=True)
    ledger = producer / "release" / "index.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(
        LedgerEntry(
            tag=tag, created=CREATED, hf=HfRef(repo=HF_REPO, revision="b" * 40),
            manifest_sha256=sha,
        ).to_line()
    )
    return ledger


def test_bump_writes_tag_and_manifest_sha(tmp_path: Path, consumer: Path) -> None:
    producer = tmp_path / "ndb"
    _ledger(producer, sha=A64)
    _name, old, new = bump("ndb", _cfg(producer), consumer)
    assert (consumer / "bench.lock").read_text() == f"ndb={TAG} manifest={A64}\n"
    assert old == "(none)"
    assert new == f"{TAG} manifest={A64}"


def test_bump_updates_only_its_producer_and_prints_old_to_new(
    tmp_path: Path, consumer: Path
) -> None:
    producer = tmp_path / "ndb"
    _ledger(producer, tag="ndb-v0.0.2", sha=B64)
    (consumer / "bench.lock").write_text(
        f"et=et-v0.9.3 manifest={'c' * 64}\nndb={TAG} manifest={A64}\n"
    )
    _name, old, new = bump("ndb", _cfg(producer), consumer)
    assert old == TAG and new == f"ndb-v0.0.2 manifest={B64}"
    text = (consumer / "bench.lock").read_text()
    assert text == f"et=et-v0.9.3 manifest={'c' * 64}\nndb=ndb-v0.0.2 manifest={B64}\n"


def test_bump_refuses_an_unknown_repo(tmp_path: Path, consumer: Path) -> None:
    with pytest.raises(FetchError, match="unknown repo"):
        bump("nope", _cfg(tmp_path / "ndb"), consumer)
    assert not (consumer / "bench.lock").exists()


def test_bump_refuses_a_repo_that_is_not_a_producer(tmp_path: Path, consumer: Path) -> None:
    root = tmp_path / "root"
    cfg = config.Config(
        root=root,
        repos=[config.Repo(name="blaw", url="x", role="consumer", path=tmp_path / "blaw")],
    )
    with pytest.raises(FetchError, match="producer"):
        bump("blaw", cfg, consumer)


def test_bump_refuses_a_producer_without_a_ledger(tmp_path: Path, consumer: Path) -> None:
    producer = tmp_path / "ndb"
    producer.mkdir()
    with pytest.raises(FetchError, match="no release ledger"):
        bump("ndb", _cfg(producer), consumer)
    assert not (consumer / "bench.lock").exists()


def test_bump_refuses_a_torn_ledger(tmp_path: Path, consumer: Path) -> None:
    producer = tmp_path / "ndb"
    ledger = _ledger(producer)
    ledger.write_text(ledger.read_text() + '{"tag": torn\n')
    with pytest.raises(FetchError, match="torn"):
        bump("ndb", _cfg(producer), consumer)


# ---- fetch -------------------------------------------------------------------------------


def test_fetch_writes_the_release_tree_under_bench(
    tmp_path: Path, consumer: Path
) -> None:
    hub = FakeHub(tmp_path)
    sha, manifest_bytes, r1, _r2 = _release(
        hub, TAG, {"results/scores.json": b"{}", "cases/index.json": b"[]"}
    )
    _pin(consumer, f"ndb={TAG} manifest={sha}")
    fetch(consumer, _cfg(tmp_path / "ndb"), hub)
    # the manifest is fetched at the HF tag, the files at the manifest's own revision
    assert f"download:release/{TAG}/manifest.json@{TAG}" in hub.calls
    assert f"download:release/{TAG}/results/scores.json@{r1}" in hub.calls
    # paths relative to release/<tag>/, plus manifest.json
    assert (consumer / ".bench/ndb/manifest.json").read_bytes() == manifest_bytes
    assert (consumer / ".bench/ndb/results/scores.json").read_bytes() == b"{}"
    assert (consumer / ".bench/ndb/cases/index.json").read_bytes() == b"[]"
    # nothing else: no temp dirs, no leftovers
    assert [p.name for p in (consumer / ".bench").iterdir()] == ["ndb"]


def test_fetch_refuses_a_manifest_that_is_not_the_pinned_one(
    tmp_path: Path, consumer: Path
) -> None:
    hub = FakeHub(tmp_path)
    _release(hub, TAG, {"results/scores.json": b"{}"})
    _pin(consumer, f"ndb={TAG} manifest={'0' * 64}")
    with pytest.raises(FetchError, match="manifest"):
        fetch(consumer, _cfg(tmp_path / "ndb"), hub)
    assert not (consumer / ".bench").exists()


def test_fetch_refuses_a_lock_line_without_a_manifest_pin(
    tmp_path: Path, consumer: Path
) -> None:
    hub = FakeHub(tmp_path)
    _pin(consumer, f"ndb={TAG}")
    with pytest.raises(FetchError, match="bench bump"):
        fetch(consumer, _cfg(tmp_path / "ndb"), hub)
    assert not (consumer / ".bench").exists()


def test_fetch_refuses_a_lock_line_for_an_unknown_repo(
    tmp_path: Path, consumer: Path
) -> None:
    _pin(consumer, f"nope={TAG} manifest={A64}")
    with pytest.raises(FetchError, match="unknown repo"):
        fetch(consumer, _cfg(tmp_path / "ndb"), FakeHub(tmp_path))


def test_fetch_refuses_a_producer_without_a_release_spec(
    tmp_path: Path, consumer: Path
) -> None:
    producer = tmp_path / "ndb"
    producer.mkdir()
    cfg = config.Config(
        root=tmp_path / "root",
        repos=[config.Repo(name="ndb", url="x", role="producer", path=producer)],
    )
    _pin(consumer, f"ndb={TAG} manifest={A64}")
    with pytest.raises(FetchError, match="release"):
        fetch(consumer, cfg, FakeHub(tmp_path))


def test_fetch_skips_files_already_present_with_the_right_hash(
    tmp_path: Path, consumer: Path
) -> None:
    hub = FakeHub(tmp_path)
    sha, _bytes, _r1, _r2 = _release(hub, TAG, {"results/scores.json": b"{}"})
    _pin(consumer, f"ndb={TAG} manifest={sha}")
    present = consumer / ".bench/ndb/results/scores.json"
    present.parent.mkdir(parents=True)
    present.write_bytes(b"{}")
    fetch(consumer, _cfg(tmp_path / "ndb"), hub)
    assert not any("scores.json" in c for c in hub.calls)  # not downloaded again
    assert present.read_bytes() == b"{}"
    assert (consumer / ".bench/ndb/manifest.json").exists()


def test_fetch_is_atomic_per_producer(tmp_path: Path, consumer: Path) -> None:
    hub = FakeHub(tmp_path)
    sha, _bytes, _r1, _r2 = _release(
        hub, TAG, {"results/scores.json": b"{}", "results/other.json": b"[]"}
    )
    _pin(consumer, f"ndb={TAG} manifest={sha}")
    prod = consumer / ".bench/ndb"
    prod.mkdir(parents=True)
    (prod / "manifest.json").write_bytes(b"the previous release's manifest")
    hub.fail_on.add(f"release/{TAG}/results/scores.json")
    with pytest.raises(OSError):
        fetch(consumer, _cfg(tmp_path / "ndb"), hub)
    # the previous tree survives a crash mid-download, and no scratch dirs are left
    assert (prod / "manifest.json").read_bytes() == b"the previous release's manifest"
    assert [p.name for p in (consumer / ".bench").iterdir()] == ["ndb"]


def test_fetch_replaces_files_the_manifest_no_longer_lists(
    tmp_path: Path, consumer: Path
) -> None:
    hub = FakeHub(tmp_path)
    sha, _bytes, _r1, _r2 = _release(hub, TAG, {"results/scores.json": b"{}"})
    _pin(consumer, f"ndb={TAG} manifest={sha}")
    prod = consumer / ".bench/ndb"
    prod.mkdir(parents=True)
    (prod / "results/scores.json").parent.mkdir()
    (prod / "results/scores.json").write_bytes(b"{}")
    (prod / "results/gone.json").write_bytes(b"stale")
    fetch(consumer, _cfg(tmp_path / "ndb"), hub)
    assert not (prod / "results/gone.json").exists()
    assert (prod / "results/scores.json").exists()


def test_fetch_refuses_a_manifest_whose_files_left_the_release_tree(
    tmp_path: Path, consumer: Path
) -> None:
    hub = FakeHub(tmp_path)
    r1 = hub.commit({f"release/{TAG}/../evil.json": b"x"})
    m = Manifest(
        tag=TAG, created=CREATED, producer="ndb",
        hf=HfRef(repo=hub.repo, revision=r1), r2_prefix=f"{TAG}/",
        files=[FileEntry(
            path=f"release/{TAG}/../evil.json",
            sha256=hashlib.sha256(b"x").hexdigest(), bytes=1,
        )],
    )
    data = dumps(m).encode()
    r2 = hub.commit({f"release/{TAG}/manifest.json": data})
    hub.tag(TAG, r2)
    _pin(consumer, f"ndb={TAG} manifest={hashlib.sha256(data).hexdigest()}")
    with pytest.raises(FetchError, match="escape"):
        fetch(consumer, _cfg(tmp_path / "ndb"), hub)
    assert not (consumer / ".bench").exists()


def test_fetch_refuses_a_file_whose_bytes_do_not_match_its_entry(
    tmp_path: Path, consumer: Path
) -> None:
    hub = FakeHub(tmp_path)
    sha, _bytes, r1, r2 = _release(hub, TAG, {"results/scores.json": b"{}"})
    # corrupt the file at R1 after the release was built
    hub.commits[r1][f"release/{TAG}/results/scores.json"] = b"tampered"
    hub.commits[r2][f"release/{TAG}/results/scores.json"] = b"tampered"
    _pin(consumer, f"ndb={TAG} manifest={sha}")
    with pytest.raises(FetchError, match="scores.json"):
        fetch(consumer, _cfg(tmp_path / "ndb"), hub)
    assert not (consumer / ".bench/ndb/results/scores.json").exists()


def test_fetch_without_bench_lock_refuses(tmp_path: Path, consumer: Path) -> None:
    with pytest.raises(FetchError, match="bench.lock"):
        fetch(consumer, _cfg(tmp_path / "ndb"), FakeHub(tmp_path))


# ---- check (offline) ---------------------------------------------------------------------


def _fetched(tmp_path: Path, consumer: Path, files: dict[str, bytes]) -> tuple[str, str]:
    hub = FakeHub(tmp_path)
    sha, _bytes, _r1, _r2 = _release(hub, TAG, files)
    _pin(consumer, f"ndb={TAG} manifest={sha}")
    fetch(consumer, _cfg(tmp_path / "ndb"), hub)
    return sha, hub.tags[TAG]


def test_check_passes_on_a_fetched_tree(tmp_path: Path, consumer: Path) -> None:
    _fetched(tmp_path, consumer, {"results/scores.json": b"{}", "cases/index.json": b"[]"})
    assert check_tree(consumer) == []


def test_check_names_the_first_mismatched_file(tmp_path: Path, consumer: Path) -> None:
    _fetched(tmp_path, consumer, {"a/one.json": b"1", "b/two.json": b"2"})
    (consumer / ".bench/ndb/a/one.json").write_bytes(b"no")
    (consumer / ".bench/ndb/b/two.json").write_bytes(b"no")
    problems = check_tree(consumer)
    assert len(problems) >= 2
    assert ".bench/ndb/a/one.json" in problems[0]  # manifest order: a/one.json first


def test_check_names_a_missing_file(tmp_path: Path, consumer: Path) -> None:
    _fetched(tmp_path, consumer, {"a/one.json": b"1"})
    (consumer / ".bench/ndb/a/one.json").unlink()
    assert ".bench/ndb/a/one.json" in check_tree(consumer)[0]


def test_check_fails_on_a_tampered_manifest(tmp_path: Path, consumer: Path) -> None:
    _sha, _tag = _fetched(tmp_path, consumer, {"a/one.json": b"1"})
    (consumer / ".bench/ndb/manifest.json").write_bytes(b"{}")
    problems = check_tree(consumer)
    assert len(problems) == 1  # the manifest first; its files are not even listed
    assert "manifest.json" in problems[0]


def test_check_fails_on_extra_files(tmp_path: Path, consumer: Path) -> None:
    _fetched(tmp_path, consumer, {"a/one.json": b"1"})
    extra = consumer / ".bench/ndb/stray.json"
    extra.write_text("{}")
    problems = check_tree(consumer)
    assert any("stray.json" in p and "not in the manifest" in p for p in problems)


def test_check_fails_without_a_fetch(tmp_path: Path, consumer: Path) -> None:
    _sha, _tag = _fetched(tmp_path, consumer, {"a/one.json": b"1"})
    # a second producer pinned but never fetched
    with (consumer / "bench.lock").open("a") as f:
        f.write(f"et=et-v0.9.4 manifest={B64}\n")
    problems = check_tree(consumer)
    assert any(".bench/et/manifest.json" in p and "missing" in p for p in problems)


def test_check_fails_when_the_lock_has_no_pin(tmp_path: Path, consumer: Path) -> None:
    _pin(consumer, f"ndb={TAG}")
    assert any("bench bump" in p for p in check_tree(consumer))


def test_check_fails_without_bench_lock(tmp_path: Path, consumer: Path) -> None:
    assert any("bench.lock" in p for p in check_tree(consumer))


def test_check_never_touches_the_hub(tmp_path: Path, consumer: Path) -> None:
    _fetched(tmp_path, consumer, {"a/one.json": b"1"})

    def _boom() -> None:
        raise AssertionError("--check is offline; no hub may be constructed")

    from bench_ops import cli

    original = cli.HfHub
    cli.HfHub = _boom  # type: ignore[assignment]
    try:
        result = runner.invoke(app, ["fetch", "--check", "--repo", str(consumer)])
    finally:
        cli.HfHub = original
    assert result.exit_code == 0, result.output


# ---- the CLI -----------------------------------------------------------------------------


def _cli_root(tmp_path: Path, producer: Path) -> Path:
    root = tmp_path / "cliroot"
    root.mkdir(exist_ok=True)
    (root / "repos.toml").write_text(
        f'[[repos]]\nname = "ndb"\nurl = "x"\nbranch = "main"\nrole = "producer"\n'
        f'path = "{producer}"\n\n[repos.release]\nhf_repo = "{HF_REPO}"\nfiles = ["out/**"]\n'
    )
    return root


def test_cli_bump_writes_the_lock(
    tmp_path: Path, consumer: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    producer = tmp_path / "ndb"
    _ledger(producer, sha=A64)
    monkeypatch.setenv("BENCH_ROOT", str(_cli_root(tmp_path, producer)))
    result = runner.invoke(app, ["bump", "ndb", "--repo", str(consumer)])
    assert result.exit_code == 0, result.output
    assert f"-> {TAG} manifest={A64}" in result.output
    assert (consumer / "bench.lock").read_text() == f"ndb={TAG} manifest={A64}\n"


def test_cli_bump_refuses_a_producer_without_a_ledger(
    tmp_path: Path, consumer: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    producer = tmp_path / "ndb"
    producer.mkdir()
    monkeypatch.setenv("BENCH_ROOT", str(_cli_root(tmp_path, producer)))
    result = runner.invoke(app, ["bump", "ndb", "--repo", str(consumer)])
    assert result.exit_code == 1, result.output
    assert "ledger" in result.output
    assert not (consumer / "bench.lock").exists()


def test_cli_fetch_then_check(
    tmp_path: Path, consumer: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    producer = tmp_path / "ndb"
    producer.mkdir()
    monkeypatch.setenv("BENCH_ROOT", str(_cli_root(tmp_path, producer)))
    hub = FakeHub(tmp_path)
    sha, _bytes, _r1, _r2 = _release(hub, TAG, {"results/scores.json": b"{}"})
    monkeypatch.setattr("bench_ops.cli.HfHub", lambda: hub)
    _pin(consumer, f"ndb={TAG} manifest={sha}")

    result = runner.invoke(app, ["fetch", "--repo", str(consumer)])
    assert result.exit_code == 0, result.output
    assert TAG in result.output
    assert (consumer / ".bench/ndb/results/scores.json").exists()

    result = runner.invoke(app, ["fetch", "--check", "--repo", str(consumer)])
    assert result.exit_code == 0, result.output

    (consumer / ".bench/ndb/results/scores.json").write_bytes(b"tampered")
    result = runner.invoke(app, ["fetch", "--check", "--repo", str(consumer)])
    assert result.exit_code == 1, result.output
    assert "scores.json" in result.output  # the first mismatch is named


def test_cli_fetch_refuses_a_moved_tag(
    tmp_path: Path, consumer: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    producer = tmp_path / "ndb"
    producer.mkdir()
    monkeypatch.setenv("BENCH_ROOT", str(_cli_root(tmp_path, producer)))
    hub = FakeHub(tmp_path)
    _release(hub, TAG, {"results/scores.json": b"{}"})
    monkeypatch.setattr("bench_ops.cli.HfHub", lambda: hub)
    _pin(consumer, f"ndb={TAG} manifest={'0' * 64}")
    result = runner.invoke(app, ["fetch", "--repo", str(consumer)])
    assert result.exit_code == 1, result.output
    assert "manifest" in result.output
    assert not (consumer / ".bench").exists()


def test_cli_fetch_default_repo_is_the_cwd(
    tmp_path: Path, consumer: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    producer = tmp_path / "ndb"
    producer.mkdir()
    monkeypatch.setenv("BENCH_ROOT", str(_cli_root(tmp_path, producer)))
    hub = FakeHub(tmp_path)
    sha, _bytes, _r1, _r2 = _release(hub, TAG, {"results/scores.json": b"{}"})
    monkeypatch.setattr("bench_ops.cli.HfHub", lambda: hub)
    _pin(consumer, f"ndb={TAG} manifest={sha}")
    monkeypatch.chdir(consumer)
    result = runner.invoke(app, ["fetch"])
    assert result.exit_code == 0, result.output
    assert (consumer / ".bench/ndb/manifest.json").exists()


# ---- json smoke: what a real lock line looks like ----------------------------------------


def test_lock_line_matches_the_ledger_shape() -> None:
    """The ndb ledger line (real shape, redacted): tag + manifest_sha256 is all bump needs."""
    line = json.loads(
        '{"tag":"ndb-v0.0.1","created":"2026-11-02T22:29:58.778252Z",'
        '"hf":{"repo":"arthrod/neurotic_docx_bench",'
        '"revision":"a753eea5a629261a917924ec3a9d4f9bd6ba814c"},'
        '"manifest_sha256":"' + A64 + '"}'
    )
    assert LedgerEntry.model_validate(line).manifest_sha256 == A64
