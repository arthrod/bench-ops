"""check_ledger (plan 2): the ledger only grows — appends ok, anything else is a problem (D7/D14)."""

from datetime import UTC, datetime

from bench_ops.ledger import check_ledger
from bench_ops.models import HfRef, LedgerEntry

CREATED = datetime(2026, 11, 2, tzinfo=UTC)


def _line(tag: str, sha: str = "a" * 64) -> str:
    return LedgerEntry(
        tag=tag,
        created=CREATED,
        hf=HfRef(repo="arthrod/neurotic_docx_bench", revision="b" * 40),
        manifest_sha256=sha,
    ).to_line()


L1 = _line("ndb-v1.0.0")
L1_REWRITTEN = _line("ndb-v1.0.0", sha="b" * 64)
L2 = _line("ndb-v1.0.1")


def test_pure_append_is_ok() -> None:
    assert check_ledger(L1, L1 + L2) == []


def test_new_file_old_none_is_ok() -> None:
    assert check_ledger(None, L1) == []
    assert check_ledger(None, L1 + L2) == []


def test_rewritten_line_is_a_problem() -> None:
    problems = check_ledger(L1 + L2, L1_REWRITTEN + L2)
    assert problems
    assert any("changed or removed" in p for p in problems)


def test_removed_line_is_a_problem() -> None:
    problems = check_ledger(L1 + L2, L1)
    assert problems
    assert any("changed or removed" in p for p in problems)


def test_reordered_lines_are_a_problem() -> None:
    assert check_ledger(L1 + L2, L2 + L1) != []


def test_garbage_line_is_a_problem() -> None:
    problems = check_ledger(L1, L1 + "not a ledger entry\n")
    assert problems
    assert any("not a ledger entry" in p for p in problems)


def test_duplicate_tag_is_a_problem() -> None:
    problems = check_ledger(L1, L1 + L1)
    assert problems
    assert any("duplicate tag ndb-v1.0.0" in p for p in problems)
