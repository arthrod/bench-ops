# NDB release-bundle contract survey

**Scope:** read-only survey of the gh-pages site, the benchmarks.law copy, the ndb producer/store code, and the existing bench-ops Pydantic contract. No repository file was changed.

## Executive conclusion

The release contract needs two new data products, not two more HTML snapshots:

1. **`cases.json`** — one strict, versioned wrapper for both the DOCX→PDF and redline case browsers. The current case shape is already stable enough to model, but its authoritative current bytes are split between inline HTML and ad hoc review-build artifacts. The canonical ndb JSONL stores do **not** contain the page/PDF URLs, shown-page selections, reference application, or most per-case metrics needed by these pages.
2. **`speed.json`** — a structured version of the 10,000-input speed page: coverage, three tables, notes, and the unmeasured PDF task. The current page is generated from Markdown, and the canonical `results/speed.jsonl` describes a different speed view. The 10k page can be reproduced from `results/speed_10k/`, but no JSON emitter exists.

The ndb producer can emit a **leaderboard** speed summary today through `ledger.rows.load_speed_rows()` → `ledger.policy.select_speed_headline()` → `ledger.tables.speed_tables()`. It cannot emit the **case-browser** or **10k speed-page** contracts from the canonical stores alone. The missing seam is a release adapter over a canonical case/asset record, not another viewer template.

The page images and shown-page PDFs should remain **R2 objects**. `cases.json` should carry their URLs or R2-relative paths; it should not carry image bytes.

### Page/source/emit summary

| Page | Data rendered today | Current source of truth | Can ndb emit it from its canonical stores today? |
|---|---|---|---|
| Case browser (DOCX→PDF) | 5,193 cases, per-engine pages/PDFs/page counts/scores/times/versions/errors/metadata | gh-pages `site/index.html` inline `DATA`; benchmarks.law `data.json` is the same data with relative R2 paths | **No.** `results/bench.jsonl`/`results/converters.jsonl` have scores but not the asset URLs, shown-page vectors, reference app, or full J/SSIM/TB case data. New case adapter and canonical asset records are missing. |
| Redline browser | 2,229 cases in 8 groups, same case shape | gh-pages `site/redlines/index.html` inline `DATA`; benchmarks.law `redlines/data.json` is equivalent after URL expansion | **No.** `results/redlines_0929_full/scores.jsonl` has aggregate rows; the split store has some per-pair rows. Neither contains page/PDF URLs or the complete current 2,229-case asset graph. New adapter is missing. |
| Speed | One redline task: 5 lanes, 5 coverage rows, 3 tables with 50 rows, 2 note sections; PDF task says “Not measured yet.” | `results/speed_10k/redlines/{SUMMARY.md,NOTES.md}` plus per-pair JSONL and `pairs_10k.csv`; rendered by `site/_build/speed/build_speed_site.py` | **Partly.** The 10k tables are computable with `results/speed_10k/summarize.py::{stats,table,redlines}`, but it writes Markdown, not JSON. `ledger.rows.load_speed_rows()` serves a different leaderboard view. New JSON adapter is missing. |
| Home | Static prose/KPIs/cards: 4 benches, 10,000 speed inputs, 13 editors, 99 checks, 1 reference, engine/lane counts, methodology and links | Hand-authored benchmarks.law `public/neurotic-docx-bench/home/index.html`; `scripts/sync.ts` only injects links/navigation | **No.** Some values could be derived from the future Cases/Speed files, but editor facts come from the separate editors-trial producer. No generator or machine-readable home contract exists. |
| Editors | Static page plus a 14-row scorecard (13 participants + LibreOffice baseline), 13 vendor cards, findings and report links | Hand-authored benchmarks.law `editors/index.html`; `scripts/sync.ts::assets()` calls `src/lib/scorecard.ts::rebuildScoreTable()` against the editors-trial main Markdown and `out/word_verdicts.json` | **No, and it should not be ndb’s job.** This belongs to the editors-trial (`et`) release/facts contract. |

## Evidence baseline

### Current artifacts

| Artifact | Bytes | Shape/count | gzip -9 |
|---|---:|---|---:|
| `site/index.html` | 18,237,455 | Inline `DATA`: 5,193 conversion cases | 1,090,637 for the JSON value alone |
| `site/redlines/index.html` | 10,924,723 | Inline `DATA`: 2,229 redline/accept/reject cases | 449,841 for the JSON value alone |
| `site/redlines/cases.json` | 171,539 | **Stale:** only 140 old cases | — |
| benchmarks.law `data.json` | 13,585,031 | `{base, cases}`; 5,193 cases | 1,035,332 |
| benchmarks.law `redlines/data.json` | 8,817,962 | `{base, cases}`; 2,229 cases | 424,594 |
| `site/speed/index.html` | 21,306 | Static Markdown-derived HTML | 5,038 for the whole page |
| benchmarks.law `speed/index.html` | 23,324 | Restyled static copy | — |
| benchmarks.law `home/index.html` | 13,387 | Static HTML | — |
| benchmarks.law `editors/index.html` | 26,211 | Static HTML plus generated scorecard | — |

The benchmarks.law conversion data is semantically identical to the gh-pages inline conversion data after expanding its `~/` paths against `base`; the same is true for the redline data. This was checked case by case, including nested maps and lists.

### Canonical ndb stores

| Store | Current shape | What it does not carry |
|---|---|---|
| `results/bench.jsonl` | 66 rows, 127,871,484 bytes; 63 rows have `per_doc` | Candidate/reference page images, shown-page PDFs, per-case J/SSIM/TB for the browser, reference app, asset URLs, R2 paths |
| `results/converters.jsonl` | 131 rows, 17,194,500 bytes; per-document converter scores and aggregate extra metrics | Page counts, shown-page selections, image/PDF URLs, per-document SSIM/TB, candidate/reference producer metadata |
| `results/speed.jsonl` | 126 rows, 109,583 bytes: 114 `ms_per_redline`, 12 `ms_per_render` | The 10k common/category/quartile tables and notes; even the 10k merged docxodus row is absent |
| `results/redlines_0929_full/scores.jsonl` | 6 aggregate action rows | Per-pair scores for the 1,400 compare cases; all asset URLs |
| `results/redlines_0929_split/scores.jsonl` | 8 action rows; split rows contain per-pair metrics for 400 accept/reject pairs | All current compare-case assets and the rest of the redline corpus |

The current redline `cases.json` cannot be treated as the release source: `site/_build/redlines/build_redlines_site.py::main()` writes 140 selected cases there, while the published inline page has since been renewed to 2,229 cases by the Phase 1028/1030 builders.

---

## 1. Page-by-page data contract

## 1.1 Case browser and redline browser: common case schema

### Envelope

The gh-pages pages embed a bare `list[Case]`. The benchmarks.law pages fetch:

```json
{
  "base": "https://pub-92c80eda1059428394970066c8500eda.r2.dev/",
  "cases": [ ... ]
}
```

`base` is the public R2 base URL. Page and PDF paths use a `~/` sentinel meaning “relative to `base`”; the law pages expand it once after fetching.

The release contract should use one envelope for both pages, add an explicit `kind`, and retain the public asset base.

### Case fields

`C` = conversion data (5,193 cases); `R` = redline data (2,229 cases).

| Field | Type | C presence | R presence | Meaning/current source |
|---|---|---:|---:|---|
| `kind` | `conversion \| redline` | new discriminator | new discriminator | Classifies the source page. Do not infer it from group spelling. |
| `group` | `str` | 5,193 | 2,229 | Composite identity component; 13 conversion groups, 8 redline groups. Direct from current `DATA`. |
| `case` | `str` | 5,193 | 2,229 | Composite identity component; C length 2–170, R length 10–143. Direct from current `DATA`. |
| `pages` | `dict[str, list[str \| null]]` | 5,193 | 2,229 | Engine → shown WebP pages. `null` is a deliberate gap for a failed/short candidate. |
| `page_counts` | `dict[str, int]` | 5,193 | 2,229 | Engine → total document pages, including pages not shown. |
| `scores` | `dict[str, dict[str, float \| null]]` | 5,193 | 2,229 | Engine → `pixel`, `jaccard`, `ssim`, `text_boundary`; 0–100 scale. Upstream nulls remain null. |
| `times` | `dict[str, float]` | 5,193 | 2,229 | Engine → seconds per conversion. C has 6,675 values in range 0.009–223.238; R is empty everywhere. |
| `versions` | `dict[str, str]` | 5,193 | 2,229 | Engine → pinned/version label for this case. Present for every engine key in every case. |
| `dropped` | `list[str]` | 5,193 | 2,229 | Engine keys hidden for this case. This is the field the viewer actually uses to suppress stale lanes. |
| `reference_app` | `str` | 5,193 | 2,229 | Word/Quartz producer note; C length 0–52, R length 14–52. |
| `pdfs` | `dict[str, str]` | 5,061 | 2,200 | Engine → shown-pages PDF URL/path. Optional because older cases may not have one. |
| `errors` | `dict[str, str]` | 3,325 | 1,298 | Engine → failure explanation. A failed engine may have a Pixel score of 0 and no assets. |
| `metric_diagnostics` | `dict[str, MetricDiagnostics]` | 3,964 | 2,200 | Engine → `ref_pages`, `pages`, `scored_pages`, `max_break_drift`. |
| `shown` | `list[int]` | 4,125 | 2,200 | Actual page numbers represented by each `pages` list. Absent means the historical “pages present” convention. |
| `stem` | `str` | 4,985 | 400 | Corpus/source document identity. |
| `lang` | `str` | 800 | 1,000 | Language code; C length 1–2, R length 1–2. |
| `type` | `str` | 800 | 1,000 | Document category (`legal`, `forms`, `technical`, etc.). |
| `set` | `str` | 3,325 | 400 | Cohort/source-set label. |
| `sets` | `str` | 400 | 1,400 | Semicolon-separated source-set labels. |
| `state` | `str` | 400 | 400 | Corpus document state. |
| `pair` | `str \| null` | 400, all `null` in C | 400, all strings in R | Comparison identity where meaningful. |
| `not_applicable` | `list[str]` | 400 | 400 | Engines excluded for a documented reason, not scored as zero. |
| `unavailable` | `list[str]` | 400 | 400 | Cohort-level engine lanes with no published measurement. |

### Nested sizes and edge cases

| Nested data | Conversion | Redline |
|---|---:|---:|
| Page slots | 58,663 | 30,256 |
| `null` page slots | 462 | 2,578 |
| Relative page-path length | 43–253 chars | 72–196 chars |
| Pixel values | 13,504; 0–100 | 7,200; 0–100 |
| Jaccard values | 18,936; 98 null | 5,582; 158 null |
| SSIM values | 18,936; 98 null | 5,582; 158 null |
| Text-boundary values | 17,636; 1,085 null | 5,570; 555 null |
| Diagnostic records | 12,626 | 5,553 |
| PDF paths | 19,173 | 7,753 |
| Version strings | 31,044 | 10,916 |
| Error strings | 191 | 2,047 |

`max_break_drift` is an **integer**, not a non-negative distance: observed values range from −11,916 to 6,288 in conversion data and −4,537 to 9,310 in redline data. The model must not impose `ge=0` on it.

The current top-level `VERSIONS` maps are stale fallbacks (for example, they say jubarte 0.12.0 while current per-case versions say 0.14.0). Per-case `versions` is complete, so the release should not depend on a global version map.

### Group populations

**Conversion groups**

| Group | Cases | Group | Cases |
|---|---:|---|---:|
| `v1/clean` | 2,478 | `corpus/with_comments_tracking` | 145 |
| `corpus/clean` | 977 | `v1/with_comments_clean` | 112 |
| `corpus/tracking_without_comments` | 476 | `v1/with_comments_tracking` | 98 |
| `wordcraft1014/convert` | 400 | `cases` | 76 |
| `v1/tracking_without_comments` | 237 | `new` | 74 |
| `corpus/with_comments_clean` | 62 | `scraped` | 53 |
| `samples` | 5 | | |

**Redline groups**

| Group | Cases | Group | Cases |
|---|---:|---|---:|
| `redlines/tracking_without_comments` | 902 | `redlines/with_comments_clean` | 2 |
| `wordcraft1014/compare` | 400 | `rejected/tracking_without_comments` | 8 |
| `ar/accept_all` | 400 | `accepted/tracking_without_comments` | 5 |
| `ar/reject_all` | 400 | `redlines/with_comments_tracking` | 112 |

### Where the data comes from today

**Historical conversion cases**

- `site/_build/build_site.py::docxide_case()` (lines 160–181) creates docxide-fixture rows.
- `site/_build/build_site.py::corpus_case()` (lines 183–207) creates the older corpus-sample rows.
- `site/_build/build_site.py::main()` (lines 242–290) assembles and writes the historical site.

**2026 v1 / Wordcraft conversion renewal**

- `site/_build/site_1028/prepare.py::v1()` (lines 28–51) reads `results/docx_to_pdf_v1_89/full.json` and `soffice_full.json` and defines the engine/version/error rows.
- `site/_build/site_1028/prepare.py::wordcraft()` (lines 95–123) reads the Wordcraft metadata/scores.
- `site/_build/site_1028/process.py::case()` (lines 63–108) creates `pages`, `page_counts`, scores, shown pages, and shown-page PDFs.
- `site/_build/site_1028/patch_site.py::{remote,merge_cases,patch_html}` (lines 37–119) injects those rows into the inline viewer.
- `site/_build/site_1029/common.py::{shown_pages,replace_case,patch_html}` (lines 20–83) renews the jubarte 0.14.0 conversion columns.

**Current redline / accept-reject renewal**

- `site/_build/site_1030/prepare.py::main()` (lines 54–168) joins the phase-1 redline and accept/reject inputs, hashes, versions, failures, and reference PDFs.
- `site/_build/site_1030/process.py::{empty_row,failure_row,encode_shown,case}` (lines 31–111) creates per-tool rows, shown-page WebPs/PDFs, metrics, and diagnostics.
- `site/_build/site_1030/common.py::{replace_case,patch_html,merge_ar}` (lines 38–104) replaces 1,400 compare cases and appends 800 accept/reject cases.
- `site/_build/site_1030/patch_site.py::main()` (lines 11–27) writes the preview. The current published redline `DATA` has the same 2,229 cases as that preview.

These builders live in the **gh-pages worktree**, not in `src/neurotic_docx_bench/`. They are review scripts, not a producer API.

### Can ndb emit the case contract today?

**No, not from the canonical stores.** The nearest existing ndb functions are:

- `ledger.rows.load_bench_rows()` — normalizes `results/bench.jsonl`; keeps aggregate and sometimes per-document scores.
- `ledger.rows.load_converter_rows()` — normalizes `results/converters.jsonl`.
- `ledger.converters.lines_from_report()` — creates converter store lines from reports.
- `ledger.tables.redline_action_section()` — renders redline action aggregates.

Those functions cannot reconstruct:

- the selected case population and its group/metadata vocabulary;
- reference and candidate WebP/PDF URLs or their R2 paths;
- `shown` page vectors and `null` gaps;
- `reference_app` and per-case versions;
- the complete conversion J/SSIM/TB records;
- complete current redline per-pair records and assets.

**Missing producer seam:** a canonical case-record/asset-manifest input plus a `cases.build_cases()` release adapter. The adapter should validate every URL against the served-asset manifest before publishing `results/cases.json`.

---

## 1.2 Speed page

### What it renders

The page has:

- one page title and methodology introduction;
- one available redline task;
- one unavailable PDF task.

The redline task contains:

| Data | Type | Count | Current source |
|---|---|---:|---|
| Coverage | `tool → completed/planned` | 5 lanes, 10,000 each | `SUMMARY.md` “Coverage”; `results/speed_10k/summarize.py::redlines()` lines 84–87 |
| Common-pairs table | `tool, bucket, n, ok, failed, median_ms, mean_ms, p95_ms, p99_ms, max_ms, ok_total_s` | 5 rows | `SUMMARY.md` “Same pairs for every tool”; `summarize.py::stats/table` |
| Category table | same row shape, bucket = category | 25 rows (5 tools × 5 categories) | `SUMMARY.md` “By pair category” |
| Size-quartile table | same row shape, bucket = Q1–Q4 | 20 rows (5 tools × 4 quartiles) | `SUMMARY.md` “By base size quartile” |
| Notes | heading + paragraphs/bullets | 2 sections | `results/speed_10k/redlines/NOTES.md` |
| PDF task | available=false | 1 | `site/_build/speed/build_speed_site.py::main()` emits “Not measured yet.” when `results/speed_10k/pdf/SUMMARY.md` is absent |

The current summary has 5 tools: `docxodus`, `jubarte-rust`, `jubarte-rust-inproc`, `jubarte-wasm`, `superdoc`. Quartile cuts are `[10601, 19102, 56197]` bytes; timeout is 120,000 ms.

### Builder and source path

- `site/_build/speed/build_speed_site.py::render()` (lines 38–79) converts the Markdown subset (headings, paragraphs, bullets, pipe tables) to HTML.
- `site/_build/speed/build_speed_site.py::main()` (lines 82–111) reads `results/speed_10k/{redlines,pdf}/SUMMARY.md` and optional `NOTES.md`.
- `results/speed_10k/summarize.py::stats()` (lines 27–42) computes successful-call distributions; failures are excluded from timing statistics and counted separately.
- `results/speed_10k/summarize.py::table()` (lines 44–58) writes the table rows.
- `results/speed_10k/summarize.py::redlines()` (lines 66–106) joins per-pair JSONL with `pairs_10k.csv`, computes category/quartile cuts, and writes `SUMMARY.md`.

### Can ndb emit it today?

**Not in the required shape.** `summarize.py` has all current page statistics but writes Markdown. A JSON emitter is missing.

There is also a second, different speed dataset:

- `ledger.rows.load_speed_rows()` reads `results/speed.jsonl` and `results/redline_speed_bench/**/summary.json`.
- `ledger.policy.select_speed_headline()` selects the latest eligible large-N/microbench rows.
- `ledger.tables.speed_tables()` renders the RESULTS.md speed leaderboard.

That is useful for `RESULTS.md`, but it cannot reproduce the 10k page’s common/category/quartile tables. Keep the two views distinct. If the release needs both, add a separate `headline` section to `speed.json`; do not make the page’s `tasks` tables impersonate the leaderboard.

A concrete inconsistency to resolve before freezing the contract: `results/speed_10k/redlines/summary.json` currently contains only one docxodus row with `pair_count=8665`, while the page uses a merged 10,000-row docxodus sample and five lanes. The page’s real source is `per_pair/*.jsonl` plus `pairs_10k.csv`, not that stale summary JSON.

---

## 1.3 Home page

The home page has no machine-readable data file. Its numbers and labels are literal HTML:

- 4 benches;
- 10,000 planned speed inputs;
- 13 editor-trial participants and 99 checks;
- 1 Word reference;
- 9 current conversion engines plus 11 pinned older versions;
- 5 speed lanes, Apple M4 Pro, 120 s timeout;
- four descriptive bench cards, six methodology steps, four metric definitions, and links.

**Current builder/source:** hand-authored `benchmarks-law/public/neurotic-docx-bench/home/index.html`, introduced with commit `7e0f8ee`. `scripts/sync.ts::assets()` only applies `patchNeuroticHtml()` and `benchPage()` (links/navbar); it does not generate the figures.

**Ndb status:** ndb cannot emit the page today. After Cases/Speed exist, ndb could derive its ndb-owned counts, but the editor counts and checks belong to the editors-trial release. The home page therefore needs either:

1. a static template whose few computed values are injected from Cases/Speed + ET facts; or
2. no release data contract at all, with explicit manual-review gates.

Do not put editor-trial facts into ndb’s `Cases` or `Speed` models.

---

## 1.4 Editors page

The editors page is an **editors-trial (`et`) artifact**, not an ndb artifact.

### Scorecard schema

The generated score table has 14 rows: 13 participants plus the LibreOffice baseline.

| Field | Type | Notes/source |
|---|---|---|
| `tool` | `str` | Editor name from the editors-trial Markdown scorecard |
| `weighted_pct` | `float` | Weighted score, 0–100 |
| `critical_passed`, `critical_total` | `int` | Currently 29 total critical features |
| `text_retained_pct` | `float` | Text retention, 0–100 |
| `parts_kept`, `parts_total` | `int` | Currently 35 total package parts |
| `failed_checks` | `int` | Failed check count; one row also carries a parenthetical caveat |
| `second_pass` | `str` | Byte-identical / stable-not-byte-identical / a second-pass score / trial-stamped |
| `word_opened` | `bool` plus optional label | Word 16.115 open/refusal verdict |
| `is_top` | `bool` | Presentation class |
| `is_baseline` | `bool` | LibreOffice calibration-floor class |

The rest of the page is static: title, KPIs, findings, 13 vendor cards, report links, method and limitations.

### Current builder/source

- `benchmarks-law/scripts/sync.ts::assets()` reads the editors-trial main report and `out/word_verdicts.json`, then calls `rebuildScoreTable()`.
- `benchmarks-law/src/lib/scorecard.ts::rebuildScoreTable()` parses the Markdown score table and updates the existing HTML table.
- The surrounding page is hand-authored in `public/neurotic-docx-bench/editors/index.html`.

**Ndb status:** ndb must not emit this. The editors-trial producer should expose these fields in its own `facts.json`/release contract (the `et` producer planned in the bench-ops work). The consumer should generate the scorecard rather than keep rewriting a copied HTML page.

---

## 2. Proposed Pydantic models

The following follows the strict style of `/Users/arthrod/bench/src/bench_ops/models.py`: `extra="forbid"`, frozen models, constrained aliases, and no unbounded `dict[str, Any]` at the contract boundary.

The comments on every field identify its current source. `generated` is the only field without a direct current source: it is the release-emission timestamp.

```python
"""Proposed ndb release contracts for the per-case browsers and the speed page."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


MetricName = Literal["pixel", "jaccard", "ssim", "text_boundary"]
MetricValue = Annotated[float, Field(ge=0, le=100.000001)]
AssetPath = Annotated[
    str,
    StringConstraints(pattern=r"^~/[A-Za-z0-9._~/-]+$"),
]


class Engine(Strict):
    key: str  # Current const ENGINES key.
    label: str  # Current const ENGINES display label.


class MetricDiagnostics(Strict):
    ref_pages: int = Field(ge=0)  # Current metric_diagnostics.*.ref_pages.
    pages: int = Field(ge=0)  # Current metric_diagnostics.*.pages.
    scored_pages: int = Field(ge=0)  # Current metric_diagnostics.*.scored_pages.
    max_break_drift: int  # Current value; signed, so no ge=0 constraint.


class Case(Strict):
    kind: Literal["conversion", "redline"]
    # NEW discriminator: source page; do not infer it from group spelling.

    group: str  # Current case.group; first half of composite identity.
    case: str  # Current case.case; second half of composite identity.

    pages: dict[str, list[AssetPath | None]]
    # Current case.pages: engine -> shown WebP paths; null is an intentional gap.

    page_counts: dict[str, int]
    # Current case.page_counts: engine -> total pages (not shown-page count).

    scores: dict[str, dict[MetricName, MetricValue | None]]
    # Current case.scores: engine -> 0..100 metrics; null preserves an
    # upstream metric that could not be computed.

    times: dict[str, float]
    # Current case.times: engine -> wall-clock seconds per conversion.

    versions: dict[str, str]
    # Current case.versions: engine -> per-case version/pin label.

    dropped: list[str]
    # Current case.dropped: engine keys hidden for this case.

    reference_app: str
    # Current case.reference_app: Word/PDF producer note.

    pdfs: dict[str, AssetPath] = Field(default_factory=dict)
    # Current optional case.pdfs: engine -> shown-pages PDF path.

    errors: dict[str, str] = Field(default_factory=dict)
    # Current optional case.errors: engine -> failure reason.

    metric_diagnostics: dict[str, MetricDiagnostics] = Field(default_factory=dict)
    # Current optional case.metric_diagnostics: engine -> page diagnostics.

    shown: list[int] | None = None
    # Current optional case.shown: page numbers represented by pages[].

    stem: str | None = None
    # Current optional case.stem: corpus/source document identity.

    lang: str | None = None
    # Current optional case.lang: language code.

    type: str | None = None
    # Current optional case.type: document category.

    set: str | None = None
    # Current optional case.set: cohort/source-set label.

    sets: str | None = None
    # Current optional case.sets: semicolon-separated source-set labels.

    state: str | None = None
    # Current optional case.state: corpus document state.

    pair: str | None = None
    # Current optional case.pair: comparison identity; null on some conversions.

    not_applicable: list[str] = Field(default_factory=list)
    # Current optional case.not_applicable: engines outside applicability.

    unavailable: list[str] = Field(default_factory=list)
    # Current optional case.unavailable: cohort lanes with no measurement.


class Cases(Strict):
    version: str
    # NEW contract schema version, e.g. "1"; release tag stays in Manifest.

    generated: datetime
    # NEW release-emission timestamp.

    repo: str
    # NEW/constant producer repository identity.

    base: str
    # Current benchmarks.law data.json base: public HTTPS R2 base URL.

    engines: list[Engine]
    # Union/ordered form of current const ENGINES from the two pages.

    cases: list[Case]
    # Current inline DATA, normalized to the proposed shape; conversion and
    # redline rows carry an explicit kind.


class SpeedCoverage(Strict):
    tool: str  # Current SUMMARY.md Coverage tool name.
    completed: int = Field(ge=0)  # Current “N of M planned” completed count.
    planned: int = Field(ge=0)  # Current “N of M planned” planned count.


class SpeedRow(Strict):
    tool: str  # Current table tool column.
    bucket: str  # Current table's pairs/category/quartile column.
    n: int = Field(ge=0)  # Current table n.
    ok: int = Field(ge=0)  # Current table ok.
    failed: int = Field(ge=0)  # Current table failed.
    median_ms: float | None = None  # Current table median ms.
    mean_ms: float | None = None  # Current table mean ms.
    p95_ms: float | None = None  # Current table p95 ms.
    p99_ms: float | None = None  # Current table p99 ms.
    max_ms: float | None = None  # Current table max ms.
    ok_total_s: float | None = None  # Current table ok total s.


class SpeedTable(Strict):
    id: str  # Stable table id: common, category, quartile.
    title: str  # Current SUMMARY.md table heading.
    bucket_header: str  # Current table column: pairs, category, or quartile.
    rows: list[SpeedRow]  # Current SUMMARY.md table body.


class SpeedNote(Strict):
    heading: str  # Current NOTES.md section heading.
    paragraphs: list[str] = Field(default_factory=list)
    # Current NOTES.md paragraphs under heading.
    bullets: list[str] = Field(default_factory=list)
    # Current NOTES.md bullets under heading.


class SpeedTask(Strict):
    id: str  # Current page section id: redlines or pdf.
    title: str  # Current rendered task/section title.
    description: str  # Current SUMMARY.md introductory paragraph.
    available: bool = True  # False for the current “Not measured yet” PDF task.
    planned: int | None = None  # Planned inputs for the task.
    timeout_ms: int | None = None  # Current 120 s per-call timeout.
    quartile_cuts_bytes: list[int] = Field(default_factory=list)
    # Current base-size quartile cuts.
    coverage: list[SpeedCoverage] = Field(default_factory=list)
    # Current SUMMARY.md Coverage list.
    tables: list[SpeedTable] = Field(default_factory=list)
    # Current SUMMARY.md tables.
    notes: list[SpeedNote] = Field(default_factory=list)
    # Current NOTES.md sections.


class Speed(Strict):
    version: str
    # NEW contract schema version, e.g. "1".

    generated: datetime
    # NEW release-emission timestamp.

    repo: str
    # NEW/constant producer repository identity.

    title: str
    # Current page title: “Speed on 10,000 varied inputs”.

    intro: str
    # Current build_speed_site.py page introduction paragraph.

    tasks: list[SpeedTask]
    # Current speed page sections, in render order.
```

### Contract invariants to test outside the field types

1. `(kind, group, case)` is unique.
2. `shown` is strictly increasing, contains positive page numbers, and has the same length as every non-empty `pages[engine]` list.
3. `pages.reference` exists and its length equals `len(shown)` when `shown` is present.
4. `page_counts` is non-negative; zero is allowed for a failed candidate.
5. Every metric is finite and within 0–100, except the deliberate `100.000001` epsilon for floating-point pixel scores currently observed at `100.00000000000003`.
6. `base` is an absolute HTTPS URL ending in `/`; every `AssetPath` is relative and R2-served.
7. Every non-null page/PDF URL is covered by the release’s served-asset witness. A URL that is not in that witness fails release publication.
8. `dropped` does not name an engine that is simultaneously active in the case’s score/page maps.
9. `kind` agrees with the source group policy; e.g. `wordcraft1014/convert` is conversion and `wordcraft1014/compare` is redline.

### Producer seam still required

The Pydantic types are straightforward; the missing work is the producer input and emission API. A practical ndb-side shape would be:

- `neurotic_docx_bench.cases.build_cases(root, *, base, generated) -> Cases`
- `neurotic_docx_bench.speed.build_speed_page(root, *, generated) -> Speed`
- `bench report` gains an explicit release-export command or writes these beside `results/scores.json`.

The cases builder needs a canonical, immutable case/asset record. It should not parse `site/index.html` at release time and should not depend on the gh-pages worktree.

---

## 3. Size estimate and R2 decision

### Measured/prototype sizes

The prototype below applies the proposed models to the current law data, adds the explicit `kind`, and uses relative R2 paths.

| Artifact/encoding | Bytes | MiB | gzip -9 |
|---|---:|---:|---:|
| Current conversion `data.json`, compact | 13,585,031 | 12.95 | 1,035,332 |
| Current redline `data.json`, compact | 8,817,962 | 8.41 | 424,594 |
| Proposed combined Cases, compact model serialization | 23,346,571 | 22.27 | 1,471,914 |
| Proposed combined Cases, canonical indent-2/sorted-keys | 31,856,025 | 30.38 | 1,591,170 |
| Proposed combined Cases with full absolute URLs, canonical | ~36,258,855 | ~34.58 | ~1,612,707 |
| Proposed Speed, compact | 11,430 | 0.011 | ~3,108 |
| Proposed Speed, canonical indent-2/sorted-keys | 22,547 | 0.022 | ~3,426 |

The compact model is larger than the two current files combined because strict models materialize absent optional fields as `null` unless the serializer uses `exclude_none=True`. `exclude_none=True` reduces the canonical Cases estimate to about 31.1 MB; it does not change the order of magnitude.

### Images remain R2 URLs

Yes. The proposed Cases contract deliberately excludes image/PDF bytes.

Current page-image references:

- conversion: 58,663 page slots, of which 58,201 are non-null (462 null gaps), plus 19,173 PDF paths;
- redline: 30,256 page slots, of which 27,678 are non-null (2,578 null gaps), plus 7,753 PDF paths.

The release stores their paths/URLs; R2 stores the WebP/PDF objects. This avoids adding the roughly 1.5 GB of redline renewal assets (and the other historical asset trees) to the release bundle.

Use **relative R2 paths plus one public base URL**, not repeated absolute URLs. Repetition adds about 4 MB to the canonical Cases file and creates two places to update when a URL changes.

### Performance implication

Moving from an inline 18.2 MB JSON value to a fetched release file fixes HTML parse/cacheability but does not make the data small. The proposed file is ~30 MB canonical and ~1.6 MB compressed. Both browser pages still parse the entire combined file before rendering.

Options:

- keep one `cases.json` and accept a ~1.6 MB compressed transfer plus JSON parse cost;
- split release data into `cases.json` plus page-specific shards, or use separate `conversion`/`redline` arrays and a streaming parser;
- cache the immutable release URL at the CDN and version the path by release tag.

The contract should not claim that externalizing the inline data fixes the benchmarks.law 6.8 s mobile LCP unless the viewer stops parsing both kinds together.

---

## 4. Render functions bench-viewer must port

### Canonical desktop case viewer

The gh-pages conversion script is at `site/index.html` lines 193–509. The redline script at `site/redlines/index.html` lines 192–508 is functionally the same: after normalizing the constants and one PDF tooltip, the shared functions are byte-identical.

| Function/block | Conversion lines | Redline lines | Classification |
|---|---:|---:|---|
| `ENGINES`, `METRICS`, `VERSIONS`, `TABLE_COLS`, labels/info | 193–204 | 192–203 | Configuration + prose; mostly ndb data, not viewer logic |
| `versionLabel()` | 205–209 | 204–208 | Generic, with data-driven versions |
| `applyHash()` | 226–232 | 225–231 | Generic deep-link routing |
| `visibleCases()` | 245–248 | 244–247 | Generic search/filter over `DATA` |
| `refPages()`, `passesFx()` | 251–268 | 250–267 | Generic filters; thresholds are UI policy |
| `showSel()` | 270–276 | 269–275 | Generic list scrolling |
| `pickRandom()`, `fxChanged()`, `setupFx()` | 277–319 | 276–318 | Generic case controls |
| `step()`, `toggleSide()` | 321–327 | 320–326 | Generic navigation |
| `accordionLegend()` | 330–351 | 329–350 | Generic structure; legend contents are page prose |
| `fmt()`, `fmtM()`, `val()` | 352–354 | 351–353 | Generic formatters; metric names are configuration |
| `renderList()` | 356–369 | 355–368 | Generic list rendering |
| `renderScores()` | 371–410 | 370–409 | Generic score table; metric/column policy is configuration |
| `render()` | 412–475 | 411–474 | Generic page/overlay renderer; reference-producer note is data-aware |
| Event bindings and keyboard shortcuts | 477–508 | 476–507 | Generic interaction shell |

`site/_build/viewer_features.py::FX_JS` (the `FX_JS` string, roughly lines 80–194) is the maintainable source for the filter/navigation/accordion functions. `site/_build/viewer_features.py::swaps()` (roughly lines 198–258) shows which additions are page-local versus generic.

### Hardened/fetching benchmarks.law variant

benchmarks.law has the newer production form and should be the behavioral reference where it differs from gh-pages:

- case fetch, `base` expansion, and load-error UI: `public/neurotic-docx-bench/index.html` lines 55–76;
- hardened `versionLabel` through score table: lines 85–296;
- desktop renderer: lines 297–366;
- keyboard and mobile behavior: lines 381–438;
- redline equivalents: `public/neurotic-docx-bench/redlines/index.html` lines 54–67, 84–297, 298–447.

The law variant adds two important requirements:

1. **Escape every data-derived string before `innerHTML`.** `escHtml()` is at case lines 57–58 and redline lines 57–58. The gh-pages inline script does not consistently do this.
2. **Preserve the mobile-only renderer** (`mEngines`, `mSecond`, `mOvl`, `renderMobile`, and the mobile layout state) if the new viewer must match benchmarks.law rather than only the desktop gh-pages behavior. The redline copy additionally has `ovlTop()` to choose an available overlay engine.

### Speed page

There is no runtime JavaScript in either speed page. Bench-viewer needs to port the **build-time rendering semantics** from `site/_build/speed/build_speed_site.py::render()` (lines 38–79): headings, paragraphs, bullet lists, pipe tables, numeric-cell alignment, inline code, and bold. With the proposed structured `Speed` model, it should render tables directly rather than recreate the Markdown parser.

### Jubarte-specific versus generic

**Generic and reusable in bench-viewer**

- case filtering, text search, random selection, previous/next navigation;
- list selection and scrolling;
- score-table sorting, means, “best engine” highlighting, and row-to-viewer navigation;
- multi-engine page grids, page stepping, missing-page placeholders;
- image overlays, blend mode, opacity and zoom;
- deep links, keyboard shortcuts, local state, mobile drawer/layout;
- metric/value formatting and version aggregation.

**Configuration or data, not hardcoded viewer logic**

- engine keys, display labels, and ordering;
- metric keys and labels;
- per-case versions;
- reference application note;
- group names, shown-page numbers, scores, diagnostics, and errors;
- which engines are dropped for a case;
- R2 base/path expansion.

**Jubarte/product-specific presentation that should not enter the data contract**

- `jubarte.pro` whale/wordmark header and footer;
- jubarte color palette and theme tokens;
- the long, dated legend prose and “what this is” copy;
- default overlay engine (`generated` on conversion, `jubarte-rust` on redline);
- localStorage namespaces (`ec.` versus `ec.redline.`);
- page titles/navigation and the decision to display one or multiple engines;
- the historical sentence that conversion times are parallel-run wall times.

Those belong in a theme/configuration layer. The release contract should carry facts, not the jubarte skin.

---

## 5. Risks and unknowns

1. **No canonical current case source.** The published inline `DATA` is authoritative, while `site/redlines/cases.json` is stale. A release adapter must not silently choose the smaller file.
2. **The case artifact graph is not in the release manifest.** The current page references assets under several historical prefixes (`gh-pages-tree`, `d2p`, `renew1028`, `renew1030`, etc.). A new release key prefix does not automatically make those URLs covered by the new R2 witness.
3. **R2 base versus key prefix.** bench-ops’ `Manifest.r2_prefix` is a key prefix, not a public URL. `Cases.base` is a public URL. Define which component owns each and test that every served URL is reachable.
4. **One large Cases file still parses both page types.** It externalizes HTML but may not fix the mobile LCP. Consider shards or a streaming reader.
5. **Strict models and old optional fields.** `extra="forbid"` will reject new fields. Version the contract and add fields deliberately; do not silently accept them.
6. **Absent versus null metrics.** Upstream J/SSIM/TB nulls are meaningful. `scores` must preserve explicit nulls; absent optional case metadata is different and can use `exclude_none`.
7. **Failure semantics.** A failure can have Pixel 0, a zero page count, null page slots, and an error string. Do not omit the engine from `scores`, and do not turn absent pages into a successful zero-page document.
8. **Shown pages versus total pages.** `shown` can be hundreds of entries; `page_counts` is the full document count. Never infer one from the other.
9. **Signed `max_break_drift`.** It is not a distance despite its name and can be negative.
10. **Engine/version drift.** Global `VERSIONS` is stale; per-case versions are complete. Engine labels/order should be emitted once and checked against every case key.
11. **Speed has two incompatible meanings.** `results/speed.jsonl` drives the RESULTS.md leaderboard; the 10k page drives from per-pair raw rows. The release contract must name the view it carries.
12. **The 10k `summary.json` is stale/partial.** The page’s merged docxodus result cannot be reconstructed from that file alone.
13. **Speed notes contain prose and inline code.** They need a safe, bounded text/Markdown subset, not arbitrary HTML.
14. **HTML injection.** Current gh-pages code inserts case/group/error strings into `innerHTML`; the law copy fixed this. Bench-viewer must escape every data-derived string and reject/sanitize markup.
15. **Mobile parity is a separate decision.** gh-pages and benchmarks.law do not have identical mobile behavior. Define the target before porting.
16. **Home and editors are cross-producer pages.** Home combines ndb and ET facts; editors belongs to ET. Putting either in ndb’s release contract would blur ownership.
17. **Provenance is thin in the current case rows.** The release would benefit from scorer fingerprint, renderer/oracle identity, selection manifest hash, and source-run IDs beyond display-only version strings.
18. **R2 availability is part of the contract.** A valid JSON URL can still 404. Release publication needs an existence/checksum witness for every page/PDF URL, not only for `cases.json` itself.
19. **Canonical serialization size.** The existing `bench_ops.models.dumps()` uses sorted keys and two-space indentation. Reusing it produces a ~30.4 MiB Cases file. If compact release bytes matter, define a separate canonical serializer and hash its output.

## Recommended next decision

Approve the v1 data shape and ownership boundary before writing renderer code:

- ndb release owns `Cases` (conversion + redline) and the 10k-page `Speed`;
- ET release owns the editors scorecard/facts;
- home remains a consumer template fed by derived counts, not an ndb release artifact;
- page images/PDFs stay on R2;
- the ndb producer gains explicit JSON emitters backed by a canonical case/asset record, not by parsing gh-pages HTML.

The existing bench-ops contract can then add `Cases` and `Speed` beside `Scores`, with strict validation and a separate compact serializer if the ~30 MiB canonical file is too large for the release path.

DONE
