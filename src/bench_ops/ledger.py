"""The release ledger only grows: appends are fine, anything else is a problem (D7/D14).

`check_ledger` is the shared authority of the pre-commit hook, of `append_ledger` (publish
itself re-checks before writing) and of anyone diffing two ledger texts. The old text must
survive as a byte-for-byte prefix of the new one (compared on raw bytes, not normalised
lines: a CRLF rewrite or a hidden CR is a change, not an append — N2); what follows the
prefix may only be whole lines, each a valid `LedgerEntry`, and no tag may appear twice.
"""

from __future__ import annotations

from pydantic import ValidationError

from bench_ops.models import LedgerEntry


def check_ledger(old: str | None, new: str) -> list[str]:
    """Problems with `new` given `old` (None: the ledger is new). Empty list means append-only."""
    old_bytes = (old or "").encode()
    new_bytes = new.encode()
    problems: list[str] = []

    # Old lines must survive unchanged, in place, byte for byte: the ledger is append-only.
    if not new_bytes.startswith(old_bytes):
        old_lines = old_bytes.splitlines(keepends=True)
        new_lines = new_bytes.splitlines(keepends=True)
        changed = next(
            (i for i, prev in enumerate(old_lines)
             if i >= len(new_lines) or new_lines[i] != prev),
            None,
        )
        if changed is not None:
            problems.append(f"line {changed + 1}: changed or removed")
        else:
            problems.append("old text is not a byte-for-byte prefix of the new text")

    # Only whole lines may be appended (a torn append without the final newline is a problem).
    appended = new_bytes[len(old_bytes):]
    if appended and not appended.endswith(b"\n"):
        problems.append("the appended text is not whole lines (torn append)")

    # Every line must parse (the appended ones were never checked by anyone else).
    tags: list[str] = []
    for i, line in enumerate(ln for ln in new.splitlines() if ln.strip()):
        try:
            tags.append(LedgerEntry.from_line(line).tag)
        except ValidationError:
            problems.append(f"line {i + 1}: not a ledger entry")
    for tag in sorted({t for t in tags if tags.count(t) > 1}):
        problems.append(f"duplicate tag {tag}")

    return problems
