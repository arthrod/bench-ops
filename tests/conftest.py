import pytest


@pytest.fixture(autouse=True)
def _wide_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rich wraps tables at 80 columns; tests assert on whole cell text."""
    monkeypatch.setenv("COLUMNS", "200")
