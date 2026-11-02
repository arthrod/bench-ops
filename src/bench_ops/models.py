"""The release contract shared by producers, the record (HF), the cache (R2) and consumers."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import datetime
from itertools import pairwise
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
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


def _require_sorted(values: list, key: Callable, what: str, by: str) -> None:
    """List order is part of the contract (M1): the release bytes must be reproducible."""
    keys = [key(v) for v in values]
    if keys != sorted(keys):
        raise ValueError(f"{what} must be sorted by {by}")


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
    """scores.json, the pre-existing release.

    ``generated`` keeps its published plain-string spelling; the newer CasesIndex and Speed
    models use ``AwareDatetime`` instead (review L6 notes the inconsistency, deliberately).
    """

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
# (CaseShard, one per group, its path always shard_path(kind, group)). Page WebPs and
# shown-page PDFs stay on R2: Case carries paths relative to CasesIndex.assets_base
# (~/ sentinel, as in the published data), never bytes or absolute URLs.

MetricName = Literal["pixel", "jaccard", "ssim", "text_boundary"]
MetricValue = Annotated[float, Field(ge=0, le=100.000001)]  # epsilon: upstream pixel hits 100.00000000000003
PageCount = Annotated[int, Field(ge=0)]  # zero is a failed candidate
Finite = Annotated[float, Field(allow_inf_nan=False)]  # NaN and ±inf never reach a release
Duration = Annotated[float, Field(allow_inf_nan=False, ge=0)]  # a measured duration: finite, >= 0

# Asset segments are any UTF-8 (review L5): everything except "/" (the separator), control
# characters, and the exact "." / ".." segments (traversal). There is no ASCII-only rule —
# sample corpora are human-named ("samples/CV/…"), so a space, a "+" or "résumé.docx" passes.
_NoCtrl = r"[^\x00-\x1f\x7f-\x9f/]"  # any character except control characters and the separator
_NoDot = r"[^\x00-\x1f\x7f-\x9f/.]"  # ... and not a dot
# Any segment except exactly "." and ".." (path traversal): optional dots, then a non-dot, then
# anything ("..x", ".x", "a..b" pass; "..." is three-plus dots, not the dotdot parent).
_AssetSegment = rf"(?:\.*{_NoDot}{_NoCtrl}*|\.{3,})"
AssetPath = Annotated[
    str, StringConstraints(pattern=rf"^~/(?:{_AssetSegment}/)*{_AssetSegment}$")
]  # ~/x/y.webp: relative to assets_base, no scheme, no traversal, no control characters
_Char = r"[A-Za-z0-9_~-]"  # a release-file segment character that is not a dot
_Any = r"[A-Za-z0-9._~-]"
# Any segment except exactly "." and ".." (path traversal), look-around-free for pydantic-core:
# "..x" or ".x" or any segment starting with a non-dot.
_Segment = rf"(?:\.\.{_Any}+|\.{_Char}{_Any}*|{_Char}{_Any}*)"
ShardPath = Annotated[
    str, StringConstraints(pattern=rf"^(?:{_Segment}/)*{_Segment}\.json$")
]  # release-file path; always produced by shard_path(), e.g. cases/conversion/v1--clean.json

Kind = Literal["conversion", "redline"]
# Which kind a case group belongs to (review L2): the prefixes of the published corpora.
# "redlines/", "accepted/", "rejected/", "ar/" and "wordcraft1014/compare" are redline
# corpora; every other registered prefix is a conversion corpus. A group matches a prefix by
# equality or by path prefix ("corpus/" covers "corpus/clean", ...). An unregistered group is
# refused by name: kind is policy recorded here, never inferred from spelling alone.
GROUP_KINDS: dict[str, Kind] = {
    "redlines/": "redline",
    "accepted/": "redline",
    "rejected/": "redline",
    "ar/": "redline",
    "wordcraft1014/compare": "redline",
    "cases": "conversion",
    "corpus/": "conversion",
    "new": "conversion",
    "samples": "conversion",
    "scraped": "conversion",
    "v1/": "conversion",
    "wordcraft1014/convert": "conversion",
}


def kind_for_group(group: str) -> Kind:
    """The kind a case group belongs to; unknown groups are refused by name."""
    matches = [
        p
        for p in GROUP_KINDS
        if group == p or group.startswith(p if p.endswith("/") else p + "/")
    ]
    if not matches:
        raise ValueError(f"unknown group {group!r}: register its prefix in GROUP_KINDS")
    return GROUP_KINDS[max(matches, key=len)]


def shard_path(kind: str, group: str) -> str:
    """The release path of a group's shard: ``cases/<kind>/<group>.json`` with "/" as "--".

    Slashes become ``--`` (not "_": group names already contain underscores, so that mapping
    would not be reversible). Both the producer and the CasesIndex validator use this helper,
    so a ShardRef can never point anywhere else in the bundle.
    """
    return f"cases/{kind}/{group.replace('/', '--')}.json"


class Engine(Strict):
    key: str  # engine/pin lane key, including "reference" and superseded pins
    label: str  # display label; unique across the index (the viewer renders lanes by label)


class MetricDiagnostics(Strict):
    ref_pages: int = Field(ge=0)
    pages: int = Field(ge=0)
    scored_pages: int = Field(ge=0)
    max_break_drift: int  # signed, not a distance: observed -11916..9310


class Case(Strict):
    """One case row, as published.

    ``pages`` lists the shown WebPs per engine. A lane may legitimately have rendered fewer
    pages than ``shown`` (``len(pages[e]) <= len(shown)``); the builder pads short lanes with
    trailing ``null`` slots so lane vectors align — a trailing null run is that padding
    marker, never a data loss (interior gaps do not occur in the corpus).
    """

    kind: Kind  # source page; checked against GROUP_KINDS (kind_for_group)
    group: str  # composite identity: group
    case: str  # composite identity: case
    pages: dict[str, list[AssetPath | None]]  # engine -> shown WebPs; null is a padding slot
    page_counts: dict[str, PageCount]  # engine -> whole-document pages (not shown-page count)
    scores: dict[str, dict[MetricName, MetricValue | None]]  # null = metric not computable
    times: dict[str, Duration]  # engine -> wall-clock seconds
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

    def versioned_lane_keys(self) -> set[str]:
        """The lanes that must carry a version label: every measured lane plus the
        not_applicable/unavailable ones. Dropped lanes may carry one but need not."""
        keys: set[str] = set()
        for m in (
            self.pages, self.page_counts, self.scores, self.times,
            self.pdfs, self.errors, self.metric_diagnostics,
        ):
            keys |= m.keys()
        return keys | set(self.not_applicable) | set(self.unavailable)

    def lane_keys(self) -> set[str]:
        """Every lane key this case mentions anywhere: maps, versions labels, dropped,
        not_applicable and unavailable lists. The index engine list must cover these."""
        return self.versioned_lane_keys() | set(self.versions) | set(self.dropped)

    @model_validator(mode="after")
    def _check_case(self) -> Case:
        expected = kind_for_group(self.group)
        if self.kind != expected:
            raise ValueError(f"group {self.group!r} belongs to kind {expected!r}, not {self.kind!r}")
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
        ghosts = sorted(set(self.pages) - set(self.page_counts))
        if ghosts:
            raise ValueError(f"engines with pages but no page_counts entry: {ghosts}")
        empty = sorted(e for e, s in self.scores.items() if not s)
        if empty:
            raise ValueError(f"engines with an empty scores entry: {empty}")
        unversioned = sorted(self.versioned_lane_keys() - self.versions.keys())
        if unversioned:
            raise ValueError(f"lane keys without a version label: {unversioned}")
        if self.shown is not None:
            pages_ = list(self.shown)
            if any(p < 1 for p in pages_) or any(b <= a for a, b in pairwise(pages_)):
                raise ValueError("shown must be strictly increasing 1-based page numbers")
            if pages_:
                reference = self.pages.get("reference")
                if reference is None or len(reference) != len(pages_):
                    raise ValueError(
                        f"a case showing {len(pages_)} pages needs a reference lane "
                        f"of the same length"
                    )
            too_long = sorted(e for e, lst in self.pages.items() if lst and len(lst) > len(pages_))
            if too_long:
                raise ValueError(f"page lists longer than shown ({len(pages_)} entries): {too_long}")
        return self


class CaseShard(Strict):
    """One group's file: cases/<kind>/<group>.json (its path is shard_path(kind, group))."""

    kind: Kind
    group: str
    cases: list[Case]  # sorted by case id

    @model_validator(mode="after")
    def _check_shard(self) -> CaseShard:
        stray = [c.case for c in self.cases if c.kind != self.kind or c.group != self.group]
        if stray:
            raise ValueError(f"shard {self.kind}/{self.group} holds foreign cases: {stray[:5]}")
        _require_sorted(self.cases, lambda c: c.case, "cases", "case")
        seen: set[str] = set()
        dupes: set[str] = set()
        for c in self.cases:
            (dupes if c.case in seen else seen).add(c.case)
        if dupes:
            raise ValueError(f"duplicate case ids in shard {self.kind}/{self.group}: {sorted(dupes)}")
        return self


class ShardRef(Strict):
    """One index entry pointing at a shard file (path == shard_path(kind, group))."""

    kind: Kind
    group: str
    count: int = Field(ge=0)
    path: ShardPath


class CasesIndex(Strict):
    """cases/index.json: the shard directory, the engine list and the public asset base."""

    version: str  # contract schema version; the release tag stays in the Manifest
    generated: AwareDatetime  # naive timestamps are rejected
    repo: str  # producer repository identity
    assets_base: str  # public HTTPS R2 base URL the ~/ paths resolve against
    engines: list[Engine]  # sorted by key; the union of every lane key used in the data
    groups: list[ShardRef]  # sorted by (kind, group); unique paths from shard_path()

    @field_validator("assets_base")
    @classmethod
    def _https_directory(cls, v: str) -> str:
        if not (v.startswith("https://") and v.endswith("/") and v[len("https://") : -1]):
            raise ValueError("assets_base must be an absolute https:// URL ending in /")
        return v

    @model_validator(mode="after")
    def _ordered_and_unique(self) -> CasesIndex:
        _require_sorted(self.engines, lambda e: e.key, "engines", "key")
        keys = [e.key for e in self.engines]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            raise ValueError(f"duplicate engine keys: {sorted(dupes)}")
        labels = [e.label for e in self.engines]
        ldupes = {label for label in labels if labels.count(label) > 1}
        if ldupes:
            raise ValueError(f"duplicate engine labels: {sorted(ldupes)}")
        _require_sorted(self.groups, lambda g: (g.kind, g.group), "groups", "(kind, group)")
        refs = [(g.kind, g.group) for g in self.groups]
        gdupes = {r for r in refs if refs.count(r) > 1}
        if gdupes:
            raise ValueError(f"duplicate groups: {sorted(gdupes)}")
        for g in self.groups:
            expected = shard_path(g.kind, g.group)
            if g.path != expected:
                raise ValueError(
                    f"shard path for {g.kind}/{g.group} must be {expected}, got {g.path}"
                )
        paths = [g.path for g in self.groups]
        pdupes = {p for p in paths if paths.count(p) > 1}
        if pdupes:
            raise ValueError(f"duplicate shard paths: {sorted(pdupes)}")
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
            unknown |= c.lane_keys() - known
        if unknown:
            raise ValueError(f"engine keys missing from the index engines: {sorted(unknown)}")
        return shard


def engines_from_cases(cases: Iterable[Case], labels: dict[str, str]) -> list[Engine]:
    """The index engine list the way the producer must build it (review H1).

    The published pages' ``ENGINES`` consts are display metadata and are already stale: they
    miss lanes that only ever appear as dropped/unavailable/superseded pins. The union of the
    lane keys used by the data is the only list that satisfies ``validate_shard`` on every
    shard. Keys missing from ``labels`` default to the key itself; the producer disambiguates
    colliding display names itself (``jubarte-rust`` -> ``"jubarte (rust)"``, review M5).
    """
    keys: set[str] = set()
    for case in cases:
        keys |= case.lane_keys()
    return [Engine(key=k, label=labels.get(k, k)) for k in sorted(keys)]


def validate_bundle(index: CasesIndex, shards: Iterable[CaseShard]) -> None:
    """Cross-check a whole release bundle in one call (review L8): every ShardRef has
    exactly one shard with a matching count and no unknown lane keys, and every shard is
    registered. All problems are collected into a single ValueError."""
    problems: list[str] = []
    by_ref: dict[tuple[str, str], list[CaseShard]] = {}
    for shard in shards:
        by_ref.setdefault((shard.kind, shard.group), []).append(shard)
    for ref in index.groups:
        found = by_ref.get((ref.kind, ref.group), [])
        if not found:
            problems.append(f"{ref.kind}/{ref.group}: no shard in the bundle")
        elif len(found) > 1:
            problems.append(
                f"{ref.kind}/{ref.group}: {len(found)} shards in the bundle, expected exactly one"
            )
        else:
            try:
                index.validate_shard(found[0])
            except ValueError as err:
                problems.append(f"{ref.kind}/{ref.group}: {err}")
    registered = {(g.kind, g.group) for g in index.groups}
    for kind, group in sorted(set(by_ref) - registered):
        problems.append(f"{kind}/{group}: shard not registered in the index")
    if problems:
        raise ValueError(
            f"{len(problems)} problem(s) in the cases bundle:\n  " + "\n  ".join(problems)
        )


# ---- speed.json: the 10,000-input speed page (not the RESULTS.md leaderboard) ----------------


class SpeedCoverage(Strict):
    tool: str
    completed: int = Field(ge=0)
    planned: int = Field(ge=0)

    @model_validator(mode="after")
    def _check_coverage(self) -> SpeedCoverage:
        if self.completed > self.planned:
            raise ValueError(f"completed ({self.completed}) exceeds planned ({self.planned})")
        return self


class SpeedRow(Strict):
    tool: str
    bucket: str  # pairs / category / quartile bucket
    n: int = Field(ge=0)
    ok: int = Field(ge=0)
    failed: int = Field(ge=0)
    median_ms: Duration | None = None  # None when all calls failed; a kept null, never dropped
    mean_ms: Duration | None = None
    p95_ms: Duration | None = None
    p99_ms: Duration | None = None
    max_ms: Duration | None = None
    ok_total_s: Duration | None = None

    @model_validator(mode="after")
    def _check_row(self) -> SpeedRow:
        if self.ok + self.failed != self.n:
            raise ValueError(
                f"ok + failed must equal n (ok={self.ok}, failed={self.failed}, n={self.n})"
            )
        return self


class SpeedTable(Strict):
    id: str  # common / category / quartile
    title: str
    bucket_header: str  # the bucket column's label
    rows: list[SpeedRow]


class SpeedNote(Strict):
    """Notes prose. Plain text: the viewer escapes every string before rendering.

    ``backtick spans`` mean inline code (the viewer renders them as <code> after escaping);
    no other convention is attached to the text. No field in this contract rejects markup —
    the escaping viewer is the only line of defence, everywhere (review M2).
    """

    heading: str
    paragraphs: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)


class SpeedTask(Strict):
    id: str  # redlines / pdf
    title: str
    description: str  # plain text; `backtick spans` are inline code (see SpeedNote)
    available: bool = True  # False renders as "Not measured yet."
    planned: int | None = None
    timeout_ms: int | None = None
    quartile_cuts_bytes: list[int] = Field(default_factory=list)
    coverage: list[SpeedCoverage] = Field(default_factory=list)
    tables: list[SpeedTable] = Field(default_factory=list)  # sorted by id; unique ids
    notes: list[SpeedNote] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_task(self) -> SpeedTask:
        _require_sorted(self.tables, lambda t: t.id, "tables", "id")
        ids = [t.id for t in self.tables]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate table ids: {sorted(dupes)}")
        if not self.available and (self.tables or self.coverage):
            raise ValueError("an unavailable task carries no tables or coverage")
        if self.available and not self.tables:
            raise ValueError("an available task must carry tables")
        return self


class Speed(Strict):
    version: str
    generated: AwareDatetime  # naive timestamps are rejected
    repo: str
    title: str
    intro: str
    tasks: list[SpeedTask]  # sorted by id; unique ids

    @model_validator(mode="after")
    def _check_speed(self) -> Speed:
        _require_sorted(self.tasks, lambda t: t.id, "tasks", "id")
        ids = [t.id for t in self.tasks]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate task ids: {sorted(dupes)}")
        return self


# Optional metadata whose None means "absent": the only keys the compact form may omit
# (review M6). Data nulls — score metrics, page-list slots, SpeedRow statistics — are kept.
COMPACT_DROP_NONE: frozenset[str] = frozenset(
    {
        # Case: optional per-case metadata
        "shown", "stem", "lang", "type", "set", "sets", "state", "pair",
        # SpeedTask: optional run metadata
        "planned", "timeout_ms",
        # Manifest: optional viewer pin
        "viewer_version",
    }
)


def dumps(model: BaseModel) -> str:
    """Canonical JSON: sorted keys, 2-space indent, trailing newline. Hash this text.

    ``allow_nan=False``: the serializer refuses NaN/±inf rather than emitting tokens that
    are not JSON (RFC 8259) and that ``JSON.parse`` rejects.
    """
    data = model.model_dump(mode="json")
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def dumps_compact(model: BaseModel) -> str:
    """Compact release JSON: sorted keys, no whitespace (case shards, speed.json).

    Derived from ``model_dump(mode="json")`` — the same scalar spellings ``dumps`` writes,
    timestamps included — so the two are one encoding at two indents (review H3/L7). None
    values are dropped only for the optional metadata fields in ``COMPACT_DROP_NONE``;
    every other null (scores metrics, page slots, SpeedRow statistics) is data and is kept.
    """
    data = _drop_optional_metadata(model.model_dump(mode="json"))
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _drop_optional_metadata(value: object) -> object:
    if isinstance(value, dict):
        return {
            k: _drop_optional_metadata(v)
            for k, v in value.items()
            if not (v is None and k in COMPACT_DROP_NONE)
        }
    if isinstance(value, list):
        return [_drop_optional_metadata(v) for v in value]
    return value
