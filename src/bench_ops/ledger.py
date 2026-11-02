"""The release ledger only grows: appends are fine, anything else is a problem (D7/D14).

`check_ledger` is the shared authority of the pre-commit hook, of `append_ledger` (publish
itself re-checks before writing) and of anyone diffing two ledger texts. Old lines must be a
prefix of the new text, byte for byte; only fresh lines may appear at the end, each a valid
`LedgerEntry`, and no tag may appear twice.
"""

from __future__ import annotations

from pydantic import ValidationError

from bench_ops.models import LedgerEntry


def check_ledger(old: str | None, new: str) -> list[str]:
    """Problems with `new` given `old` (None: the ledger is new). Empty list means append-only."""
    new_lines = [ln for ln in new.splitlines() if ln.strip()]
    old_lines = [ln for ln in (old or "").splitlines() if ln.strip()]
    problems: list[str] = []

    # Old lines must survive unchanged, in place: the ledger is append-only.
    for i, prev in enumerate(old_lines):
        if i >= len(new_lines) or new_lines[i] != prev:
            problems.append(f"line {i + 1}: changed or removed")

    # Every new line must parse (the appended ones were never checked by anyone else).
    tags: list[str] = []
    for i, line in enumerate(new_lines):
        try:
            tags.append(LedgerEntry.from_line(line).tag)
        except ValidationError:
            problems.append(f"line {i + 1}: not a ledger entry")
    for tag in sorted({t for t in tags if tags.count(t) > 1}):
        problems.append(f"duplicate tag {tag}")

    return problems
