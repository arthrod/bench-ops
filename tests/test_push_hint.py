"""#11: the push command printed after a publish pushes the branch that holds the ledger commit."""

from pathlib import Path

from bench_ops.cli import push_hint

P = Path("/r/ndb")


def test_on_the_default_branch() -> None:
    assert push_hint(P, current="main", default="main", tag="ndb-v0.0.3") == (
        "push with: git -C /r/ndb push origin HEAD:main && git -C /r/ndb push origin ndb-v0.0.3"
    )


def test_on_a_release_branch_pushes_that_branch_and_suggests_a_pr() -> None:
    hint = push_hint(P, current="release/ndb-v0.0.3", default="main", tag="ndb-v0.0.3")
    assert "push origin HEAD:release/ndb-v0.0.3 &&" in hint
    assert "push origin main" not in hint
    assert "gh pr create" in hint and "--base main" in hint


def test_detached_head_says_so() -> None:
    hint = push_hint(P, current="HEAD", default="main", tag="ndb-v0.0.3")
    assert "detached" in hint and "push origin HEAD:" not in hint
