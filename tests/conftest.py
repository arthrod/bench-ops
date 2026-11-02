import pytest

from bench_ops.gitx import HOOK_ENV


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wide tables (Rich wraps at 80 columns) and no hook GIT_* vars leaking into fixture repos."""
    monkeypatch.setenv("COLUMNS", "200")
    for var in HOOK_ENV:
        monkeypatch.delenv(var, raising=False)
