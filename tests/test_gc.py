"""fetch_verified/verdicts (plan 2): gc authority is unforgeable and fail-closed (D1/D3/D22)."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from bench_ops.gc import Verdict, VerifiedManifest, fetch_verified, verdicts  # isort: skip
from bench_ops.models import FileEntry, HfRef, LedgerEntry, Manifest, dumps

HF_REPO = "arthrod/neurotic_docx_bench"
CREATED = datetime(2026, 11, 3, tzinfo=UTC)
TAG = "ndb-v1.0.0"

A, S, U = b"aaa", b"served-bytes", b"unverified-bytes"
SHA_A = hashlib.sha256(A).hexdigest()
SHA_S = hashlib.sha256(S).hexdigest()
SHA_U = hashlib.sha256(U).hexdigest()

F_A = FileEntry(path=f"release/{TAG}/out/a.bin", sha256=SHA_A, bytes=len(A))
F_S = FileEntry(path=f"release/{TAG}/site/served.pdf", sha256=SHA_S, bytes=len(S), served=True)
F_U = FileEntry(path=f"release/{TAG}/out/unv.bin", sha256=SHA_U, bytes=len(U))


def _manifest(*files: FileEntry, tag: str = TAG) -> Manifest:
    return Manifest(
        tag=tag,
        created=CREATED,
        producer="ndb",
        hf=HfRef(repo=HF_REPO, revision="b" * 40),
        r2_prefix=f"{tag}/",
        files=list(files),
    )


def _entry(m: Manifest, tag: str | None = None) -> LedgerEntry:
    return LedgerEntry(
        tag=tag or m.tag,
        created=CREATED,
        hf=HfRef(repo=m.hf.repo, revision=m.hf.revision),
        manifest_sha256=hashlib.sha256(dumps(m).encode()).hexdigest(),
    )


class FakeHubApi:
    """Stands in for hf.HubApi (the FakeApi pattern of test_hf.py): serves one manifest."""

    def __init__(self, tmp: Path, manifest_bytes: bytes, lfs: dict[str, str]) -> None:
        self.tmp, self.lfs = tmp, lfs
        self.calls: list[str] = []
        (tmp / "manifest.json").write_bytes(manifest_bytes)

    def paths_info(self, repo: str, revision: str, paths: list[str]) -> dict[str, str | None]:
        self.calls.append("paths_info")
        return {p: self.lfs[p] for p in paths if p in self.lfs}

    def download(self, repo: str, revision: str, path: str) -> Path:
        self.calls.append(f"download:{path}")
        return self.tmp / "manifest.json"


def _hub(tmp: Path, m: Manifest, lfs: dict[str, str] | None = None) -> FakeHubApi:
    return FakeHubApi(
        tmp, dumps(m).encode(), lfs if lfs is not None else {f.path: f.sha256 for f in m.files}
    )


def _verified(m: Manifest) -> VerifiedManifest:
    return VerifiedManifest(entry=_entry(m), manifest=m)


# ---- fetch_verified ----------------------------------------------------------------------


def test_fetch_verified_returns_the_manifest_when_every_check_passes(tmp_path: Path) -> None:
    m = _manifest(F_A, F_S, F_U)
    hub = _hub(tmp_path, m)
    vm = fetch_verified(_entry(m), hub)
    assert vm is not None
    assert vm.entry.tag == m.tag
    assert vm.manifest.tag == m.tag
    assert [f.path for f in vm.manifest.files] == [F_A.path, F_S.path, F_U.path]
    assert "paths_info" in hub.calls  # the witness really ran


def test_fetch_verified_none_when_the_manifest_sha_differs_from_the_ledger(tmp_path: Path) -> None:
    m = _manifest(F_A)
    hub = _hub(tmp_path, m)
    tampered = _entry(m).model_copy(update={"manifest_sha256": "0" * 64})
    assert fetch_verified(tampered, hub) is None
    assert "paths_info" not in hub.calls  # fails closed before any witness work


def test_fetch_verified_none_on_a_tag_mismatch(tmp_path: Path) -> None:
    m = _manifest(F_A)  # the manifest on the hub is for ndb-v1.0.0
    hub = _hub(tmp_path, m)
    assert fetch_verified(_entry(m, tag="ndb-v1.0.1"), hub) is None
    assert "paths_info" not in hub.calls


def test_fetch_verified_none_when_the_witness_is_not_ok(tmp_path: Path) -> None:
    m = _manifest(F_A, F_U)
    hub = _hub(tmp_path, m, lfs={F_A.path: F_A.sha256})  # F_U is missing on the hub
    assert fetch_verified(_entry(m), hub) is None


# ---- verdicts ----------------------------------------------------------------------------


def test_verdicts_deletable_only_when_verified_unserved_and_unblocked(tmp_path: Path) -> None:
    m = _manifest(F_A, F_S, F_U)
    out = tmp_path / "out"
    out.mkdir()
    a, served, unv = out / "a.bin", out / "served.pdf", out / "unv.bin"
    a.write_bytes(A)
    served.write_bytes(S)
    unv.write_bytes(U)
    vs: dict[Path, Verdict] = {
        v.path: v for v in verdicts([a, served, unv], [_verified(m)], {SHA_U})
    }
    assert vs[a].deletable and vs[a].sha256 == SHA_A and vs[a].tags == (TAG,)
    assert not vs[served].deletable  # served files are never deletable
    assert not vs[unv].deletable  # listed by an unverified manifest too


def test_verdicts_never_delete_git_paths(tmp_path: Path) -> None:
    m = _manifest(F_A)
    git_file = tmp_path / ".git" / "objects" / "ab" / "cdef"
    git_file.parent.mkdir(parents=True)
    git_file.write_bytes(A)  # same sha as the verified out/a.bin
    out = tmp_path / "out"
    out.mkdir()
    a = out / "a.bin"
    a.write_bytes(A)
    vs = {v.path: v for v in verdicts([a, git_file], [_verified(m)], set())}
    assert vs[a].deletable  # positive control: the sha is listed and unblocked
    git_verdict = vs.get(git_file)
    assert git_verdict is None or not git_verdict.deletable


def test_verdicts_skip_directories_and_symlinks(tmp_path: Path) -> None:
    m = _manifest(F_A)
    out = tmp_path / "out"
    out.mkdir()
    a = out / "a.bin"
    a.write_bytes(A)
    d = out / "adir"
    d.mkdir()
    link = out / "link.bin"
    link.symlink_to(a)
    vs = {v.path: v for v in verdicts([a, d, link], [_verified(m)], set())}
    assert vs[a].deletable
    for p in (d, link):
        v = vs.get(p)
        assert v is None or not v.deletable
