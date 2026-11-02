"""The release contract shared by producers, the record (HF), the cache (R2) and consumers."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Tag = Annotated[str, StringConstraints(pattern=r"^(ndb|et)-v\d+\.\d+\.\d+(\.\d+)?$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HfRef(Strict):
    repo: str
    revision: GitSha


class FileEntry(Strict):
    path: str
    sha256: Sha256
    bytes: int = Field(ge=0)
    served: bool = False  # mirrored to R2 under Manifest.r2_prefix


class Manifest(Strict):
    tag: Tag
    created: datetime
    producer: str
    hf: HfRef
    r2_prefix: str
    viewer_version: str | None = None
    files: list[FileEntry]

    def served(self) -> list[FileEntry]:
        return [f for f in self.files if f.served]

    def by_path(self) -> dict[str, FileEntry]:
        return {f.path: f for f in self.files}


class LedgerEntry(Strict):
    """One line of release/index.jsonl: the small ledger that stays in git."""

    tag: Tag
    created: datetime
    hf: HfRef
    manifest_sha256: Sha256

    def to_line(self) -> str:
        return self.model_dump_json() + "\n"

    @classmethod
    def from_line(cls, line: str) -> LedgerEntry:
        return cls.model_validate_json(line)

    @classmethod
    def last(cls, text: str) -> LedgerEntry | None:
        """The newest parsable entry. A torn (unparsable) final line is tolerated (M4): the
        writer may have died mid-append, and `bench status` must report the record, not
        crash — `bad_line` says whether that happened."""
        for line in reversed([ln for ln in text.splitlines() if ln.strip()]):
            try:
                return cls.from_line(line)
            except ValidationError:
                continue
        return None

    @classmethod
    def bad_line(cls, text: str) -> int | None:
        """1-based number of the first line that is not a ledger entry (blank lines don't
        count); None when every line parses. A torn append leaves exactly one, at the end."""
        for i, line in enumerate(ln for ln in text.splitlines() if ln.strip()):
            try:
                cls.from_line(line)
            except ValidationError:
                return i
        return None


class Lock(BaseModel):
    """bench.lock: `<producer>=<tag>` per line, sorted. Replaces jubarte-site/fixtures.lock."""

    tags: dict[str, str]

    @classmethod
    def parse(cls, text: str) -> Lock:
        tags: dict[str, str] = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise ValueError(f"bench.lock line is not <producer>=<tag>: {line!r}")
            k, v = (s.strip() for s in line.split("=", 1))
            tags[k] = v
        return cls(tags=tags)

    def render(self) -> str:
        return "".join(f"{k}={self.tags[k]}\n" for k in sorted(self.tags))


# ---- scores.json: the numbers every frontend prints ------------------------------------
# Mirrors jubarte-site/site/data/bench.ts so the jubarte adaptation is a format change only.


class Row(Strict):
    rank: str
    tool: str
    pin: str
    median: float
    mean: float
    docs: int
    failed: int
    note: str
    ours: bool = False


class Table(Strict):
    id: str
    title: str
    meta: str
    desc: str
    rows: list[Row]


class Headline(Strict):
    label: str
    value: str
    vs: str
    sub: str


class StateRow(Strict):
    tool: str
    median: float
    ours: bool = False


class StateCell(Strict):
    name: str
    n: str
    rows: list[StateRow]
    table: str | None = None  # id of the Table these states break down


class HomeBar(Strict):
    name: str
    v: float
    ours: bool = False


class HomeGroup(Strict):
    title: str
    meta: str
    rows: list[HomeBar]
    note: str | None = None


class MethodPoint(Strict):
    t: str
    d: str


class Scores(Strict):
    version: str
    generated: str
    repo: str
    viewer: str
    provenance: dict[str, list[str]]
    tables: list[Table]
    headlines: list[Headline]
    states: list[StateCell]
    home_groups: list[HomeGroup]
    method: list[MethodPoint]


def dumps(model: BaseModel) -> str:
    """Canonical JSON: sorted keys, 2-space indent, trailing newline. Hash this text."""
    data = model.model_dump(mode="json")
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
