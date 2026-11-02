import hashlib
from datetime import UTC, datetime
from pathlib import Path

from bench_ops.hf import Witness, verify_manifest
from bench_ops.models import FileEntry, HfRef, Manifest

SMALL = b'{"a": 1}\n'
SMALL_SHA = hashlib.sha256(SMALL).hexdigest()
BIG_SHA = "c" * 64


class FakeApi:
    """Stands in for huggingface_hub.HfApi: paths_info + download of small files."""

    def __init__(self, tmp: Path, lfs: dict[str, str], small: dict[str, bytes]) -> None:
        self.tmp, self.lfs, self.small = tmp, lfs, small
        self.calls: list[str] = []

    def paths_info(self, repo: str, revision: str, paths: list[str]) -> dict[str, str | None]:
        self.calls.append("paths_info")
        out: dict[str, str | None] = {}
        for p in paths:
            if p in self.lfs:
                out[p] = self.lfs[p]
            elif p in self.small:
                out[p] = None  # exists, not LFS
        return out

    def download(self, repo: str, revision: str, path: str) -> Path:
        self.calls.append(f"download:{path}")
        f = self.tmp / Path(path).name
        f.write_bytes(self.small[path])
        return f


def manifest() -> Manifest:
    return Manifest(
        tag="ndb-v1.0.0", created=datetime(2026, 11, 2, tzinfo=UTC), producer="ndb",
        hf=HfRef(repo="arthrod/neurotic_docx_bench", revision="b" * 40), r2_prefix="ndb-v1.0.0/",
        files=[
            FileEntry(path="release/ndb-v1.0.0/scores.json", sha256=SMALL_SHA, bytes=len(SMALL)),
            FileEntry(path="site/x.pdf", sha256=BIG_SHA, bytes=99, served=True),
        ],
    )


def test_all_good(tmp_path: Path) -> None:
    api = FakeApi(tmp_path, lfs={"site/x.pdf": BIG_SHA},
                  small={"release/ndb-v1.0.0/scores.json": SMALL})
    w: Witness = verify_manifest(manifest(), api)
    assert w.ok and w.missing == [] and w.mismatched == [] and w.checked == 2
    assert api.calls == ["paths_info", "download:release/ndb-v1.0.0/scores.json"]


def test_missing_file(tmp_path: Path) -> None:
    api = FakeApi(tmp_path, lfs={}, small={"release/ndb-v1.0.0/scores.json": SMALL})
    w = verify_manifest(manifest(), api)
    assert not w.ok and w.missing == ["site/x.pdf"]


def test_lfs_mismatch(tmp_path: Path) -> None:
    api = FakeApi(tmp_path, lfs={"site/x.pdf": "d" * 64},
                  small={"release/ndb-v1.0.0/scores.json": SMALL})
    w = verify_manifest(manifest(), api)
    assert w.mismatched == ["site/x.pdf"]


def test_small_mismatch(tmp_path: Path) -> None:
    api = FakeApi(tmp_path, lfs={"site/x.pdf": BIG_SHA},
                  small={"release/ndb-v1.0.0/scores.json": b"other"})
    w = verify_manifest(manifest(), api)
    assert w.mismatched == ["release/ndb-v1.0.0/scores.json"]
