from pathlib import Path

import pytest

from bench_ops.config import Config, Repo, load_config

TOML = """
[[repos]]
name = "ndb"
url = "https://github.com/jandira-tech/neurotic_docx_bench.git"
branch = "main"
role = "producer"

[[repos]]
name = "jubarte-app"
url = "git@github.com:arthrod/jubarte-app.git"
branch = "main"
role = "consumer"
path = "/elsewhere/jubarte-app"
"""


def test_load_config_reads_repos(tmp_path: Path) -> None:
    (tmp_path / "repos.toml").write_text(TOML)
    cfg = load_config(tmp_path)
    assert [r.name for r in cfg.repos] == ["ndb", "jubarte-app"]
    assert cfg.repos[0].role == "producer"


def test_default_path_is_root_slash_name(tmp_path: Path) -> None:
    (tmp_path / "repos.toml").write_text(TOML)
    cfg = load_config(tmp_path)
    assert cfg.repos[0].local_path == tmp_path.resolve() / "ndb"


def test_explicit_path_wins(tmp_path: Path) -> None:
    (tmp_path / "repos.toml").write_text(TOML)
    cfg = load_config(tmp_path)
    assert cfg.repos[1].local_path == Path("/elsewhere/jubarte-app")


def test_env_override_of_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "repos.toml").write_text(TOML)
    monkeypatch.setenv("BENCH_ROOT", str(tmp_path))
    cfg = load_config()
    assert cfg.root == tmp_path.resolve()


def test_unknown_role_rejected() -> None:
    with pytest.raises(ValueError):
        Repo(name="x", url="u", branch="main", role="bystander")


def test_by_name(tmp_path: Path) -> None:
    (tmp_path / "repos.toml").write_text(TOML)
    cfg: Config = load_config(tmp_path)
    assert cfg.by_name("ndb").url.endswith("neurotic_docx_bench.git")
    with pytest.raises(KeyError):
        cfg.by_name("nope")


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("https://github.com/arthrod/jubarte-app.git", "git@github.com:arthrod/jubarte-app.git"),
        ("https://github.com/Arthrod/Jubarte-App", "https://github.com/arthrod/jubarte-app.git/"),
        ("ssh://git@github.com/arthrod/x.git", "https://github.com/arthrod/x"),
    ],
)
def test_same_remote(a: str, b: str) -> None:
    from bench_ops.config import same_remote

    assert same_remote(a, b)


def test_different_remote() -> None:
    from bench_ops.config import same_remote

    assert not same_remote("https://github.com/arthrod/a", "https://github.com/arthrod/b")


def test_default_root_is_t_bench_when_bench_root_is_unset(monkeypatch, tmp_path):
    """bench-ops lives at ~/T/bench (bench-ops#28); BENCH_ROOT still overrides."""
    from bench_ops.config import default_root

    monkeypatch.delenv("BENCH_ROOT", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert default_root() == tmp_path / "T" / "bench"
    monkeypatch.setenv("BENCH_ROOT", str(tmp_path / "elsewhere"))
    assert default_root() == tmp_path / "elsewhere"
