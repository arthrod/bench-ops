"""The adversarial review of the plan 3b contract (/tmp/rev-3b.md): one test per finding.

Every test here was written against the reviewed code and failed before the fix (TDD);
``test_models_cases.py`` keeps the original contract tests.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from bench_ops.models import (
    COMPACT_DROP_NONE,
    GROUP_KINDS,
    Case,
    CaseShard,
    CasesIndex,
    Row,
    Speed,
    SpeedCoverage,
    SpeedNote,
    SpeedRow,
    SpeedTask,
    Strict,
    dumps,
    dumps_compact,
    engines_from_cases,
    shard_path,
    validate_bundle,
)
from tests.test_models_cases import (
    make_case,
    make_index,
    make_shard,
    make_speed,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Real text from the published speed page (results/speed_10k/redlines/SUMMARY.md and NOTES.md).
REAL_DESCRIPTION = (
    "One call per pair, engines run one after another on the same machine. Node lanes time an "
    "in-memory `compare(base, next)`; `jubarte-rust` spawns the CLI per pair (process start and "
    "file I/O included); SuperDoc's SDK is file based (open, compare, apply, save). No redline is "
    "kept."
)
REAL_BULLET = (
    "The 1,335 timings were read from the live process through the Node inspector (SIGUSR1, then "
    "`Runtime.queryObjects` over live arrays to find the lane's `samples`, `sampleKeys` and "
    "`outSizes`). The keys are contiguous from p00000 to p01334, so no pair in that range failed."
)


# ---- H1: the index engine list is the union of every lane key in the data ------------------


def test_h1_engines_from_cases_is_the_union_of_every_lane_key() -> None:
    case = make_case(
        versions={
            "reference": "Word 16.115",
            "jubarte": "jubarte 0.14.0",
            "jubarte-0.11.0": "jubarte 0.11.0",
            "stemma-0.5.0": "stemma 0.5.0",
            "na-lane": "na 1",
        },
        dropped=["jubarte-0.11.0"],
        unavailable=["stemma-0.5.0"],
        not_applicable=["na-lane"],
    )
    engines = engines_from_cases([case], {"reference": "Word"})
    keys = [e.key for e in engines]
    assert keys == sorted(keys)  # sorted by key
    assert {"reference", "jubarte", "jubarte-0.11.0", "stemma-0.5.0", "na-lane"} <= set(keys)
    labels = {e.key: e.label for e in engines}
    assert labels["reference"] == "Word"
    assert labels["na-lane"] == "na-lane"  # label defaults to the key
    # an index built from the union validates shards full of superseded/dropped pins
    index = make_index(engines=[{"key": k, "label": v} for k, v in sorted(labels.items())])
    assert index.validate_shard(make_shard([case])) is not None


# ---- H2: every float is finite; the serializers never emit NaN/Infinity --------------------


def test_h2_times_and_speed_stats_reject_nan_inf_and_negatives() -> None:
    for bad in (float("nan"), float("inf"), float("-inf"), -5.0):
        with pytest.raises(ValidationError):
            make_case(times={"jubarte": bad})
    for stat in ("median_ms", "mean_ms", "p95_ms", "p99_ms", "max_ms", "ok_total_s"):
        for bad in (float("nan"), float("inf"), -1.0):
            with pytest.raises(ValidationError, match=stat):
                SpeedRow(tool="t", bucket="b", n=1, ok=1, failed=0, **{stat: bad})
    # dumps() refuses NaN/Infinity instead of writing tokens JSON.parse rejects
    nan_row = Row(
        rank="1", tool="t", pin="p", median=float("nan"), mean=1.0, docs=1, failed=0, note="n"
    )
    with pytest.raises(ValueError):
        dumps(nan_row)


# ---- H3 + L7: one serializer pair, two spellings of the same model --------------------------


def _minus_dropped(full: object, compact: object) -> object:
    """full minus exactly the allow-listed None metadata keys compact omits (recursive)."""
    if isinstance(full, dict):
        out: dict = {}
        for k, v in full.items():
            if k not in compact:
                assert v is None, f"compact dropped non-None key {k!r}"
                assert k in COMPACT_DROP_NONE, f"compact dropped non-allow-listed key {k!r}"
                continue
            out[k] = _minus_dropped(v, compact[k])
        return out
    if isinstance(full, list):
        assert len(full) == len(compact)
        return [_minus_dropped(a, b) for a, b in zip(full, compact)]
    return full


def test_h3_serializers_agree_modulo_dropped_metadata() -> None:
    for model in (make_shard(), make_index(), make_speed()):
        full = json.loads(dumps(model))
        compact = json.loads(dumps_compact(model))
        assert compact == _minus_dropped(full, compact)
        if "generated" in full:  # timestamps spelled identically by both serializers
            assert full["generated"] == compact["generated"]


def test_l7_dumps_compact_handles_every_json_scalar() -> None:
    class Odd(Strict):
        when: date
        amount: Decimal
        uid: UUID

    odd = Odd(when=date(2026, 11, 2), amount=Decimal("1.25"), uid=UUID(int=7))
    data = json.loads(dumps_compact(odd))
    assert data == {"amount": "1.25", "uid": str(UUID(int=7)), "when": "2026-11-02"}
    assert json.loads(dumps(odd)) == data


# ---- M1: the release bytes are reproducible: list order is part of the contract -------------


def test_m1_release_lists_must_be_sorted() -> None:
    assert [e.key for e in make_index().engines] == sorted(e.key for e in make_index().engines)
    with pytest.raises(ValidationError, match="engines must be sorted by key"):
        make_index(engines=[{"key": "reference", "label": "Word"}, {"key": "jubarte", "label": "j"}])
    with pytest.raises(ValidationError, match="groups must be sorted by"):
        make_index(
            groups=[
                {"kind": "conversion", "group": "wordcraft1014/convert", "count": 0,
                 "path": shard_path("conversion", "wordcraft1014/convert")},
                {"kind": "conversion", "group": "v1/clean", "count": 0,
                 "path": shard_path("conversion", "v1/clean")},
            ]
        )
    with pytest.raises(ValidationError, match="cases must be sorted by case"):
        make_shard([make_case(case="doc-002"), make_case()])
    with pytest.raises(ValidationError, match="tasks must be sorted by id"):
        speed = make_speed().model_dump()
        Speed.model_validate(speed | {"tasks": list(reversed(speed["tasks"]))})
    with pytest.raises(ValidationError, match="tables must be sorted by id"):
        SpeedTask.model_validate(
            {
                "id": "redlines",
                "title": "t",
                "description": "d",
                "available": True,
                "tables": [
                    {"id": "quartile", "title": "t", "bucket_header": "b", "rows": []},
                    {"id": "category", "title": "t", "bucket_header": "b", "rows": []},
                ],
            }
        )


# ---- M2: notes are plain text; backtick spans are inline code; nothing rejects markup -------


def test_m2_notes_are_plain_text_with_backtick_code_spans() -> None:
    note = SpeedNote.model_validate(
        {"heading": "Run of 2026-09-29 (00:45 to 09:23 EDT)", "paragraphs": [], "bullets": [REAL_BULLET]}
    )
    assert "`Runtime.queryObjects`" in note.bullets[0]  # real published text validates
    # no field rejects markup any more: the viewer escapes every string
    SpeedNote.model_validate(
        {"heading": "h", "paragraphs": ["p < 10 ms stays text"], "bullets": ["<code>x</code>"]}
    )
    SpeedTask.model_validate(
        {"id": "pdf", "title": "DOCX to PDF", "description": REAL_DESCRIPTION, "available": False}
    )
    assert "backtick" in (SpeedNote.__doc__ or "")  # the convention is documented on the model


# ---- M3: a lane may render fewer pages than shown; trailing nulls are the padding ----------


def test_m3_page_lists_may_be_shorter_than_shown() -> None:
    stripped = {
        "pages": {"reference": ["~/r/1.webp", "~/r/2.webp"], "minipdf": ["~/m/1.webp"]},
        "page_counts": {"reference": 2, "minipdf": 1},
        "versions": {"reference": "Word", "minipdf": "MiniPdf 0.3"},
        "scores": {"minipdf": {"pixel": 1.0}},
        "times": {"minipdf": 0.1},
        "pdfs": {},
        "errors": {},
        "metric_diagnostics": {},
    }
    unpadded = make_case(shown=[1, 2], **stripped)
    assert len(unpadded.pages["minipdf"]) == 1  # fewer pages than shown, no padding
    padded = make_case(
        shown=[1, 2],
        **(stripped | {"pages": {"reference": ["~/r/1.webp", "~/r/2.webp"],
                                 "minipdf": ["~/m/1.webp", None]}}),
    )
    assert padded.pages["minipdf"][-1] is None  # trailing null slots are the padding marker
    with pytest.raises(ValidationError, match="longer than shown"):
        make_case(shown=[1], **(stripped | {"pages": {"reference": ["~/r/1.webp"],
                                                      "minipdf": ["~/m/1.webp", "~/m/2.webp"]}}))


# ---- M4: a shard's path is derived from its (kind, group) ----------------------------------


def test_m4_shard_path_is_derived_from_kind_and_group() -> None:
    assert shard_path("conversion", "v1/clean") == "cases/conversion/v1--clean.json"
    assert shard_path("redline", "wordcraft1014/compare") == "cases/redline/wordcraft1014--compare.json"
    make_index()  # the fixture path follows the rule
    with pytest.raises(ValidationError, match="shard path"):
        make_index(
            groups=[{"kind": "conversion", "group": "v1/clean", "count": 1,
                     "path": "cases/conversion/v1_clean.json"}]
        )
    with pytest.raises(ValidationError, match="secret"):
        make_index(
            groups=[{"kind": "conversion", "group": "v1/clean", "count": 1,
                     "path": "secret/anything.json"}]
        )
    # two groups may not share one file, not even through a slug collision
    # ("a--b" sorts before "a/b": '-' is 0x2d, '/' is 0x2f)
    with pytest.raises(ValidationError, match="duplicate shard paths"):
        make_index(
            groups=[
                {"kind": "conversion", "group": "a--b", "count": 0,
                 "path": shard_path("conversion", "a--b")},
                {"kind": "conversion", "group": "a/b", "count": 0,
                 "path": shard_path("conversion", "a/b")},
            ]
        )


# ---- M5: engine labels are unique -----------------------------------------------------------


def test_m5_engine_labels_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="duplicate engine labels"):
        make_index(
            engines=[
                {"key": "jubarte", "label": "jubarte"},
                {"key": "jubarte-rust", "label": "jubarte"},
                {"key": "minipdf", "label": "MiniPdf (Rust)"},
                {"key": "reference", "label": "Word"},
            ]
        )
    ok = make_index(
        engines=[
            {"key": "jubarte", "label": "jubarte"},
            {"key": "jubarte-rust", "label": "jubarte (rust)"},
            {"key": "minipdf", "label": "MiniPdf (Rust)"},
            {"key": "reference", "label": "Word"},
        ]
    )
    assert ok.engines[1].label == "jubarte (rust)"


# ---- M6: the compact form drops None only for allow-listed metadata ------------------------


def test_m6_compact_drops_only_allowlisted_metadata() -> None:
    row = SpeedRow(tool="t", bucket="b", n=1, ok=0, failed=1)  # every statistic is None
    data = json.loads(dumps_compact(row))
    for stat in ("median_ms", "mean_ms", "p95_ms", "p99_ms", "max_ms", "ok_total_s"):
        assert data[stat] is None, f"{stat} nulls are data, not metadata"
    case = json.loads(dumps_compact(make_shard([make_case(shown=None)])))["cases"][0]
    assert "stem" not in case and "shown" not in case  # allow-listed None metadata is dropped
    assert case["scores"]["jubarte"]["jaccard"] is None  # scores nulls are data
    for field in ("shown", "stem", "lang", "type", "set", "sets", "state", "pair",
                  "planned", "timeout_ms"):
        assert field in COMPACT_DROP_NONE
    for field in ("median_ms", "ok_total_s", "scores", "pages", "errors"):
        assert field not in COMPACT_DROP_NONE


# ---- L1: shown pages require a reference lane of the same length ---------------------------


def test_l1_shown_requires_a_reference_lane_of_equal_length() -> None:
    with pytest.raises(ValidationError, match="reference"):
        make_case(
            pages={"jubarte": ["~/j/1.webp"]},
            page_counts={"reference": 1, "jubarte": 1},
            versions={"reference": "Word 16.115", "jubarte": "jubarte 0.14.0"},
        )
    with pytest.raises(ValidationError, match="reference"):
        make_case(
            pages={"reference": ["~/r/1.webp"], "jubarte": ["~/j/1.webp"]},
            page_counts={"reference": 1, "jubarte": 1},
            versions={"reference": "Word 16.115", "jubarte": "jubarte 0.14.0"},
            pdfs={},
            errors={},
            metric_diagnostics={},
            shown=[1, 2],
        )
    assert make_case().shown == [1]  # the exact-length reference lane is the norm


# ---- L2: kind is checked against the group policy ------------------------------------------


def test_l2_kind_is_checked_against_group_policy() -> None:
    assert GROUP_KINDS["redlines/"] == "redline"
    assert GROUP_KINDS["wordcraft1014/compare"] == "redline"
    assert GROUP_KINDS["wordcraft1014/convert"] == "conversion"
    with pytest.raises(ValidationError, match="v1/clean"):
        make_case(kind="redline")  # v1/clean is a conversion group
    with pytest.raises(ValidationError, match="wordcraft1014/compare"):
        make_case(kind="conversion", group="wordcraft1014/compare")
    with pytest.raises(ValidationError, match="future-corp/x"):
        make_case(group="future-corp/x")  # unregistered prefix: the error names the group
    assert make_case(kind="redline", group="ar/accept_all").kind == "redline"


# ---- L3: per-case lane invariants -----------------------------------------------------------


def test_l3_versions_cover_lanes_and_scores_are_never_empty() -> None:
    with pytest.raises(ValidationError, match="version"):
        make_case(versions={"reference": "Word 16.115"})  # jubarte lane without a version
    make_case(dropped=["minipdf"])  # a dropped lane carries no version and needs none
    with pytest.raises(ValidationError, match="page_counts"):
        make_case(
            pages={"reference": ["~/r/1.webp"], "ghost": ["~/g/1.webp"]},
            page_counts={"reference": 1},
            versions={"reference": "Word 16.115", "ghost": "1"},
            shown=None,
        )
    with pytest.raises(ValidationError, match="empty"):
        make_case(scores={"jubarte": {}})


# ---- L4: speed tables reconcile ------------------------------------------------------------


def test_l4_speed_numbers_reconcile() -> None:
    with pytest.raises(ValidationError, match=r"ok \+ failed"):
        SpeedRow(tool="t", bucket="b", n=2, ok=1, failed=0, median_ms=1.0)
    with pytest.raises(ValidationError, match="planned"):
        SpeedCoverage(tool="t", completed=11, planned=10)
    speed = make_speed().model_dump()
    with pytest.raises(ValidationError, match="duplicate task ids"):
        Speed.model_validate(speed | {"tasks": [speed["tasks"][0], speed["tasks"][0]]})
    task = make_speed().tasks[-1].model_dump()
    with pytest.raises(ValidationError, match="duplicate table ids"):
        SpeedTask.model_validate(task | {"tables": [task["tables"][0], task["tables"][0]]})
    with pytest.raises(ValidationError, match="unavailable"):
        SpeedTask.model_validate(
            {"id": "pdf", "title": "t", "description": "d", "available": False,
             "tables": [{"id": "x", "title": "t", "bucket_header": "b", "rows": []}]}
        )
    with pytest.raises(ValidationError, match="available"):
        SpeedTask.model_validate({"id": "pdf", "title": "t", "description": "d", "available": True})


# ---- L5: asset segments are any UTF-8, minus traversal and control chars --------------------


@pytest.mark.parametrize(
    "path",
    [
        "~/samples/CV/spaces in names/contract draft.webp",
        "~/docxide/samples/sample500kB/resume+50%.docx",
        "~/a/b+c.webp",
        "~/héllo/wörld/page_001.webp",
        "~/a/..x/interior.webp",
    ],
)
def test_l5_asset_segments_allow_any_utf8(path: str) -> None:
    assert make_case(pages={"reference": [path]}, shown=None).pages["reference"] == [path]


@pytest.mark.parametrize(
    "path",
    [
        "~/a/x\x01y.webp",  # control character
        "~/a/line\nbreak.webp",
        "~/./x.webp",
        "~/a/../b.webp",  # traversal segments
        "https://pub.example/x.webp",  # absolute stays rejected
        "~/a//b.webp",  # empty segment
    ],
)
def test_l5_asset_segments_reject_traversal_and_controls(path: str) -> None:
    with pytest.raises(ValidationError):
        make_case(pages={"reference": [path]}, shown=None)


# ---- L6: generated timestamps carry an offset -----------------------------------------------


def test_l6_generated_timestamps_must_be_aware() -> None:
    naive = {"generated": "2026-11-02T12:30:00"}
    with pytest.raises(ValidationError):
        CasesIndex.model_validate(make_index().model_dump() | naive)
    with pytest.raises(ValidationError):
        Speed.model_validate(make_speed().model_dump() | naive)
    assert make_index().generated.utcoffset() is not None
    assert make_speed().generated.utcoffset() is not None


# ---- L8: the whole bundle is cross-checked in one call --------------------------------------


def test_l8_validate_bundle_reports_every_problem() -> None:
    index = make_index(
        groups=[
            {"kind": "conversion", "group": "v1/clean", "count": 2,
             "path": shard_path("conversion", "v1/clean")},
            {"kind": "conversion", "group": "v1/tracking", "count": 0,
             "path": shard_path("conversion", "v1/tracking")},
        ]
    )
    complete = [
        make_shard([make_case(), make_case(case="doc-002")]),
        CaseShard(kind="conversion", group="v1/tracking", cases=[]),
    ]
    validate_bundle(index, complete)  # a complete bundle validates silently
    with pytest.raises(ValueError) as exc:  # every problem is collected into one error
        validate_bundle(
            index,
            [
                make_shard(),  # v1/clean: count 1 != 2
                CaseShard(kind="redline", group="ar/accept_all", cases=[]),  # not registered
            ],
        )
    msg = str(exc.value)
    assert "v1/tracking" in msg and "no shard" in msg
    assert "count" in msg
    assert "ar/accept_all" in msg and "not registered" in msg
    with pytest.raises(ValueError, match="exactly one"):
        validate_bundle(
            index,
            [
                make_shard(),  # v1/clean twice ...
                make_shard(),
                CaseShard(kind="conversion", group="v1/tracking", cases=[]),
            ],
        )


# ---- L9: real published data validates end to end -------------------------------------------


def test_l9_real_fixtures_validate_end_to_end() -> None:
    bundle = json.loads((FIXTURES / "cases_sample.json").read_text(encoding="utf-8"))
    assert (FIXTURES / "cases_sample.json").stat().st_size < 200_000
    index = CasesIndex.model_validate(bundle["index"])
    shards = [CaseShard.model_validate(s) for s in bundle["shards"]]
    for shard in shards:  # Case/CaseShard validate (strict, frozen)
        for case in shard.cases:
            assert Case.model_validate(case.model_dump()) == case
    validate_bundle(index, shards)
    groups = {(s.kind, s.group) for s in shards}
    assert sum(kind == "conversion" for kind, _ in groups) >= 3
    assert sum(kind == "redline" for kind, _ in groups) == 8  # every redline group
    assert all(len(s.cases) == 2 for s in shards)
    cases = [c for s in shards for c in s.cases]
    assert any(c.unavailable for c in cases)  # the sample keeps dropped/unavailable lanes
    assert any(c.dropped for c in cases)
    assert any(c.not_applicable for c in cases)
    assert any(  # and padded trailing null slots
        None in (lst[-1:] or ()) for c in cases for lst in c.pages.values()
    )
    assert all(  # every asset is relative to assets_base
        p is None or p.startswith("~/") for c in cases for lst in c.pages.values() for p in lst
    )
    assert all(p.startswith("~/") for c in cases for p in c.pdfs.values())

    assert (FIXTURES / "speed_real.json").stat().st_size < 200_000
    speed = Speed.model_validate(json.loads((FIXTURES / "speed_real.json").read_text(encoding="utf-8")))
    assert any(t.available and t.tables for t in speed.tasks)
    assert any(not t.available for t in speed.tasks)
    assert any("`Runtime.queryObjects`" in b for t in speed.tasks for n in t.notes for b in n.bullets)
