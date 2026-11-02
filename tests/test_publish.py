"""stage/chunk/publish/append_ledger (plan 2): the publish state machine, verified step by step."""

import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from bench_ops import config
from bench_ops.gitx import git
from bench_ops.models import HfRef, LedgerEntry
from bench_ops.publish import (
    PublishError,
    StagedFile,
    append_ledger,
    chunk,
    publish,
    stage,
)

CREATED = datetime(2026, 11, 3, tzinfo=UTC)
TAG = "ndb-v0.0.1"
HF_REPO = "arthrod/neurotic_docx_bench"


def _sh(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "core.hooksPath=/dev/null", *args],
        check=True,
        capture_output=True,
    )


def _producer(tmp_path: Path, files: dict[str, bytes] | None = None) -> Path:
    root = tmp_path / "ndb"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main", "-q"], cwd=root, check=True)
    for rel, data in (files if files is not None else {"out/a.bin": b"aaa"}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    _sh(root, "add", "-A")
    _sh(root, "commit", "-qm", "init")
    return root


def _repo(root: Path, files: tuple[str, ...] = ("out/**",), served: tuple[str, ...] = (),
          before: tuple[str, ...] = ()) -> config.Repo:
    return config.Repo(
        name="ndb",
        url=str(root),
        branch="main",
        role="producer",
        path=root,
        release=config.ReleaseSpec(
            hf_repo=HF_REPO, files=list(files), served=list(served), before=list(before)
        ),
    )


def _entry(tag: str, sha: str = "a" * 64) -> LedgerEntry:
    return LedgerEntry(
        tag=tag, created=CREATED, hf=HfRef(repo=HF_REPO, revision="b" * 40), manifest_sha256=sha
    )


# ---- stage -------------------------------------------------------------------------------


def _stage_root(tmp_path: Path) -> Path:
    root = tmp_path / "ndb"
    for rel, data in {
        "out/a.bin": b"aaa",
        "out/sub/b.bin": b"bbbb",
        ".env": b"SECRET=1",
        ".git/config": b"[core]",
        "release/index.jsonl": b"{}\n",
    }.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (tmp_path / "outside.txt").write_text("not in the repo\n")
    return root


def test_stage_returns_sorted_staged_files(tmp_path: Path) -> None:
    staged = stage(_stage_root(tmp_path), ["out/**"])
    assert [s.rel for s in staged] == ["out/a.bin", "out/sub/b.bin"]
    first = staged[0]
    root = tmp_path / "ndb"
    assert first.local == root / "out" / "a.bin"
    assert first.sha256 == hashlib.sha256(b"aaa").hexdigest()
    assert first.bytes == 3


@pytest.mark.parametrize("globs", [["out/link.bin"], ["out/**"]])
def test_stage_refuses_symlinks(tmp_path: Path, globs: list[str]) -> None:
    root = _stage_root(tmp_path)
    (root / "out" / "link.bin").symlink_to(root / "out" / "a.bin")
    with pytest.raises(PublishError):
        stage(root, globs)


def test_stage_refuses_paths_escaping_the_repo(tmp_path: Path) -> None:
    root = _stage_root(tmp_path)  # its parent holds outside.txt
    with pytest.raises(PublishError):
        stage(root, ["../*"])


def test_stage_refuses_absolute_globs(tmp_path: Path) -> None:
    with pytest.raises(PublishError):
        stage(_stage_root(tmp_path), ["/etc/*"])


@pytest.mark.parametrize("globs", [["**/*"], [".env"], [".git/*"]])
def test_stage_refuses_dot_paths(tmp_path: Path, globs: list[str]) -> None:
    with pytest.raises(PublishError):
        stage(_stage_root(tmp_path), globs)


@pytest.mark.parametrize("globs", [["release/**"], ["release/*"]])
def test_stage_refuses_the_release_tree(tmp_path: Path, globs: list[str]) -> None:
    with pytest.raises(PublishError):
        stage(_stage_root(tmp_path), globs)


def test_stage_refuses_an_empty_glob_result(tmp_path: Path) -> None:
    with pytest.raises(PublishError):
        stage(_stage_root(tmp_path), ["nothing/here/*"])


# ---- chunk -------------------------------------------------------------------------------


def _sf(n: int, size: int) -> StagedFile:
    return StagedFile(local=Path(f"/x/{n}"), rel=str(n), sha256="a" * 64, bytes=size)


def test_chunk_respects_max_files() -> None:
    chunks = chunk([_sf(i, 10) for i in range(5)], max_files=2, max_bytes=10_000)
    assert [len(c) for c in chunks] == [2, 2, 1]


def test_chunk_respects_max_bytes() -> None:
    chunks = chunk([_sf(1, 30), _sf(2, 20), _sf(3, 20), _sf(4, 45)], max_files=100, max_bytes=50)
    assert [len(c) for c in chunks] == [2, 1, 1]
    assert [f.rel for c in chunks for f in c] == ["1", "2", "3", "4"]


def test_chunk_never_returns_an_empty_chunk() -> None:
    assert chunk([]) == []
    chunks = chunk([_sf(1, 100), _sf(2, 10)], max_files=10, max_bytes=50)
    assert all(chunks)  # an oversized file still ships, alone
    assert [f.rel for c in chunks for f in c] == ["1", "2"]


# ---- publish -----------------------------------------------------------------------------


class FakeHub:
    """Stands in for the Hub protocol: records every call, keeps cumulative commit trees.

    Models what the real Hub does (N8): `exists` honours the revision, a tag cannot be
    re-created at another revision, and `revision_of` finds the commit that added a path
    (what get_paths_info(expand=True).last_commit reports).
    """

    def __init__(self, tmp: Path, manifest_on_main: bool = False) -> None:
        self.tmp = tmp
        self.calls: list[str] = []
        self.commits: dict[str, dict[str, bytes]] = {}
        self.added: dict[str, set[str]] = {}  # oid -> paths this commit wrote
        self.oids: list[str] = []
        self.tags: dict[str, str] = {}
        self.manifest_on_main = manifest_on_main
        self.corrupt: set[str] = set()
        self._n = 0

    def _tree(self, revision: str) -> dict[str, bytes]:
        tree: dict[str, bytes] = {}
        for oid in self.oids:
            tree.update(self.commits[oid])
            if oid == revision:
                break
        return tree

    def exists(self, repo: str, path: str, revision: str = "main") -> bool:
        self.calls.append(f"exists:{path}@{revision}")
        if revision != "main":
            return revision in self.oids and path in self._tree(revision)
        if self.manifest_on_main:
            return True
        return any(path in self.added[o] for o in self.oids)

    def commit(self, repo: str, files: dict[str, Path], message: str) -> str:
        self.calls.append(f"commit:{message}")
        self._n += 1
        oid = f"{self._n:040x}"
        tree = dict(self._tree(self.oids[-1])) if self.oids else {}
        tree.update({path: local.read_bytes() for path, local in files.items()})
        self.commits[oid] = tree
        self.added[oid] = set(files)
        self.oids.append(oid)
        return oid

    def revision_of(self, repo: str, path: str, revision: str = "main") -> str | None:
        self.calls.append(f"revision_of:{path}@{revision}")
        for oid in reversed(self.oids):
            if path in self.added.get(oid, ()):
                return oid
        return None

    def tag_revision(self, repo: str, tag: str) -> str | None:
        self.calls.append(f"tag_revision:{tag}")
        return self.tags.get(tag)

    def create_tag(self, repo: str, tag: str, revision: str) -> None:
        self.calls.append(f"tag:{tag}@{revision}")
        if tag in self.tags and self.tags[tag] != revision:
            raise RuntimeError(f"tag {tag} already exists at {self.tags[tag]} (immutable)")
        self.tags[tag] = revision

    def paths_info(self, repo: str, revision: str, paths: list[str]) -> dict[str, str | None]:
        self.calls.append(f"paths_info:{len(paths)}@{revision}")
        tree = self._tree(revision)
        return {
            p: ("0" * 64 if p in self.corrupt else hashlib.sha256(tree[p]).hexdigest())
            for p in paths
            if p in tree
        }

    def download(self, repo: str, revision: str, path: str) -> Path:
        self.calls.append(f"download:{path}@{revision}")
        f = self.tmp / "downloads" / Path(path).name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(self._tree(revision)[path])
        return f


def _first(hub: FakeHub, prefix: str) -> int:
    return next(i for i, call in enumerate(hub.calls) if call.startswith(prefix))


def test_publish_happy_path_order_and_ledger(tmp_path: Path) -> None:
    root = _producer(tmp_path, {"out/a.bin": b"aaa", "out/b.bin": b"bbbb"})
    hub = FakeHub(tmp_path)
    m = publish(_repo(root), TAG, hub, now=CREATED, dry_run=False)

    commit_calls = [i for i, c in enumerate(hub.calls) if c.startswith("commit:")]
    assert len(commit_calls) == 2  # the chunk, then the manifest
    assert _first(hub, "exists:") < commit_calls[0]
    assert commit_calls[0] < _first(hub, "paths_info:") < commit_calls[1]
    assert commit_calls[1] < _first(hub, "download:") < _first(hub, "tag:")

    r1, r2 = hub.oids[0], hub.oids[1]
    assert hub.tags == {TAG: r2}
    assert m.tag == TAG and m.producer == "ndb" and m.created == CREATED
    assert m.hf.repo == HF_REPO and m.hf.revision == r1  # the last chunk commit
    assert {f.path: f.sha256 for f in m.files} == {
        f"release/{TAG}/out/a.bin": hashlib.sha256(b"aaa").hexdigest(),
        f"release/{TAG}/out/b.bin": hashlib.sha256(b"bbbb").hexdigest(),
    }
    assert not any(f.served for f in m.files)

    lines = (root / "release" / "index.jsonl").read_text().splitlines()
    assert len(lines) == 1
    entry = LedgerEntry.from_line(lines[0])
    assert entry.tag == TAG and entry.hf.repo == HF_REPO
    assert entry.hf.revision == r2  # the manifest commit's oid
    manifest_bytes = hub.commits[r2][f"release/{TAG}/manifest.json"]
    assert entry.manifest_sha256 == hashlib.sha256(manifest_bytes).hexdigest()
    assert git(root, "show", "HEAD:release/index.jsonl").strip() == lines[0]
    assert git(root, "tag").split() == [TAG]


def test_publish_refuses_a_tag_already_on_the_hub(tmp_path: Path) -> None:
    root = _producer(tmp_path)
    hub = FakeHub(tmp_path, manifest_on_main=True)
    with pytest.raises(PublishError):
        publish(_repo(root), TAG, hub, now=CREATED, dry_run=False)
    assert not any(c.startswith("commit:") for c in hub.calls)
    assert not (root / "release" / "index.jsonl").exists()


def test_publish_refuses_a_tag_already_in_the_local_ledger(tmp_path: Path) -> None:
    root = _producer(tmp_path)
    ledger = root / "release" / "index.jsonl"
    ledger.parent.mkdir(exist_ok=True)
    ledger.write_text(_entry(TAG).to_line())
    hub = FakeHub(tmp_path)
    with pytest.raises(PublishError):
        publish(_repo(root), TAG, hub, now=CREATED, dry_run=False)
    assert not any(c.startswith(("commit:", "tag:")) for c in hub.calls)
    assert len(ledger.read_text().splitlines()) == 1


def test_publish_refuses_served_files_until_r2_has_a_witness(tmp_path: Path) -> None:
    root = _producer(tmp_path)
    hub = FakeHub(tmp_path)
    with pytest.raises(PublishError, match="R2"):
        publish(_repo(root, served=["site/**"]), TAG, hub, now=CREATED, dry_run=False)
    assert not any(c.startswith(("commit:", "tag:")) for c in hub.calls)


def test_publish_dry_run_makes_no_writes(tmp_path: Path) -> None:
    root = _producer(tmp_path)
    hub = FakeHub(tmp_path)
    m = publish(_repo(root), TAG, hub, now=CREATED, dry_run=True)
    assert hub.calls == []  # steps 1 (local checks only) and 2: no hub traffic at all
    assert m.hf.revision == "0" * 40
    assert m.tag == TAG and len(m.files) == 1
    assert not (root / "release").exists()
    assert git(root, "tag").strip() == ""


def test_publish_aborts_before_the_manifest_when_the_witness_fails(tmp_path: Path) -> None:
    root = _producer(tmp_path)
    hub = FakeHub(tmp_path)
    hub.corrupt.add(f"release/{TAG}/out/a.bin")
    with pytest.raises(PublishError):
        publish(_repo(root), TAG, hub, now=CREATED, dry_run=False)
    assert len([c for c in hub.calls if c.startswith("commit:")]) == 1  # the chunk only
    assert not any(c.startswith("tag:") for c in hub.calls)
    assert not any(f"release/{TAG}/manifest.json" in t for t in hub.commits.values())
    assert not (root / "release" / "index.jsonl").exists()
    assert git(root, "tag").strip() == ""


# ---- the producer's own scripts run first (rev 3) ---------------------------------------


def test_publish_runs_before_commands_in_the_producer_checkout_before_any_hub_call(
    tmp_path: Path,
) -> None:
    root = _producer(tmp_path)
    hub = FakeHub(tmp_path)
    cwds: list[Path] = []

    def run(command: str, cwd: Path) -> int:
        hub.calls.append(f"before:{command}")  # one log, so the order is provable
        cwds.append(cwd)
        return 0

    publish(
        _repo(root, before=("uv run bench report --check", "uv run bench results upload")),
        TAG, hub, now=CREATED, dry_run=False, run=run,
    )
    assert hub.calls[:2] == [
        "before:uv run bench report --check",
        "before:uv run bench results upload",
    ]
    assert hub.calls[2].startswith("exists:")  # the scripts ran before any hub call
    assert cwds == [root, root]


def test_publish_stops_at_the_first_failing_before_command(tmp_path: Path) -> None:
    root = _producer(tmp_path)
    hub = FakeHub(tmp_path)
    ran: list[str] = []

    def run(command: str, cwd: Path) -> int:
        ran.append(command)
        return 1 if command.endswith("--check") else 0

    with pytest.raises(PublishError, match="report --check"):
        publish(
            _repo(root, before=("uv run bench report --check", "uv run bench results upload")),
            TAG, hub, now=CREATED, dry_run=False, run=run,
        )
    assert ran == ["uv run bench report --check"]  # the second command never ran
    assert hub.calls == []  # nothing reached the hub
    assert not (root / "release").exists()
    assert git(root, "tag").strip() == ""


def test_publish_dry_run_prints_the_before_commands_and_runs_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _producer(tmp_path)
    hub = FakeHub(tmp_path)
    ran: list[str] = []

    def run(command: str, cwd: Path) -> int:
        ran.append(command)
        return 0

    m = publish(
        _repo(root, before=("uv run bench report --check", "uv run bench results upload")),
        TAG, hub, now=CREATED, dry_run=True, run=run,
    )
    assert ran == []  # printed, never run
    out = capsys.readouterr().out
    assert "uv run bench report --check" in out
    assert "uv run bench results upload" in out
    assert m.hf.revision == "0" * 40  # still steps 1 (local) and 2 only
    assert hub.calls == []
    assert not (root / "release").exists()


def test_run_script_runs_a_shell_command_in_cwd_and_returns_its_exit_code(
    tmp_path: Path,
) -> None:
    import shlex

    from bench_ops.publish import run_script

    marker = tmp_path / "marker.txt"
    assert run_script(f"echo hi > {shlex.quote(str(marker))}", tmp_path) == 0
    assert marker.read_text().strip() == "hi"
    assert run_script("exit 3", tmp_path) == 3  # reported, not raised: publish decides


# ---- append_ledger -----------------------------------------------------------------------


def test_append_ledger_appends_exactly_one_line(tmp_path: Path) -> None:
    ledger = tmp_path / "release" / "index.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(_entry("ndb-v1.0.0").to_line())
    append_ledger(ledger, _entry("ndb-v1.0.1", sha="b" * 64))
    lines = ledger.read_text().splitlines()
    assert len(lines) == 2
    assert LedgerEntry.from_line(lines[0]).tag == "ndb-v1.0.0"
    assert LedgerEntry.from_line(lines[1]) == _entry("ndb-v1.0.1", sha="b" * 64)


def test_append_ledger_creates_a_missing_ledger(tmp_path: Path) -> None:
    ledger = tmp_path / "release" / "index.jsonl"
    append_ledger(ledger, _entry("ndb-v1.0.0"))
    assert len(ledger.read_text().splitlines()) == 1


def test_append_ledger_refuses_a_held_lock(tmp_path: Path) -> None:
    ledger = tmp_path / "release" / "index.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(_entry("ndb-v1.0.0").to_line())
    (ledger.parent / ".publish.lock").write_text("")
    with pytest.raises(PublishError):
        append_ledger(ledger, _entry("ndb-v1.0.1", sha="b" * 64))
    assert len(ledger.read_text().splitlines()) == 1


def test_append_ledger_refuses_a_duplicate_tag(tmp_path: Path) -> None:
    ledger = tmp_path / "release" / "index.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(_entry("ndb-v1.0.0").to_line())
    with pytest.raises(PublishError):
        append_ledger(ledger, _entry("ndb-v1.0.0", sha="b" * 64))
    assert len(ledger.read_text().splitlines()) == 1
