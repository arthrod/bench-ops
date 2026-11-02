"""Round 3 of the adversarial review (plan 6, findings C1, H1–H2, M1–M7, L1–L7).

One test per finding, named after it. Every repro is offline: fake hubs and throwaway git
repos under tmp_path; no call ever leaves the machine.
"""

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bench_ops.archive import ARCHIVE_REPO, archive
from bench_ops.cli import app
from bench_ops.config import Repo
from bench_ops.models import HfRef, LedgerEntry
from bench_ops.publish import PublishError, publish, stage
from bench_ops.work import scan
from tests.test_archive import _age_files, _ServingHub
from tests.test_publish import CREATED, FakeHub
from tests.test_publish import _producer as _ndb_producer
from tests.test_publish import _repo as _ndb_repo

runner = CliRunner()
NOW = datetime.now(UTC)  # the CLI computes its own clock, so the fixtures anchor to it
UTC_OLD = NOW - timedelta(days=40)  # older than any 14d TTL
TTL = timedelta(days=14)
NAME = "ndb-105"
TAG = f"arc-{NAME}"
PREFIX = f"archive/{NAME}/"


def sh(repo: Path, *args: str, when: datetime | None = None) -> None:
    env = {**os.environ}
    if when is not None:
        stamp = when.strftime("%Y-%m-%d %H:%M:%S +0000")
        env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = stamp
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "core.hooksPath=/dev/null", *args],
        check=True, capture_output=True, env=env,
    )


def workshop(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """(origin, main clone, bench root, .work pen): one linked ndb repo and its pen."""
    src = tmp_path / "src"
    src.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", "main", "-q"], cwd=src, check=True)
    (src / "README").write_text("x\n")
    sh(src, "add", "-A")
    sh(src, "commit", "-qm", "init", when=UTC_OLD)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(origin)], check=True)
    main = tmp_path / "main"
    subprocess.run(["git", "clone", "-q", str(origin), str(main)], check=True)
    root = tmp_path / "root"
    (root / ".work").mkdir(parents=True)
    (root / "repos.toml").write_text(
        f'[[repos]]\nname = "ndb"\nurl = "{origin}"\nbranch = "main"\n'
        f'role = "producer"\npath = "{main}"\n'
    )
    return origin, main, root, root / ".work"


def _repo(main: Path, origin: Path) -> Repo:
    return Repo(name="ndb", url=str(origin), branch="main", role="producer", path=main)


def _age_creation(main: Path, name: str, when: datetime) -> None:
    """Backdate a worktree's creation clock: its admin gitdir file under .git/worktrees."""
    for entry in sorted((main / ".git" / "worktrees").iterdir()):
        gitdir = entry / "gitdir"
        if gitdir.is_file() and gitdir.read_text().strip().endswith(f"{name}/.git"):
            os.utime(gitdir, (when.timestamp(), when.timestamp()))


def _infos(work: Path, main: Path, origin: Path):
    return {i.path.name: i for i in scan(work, [_repo(main, origin)], ttl=TTL, now=NOW)}


def satellite(tmp_path: Path, name: str) -> Path:
    """A dirty git satellite: one committed file, one modification, one untracked file."""
    sat = tmp_path / name
    sat.mkdir()
    subprocess.run(["git", "init", "-b", "main", "-q"], cwd=sat, check=True)
    (sat / "tracked.txt").write_text("committed\n")
    sh(sat, "add", "-A")
    sh(sat, "commit", "-qm", "init")
    (sat / "tracked.txt").write_text("modified\n")
    (sat / "untracked.bin").write_bytes(b"untracked-bytes")
    _age_files(sat)
    return sat


def bench_root(tmp_path: Path) -> Path:
    root = tmp_path / "broot"
    root.mkdir(exist_ok=True)
    (root / "repos.toml").write_text("# no linked repos; the archive ledger is the witness\n")
    return root


def ledger_entry(bench: Path) -> LedgerEntry:
    lines = [ln for ln in (bench / "archive" / "index.jsonl").read_text().splitlines() if ln]
    assert len(lines) == 1
    return LedgerEntry.from_line(lines[0])


def _archived(tmp_path: Path, sat: Path) -> tuple[Path, FakeHub, object]:
    """(bench root, recording hub, serving hub) for a satellite archived for real."""
    bench = bench_root(tmp_path)
    hub = FakeHub(tmp_path)
    m = archive(sat, NAME, hub, now=CREATED, yes=True, bench_root=bench)
    entry = ledger_entry(bench)
    serving = _ServingHub(
        tmp_path, hub.commits[entry.hf.revision][f"{PREFIX}manifest.json"],
        {f.path: f.sha256 for f in m.files},
    )
    return bench, hub, serving


# ---- C1: bench work must never delete bytes that exist nowhere else ------------------------


def test_c1_work_removes_only_worktrees_with_no_bytes_to_lose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin, main, root, work = workshop(tmp_path)
    monkeypatch.setenv("BENCH_ROOT", str(root))

    # a worktree whose only content is git-ignored (a half-finished run and a .env)
    ignored = work / "ndb-run-105"
    sh(main, "worktree", "add", "-b", "feat", str(ignored), when=UTC_OLD)
    (ignored / ".gitignore").write_text("out/\n.env\n")
    sh(ignored, "add", "-A")
    sh(ignored, "commit", "-qm", "gitignore", when=UTC_OLD)
    sh(main, "push", "-q", "origin", "feat")
    _age_creation(main, "ndb-run-105", UTC_OLD)
    (ignored / "out").mkdir()
    (ignored / "out" / "run.log").write_text("half a run\n")
    (ignored / ".env").write_text("TOKEN=1\n")

    # an old, pushed worktree with one untracked file
    untracked = work / "ndb-run-untracked"
    sh(main, "worktree", "add", "-b", "unt", str(untracked), when=UTC_OLD)
    (untracked / "u.txt").write_text("u\n")
    sh(untracked, "add", "-A")
    sh(untracked, "commit", "-qm", "u", when=UTC_OLD)
    sh(main, "push", "-q", "origin", "unt")
    _age_creation(main, "ndb-run-untracked", UTC_OLD)
    (untracked / "scratch.txt").write_text("untracked\n")

    infos = _infos(work, main, origin)
    assert "ignored files 2" in infos["ndb-run-105"].reasons
    assert not infos["ndb-run-105"].removable
    assert "untracked 1" in infos["ndb-run-untracked"].reasons

    result = runner.invoke(app, ["work", "--yes"])
    assert result.exit_code == 0, result.output
    assert (ignored / "out" / "run.log").exists()  # ignored bytes survive the drain
    assert (ignored / ".env").exists()
    assert untracked.exists()

    # the TTL is the worktree's own age, not the branch's: created one second ago,
    # on an old pushed branch, it is young and stays
    fresh = work / "ndb-run-fresh"
    sh(main, "worktree", "add", "-b", "fresh", str(fresh), when=UTC_OLD)
    (fresh / "f.txt").write_text("f\n")
    sh(fresh, "add", "-A")
    sh(fresh, "commit", "-qm", "f", when=UTC_OLD)
    sh(main, "push", "-q", "origin", "fresh")
    infos = _infos(work, main, origin)
    assert "younger than 14d" in infos["ndb-run-fresh"].reasons
    assert not infos["ndb-run-fresh"].removable
    result = runner.invoke(app, ["work", "--yes"])
    assert result.exit_code == 0, result.output
    assert fresh.exists()

    # once BOTH clocks (creation and last commit) are old, it is a candidate again
    _age_creation(main, "ndb-run-fresh", UTC_OLD)
    infos = _infos(work, main, origin)
    assert infos["ndb-run-fresh"].removable
    result = runner.invoke(app, ["work", "--yes"])
    assert result.exit_code == 0, result.output
    assert not fresh.exists()


# ---- H1: bench archive is resumable ---------------------------------------------------------


class _Tag503(FakeHub):
    """A hub whose tag endpoint is down: everything before the tag succeeds."""

    def __init__(self, tmp: Path) -> None:
        super().__init__(tmp)
        self.fail_tags = True

    def create_tag(self, repo: str, tag: str, revision: str) -> None:
        if self.fail_tags:
            raise RuntimeError("503 Service Unavailable")
        super().create_tag(repo, tag, revision)


def test_h1_archive_resumes_after_a_failure_past_the_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sat = satellite(tmp_path, NAME)
    hub = _Tag503(tmp_path)
    bench = bench_root(tmp_path)

    with pytest.raises(RuntimeError, match="503"):
        archive(sat, NAME, hub, now=CREATED, yes=True, bench_root=bench)
    assert not (bench / "archive" / "index.jsonl").exists()  # the record is half-done
    commits_before = [c for c in hub.calls if c.startswith("commit:")]

    hub.fail_tags = False
    archive(sat, NAME, hub, now=CREATED, yes=True, bench_root=bench)  # resume
    out = capsys.readouterr().out
    assert "adopted" in out
    assert [c for c in hub.calls if c.startswith("commit:")] == commits_before  # nothing re-uploaded
    assert hub.tags == {TAG: ledger_entry(bench).hf.revision}
    assert (bench / "archive" / NAME / "manifest.json").exists()  # L2's local copy, finished too


# ---- H2: visibility is checked, secrets are scanned -----------------------------------------


def test_h2_archive_and_publish_refuse_the_wrong_visibility_and_archive_scans_secrets(
    tmp_path: Path,
) -> None:
    # a public archive dataset is a hard refusal, not an assumption
    sat = satellite(tmp_path, NAME)
    hub = FakeHub(tmp_path)
    hub.repo_private = False
    with pytest.raises(PublishError) as e:
        archive(sat, NAME, hub, now=CREATED, yes=True, bench_root=bench_root(tmp_path))
    assert ARCHIVE_REPO in str(e.value) and "public" in str(e.value)
    assert not any(c.startswith("commit:") for c in hub.calls)

    # a secret in the staged tree is refused, and named (assembled here so this file
    # never itself holds a ripsecrets-detectable string)
    sat2 = satellite(tmp_path, "ndb-secrets")
    (sat2 / ".env").write_text("aws_access_key_id = " + "AKIA" + "IOSFODNN7EXAMPLE\n")
    _age_files(sat2)
    hub2 = FakeHub(tmp_path)
    with pytest.raises(PublishError) as e2:
        archive(sat2, NAME, hub2, now=CREATED, yes=True, bench_root=bench_root(tmp_path))
    assert ".env" in str(e2.value)
    assert not any(c.startswith("commit:") for c in hub2.calls)

    # --exclude drops the path from the stage and is recorded in the manifest
    m = archive(sat2, NAME, hub2, now=CREATED, yes=True, bench_root=bench_root(tmp_path),
                excludes=(".env",))
    assert f"{PREFIX}.env" not in {f.path for f in m.files}
    assert m.excludes == [".env"]

    # publish refuses a dataset whose visibility differs from what repos.toml says
    root = _ndb_producer(tmp_path)
    pub_hub = FakeHub(tmp_path)
    pub_hub.repo_private = False  # the spec (default) says the dataset must be private
    with pytest.raises(PublishError, match="public"):
        publish(_ndb_repo(root), "ndb-v0.0.1", pub_hub, now=CREATED, dry_run=False)
    assert not any(c.startswith("commit:") for c in pub_hub.calls)


# ---- M1: every files glob must match at least one file ---------------------------------------


def test_m1_every_files_glob_must_match_at_least_one_file(tmp_path: Path) -> None:
    root = tmp_path / "et"
    (root / "reports").mkdir(parents=True)
    (root / "reports" / "alpha.md").write_text("# alpha\n")

    with pytest.raises(PublishError, match="out/facts.json"):
        stage(root, ["out/facts.json", "reports/*.md"])  # a tagged release without the facts

    staged = stage(root, ["?out/facts.json", "reports/*.md"])  # "?" marks the glob optional
    assert [s.rel for s in staged] == ["reports/alpha.md"]


# ---- M2: gc's satellite rule is provenance, not content ---------------------------------------


def test_m2_gc_deletes_only_what_an_archive_of_the_same_tree_lists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept_sat = satellite(tmp_path, "ndb-apryse")
    gone_sat = satellite(tmp_path, NAME)
    (gone_sat / "dup.bin").write_bytes(b"byte-identical")
    _age_files(gone_sat)
    bench, _hub, serving = _archived(tmp_path, gone_sat)
    (kept_sat / "dup.bin").write_bytes(b"byte-identical")  # same bytes, never archived
    _age_files(kept_sat)
    monkeypatch.setenv("BENCH_ROOT", str(bench))
    monkeypatch.setattr("bench_ops.cli.HfHub", lambda: serving)

    result = runner.invoke(app, ["gc", str(kept_sat / "dup.bin"), "--yes"])
    assert result.exit_code == 0, result.output
    assert (kept_sat / "dup.bin").exists()  # bytes elsewhere are not a witness for this tree

    result = runner.invoke(app, ["gc", str(gone_sat / "dup.bin"), "--yes"])
    assert result.exit_code == 0, result.output
    assert not (gone_sat / "dup.bin").exists()  # the archived tree is collected


# ---- M3: the .git protection is structural ---------------------------------------------------


def test_m3_gc_spares_git_directories_however_they_are_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sat = tmp_path / "ndb-meta"
    sat.mkdir()
    subprocess.run(
        ["git", "init", "-q", "-b", "main", "--separate-git-dir", str(sat / "meta"), str(sat)],
        check=True,
    )
    assert (sat / ".git").is_file()  # a pointer: gitdir: <sat>/meta
    (sat / "hello.txt").write_text("hello\n")
    sh(sat, "add", "-A")
    sh(sat, "commit", "-qm", "init")
    _age_files(sat)

    bench, _hub, serving = _archived(tmp_path, sat)
    monkeypatch.setenv("BENCH_ROOT", str(bench))
    monkeypatch.setattr("bench_ops.cli.HfHub", lambda: serving)

    result = runner.invoke(app, ["gc", str(sat), "--yes"])
    assert result.exit_code == 0, result.output
    assert not (sat / "hello.txt").exists()  # the archived working tree went
    assert (sat / ".git").exists()  # the pointer stays
    assert (sat / "meta" / "HEAD").exists()  # the git dir stays, whatever it is called
    assert any((sat / "meta" / "objects").rglob("*"))  # history survives


# ---- M4: the append-only hook guards the archive ledger too ----------------------------------


def test_m4_the_append_only_hook_guards_the_archive_ledger_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "bench-ops"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", "-q"], cwd=repo, check=True)
    (repo / "archive").mkdir()
    (repo / "archive" / "index.jsonl").write_text(
        LedgerEntry(
            tag=TAG, created=CREATED, hf=HfRef(repo=ARCHIVE_REPO, revision="b" * 40),
            manifest_sha256="a" * 64,
        ).to_line()
    )
    sh(repo, "add", "-A")
    sh(repo, "commit", "-qm", "archive")
    sh(repo, "rm", "-q", "archive/index.jsonl")  # a staged wipe of the archive ledger
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["hook", "ledger"])
    assert result.exit_code == 1, result.output
    assert "archive/index.jsonl" in result.output and "append-only" in result.output

    # and bench-ops' own lefthook.yml runs the job that guards it
    own = (Path(__file__).resolve().parents[1] / "lefthook.yml").read_text()
    assert "bench hook ledger" in own


# ---- M5: the pushed verdict rests on fresh server refs ----------------------------------------


def test_m5_work_fetches_origin_before_the_pushed_verdict_and_offline_never_removes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin, main, root, work = workshop(tmp_path)
    monkeypatch.setenv("BENCH_ROOT", str(root))
    wt = work / "ndb-run-side"
    sh(main, "worktree", "add", "-b", "feature", str(wt), when=UTC_OLD)
    (wt / "s.txt").write_text("s\n")
    sh(wt, "add", "-A")
    sh(wt, "commit", "-qm", "s", when=UTC_OLD)
    sh(main, "push", "-q", "origin", "feature")
    _age_creation(main, "ndb-run-side", UTC_OLD)

    # the server rewrites the branch; this machine runs only what `bench sync` runs
    sh(main, "fetch", "-q", "origin", "main")
    rewrite = tmp_path / "rewrite"
    subprocess.run(
        ["git", "clone", "-q", "-b", "feature", str(origin), str(rewrite)], check=True
    )
    sh(rewrite, "commit", "-q", "--allow-empty", "-m", "rewritten", when=UTC_OLD)
    sh(rewrite, "push", "-q", "-f", "origin", "feature")

    infos = _infos(work, main, origin)
    assert "unpushed" in infos["ndb-run-side"].reasons
    assert not infos["ndb-run-side"].removable
    result = runner.invoke(app, ["work", "--yes"])
    assert result.exit_code == 0, result.output
    assert wt.exists()  # the verdict that would justify the removal was false

    # --offline skips the fetch and never removes anything
    result = runner.invoke(app, ["work", "--yes", "--offline"])
    assert result.exit_code == 0, result.output
    assert wt.exists()

    # an origin that cannot be reached vouches for nothing either (fail safe)
    gone = tmp_path / "origin-gone.git"
    origin.rename(gone)
    try:
        infos = _infos(work, main, origin)
        assert "origin unreachable" in infos["ndb-run-side"].reasons
        assert not infos["ndb-run-side"].removable
    finally:
        gone.rename(origin)


# ---- M6: the archive records symlinks ---------------------------------------------------------


def test_m6_the_archive_records_symlinks_in_a_sidecar(tmp_path: Path) -> None:
    sat = satellite(tmp_path, NAME)
    (tmp_path / "outside.bin").write_bytes(b"outside-bytes")
    (sat / "alias.txt").symlink_to(sat / "tracked.txt")
    (sat / "corpus.bin").symlink_to(tmp_path / "outside.bin")
    hub = FakeHub(tmp_path)
    bench = bench_root(tmp_path)

    m = archive(sat, NAME, hub, now=CREATED, yes=True, bench_root=bench)
    sidecar = f"{PREFIX}symlinks.json"
    assert sidecar in {f.path for f in m.files}  # part of the archive, so part of the witness
    entry = ledger_entry(bench)
    links = json.loads(hub.commits[entry.hf.revision][sidecar])
    assert links["alias.txt"] == str(sat / "tracked.txt")
    assert links["corpus.bin"] == str(tmp_path / "outside.bin")


# ---- M7: a hub failure is a line, not a traceback ----------------------------------------------


def test_m7_gc_reports_a_hub_error_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sat = satellite(tmp_path, NAME)
    bench, _hub, serving = _archived(tmp_path, sat)

    def boom(*args: object, **kwargs: object) -> dict[str, str | None]:
        raise RuntimeError("503 Service Unavailable")

    serving.paths_info = boom  # the manifest downloads, the witness cannot reach the hub
    monkeypatch.setenv("BENCH_ROOT", str(bench))
    monkeypatch.setattr("bench_ops.cli.HfHub", lambda: serving)

    result = runner.invoke(app, ["gc", str(sat / "tracked.txt"), "--yes"])
    assert result.exit_code == 1
    assert not isinstance(result.exception, RuntimeError)  # handled, not escaped
    assert "witness failed" in result.output and "503" in result.output
    assert "Traceback" not in result.output
    assert (sat / "tracked.txt").exists()  # nothing was deleted


# ---- L1: release tags and archive tags never share a namespace ---------------------------------


def test_l1_publish_refuses_archive_tags(tmp_path: Path) -> None:
    root = _ndb_producer(tmp_path)
    hub = FakeHub(tmp_path)
    with pytest.raises(PublishError, match="arc-"):
        publish(_ndb_repo(root), "arc-ndb-105", hub, now=CREATED, dry_run=False)
    assert hub.calls == []

    from bench_ops.publish import validate_tag

    with pytest.raises(PublishError):
        validate_tag("ndb-v0.0.1", kind="archive")  # a release tag is not an archive tag
    with pytest.raises(PublishError):
        validate_tag("arc-x", kind="release")
    validate_tag("ndb-v0.0.1")  # the release namespace, the default
    validate_tag("arc-x", kind="archive")


# ---- L2: a local manifest copy for gc's fail-closed blocklist -----------------------------------


def test_l2_archive_leaves_a_local_manifest_copy_for_gc(tmp_path: Path) -> None:
    sat = satellite(tmp_path, NAME)
    hub = FakeHub(tmp_path)
    bench = bench_root(tmp_path)
    archive(sat, NAME, hub, now=CREATED, yes=True, bench_root=bench)

    local = bench / "archive" / NAME / "manifest.json"
    assert local.exists()
    assert local.read_bytes() == hub.commits[ledger_entry(bench).hf.revision][
        f"{PREFIX}manifest.json"
    ]

    local.write_bytes(b"{}")  # a different copy is never silently overwritten
    with pytest.raises(PublishError, match="manifest"):
        archive(sat, NAME, hub, now=CREATED, yes=True, bench_root=bench)


# ---- L3: the blast radius of bench work is the pen ----------------------------------------------


def test_l3_work_never_removes_a_worktree_outside_the_pen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin, main, root, work = workshop(tmp_path)
    monkeypatch.setenv("BENCH_ROOT", str(root))
    outside = tmp_path / "outside-wt"
    sh(main, "worktree", "add", "-b", "side", str(outside), when=UTC_OLD)
    (outside / "o.txt").write_text("o\n")
    sh(outside, "add", "-A")
    sh(outside, "commit", "-qm", "o", when=UTC_OLD)
    sh(main, "push", "-q", "origin", "side")
    _age_creation(main, "outside-wt", UTC_OLD)
    (work / "linked-side").symlink_to(outside)  # a symlink in the pen points outside it

    infos = _infos(work, main, origin)
    assert "outside .work" in infos["linked-side"].reasons
    assert not infos["linked-side"].removable

    result = runner.invoke(app, ["work", "--yes"])
    assert result.exit_code == 0, result.output
    assert outside.exists()  # the registered worktree elsewhere was not touched
    assert (work / "linked-side").exists()


# ---- L4: gc has guardrails against wild targets -------------------------------------------------


def test_l4_gc_refuses_root_home_bench_root_and_repo_parents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "a.txt").write_text("a\n")
    root = tmp_path / "root"
    root.mkdir()
    (root / "repos.toml").write_text(
        f'[[repos]]\nname = "ndb"\nurl = "x"\nbranch = "main"\n'
        f'role = "producer"\npath = "{linked}"\n'
    )
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("BENCH_ROOT", str(root))

    result = runner.invoke(app, ["gc", str(tmp_path), "--yes"])  # a parent of a linked repo
    assert result.exit_code == 1, result.output
    assert "refusing" in result.output
    assert (linked / "a.txt").exists()

    for target in (str(home), str(root), "/"):
        result = runner.invoke(app, ["gc", target, "--yes"])
        assert result.exit_code == 1, (target, result.output)
        assert "refusing" in result.output
    assert (linked / "a.txt").exists()


# ---- L5: the pen is not debris -------------------------------------------------------------------


def test_l5_status_does_not_report_the_pen_as_stray(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin, main, root, work = workshop(tmp_path)
    sh(main, "worktree", "add", str(work / "ndb-run-105"), when=UTC_OLD)
    monkeypatch.setenv("BENCH_ROOT", str(root))

    from bench_ops.status import report

    rep = report(_repo(main, origin), {}, None)
    assert rep.stray_worktrees  # without knowing the pen, it is just another worktree
    rep = report(_repo(main, origin), {}, None, pen=root / ".work")
    assert rep.stray_worktrees == []  # the pen .work/ is drained by bench work

    result = runner.invoke(app, ["status", "--offline"])
    assert result.exit_code == 0, result.output
    assert ".work" not in result.output  # no path from the pen appears as stray


# ---- L6: a locked worktree says so ----------------------------------------------------------------


def test_l6_work_lists_a_locked_worktree_with_its_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin, main, root, work = workshop(tmp_path)
    monkeypatch.setenv("BENCH_ROOT", str(root))
    wt = work / "ndb-run-locked"
    sh(main, "worktree", "add", "-b", "lk", str(wt), when=UTC_OLD)
    (wt / "l.txt").write_text("l\n")
    sh(wt, "add", "-A")
    sh(wt, "commit", "-qm", "l", when=UTC_OLD)
    sh(main, "push", "-q", "origin", "lk")
    _age_creation(main, "ndb-run-locked", UTC_OLD)
    sh(main, "worktree", "lock", str(wt))

    infos = _infos(work, main, origin)
    assert "locked" in infos["ndb-run-locked"].reasons
    assert not infos["ndb-run-locked"].removable

    result = runner.invoke(app, ["work", "--yes"])
    assert result.exit_code == 0, result.output
    assert wt.exists()


# ---- L7: the unlink re-checks the pinned file -----------------------------------------------------


def test_l7_gc_restats_right_before_the_unlink(tmp_path: Path) -> None:
    from bench_ops.gc import Verdict, safe_unlink

    d = tmp_path / "out"
    d.mkdir()
    f = d / "a.bin"
    f.write_bytes(b"aaa")
    st = f.stat()

    def pinned() -> Verdict:
        return Verdict(
            path=f, sha256=hashlib.sha256(b"aaa").hexdigest(), tags=("ndb-v1.0.0",),
            deletable=True, reason="verified on HF (ndb-v1.0.0)",
            stat=(st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns),
        )

    f.unlink()  # swapped for a different file between the check and the unlink
    f.write_bytes(b"attacker")
    assert not safe_unlink(pinned())
    assert f.read_bytes() == b"attacker"  # the replacement survived

    st = f.stat()  # re-pin the current file: unchanged, it goes
    assert safe_unlink(pinned())
    assert not f.exists()

    f.symlink_to(d / "elsewhere")  # a symlink where the verified file was: never followed
    st = f.lstat()
    assert not safe_unlink(pinned())
    assert f.is_symlink()
