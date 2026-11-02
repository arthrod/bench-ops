#!/usr/bin/env python3
"""Full-corpus check of the published case pages against the plan 3b contract (review L9).

Usage:

    uv run scripts/check_real_cases.py PATH[=kind] [PATH[=kind] ...]

Each PATH is a published page with an inline ``const DATA = ...`` (the ndb gh-pages
``site/index.html`` and ``site/redlines/index.html``) or a JSON file with a ``cases``
member (the benchmarks.law copies). ``kind`` defaults to ``redline`` for paths under a
``redlines`` directory and to ``conversion`` otherwise; ``PATH=redline`` overrides.

The script maps the data the way the producer will (every asset URL relative to the one
``assets_base``, everything else verbatim), builds one CaseShard per group with
``shard_path()``, an index with ``engines_from_cases()`` (display labels from the pages'
``ENGINES`` consts where present, ``jubarte-rust`` labelled ``jubarte (rust)`` per M5)
and runs ``validate_bundle()`` over the whole corpus. Prints the counts and exits
non-zero on the first rejection class.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pydantic import ValidationError

from bench_ops.models import (
    Case,
    CaseShard,
    CasesIndex,
    ShardRef,
    dumps_compact,
    engines_from_cases,
    shard_path,
    validate_bundle,
)

DEFAULT_ASSETS_BASE = "https://pub-92c80eda1059428394970066c8500eda.r2.dev/"


def load_inline_data(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    marker = "const DATA = "
    start = text.index(marker) + len(marker)
    return json.JSONDecoder().raw_decode(text[start:])[0]


def load_engines_const(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
        marker = "const ENGINES = "
        start = text.index(marker) + len(marker)
    except (ValueError, OSError):
        return {}
    return dict(json.JSONDecoder().raw_decode(text[start:])[0])


def load_cases(path: Path) -> list[dict]:
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))["cases"]
    return load_inline_data(path)


def to_rel(value: object, base: str) -> object:
    """Absolute R2 URL -> ~/relative; ~/ paths (the benchmarks.law copies) stay as they are."""
    if isinstance(value, str) and value.startswith(base):
        return "~/" + value[len(base) :]
    return value


def map_case(raw: dict, kind: str) -> dict:
    out = {
        "kind": kind,
        "group": raw["group"],
        "case": raw["case"],
        "pages": {k: [to_rel(p, DEFAULT_ASSETS_BASE) for p in v] for k, v in raw.get("pages", {}).items()},
        "page_counts": raw.get("page_counts", {}),
        "scores": raw.get("scores", {}),
        "times": raw.get("times", {}),
        "versions": raw.get("versions", {}),
        "dropped": raw.get("dropped", []),
        "reference_app": raw.get("reference_app", ""),
    }
    for key in ("pdfs", "errors", "metric_diagnostics", "shown", "stem", "lang", "type",
                "set", "sets", "state", "pair", "not_applicable", "unavailable"):
        if key in raw:
            out[key] = raw[key]
    if "pdfs" in raw:
        out["pdfs"] = {k: to_rel(v, DEFAULT_ASSETS_BASE) for k, v in raw["pdfs"].items()}
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    labels: dict[str, str] = {}
    rejects: Counter[str] = Counter()
    examples: dict[str, tuple[str, str, str]] = {}
    cases: list[Case] = []
    per_input: list[tuple[str, str, int, int]] = []  # path, kind, raw, groups

    for arg in argv[1:]:
        path_str, _, kind = arg.rpartition("=")
        path = Path(path_str or arg)
        if kind not in ("conversion", "redline"):
            kind = "redline" if "redlines" in path.parts else "conversion"
        raw = load_cases(path)
        labels |= load_engines_const(path)
        groups = {c["group"] for c in raw}
        per_input.append((str(path), kind, len(raw), len(groups)))
        for row in raw:
            try:
                cases.append(Case.model_validate(map_case(row, kind)))
            except ValidationError as err:
                for e in err.errors():
                    key = f"{'.'.join(map(str, e['loc']))}: {e['msg']}"[:160]
                    rejects[key] += 1
                    examples.setdefault(key, (kind, row.get("group", "?"), row.get("case", "?")))

    if rejects:
        print(f"REJECTED: {sum(rejects.values())} of {per_input and sum(n for _, _, n, _ in per_input)} cases")
        for key, n in rejects.most_common(20):
            print(f"  [{n:5d}] {key}  eg kind/group/case={examples[key]}")
        return 1

    labels["jubarte-rust"] = "jubarte (rust)"  # M5: the two jubarte lanes need distinct labels
    by_ref: dict[tuple[str, str], list[Case]] = {}
    for case in cases:
        by_ref.setdefault((case.kind, case.group), []).append(case)
    shards = [CaseShard(kind=k, group=g, cases=sorted(cs, key=lambda c: c.case))
              for (k, g), cs in by_ref.items()]
    index = CasesIndex(
        version="1",
        generated=datetime.now(UTC),
        repo="jandira-tech/neurotic_docx_bench",
        assets_base=DEFAULT_ASSETS_BASE,
        engines=engines_from_cases(cases, labels),
        groups=[ShardRef(kind=s.kind, group=s.group, count=len(s.cases),
                         path=shard_path(s.kind, s.group))
                for s in sorted(shards, key=lambda s: (s.kind, s.group))],
    )
    try:
        validate_bundle(index, shards)
    except ValueError as err:
        print(f"BUNDLE INVALID:\n{err}")
        return 1

    for path, kind, raw, groups in per_input:
        print(f"  {path}: kind={kind} cases={raw} groups={groups}")
    compact = sum(len(dumps_compact(s).encode()) for s in shards) + len(dumps_compact(index).encode())
    print(f"OK: {len(cases)} cases, {len(shards)} shards, {len(index.engines)} engines, "
          f"{compact / 1e6:.1f} MB compact; validate_bundle passes over the whole corpus")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
