"""Round 2 of the adversarial review (docs/plans/reviews, findings C1–C3, M1–M6, N1–N8).

One test per finding, named after it; each one was red against the code it polices.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

import bench_ops.cli
import bench_ops.publish
from bench_ops.cli import app
from bench_ops.gc import fetch_verified, verdicts
from bench_ops.gitx import clone_or_pull, git
from bench_ops.ledger import check_ledger
from bench_ops.models import FileEntry, HfRef, LedgerEntry
from bench_ops.publish import PublishError, publish, stage
from bench_ops.status import report
from tests.test_cli_plan2 import _clone, _config
from tests.test_gc import SHA_A, _entry, _hub, _manifest, _verified
from tests.test_gitx import make_origin
from tests.test_publish import CREATED, HF_REPO, TAG, FakeHub, _producer, _repo

runner = CliRunner()
BENCH_BIN = Path(sys.executable).parent
BYTES_A = b"aaa"


def _line(tag: str, sha: str = "a" * 64) -> str:
    return LedgerEntry(
        tag=tag, created=CREATED, hf=HfRef(repo=HF_REPO, revision="b" * 40), manifest_sha256=sha
    ).to_line()


def _hook_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """PATH with the venv's `bench`, so hook scripts can run the real CLI."""
    env = {k: v for k, v in os.environ.items()}
    env["PATH"] = f"{BENCH_BIN}{os.pathsep}{env.get('PATH', '')}"
    monkeypatch.setenv("PATH", env["PATH"])
    return env


def _git_commit(repo: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "attempt", *args],
        check=False, cwd=repo, capture_output=True, text=True, env=env,
    )


def _producer_repo_model(root: Path):
    from bench_ops.config import ReleaseSpec, Repo

    return Repo(
        name="ndb", url=str(root), branch="main", role="producer", path=root,
        release=ReleaseSpec(hf_repo=HF_REPO, files=["out/**"]),
    )


# ---- C1: the lefthook glob filter (ACMR) never runs the job for a staged deletion/rename ----


@pytest.mark.skipif(
    shutil.which("lefthook") is None or not (BENCH_BIN / "bench").exists(),
    reason="needs lefthook and an installed bench (the e2e hook path)",
)
def test_c1_lefthook_refuses_staged_deletion_via_name_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`git rm release/index.jsonl` (and a rename) must be refused by the real hook run.

    The scratch repo runs the shared lefthook/common.yml verbatim: lefthook builds globbed
    file lists with --diff-filter=ACMR, so a `glob:` on the ledger file skips the job exactly
    when it is needed; the ledger job must run on every commit.
    """
    repo = _clone(tmp_path)
    (repo / "release").mkdir()
    (repo / "release" / "index.jsonl").write_text(_line("ndb-v1.0.0"))
    _sh(repo, "add", "release/index.jsonl")
    _sh(repo, "commit", "-qm", "release: ndb-v1.0.0")
    common = (Path(__file__).resolve().parents[1] / "lefthook" / "common.yml").read_text()
    (repo / "lefthook.yml").write_text(common)  # the shared config, exactly as shipped
    env = _hook_env(monkeypatch)
    root = _config(tmp_path, repo)  # so the shared `release state` job resolves this repo
    env["BENCH_ROOT"] = str(root)
    monkeypatch.setenv("BENCH_ROOT", str(root))
    subprocess.run(["lefthook", "install"], cwd=repo, check=True, capture_output=True, env=env)

    _sh(repo, "rm", "-q", "release/index.jsonl")  # a staged deletion
    r = _git_commit(repo, env)
    assert r.returncode != 0, "lefthook let a staged deletion of the ledger through"
    assert "may only grow" in (r.stdout + r.stderr)  # refused by the ledger job itself
    assert json.loads(git(repo, "show", "HEAD:release/index.jsonl"))["tag"] == "ndb-v1.0.0"
    _sh(repo, "reset", "-q", "HEAD", "--", "release/index.jsonl")
    _sh(repo, "checkout", "-q", "--", "release/index.jsonl")

    _sh(repo, "mv", "release/index.jsonl", "release/ledger.jsonl")  # a staged rename
    r = _git_commit(repo, env)
    assert r.returncode != 0, "lefthook let a rename of the ledger through"
    assert "may only grow" in (r.stdout + r.stderr)
    assert json.loads(git(repo, "show", "HEAD:release/index.jsonl"))["tag"] == "ndb-v1.0.0"
    _sh(repo, "reset", "-q", "HEAD")
    _sh(repo, "checkout", "-q", "--", ".")

    (repo / "release" / "index.jsonl").write_text(
        _line("ndb-v1.0.0") + _line("ndb-v1.0.1", sha="b" * 64)
    )
    _sh(repo, "add", "release/index.jsonl")
    r = _git_commit(repo, env)  # a pure append still commits through the same config
    assert r.returncode == 0, r.stderr
    assert len(git(repo, "show", "HEAD:release/index.jsonl").splitlines()) == 2


# ---- C2: a path-limited commit commits the working tree, not the index the hook reads -------


@pytest.mark.skipif(
    not (BENCH_BIN / "bench").exists(), reason="needs an installed bench (a real git commit)"
)
def test_c2_hook_reads_the_index_the_partial_commit_commits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`git commit -- release/index.jsonl` commits the WORKING TREE bytes; the hook must read
    the temporary index git exports as GIT_INDEX_FILE, not .git/index."""
    repo = _clone(tmp_path)
    (repo / "release").mkdir()
    (repo / "release" / "index.jsonl").write_text(_line("ndb-v1.0.0"))
    _sh(repo, "add", "release/index.jsonl")
    _sh(repo, "commit", "-qm", "release: ndb-v1.0.0")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexec bench hook ledger\n")
    hook.chmod(0o755)
    env = _hook_env(monkeypatch)

    # nothing staged; the working tree rewrites the line: the committed bytes would change
    (repo / "release" / "index.jsonl").write_text(_line("ndb-v1.0.0", sha="c" * 64))
    r = _git_commit(repo, env, "--", "release/index.jsonl")
    assert r.returncode != 0, "the hook validated .git/index while git committed the worktree"
    assert json.loads(git(repo, "show", "HEAD:release/index.jsonl"))["manifest_sha256"] == "a" * 64

    # and a genuine append through the same path-limited form still passes
    (repo / "release" / "index.jsonl").write_text(
        _line("ndb-v1.0.0") + _line("ndb-v1.0.1", sha="b" * 64)
    )
    r = _git_commit(repo, env, "--", "release/index.jsonl")
    assert r.returncode == 0, r.stderr


# ---- C3: publish is resumable and idempotent; a published release is never overwritten ------


def test_c3_publish_resumes_and_finishes_after_a_failure_past_the_hub_tag(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A publish that dies after the immutable hub tag (here: the ledger lock is held) must
    finish on a rerun: adopt the hub release, then create the missing records."""
    root = _producer(tmp_path, {"out/a.bin": BYTES_A})
    hub = FakeHub(tmp_path)
    lock = root / "release" / ".publish.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("")
    with pytest.raises(PublishError, match="another publish"):
        publish(_repo(root), TAG, hub, now=CREATED, dry_run=False)
    assert hub.tags and not (root / "release" / "index.jsonl").exists()  # orphaned on the hub
    commits_before = [c for c in hub.calls if c.startswith("commit:")]

    lock.unlink()
    m = publish(_repo(root), TAG, hub, now=CREATED, dry_run=False)  # resume
    assert [c for c in hub.calls if c.startswith("commit:")] == commits_before  # nothing re-uploaded
    out = capsys.readouterr().out  # every step says what this run did with it
    assert "manifest: done" in out and "hugging face tag: done" in out
    assert "ledger: did" in out and "git commit: did" in out and "git tag: did" in out
    m = publish(_repo(root), TAG, hub, now=CREATED, dry_run=False)  # idempotent
    out = capsys.readouterr().out
    assert out.count(": done") == 6 and ": did" not in out  # a rerun shows only progress
    assert m.hf.revision == hub.oids[0]
    entry = LedgerEntry.from_line((root / "release" / "index.jsonl").read_text().splitlines()[-1])
    assert entry.tag == TAG and entry.hf.revision == hub.oids[1]
    assert git(root, "tag").split() == [TAG]
    assert git(root, "show", "HEAD:release/index.jsonl") == (
        root / "release" / "index.jsonl"
    ).read_text()


def test_c3_publish_completes_missing_git_commit_and_tag(tmp_path: Path) -> None:
    """A rerun after the git side was lost (tag deleted, release commit reset) creates what
    is missing and touches nothing on the hub."""
    root = _producer(tmp_path, {"out/a.bin": BYTES_A})
    hub = FakeHub(tmp_path)
    publish(_repo(root), TAG, hub, now=CREATED, dry_run=False)
    ledger_text = (root / "release" / "index.jsonl").read_text()
    manifest_bytes = (root / "release" / TAG / "manifest.json").read_bytes()
    _sh(root, "tag", "-d", TAG)
    _sh(root, "reset", "-q", "HEAD~1")  # undo the release commit, keep the working tree
    assert not git(root, "tag").strip()

    publish(_repo(root), TAG, hub, now=CREATED, dry_run=False)  # resume
    assert git(root, "tag").split() == [TAG]
    assert git(root, "show", "HEAD:release/index.jsonl") == ledger_text
    assert (root / "release" / TAG / "manifest.json").read_bytes() == manifest_bytes
    assert git(root, "show", f"HEAD:release/{TAG}/manifest.json").encode() == manifest_bytes


def test_c3_publish_refuses_to_adopt_a_release_that_differs_from_the_tree(tmp_path: Path) -> None:
    root = _producer(tmp_path, {"out/a.bin": BYTES_A})
    hub = FakeHub(tmp_path)
    publish(_repo(root), TAG, hub, now=CREATED, dry_run=False)
    tags_before, ledger_before = dict(hub.tags), (root / "release" / "index.jsonl").read_text()
    (root / "out" / "a.bin").write_bytes(b"CHANGED")  # the tree moved on after the upload
    with pytest.raises(PublishError, match="does not match|never overwrit"):
        publish(_repo(root), TAG, hub, now=CREATED, dry_run=False)
    assert hub.tags == tags_before
    assert (root / "release" / "index.jsonl").read_text() == ledger_before
    assert git(root, "tag").split() == [TAG]


def test_c3_publish_refuses_a_git_ignored_release_tree(tmp_path: Path) -> None:
    """`git add release/index.jsonl` fails when release/ is ignored; refuse before uploading."""
    root = _producer(tmp_path, {"out/a.bin": BYTES_A})
    (root / ".gitignore").write_text("release/\n")
    _sh(root, "add", ".gitignore")
    _sh(root, "commit", "-qm", "ignore release")
    hub = FakeHub(tmp_path)
    with pytest.raises(PublishError, match="git-ignored"):
        publish(_repo(root), TAG, hub, now=CREATED, dry_run=False)
    assert hub.calls == []  # refused before any hub traffic


# ---- M1: gc deletes the bytes it verified, not whatever is at the path at delete time -------


def test_m1_gc_refuses_to_unlink_a_file_that_changed_after_hashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    f = out / "a.bin"
    f.write_bytes(BYTES_A)
    m = _manifest(FileEntry(path=f"release/{TAG}/out/a.bin", sha256=SHA_A, bytes=3))
    v = verdicts([f], [_verified(m)], set())[0]
    assert v.deletable and v.current()

    f.write_bytes(b"NEW-BYTES-NOT-VERIFIED")  # a concurrent run rewrites it after hashing
    assert not v.current()

    f.write_bytes(BYTES_A)
    stale = verdicts([f], [_verified(m)], set())[0]  # hashed, then swapped again
    f.write_bytes(b"NEW-BYTES-NOT-VERIFIED")
    monkeypatch.setattr(bench_ops.cli, "verdicts", lambda *a, **k: [stale])
    root = _config(tmp_path, tmp_path / "producer")  # any linked repo: verdicts is patched
    monkeypatch.setenv("BENCH_ROOT", str(root))
    result = runner.invoke(app, ["gc", str(f), "--yes"])
    assert result.exit_code == 0, result.output
    assert "changed since hashing" in result.output
    assert f.exists() and f.read_bytes() == b"NEW-BYTES-NOT-VERIFIED"  # not the verified bytes


# ---- M2: the .git protection is a containment check on the resolved path -------------------


def test_m2_gc_resolves_paths_before_the_git_check(tmp_path: Path) -> None:
    root = _producer(tmp_path, {"out/a.bin": BYTES_A})
    obj = root / ".git" / "objects" / "ab" / "cdef1234"
    obj.parent.mkdir(parents=True)
    obj.write_bytes(BYTES_A)  # same sha as the verified out/a.bin
    (root / "out" / "link").symlink_to(root / ".git", target_is_directory=True)
    escape = root / "out" / "link" / "objects" / "ab" / "cdef1234"
    outside = tmp_path / "elsewhere" / "a.bin"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(BYTES_A)
    m = _manifest(FileEntry(path=f"release/{TAG}/out/a.bin", sha256=SHA_A, bytes=3))
    vs = {
        v.path: v
        for v in verdicts([escape, outside, root / "out" / "a.bin"], [_verified(m)], set(),
                          roots=[root])
    }
    assert not vs[escape].deletable and ".git" in vs[escape].reason
    assert not vs[outside].deletable and "outside" in vs[outside].reason
    assert vs[root / "out" / "a.bin"].deletable  # control
    assert obj.exists()  # the git object survived


# ---- M3: empty, tracked and fresher-than-the-manifest files are never collected -------------


def test_m3_gc_refuses_empty_tracked_and_fresh_candidates(tmp_path: Path) -> None:
    from bench_ops.gc import tracked_in

    root = _producer(tmp_path, {"out/a.bin": BYTES_A})
    ledger = root / "release" / "index.jsonl"
    ledger.parent.mkdir(exist_ok=True)
    ledger_bytes = _line("ndb-v1.0.0").encode()
    ledger.write_bytes(ledger_bytes)
    _sh(root, "add", "release/index.jsonl")
    _sh(root, "commit", "-qm", "ledger")
    empty = root / "out" / "empty.bin"
    empty.write_bytes(b"")
    fresh = root / "out" / "fresh.bin"
    fresh.write_bytes(BYTES_A)
    plain = root / "out" / "plain.bin"  # untracked (out/a.bin was committed by the fixture)
    plain.write_bytes(BYTES_A)
    os.utime(plain, (datetime(2026, 1, 1, tzinfo=UTC).timestamp(),) * 2)
    os.utime(fresh, (datetime(2027, 6, 1, tzinfo=UTC).timestamp(),) * 2)
    m = _manifest(
        FileEntry(path=f"release/{TAG}/out/a.bin", sha256=SHA_A, bytes=3),
        FileEntry(path=f"release/{TAG}/out/empty.bin",
                  sha256=hashlib.sha256(b"").hexdigest(), bytes=0),
        FileEntry(path="release/index.jsonl",
                  sha256=hashlib.sha256(ledger_bytes).hexdigest(), bytes=len(ledger_bytes)),
    )
    vs = {
        v.path: v
        for v in verdicts([empty, fresh, plain, ledger], [_verified(m)], set(),
                          roots=[root], tracked=tracked_in([root]))
    }
    assert not vs[empty].deletable and "0 bytes" in vs[empty].reason
    assert not vs[fresh].deletable and "newer than" in vs[fresh].reason
    assert not vs[ledger].deletable and "tracked" in vs[ledger].reason
    assert vs[plain].deletable  # control: listed, untracked, older than the manifest
    assert ledger.exists()


# ---- M4: a torn ledger line is BROKEN, never a traceback ------------------------------------


def test_m4_status_reports_a_torn_last_ledger_line_as_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = _line("ndb-v1.0.0") + '{"tag": "ndb-v1.0.1", "created": "2026-11-0'
    assert LedgerEntry.last(text).tag == "ndb-v1.0.0"  # tolerant of the torn last line

    from bench_ops.config import Repo
    from bench_ops.hf import Witness

    origin = make_origin(tmp_path)
    repo_path = tmp_path / "ndb"
    clone_or_pull(str(origin), repo_path, "main")
    (repo_path / "release").mkdir()
    (repo_path / "release" / "index.jsonl").write_text(text)
    repo = Repo(name="ndb", url=str(origin), branch="main", role="producer", path=repo_path)
    rep = report(repo, latest={}, witness=lambda e: Witness())
    assert rep.broken and rep.witness.startswith("torn ledger")

    root = tmp_path / "root"
    root.mkdir()
    (root / "repos.toml").write_text(
        f'[[repos]]\nname = "ndb"\nurl = "x"\nrole = "producer"\npath = "{repo_path}"\n'
    )
    monkeypatch.setenv("BENCH_ROOT", str(root))
    result = runner.invoke(app, ["status", "--offline", "--fail-on-stale"])
    assert result.exit_code == 1
    assert "BROKEN" in result.output and "torn ledger" in result.output
    assert "Traceback" not in result.output


# ---- M5: a missing manifest on HF is BROKEN, not a RemoteEntryNotFoundError crash -----------


class _MissingHub:
    def download(self, repo: str, revision: str, path: str) -> Path:
        raise RuntimeError("404 Client Error: Entry Not Found for url:\nhttps://huggingface.co/…")

    def paths_info(self, repo: str, revision: str, paths: list[str]) -> dict[str, str | None]:
        raise AssertionError("unreachable")


def test_m5_status_shows_broken_when_the_manifest_is_missing_on_hf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _clone(tmp_path)
    (repo / "release").mkdir()
    (repo / "release" / "index.jsonl").write_text(_line("ndb-v1.0.0"))
    _sh(repo, "add", "release/index.jsonl")
    _sh(repo, "commit", "-qm", "release: ndb-v1.0.0")
    root = _config(tmp_path, repo)
    monkeypatch.setenv("BENCH_ROOT", str(root))
    monkeypatch.setattr(bench_ops.cli, "HfHub", _MissingHub)
    result = runner.invoke(app, ["status", "--fail-on-stale"])
    assert result.exit_code == 1
    assert "ndb" in result.output  # the table was printed
    assert "BROKEN" in result.output and "404" in result.output
    assert "Traceback" not in result.output


# ---- M6: publish failures are PublishErrors the CLI renders as one line ---------------------


def test_m6_publish_failures_are_one_line_errors_never_tracebacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = tmp_path / "plain" / "ndb"  # a producer checkout that is NOT a git repo
    plain.parent.mkdir(parents=True)
    (plain / "out").mkdir(parents=True)
    (plain / "out" / "a.bin").write_bytes(BYTES_A)
    with pytest.raises(PublishError, match="git"):
        publish(_producer_repo_model(plain), TAG, FakeHub(tmp_path / "plain"),
                now=CREATED, dry_run=False)

    (tmp_path / "ok").mkdir()  # _producer creates <tmp>/ndb without parents=True
    repo = _producer(tmp_path / "ok", {"out/a.bin": BYTES_A})
    release = f'\n[repos.release]\nhf_repo = "{HF_REPO}"\nfiles = ["out/**"]\n'
    root = _config(tmp_path / "ok", repo, release)
    monkeypatch.setenv("BENCH_ROOT", str(root))
    result = runner.invoke(app, ["publish", "ndb", "NOT-A-TAG"])
    assert result.exit_code == 1
    assert "must match" in result.output
    assert "ValidationError" not in result.output and "Traceback" not in result.output


# ---- N1: stage never blocks on a FIFO -------------------------------------------------------


def test_n1_stage_skips_non_regular_files(tmp_path: Path) -> None:
    root = tmp_path / "ndb"
    (root / "out").mkdir(parents=True)
    (root / "out" / "a.bin").write_bytes(BYTES_A)
    os.mkfifo(root / "out" / "fifo")
    outcome: dict[str, object] = {}

    def run() -> None:
        outcome["staged"] = stage(root, ["out/**"])

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(10)
    assert not t.is_alive(), "stage() blocked on a FIFO"
    assert [f.rel for f in outcome["staged"]] == ["out/a.bin"]  # type: ignore[union-attr]


# ---- N2: check_ledger enforces the byte prefix it promises ----------------------------------


def test_n2_check_ledger_enforces_the_byte_prefix() -> None:
    l1, l2 = _line("ndb-v1.0.0"), _line("ndb-v1.0.1", sha="b" * 64)
    assert check_ledger(l1, l1 + l2) == []  # a pure append is still fine
    assert check_ledger(l1, l1.replace("\n", "\r\n")) != []  # a CRLF rewrite is a change
    assert check_ledger(l1, l1.replace("}\n", "}\r\n") + l2) != []  # a hidden CR too
    assert any("torn append" in p for p in check_ledger(l1, l1 + l2.rstrip("\n")))


# ---- N3: fetch_verified hashes the bytes it parses ------------------------------------------


def test_n3_fetch_verified_hashes_the_bytes_it_parses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import bench_ops.gc

    m = _manifest(FileEntry(path=f"release/{TAG}/out/a.bin", sha256=SHA_A, bytes=3))
    entry = _entry(m)
    hub = _hub(tmp_path, m)

    def second_read_rewrites(path: Path) -> str:  # the cache file changes between two reads
        path.write_bytes(b"tampered")
        return hashlib.sha256(b"tampered").hexdigest()

    monkeypatch.setattr(bench_ops.gc, "sha256_of", second_read_rewrites, raising=False)
    assert fetch_verified(entry, hub) is not None  # the parsed bytes were the checked bytes


# ---- N4: the published manifest lands in the producer tree (gc's blocklist source) ----------


def test_n4_publish_writes_the_manifest_into_the_producer_tree(tmp_path: Path) -> None:
    root = _producer(tmp_path, {"out/a.bin": BYTES_A})
    hub = FakeHub(tmp_path)
    publish(_repo(root), TAG, hub, now=CREATED, dry_run=False)
    local = root / "release" / TAG / "manifest.json"
    hub_bytes = hub.commits[hub.oids[1]][f"release/{TAG}/manifest.json"]
    assert local.read_bytes() == hub_bytes  # what gc's unverified blocklist reads
    assert git(root, "show", f"HEAD:release/{TAG}/manifest.json").encode() == hub_bytes


# ---- N5: the committed ledger is the line append_ledger wrote -------------------------------


def test_n5_publish_asserts_the_committed_ledger_is_the_appended_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _producer(tmp_path, {"out/a.bin": BYTES_A})
    hub = FakeHub(tmp_path)
    real_git = bench_ops.publish.git

    def tamper_between_add_and_commit(repo: Path, *args: str) -> str:
        if args[:1] == ("commit",):  # the ledger is rewritten after `git add`, before commit
            (repo / "release" / "index.jsonl").write_text(_line("ndb-v9.9.9"))
        return real_git(repo, *args)

    monkeypatch.setattr(bench_ops.publish, "git", tamper_between_add_and_commit)
    with pytest.raises(PublishError, match="committed"):
        publish(_repo(root), TAG, hub, now=CREATED, dry_run=False)


# ---- N6: the tag is validated before it reaches a hub URL -----------------------------------


def test_n6_publish_validates_the_tag_before_any_hub_call(tmp_path: Path) -> None:
    root = _producer(tmp_path)
    hub = FakeHub(tmp_path)
    with pytest.raises(PublishError, match="must match"):
        publish(_repo(root), "../../x", hub, now=CREATED, dry_run=False)
    assert hub.calls == []


# ---- N7: skipped paths are reported; status prints broken AND stale -------------------------


def test_n7_gc_reports_skipped_paths_and_status_prints_broken_and_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _clone(tmp_path)
    d = repo / "out"
    d.mkdir()
    root = _config(tmp_path, repo)
    monkeypatch.setenv("BENCH_ROOT", str(root))
    result = runner.invoke(app, ["gc", str(d)])
    assert "not a regular file" in result.output  # a directory is reported, not silent

    (repo / "release").mkdir()
    (repo / "release" / "index.jsonl").write_text(_line("ndb-v1.0.1"))
    _sh(repo, "add", "release/index.jsonl")
    _sh(repo, "commit", "-qm", "release")
    consumer = tmp_path / "jubarte-app"
    clone_or_pull(str(make_origin(tmp_path / "c")), consumer, "main")
    (consumer / "bench.lock").write_text("ndb=ndb-v1.0.0\n")
    (root / "repos.toml").write_text(
        f'[[repos]]\nname = "ndb"\nurl = "x"\nrole = "producer"\npath = "{repo}"\n'
        f'[[repos]]\nname = "jubarte-app"\nurl = "x"\nrole = "consumer"\npath = "{consumer}"\n'
    )

    def boom(entry: LedgerEntry) -> None:
        raise RuntimeError("network gone")

    monkeypatch.setattr(bench_ops.cli, "_witness_via_hf", boom)
    result = runner.invoke(app, ["status", "--fail-on-stale"])
    assert result.exit_code == 1
    assert "BROKEN" in result.output and "network gone" in result.output
    assert "behind its producer" in result.output  # the stale consumer is still named


# ---- N8: the gc --yes delete path, against the real ledger ----------------------------------


def test_n8_gc_cli_delete_path_deletes_verified_files_and_spares_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bench_ops.gc import VerifiedManifest

    repo = _clone(tmp_path)
    ledger = repo / "release" / "index.jsonl"
    ledger.parent.mkdir(exist_ok=True)
    ledger_bytes = _line("ndb-v1.0.0").encode()
    ledger.write_bytes(ledger_bytes)
    _sh(repo, "add", "release/index.jsonl")
    _sh(repo, "commit", "-qm", "ledger")
    target = repo / "out" / "x.bin"
    target.parent.mkdir(parents=True)
    target.write_bytes(BYTES_A)
    old = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    os.utime(target, (old, old))
    m = _manifest(
        FileEntry(path=f"release/{TAG}/out/a.bin", sha256=SHA_A, bytes=3),
        FileEntry(path="release/index.jsonl",
                  sha256=hashlib.sha256(ledger_bytes).hexdigest(), bytes=len(ledger_bytes)),
    )
    vm = VerifiedManifest(entry=_entry(m), manifest=m)
    monkeypatch.setattr(bench_ops.cli, "fetch_verified", lambda entry, hub: vm)
    monkeypatch.setattr(bench_ops.cli, "HfHub", object)
    root = _config(tmp_path, repo)
    monkeypatch.setenv("BENCH_ROOT", str(root))

    result = runner.invoke(app, ["gc", str(target), str(ledger), "--yes"])
    assert result.exit_code == 0, result.output
    assert not target.exists() and "deleted" in result.output  # the verified bytes went
    assert ledger.exists()  # the git-tracked ledger never does
    assert "tracked" in result.output


def _sh(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "core.hooksPath=/dev/null", *args],
        check=True, capture_output=True,
    )
