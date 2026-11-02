"""Plan 3b contract: case shards, the cases index, the speed page, and dumps_compact."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from bench_ops.models import (
    Case,
    CaseShard,
    CasesIndex,
    Engine,
    MetricDiagnostics,
    ShardRef,
    Speed,
    SpeedCoverage,
    SpeedNote,
    SpeedRow,
    SpeedTable,
    SpeedTask,
    dumps_compact,
)

GENERATED = datetime(2026, 11, 2, 12, 30, tzinfo=UTC)
BASE = "https://pub-92c80eda1059428394970066c8500eda.r2.dev/"


def make_case(**over: object) -> Case:
    """A conversion case shaped like the published DATA (nulls, partial scores, drift)."""
    payload: dict = {
        "kind": "conversion",
        "group": "v1/clean",
        "case": "doc-001",
        "pages": {
            "reference": ["~/d2p/v1/clean/doc-001/reference/page_001.webp"],
            "jubarte": ["~/d2p/v1/clean/doc-001/jubarte/page_001.webp"],
        },
        "page_counts": {"reference": 2, "jubarte": 2},
        "scores": {"jubarte": {"pixel": 98.2, "jaccard": None, "ssim": 99.1, "text_boundary": None}},
        "times": {"jubarte": 0.512},
        "versions": {"reference": "Word 16.115", "jubarte": "jubarte 0.14.0"},
        "dropped": [],
        "reference_app": "Word 16.115",
        "pdfs": {"jubarte": "~/d2p/v1/clean/doc-001/jubarte/pages.pdf"},
        "errors": {},
        "metric_diagnostics": {
            "jubarte": {"ref_pages": 2, "pages": 2, "scored_pages": 2, "max_break_drift": -11916}
        },
        "shown": [1],
    }
    payload.update(over)
    return Case.model_validate(payload)


def make_index(**over: object) -> CasesIndex:
    payload: dict = {
        "version": "1",
        "generated": GENERATED,
        "repo": "jandira-tech/neurotic_docx_bench",
        "assets_base": BASE,
        "engines": [
            {"key": "reference", "label": "Word"},
            {"key": "jubarte", "label": "jubarte"},
            {"key": "minipdf", "label": "MiniPdf (Rust)"},
        ],
        "groups": [
            {"kind": "conversion", "group": "v1/clean", "count": 1, "path": "cases/conversion/v1_clean.json"}
        ],
    }
    payload.update(over)
    return CasesIndex.model_validate(payload)


def make_shard(cases: list[Case] | None = None) -> CaseShard:
    return CaseShard(
        kind="conversion", group="v1/clean", cases=cases if cases is not None else [make_case()]
    )


def make_speed() -> Speed:
    return Speed.model_validate(
        {
            "version": "1",
            "generated": "2026-11-02T12:30:00+00:00",
            "repo": "jandira-tech/neurotic_docx_bench",
            "title": "Speed on 10,000 varied inputs",
            "intro": "One call per pair, engines run one after another on the same machine.",
            "tasks": [
                {
                    "id": "redlines",
                    "title": "Redline comparison",
                    "description": "Each engine compares 10,000 redline pairs.",
                    "planned": 10000,
                    "timeout_ms": 120000,
                    "quartile_cuts_bytes": [10601, 19102, 56197],
                    "coverage": [{"tool": "jubarte-rust", "completed": 10000, "planned": 10000}],
                    "tables": [
                        {
                            "id": "common",
                            "title": "Same pairs for every tool",
                            "bucket_header": "pairs",
                            "rows": [
                                {
                                    "tool": "jubarte-rust",
                                    "bucket": "10000 common",
                                    "n": 10000,
                                    "ok": 10000,
                                    "failed": 0,
                                    "median_ms": 45.0,
                                    "mean_ms": 83.0,
                                    "p95_ms": 246.8,
                                    "p99_ms": 671.7,
                                    "max_ms": 3818.1,
                                    "ok_total_s": 830.1,
                                }
                            ],
                        }
                    ],
                    "notes": [{"heading": "Machine", "paragraphs": ["Apple M4 Pro"], "bullets": []}],
                },
                {"id": "pdf", "title": "DOCX to PDF", "description": "Not measured yet.",
                 "available": False},
            ],
        }
    )


# ---- round trips ---------------------------------------------------------------------------


def test_shard_index_speed_round_trip_compact() -> None:
    for model in (make_shard(), make_index(), make_speed()):
        again = type(model).model_validate_json(dumps_compact(model))
        assert again == model


# ---- strictness ----------------------------------------------------------------------------


def test_unknown_fields_are_forbidden() -> None:
    samples = [
        (Engine, {"key": "jubarte", "label": "jubarte", "surprise": 1}),
        (MetricDiagnostics, {"ref_pages": 1, "pages": 1, "scored_pages": 1, "max_break_drift": 0,
                             "surprise": 1}),
        (Case, make_case().model_dump() | {"surprise": 1}),
        (CaseShard, make_shard().model_dump() | {"surprise": 1}),
        (ShardRef, {"kind": "conversion", "group": "g", "count": 1,
                    "path": "cases/conversion/g.json", "surprise": 1}),
        (CasesIndex, make_index().model_dump() | {"surprise": 1}),
        (SpeedCoverage, {"tool": "t", "completed": 1, "planned": 1, "surprise": 1}),
        (SpeedRow, {"tool": "t", "bucket": "b", "n": 1, "ok": 1, "failed": 0, "surprise": 1}),
        (SpeedTable, {"id": "common", "title": "t", "bucket_header": "pairs",
                      "rows": [], "surprise": 1}),
        (SpeedTask, {"id": "redlines", "title": "t", "description": "d", "surprise": 1}),
        (SpeedNote, {"heading": "h", "paragraphs": [], "bullets": [], "surprise": 1}),
        (Speed, make_speed().model_dump() | {"surprise": 1}),
    ]
    for model, payload in samples:
        with pytest.raises(ValidationError):
            model.model_validate(payload)


def test_metric_names_and_kinds_are_closed() -> None:
    with pytest.raises(ValidationError):
        make_case(scores={"jubarte": {"pixel": 1.0, "bogus": 2.0}})
    with pytest.raises(ValidationError):
        make_case(kind="docx")


# ---- metric values, nulls, diagnostics -----------------------------------------------------


def test_metric_value_bounds() -> None:
    for ok in (0.0, 100.0, 100.00000000000003):  # observed upstream pixel epsilon
        make_case(scores={"jubarte": {"pixel": ok}})
    for bad in (-0.1, 100.001, float("inf"), float("nan")):
        with pytest.raises(ValidationError):
            make_case(scores={"jubarte": {"pixel": bad}})


def test_scores_nulls_are_preserved() -> None:
    case = make_case()
    assert case.scores["jubarte"]["jaccard"] is None
    text = dumps_compact(make_shard())
    data = json.loads(text)["cases"][0]
    assert data["scores"]["jubarte"]["jaccard"] is None
    again = CaseShard.model_validate_json(text)
    assert again.cases[0].scores["jubarte"]["text_boundary"] is None


def test_null_page_slots_are_preserved() -> None:
    case = make_case(
        pages={"reference": ["~/r/1.webp"], "minipdf": [None]},
        page_counts={"reference": 1, "minipdf": 0},
        versions={"reference": "Word", "minipdf": "MiniPdf 0.3"},
    )
    text = dumps_compact(make_shard([case]))
    assert json.loads(text)["cases"][0]["pages"]["minipdf"] == [None]
    assert CaseShard.model_validate_json(text).cases[0].pages["minipdf"] == [None]


def test_max_break_drift_is_signed() -> None:
    for drift in (-11916, 0, 9310):
        assert MetricDiagnostics(ref_pages=1, pages=1, scored_pages=1, max_break_drift=drift).max_break_drift == drift
    for bad in (-1, -1, -1):
        with pytest.raises(ValidationError):
            MetricDiagnostics(ref_pages=bad, pages=1, scored_pages=1, max_break_drift=0)


# ---- asset paths and the base URL ----------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "https://pub.example/x.webp",  # absolute URL
        "http://pub.example/x.webp",  # wrong scheme
        "file:///tmp/x.webp",  # any scheme
        "/abs/x.webp",  # absolute path
        "rel/x.webp",  # missing the ~/ sentinel
        "~/a/../b.webp",  # parent traversal
        "~/a/./b.webp",  # current-dir segment
        "~/..",
        "~/a//b.webp",  # empty segment
        "~/a/b.webp/",  # trailing slash
        "",
        "~",
    ],
)
def test_asset_path_rejects_non_relative(path: str) -> None:
    with pytest.raises(ValidationError):
        make_case(pages={"reference": [path]}, shown=None)


@pytest.mark.parametrize(
    "path",
    [
        "~/x.webp",
        "~/gh-pages-tree/redlines/aec80553e5/reference/page_001.webp",
        "~/renew1030/redlines/x/jubarte-rust/pages.pdf",
    ],
)
def test_asset_path_accepts_relative_sentinel_paths(path: str) -> None:
    assert make_case(pages={"reference": [path]}, shown=None).pages["reference"] == [path]


def test_assets_base_must_be_https_directory() -> None:
    assert make_index().assets_base == BASE
    for bad in ("http://pub.example/", "https://pub.example", "ftp://pub.example/", "pub.example/",
                "https://", "https:///"):
        with pytest.raises(ValidationError):
            make_index(assets_base=bad)


# ---- index/shard cross checks --------------------------------------------------------------


def test_validate_shard_checks_engine_keys() -> None:
    index = make_index()
    shard = make_shard()
    assert index.validate_shard(shard) is shard
    stray = make_case(
        pages={"reference": ["~/r/1.webp"], "office2pdf": ["~/o/1.webp"]},
        page_counts={"reference": 1, "office2pdf": 1},
        scores={"office2pdf": {"pixel": 50.0}},
        times={"office2pdf": 1.0},
        versions={"reference": "Word", "office2pdf": "0.9"},
        dropped=["old-lane"],
        unavailable=["future-lane"],
    )
    with pytest.raises(ValueError, match="office2pdf"):
        index.validate_shard(make_shard([stray]))


def test_validate_shard_checks_registration_and_count() -> None:
    index = make_index()
    with pytest.raises(ValueError, match="count"):
        index.validate_shard(make_shard([make_case(), make_case(case="doc-002")]))
    with pytest.raises(ValueError, match="registered"):
        index.validate_shard(CaseShard(kind="redline", group="ar/accept_all", cases=[]))


def test_index_rejects_duplicate_engines_and_groups() -> None:
    with pytest.raises(ValidationError):
        make_index(engines=[{"key": "jubarte", "label": "a"}, {"key": "jubarte", "label": "b"}])
    dup = [{"kind": "conversion", "group": "v1/clean", "count": 1,
            "path": "cases/conversion/v1_clean.json"}] * 2
    with pytest.raises(ValidationError):
        make_index(groups=dup)


# ---- failure semantics ---------------------------------------------------------------------


def test_failed_engine_keeps_its_scores_entry() -> None:
    failed = make_case(
        pages={"reference": ["~/r/1.webp"], "minipdf": [None]},
        page_counts={"reference": 1, "minipdf": 0},
        versions={"reference": "Word 16.115", "minipdf": "MiniPdf 0.3"},
        scores={"minipdf": {"pixel": 0}},
        errors={"minipdf": "convert crashed"},
    )
    assert failed.scores["minipdf"] == {"pixel": 0}
    with pytest.raises(ValidationError):
        make_case(
            pages={"reference": ["~/r/1.webp"]},
            page_counts={"reference": 1, "minipdf": 0},
            versions={"reference": "Word 16.115", "minipdf": "MiniPdf 0.3"},
            scores={},
            errors={"minipdf": "convert crashed"},
        )


def test_unavailable_and_dropped_lanes_may_explain_without_scores() -> None:
    case = make_case(
        unavailable=["stemma-0.5.0"],
        dropped=["jubarte-0.11.0"],
        versions={"reference": "Word 16.115", "jubarte": "jubarte 0.14.0",
                  "stemma-0.5.0": "stemma 0.5.0", "jubarte-0.11.0": "jubarte 0.11.0"},
        errors={
            "stemma-0.5.0": "No published PDF or score; superseded lane",
            "jubarte-0.11.0": "superseded pin",
        },
    )
    assert set(case.errors) == {"stemma-0.5.0", "jubarte-0.11.0"}


def test_dropped_engine_must_not_be_active() -> None:
    make_case(  # a dropped lane may still carry its version label
        dropped=["minipdf"],
        versions={"reference": "Word 16.115", "jubarte": "jubarte 0.14.0", "minipdf": "MiniPdf 0.3"},
    )
    with pytest.raises(ValidationError):
        make_case(dropped=["jubarte"])  # jubarte is active by default


# ---- page counts vs shown ------------------------------------------------------------------


def test_page_counts_and_shown_are_independent() -> None:
    case = make_case(
        pages={"reference": ["~/r/1.webp"], "jubarte": ["~/j/1.webp"]},
        shown=[1],
        page_counts={"reference": 7, "jubarte": 5},  # whole-document counts, not shown counts
    )
    assert case.page_counts == {"reference": 7, "jubarte": 5}
    make_case(shown=None)  # absent shown is the historical convention
    with pytest.raises(ValidationError):
        make_case(page_counts={"reference": -1})


def test_shown_shape() -> None:
    make_case(shown=[1])
    with pytest.raises(ValidationError):
        make_case(shown=[2, 1])  # not strictly increasing
    with pytest.raises(ValidationError):
        make_case(shown=[0, 1])  # page numbers are 1-based
    with pytest.raises(ValidationError):
        make_case(shown=[1, 2])  # length differs from the page lists


# ---- shard homogeneity ---------------------------------------------------------------------


def test_shard_holds_one_kind_and_group() -> None:
    with pytest.raises(ValidationError):
        CaseShard(kind="conversion", group="v1/clean", cases=[make_case(kind="redline")])
    with pytest.raises(ValidationError):
        CaseShard(kind="conversion", group="v1/clean", cases=[make_case(group="corpus/clean")])
    with pytest.raises(ValidationError):
        make_shard([make_case(), make_case()])  # duplicate case id within the shard


# ---- speed notes are plain text ------------------------------------------------------------


def test_speed_notes_reject_markup() -> None:
    SpeedNote(heading="Machine", paragraphs=["Apple M4 Pro"], bullets=["128 GB RAM"])
    for bad in ({"heading": "a <b>x</b>"}, {"paragraphs": ["p < 10 ms"]}, {"bullets": ["<script>"]}):
        with pytest.raises(ValidationError):
            SpeedNote.model_validate({"heading": "h", "paragraphs": [], "bullets": [], **bad})


# ---- dumps_compact -------------------------------------------------------------------------


def test_dumps_compact_is_sorted_and_indent_free() -> None:
    for model in (make_shard(), make_index(), make_speed()):
        text = dumps_compact(model)
        assert "\n" not in text
        assert list(json.loads(text)) == sorted(json.loads(text))
        assert text == json.dumps(
            json.loads(text), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )


def test_dumps_compact_drops_only_optional_metadata() -> None:
    case = make_case(
        stem=None,
        shown=None,
        pages={"reference": ["~/r/1.webp"], "minipdf": [None]},
        page_counts={"reference": 1, "minipdf": 0},
        versions={"reference": "Word", "minipdf": "MiniPdf 0.3"},
        errors={"minipdf": "convert crashed"},
        scores={"jubarte": {"pixel": 98.2, "jaccard": None, "ssim": 99.1, "text_boundary": None},
                "minipdf": {"pixel": 0}},
    )
    data = json.loads(dumps_compact(make_shard([case])))["cases"][0]
    for gone in ("stem", "shown", "pair", "set", "sets", "state", "lang", "type"):
        assert gone not in data
    assert data["scores"]["jubarte"]["jaccard"] is None  # scores nulls are kept
    assert data["pages"]["minipdf"] == [None]  # page gaps are kept
    assert data["errors"] == {"minipdf": "convert crashed"}


def test_dumps_compact_speed_optionals() -> None:
    task = json.loads(dumps_compact(make_speed()))["tasks"][1]
    assert task["available"] is False
    for gone in ("planned", "timeout_ms", "quartile_cuts_bytes", "coverage", "tables", "notes"):
        assert gone not in task or task[gone] == []
