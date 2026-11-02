"""Static checks on the shared lefthook config every linked repo pulls."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMON = (ROOT / "lefthook" / "common.yml").read_text()


def _top_level_keys(text: str) -> set[str]:
    return {ln.split(":", 1)[0] for ln in text.splitlines() if ln and not ln[0].isspace()
            and not ln.startswith("#") and ":" in ln}


def test_no_hook_that_git_lfs_owns() -> None:
    """lefthook replaces any hook it configures. For git-lfs hooks (pre-push, post-checkout,
    post-commit, post-merge) it then calls `git lfs` only on branches whose lefthook.yml
    configures that hook; on any other branch the push succeeds WITHOUT uploading LFS objects.
    ndb is an LFS repo with many branches, so the shared config must stay off those hooks."""
    lfs_hooks = {"pre-push", "post-checkout", "post-commit", "post-merge"}
    assert not _top_level_keys(COMMON) & lfs_hooks


def test_release_state_runs_on_commit() -> None:
    assert "bench status --offline --fail-on-stale --repo ." in COMMON
    assert "pre-commit" in _top_level_keys(COMMON)
