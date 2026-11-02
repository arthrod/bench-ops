"""The release contract shared by producers, the record (HF), the cache (R2) and consumers."""

from __future__ import annotations

import json
from datetime import datetime
from itertools import pairwise
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

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


# ---- cases: the per-case browsers (conversion + redline), sharded one file per group ----------
# Plan 3b: `cases/index.json` (CasesIndex) points at `cases/<kind>/<group>.json` shards
# (CaseShard). Page WebPs and shown-page PDFs stay on R2: Case carries paths relative to
# CasesIndex.assets_base (~/ sentinel, as in the published data), never bytes or absolute URLs.

MetricName = Literal["pixel", "jaccard", "ssim", "text_boundary"]
MetricValue = Annotated[float, Field(ge=0, le=100.000001)]  # epsilon: upstream pixel hits 100.00000000000003
PageCount = Annotated[int, Field(ge=0)]  # zero is a failed candidate
_Char = r"[A-Za-z0-9_~-]"  # a segment character that is not a dot
_Any = r"[A-Za-z0-9._~-]"
# Any segment except exactly "." and ".." (path traversal), look-around-free for pydantic-core:
# "..x" or ".x" or any segment starting with a non-dot.
_Segment = rf"(?:\.\.{_Any}+|\.{_Char}{_Any}*|{_Char}{_Any}*)"
AssetPath = Annotated[
    str, StringConstraints(pattern=rf"^~/(?:{_Segment}/)*{_Segment}$")
]  # ~/x/y.webp: relative to assets_base, no scheme, no traversal
ShardPath = Annotated[
    str, StringConstraints(pattern=rf"^(?:{_Segment}/)*{_Segment}\.json$")
]  # release-file path such as cases/conversion/v1_clean.json


class Engine(Strict):
    key: str  # engine/pin lane key, including "reference" and superseded pins
    label: str  # display label


class MetricDiagnostics(Strict):
    ref_pages: int = Field(ge=0)
    pages: int = Field(ge=0)
    scored_pages: int = Field(ge=0)
    max_break_drift: int  # signed, not a distance: observed -11916..9310


class Case(Strict):
    kind: Literal["conversion", "redline"]  # source page; never inferred from group spelling
    group: str  # composite identity: group
    case: str  # composite identity: case
    pages: dict[str, list[AssetPath | None]]  # engine -> shown WebPs; null is a deliberate gap
    page_counts: dict[str, PageCount]  # engine -> whole-document pages (not shown-page count)
    scores: dict[str, dict[MetricName, MetricValue | None]]  # null = metric not computable
    times: dict[str, float]  # engine -> wall-clock seconds
    versions: dict[str, str]  # engine -> per-case pin/version label (dropped lanes too)
    dropped: list[str]  # engine keys hidden for this case
    reference_app: str  # Word/Quartz producer note
    pdfs: dict[str, AssetPath] = Field(default_factory=dict)  # engine -> shown-pages PDF
    errors: dict[str, str] = Field(default_factory=dict)  # engine -> failure explanation
    metric_diagnostics: dict[str, MetricDiagnostics] = Field(default_factory=dict)
    shown: list[int] | None = None  # page numbers represented by pages[]; None = legacy convention
    stem: str | None = None  # corpus/source document identity
    lang: str | None = None
    type: str | None = None  # document category
    set: str | None = None  # cohort/source-set label
    sets: str | None = None  # semicolon-separated source-set labels
    state: str | None = None  # corpus document state
    pair: str | None = None  # comparison identity where meaningful
    not_applicable: list[str] = Field(default_factory=list)  # engines excluded for cause
    unavailable: list[str] = Field(default_factory=list)  # cohort lanes with no published measurement

    @model_validator(mode="after")
    def _check_case(self) -> Case:
        active = (
            self.pages.keys()
            | self.page_counts.keys()
            | self.scores.keys()
            | self.times.keys()
            | self.pdfs.keys()
            | self.metric_diagnostics.keys()
        )
        clash = sorted(set(self.dropped) & active)
        if clash:
            raise ValueError(f"dropped engines still active in this case: {clash}")
        orphaned = sorted(
            set(self.errors) - self.scores.keys() - set(self.dropped) - set(self.unavailable)
        )
        if orphaned:
            raise ValueError(
                f"engines with errors but no scores entry (and not dropped/unavailable): {orphaned}"
            )
        if self.shown is not None:
            pages_ = list(self.shown)
            if any(p < 1 for p in pages_) or any(b <= a for a, b in pairwise(pages_)):
                raise ValueError("shown must be strictly increasing 1-based page numbers")
            wrong = sorted(e for e, lst in self.pages.items() if lst and len(lst) != len(pages_))
            if wrong:
                raise ValueError(f"shown has {len(pages_)} entries but page lists differ: {wrong}")
        return self


class CaseShard(Strict):
    """One group's file: cases/<kind>/<group>.json."""

    kind: Literal["conversion", "redline"]
    group: str
    cases: list[Case]

    @model_validator(mode="after")
    def _check_shard(self) -> CaseShard:
        stray = [c.case for c in self.cases if c.kind != self.kind or c.group != self.group]
        if stray:
            raise ValueError(f"shard {self.kind}/{self.group} holds foreign cases: {stray[:5]}")
        seen: set[str] = set()
        dupes: set[str] = set()
        for c in self.cases:
            (dupes if c.case in seen else seen).add(c.case)
        if dupes:
            raise ValueError(f"duplicate case ids in shard {self.kind}/{self.group}: {sorted(dupes)}")
        return self


class ShardRef(Strict):
    """One index entry pointing at a shard file."""

    kind: Literal["conversion", "redline"]
    group: str
    count: int = Field(ge=0)
    path: ShardPath


class CasesIndex(Strict):
    """cases/index.json: the shard directory, the engine list and the public asset base."""

    version: str  # contract schema version; the release tag stays in the Manifest
    generated: datetime
    repo: str  # producer repository identity
    assets_base: str  # public HTTPS R2 base URL the ~/ paths resolve against
    engines: list[Engine]  # ordered; covers every key used in any case map
    groups: list[ShardRef]

    @field_validator("assets_base")
    @classmethod
    def _https_directory(cls, v: str) -> str:
        if not (v.startswith("https://") and v.endswith("/") and v[len("https://") : -1]):
            raise ValueError("assets_base must be an absolute https:// URL ending in /")
        return v

    @model_validator(mode="after")
    def _unique(self) -> CasesIndex:
        keys = [e.key for e in self.engines]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            raise ValueError(f"duplicate engine keys: {sorted(dupes)}")
        refs = [(g.kind, g.group) for g in self.groups]
        gdupes = {r for r in refs if refs.count(r) > 1}
        if gdupes:
            raise ValueError(f"duplicate groups: {sorted(gdupes)}")
        return self

    def validate_shard(self, shard: CaseShard) -> CaseShard:
        """Cross-check a shard against the index before release: registration, count, engine keys."""
        entry = next(
            (g for g in self.groups if g.kind == shard.kind and g.group == shard.group), None
        )
        if entry is None:
            raise ValueError(f"shard {shard.kind}/{shard.group} is not registered in the index")
        if entry.count != len(shard.cases):
            raise ValueError(
                f"index counts {entry.count} cases for {shard.kind}/{shard.group}, "
                f"shard holds {len(shard.cases)}"
            )
        known = {e.key for e in self.engines}
        unknown: set[str] = set()
        for c in shard.cases:
            for m in (
                c.pages, c.page_counts, c.scores, c.times, c.versions,
                c.pdfs, c.errors, c.metric_diagnostics,
            ):
                unknown |= m.keys() - known
            unknown |= (set(c.dropped) | set(c.not_applicable) | set(c.unavailable)) - known
        if unknown:
            raise ValueError(f"engine keys missing from the index engines: {sorted(unknown)}")
        return shard


# ---- speed.json: the 10,000-input speed page (not the RESULTS.md leaderboard) ----------------


class SpeedCoverage(Strict):
    tool: str
    completed: int = Field(ge=0)
    planned: int = Field(ge=0)


class SpeedRow(Strict):
    tool: str
    bucket: str  # pairs / category / quartile bucket
    n: int = Field(ge=0)
    ok: int = Field(ge=0)
    failed: int = Field(ge=0)
    median_ms: float | None = None  # None when all calls failed
    mean_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None
    max_ms: float | None = None
    ok_total_s: float | None = None


class SpeedTable(Strict):
    id: str  # common / category / quartile
    title: str
    bucket_header: str  # the bucket column's label
    rows: list[SpeedRow]


class SpeedNote(Strict):
    heading: str
    paragraphs: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)

    @field_validator("heading", "paragraphs", "bullets")
    @classmethod
    def _plain_text(cls, v: str | list[str]) -> str | list[str]:
        texts = [v] if isinstance(v, str) else v
        if any("<" in t for t in texts):
            raise ValueError("speed notes are plain text; markup is not allowed")
        return v


class SpeedTask(Strict):
    id: str  # redlines / pdf
    title: str
    description: str
    available: bool = True  # False renders as "Not measured yet."
    planned: int | None = None
    timeout_ms: int | None = None
    quartile_cuts_bytes: list[int] = Field(default_factory=list)
    coverage: list[SpeedCoverage] = Field(default_factory=list)
    tables: list[SpeedTable] = Field(default_factory=list)
    notes: list[SpeedNote] = Field(default_factory=list)


class Speed(Strict):
    version: str
    generated: datetime
    repo: str
    title: str
    intro: str
    tasks: list[SpeedTask]  # in render order


def dumps(model: BaseModel) -> str:
    """Canonical JSON: sorted keys, 2-space indent, trailing newline. Hash this text."""
    data = model.model_dump(mode="json")
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def dumps_compact(model: BaseModel) -> str:
    """Compact release JSON: sorted keys, no whitespace (case shards, speed.json).

    ``exclude_none`` is applied to optional model metadata only: a None-valued field is
    dropped, but nulls inside ``scores`` dicts and ``pages`` lists are data and are kept.
    """
    return json.dumps(
        _compact(model), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _compact(value: object) -> object:
    if isinstance(value, BaseModel):
        return {name: _compact(v) for name, v in value if v is not None}
    if isinstance(value, dict):
        return {k: _compact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_compact(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value
