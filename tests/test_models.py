import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from bench_ops.models import (
    FileEntry,
    HfRef,
    LedgerEntry,
    Lock,
    Manifest,
    Row,
    Scores,
    Table,
    dumps,
)

SHA = "a" * 64


def manifest() -> Manifest:
    return Manifest(
        tag="ndb-v1.0.0",
        created=datetime(2026, 11, 2, tzinfo=UTC),
        producer="ndb",
        hf=HfRef(repo="arthrod/neurotic_docx_bench", revision="b" * 40),
        r2_prefix="ndb-v1.0.0/",
        viewer_version="0.1.0",
        files=[
            FileEntry(path="release/ndb-v1.0.0/scores.json", sha256=SHA, bytes=1234),
            FileEntry(path="site/convert/x.pdf", sha256=SHA, bytes=5, served=True),
        ],
    )


def test_manifest_round_trips_json() -> None:
    m = manifest()
    again = Manifest.model_validate_json(m.model_dump_json())
    assert again == m


def test_sha256_must_be_64_hex() -> None:
    with pytest.raises(ValidationError):
        FileEntry(path="p", sha256="xyz", bytes=1)


def test_served_subset() -> None:
    assert [f.path for f in manifest().served()] == ["site/convert/x.pdf"]


def test_tag_shape() -> None:
    with pytest.raises(ValidationError):
        Manifest.model_validate(manifest().model_dump() | {"tag": "v1"})


def test_ledger_line_and_back() -> None:
    e = LedgerEntry(
        tag="ndb-v1.0.0",
        created=datetime(2026, 11, 2, tzinfo=UTC),
        hf=HfRef(repo="arthrod/neurotic_docx_bench", revision="b" * 40),
        manifest_sha256=SHA,
    )
    line = e.to_line()
    assert line.endswith("\n") and json.loads(line)["tag"] == "ndb-v1.0.0"
    assert LedgerEntry.from_line(line) == e


def test_ledger_last_of_text() -> None:
    a = LedgerEntry(tag="ndb-v1.0.0", created=datetime(2026, 1, 1, tzinfo=UTC),
                    hf=HfRef(repo="r", revision="b" * 40), manifest_sha256=SHA)
    b = a.model_copy(update={"tag": "ndb-v1.0.1"})
    assert LedgerEntry.last(a.to_line() + b.to_line()).tag == "ndb-v1.0.1"
    assert LedgerEntry.last("") is None


def test_lock_parse_and_render() -> None:
    lock = Lock.parse("ndb=ndb-v1.0.0\net=et-v0.9.3\n")
    assert lock.tags == {"ndb": "ndb-v1.0.0", "et": "et-v0.9.3"}
    assert lock.render() == "et=et-v0.9.3\nndb=ndb-v1.0.0\n"


def test_lock_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        Lock.parse("just a tag\n")


def test_scores_minimal() -> None:
    s = Scores(
        version="1.0.0",
        generated="2026-10-28",
        repo="https://github.com/jandira-tech/neurotic_docx_bench",
        viewer="https://jandira-tech.github.io/neurotic_docx_bench/",
        provenance={"docsets": ["c28442e9bb5a5deb"], "scorers": ["v1"],
                    "renderers": ["oracle:microsoft_word"]},
        tables=[Table(id="docx-to-pdf", title="t", meta="m", desc="d", rows=[
            Row(rank="1", tool="jubarte 0.12.0", pin="0.12.0@3c81", median=82.45, mean=79.93,
                docs=400, failed=0, note="[81.13, 83.23]", ours=True)])],
        headlines=[], states=[], home_groups=[], method=[],
    )
    assert s.tables[0].rows[0].ours is True
    assert Scores.model_validate_json(s.model_dump_json()) == s


def test_dumps_is_canonical() -> None:
    text = dumps(manifest())
    assert text.endswith("\n")
    assert text == dumps(Manifest.model_validate_json(text))
    keys = list(json.loads(text))
    assert keys == sorted(keys)
