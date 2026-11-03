"""[repos.release] (plan 2): strict models, so a typo fails at load, not mid-release (D18)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from bench_ops import config
from bench_ops.config import Repo, load_config

TOML = """
[[repos]]
name = "ndb"
url = "https://github.com/jandira-tech/neurotic_docx_bench.git"
branch = "main"
role = "producer"

[repos.release]
hf_repo = "arthrod/neurotic_docx_bench"
files = ["out/**"]
"""


def test_release_spec_forbids_unknown_keys() -> None:
    assert hasattr(config, "ReleaseSpec"), "plan 2: ReleaseSpec is missing from config.py"
    with pytest.raises(ValidationError):
        config.ReleaseSpec(hf_repo="x", files=["f"], r2_publik="typo")


def test_release_spec_served_defaults_to_empty() -> None:
    assert hasattr(config, "ReleaseSpec"), "plan 2: ReleaseSpec is missing from config.py"
    spec = config.ReleaseSpec(hf_repo="x", files=["f"])
    assert spec.served == []


def test_repo_forbids_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        Repo(name="x", url="u", role="producer", relese={"hf_repo": "x", "files": []})


def test_release_section_loads(tmp_path: Path) -> None:
    (tmp_path / "repos.toml").write_text(TOML)
    cfg = load_config(tmp_path)
    release = getattr(cfg.repos[0], "release", None)
    assert release is not None, "plan 2: Repo.release is missing"
    assert release.hf_repo == "arthrod/neurotic_docx_bench"
    assert release.files == ["out/**"]
    assert release.served == []


def test_repo_without_release_section_is_none() -> None:
    r = Repo(name="x", url="u", role="consumer")
    assert hasattr(r, "release") and r.release is None


# ---- revision 3: `before` commands and consumer `deploy` ----------------------------------


def test_release_spec_before_defaults_to_empty() -> None:
    spec = config.ReleaseSpec(hf_repo="x", files=["f"])
    assert spec.before == []


def test_release_section_with_before_loads(tmp_path: Path) -> None:
    (tmp_path / "repos.toml").write_text(
        TOML.replace(
            'files = ["out/**"]',
            'files = ["out/**"]\nbefore = ["uv run bench report --check"]',
        )
    )
    cfg = load_config(tmp_path)
    assert cfg.repos[0].release.before == ["uv run bench report --check"]


def test_release_spec_still_forbids_unknown_keys_with_before() -> None:
    with pytest.raises(ValidationError):
        config.ReleaseSpec(hf_repo="x", files=["f"], before=["c"], befor=["typo"])


def test_repo_deploy_defaults_to_none() -> None:
    assert Repo(name="x", url="u", role="consumer").deploy is None


def test_repo_still_forbids_unknown_keys_with_deploy() -> None:
    with pytest.raises(ValidationError):
        Repo(name="x", url="u", role="consumer", deploi="typo")


def test_consumer_deploy_command_loads(tmp_path: Path) -> None:
    (tmp_path / "repos.toml").write_text(
        TOML
        + '''
[[repos]]
name = "benchmarks-law"
url = "https://github.com/arthrod/benchmarks-law.git"
branch = "main"
role = "consumer"
deploy = "pnpm run deploy"
'''
    )
    cfg = load_config(tmp_path)
    assert [r.name for r in cfg.repos] == ["ndb", "benchmarks-law"]
    assert cfg.repos[1].deploy == "pnpm run deploy"


def test_workspace_repos_toml_names_the_release_and_deploy_commands() -> None:
    """The workspace's own repos.toml carries rev 3: ndb's before scripts, consumers' deploys."""
    cfg = load_config(Path(__file__).resolve().parent.parent)
    ndb = cfg.by_name("ndb")
    assert ndb.release is not None
    assert ndb.release.hf_repo == "arthrod/neurotic_docx_bench"
    assert ndb.release.before == [
        "uv run bench report --check",
        "uv run scripts/release_views.py --check",
        "uv run bench results upload",
    ]
    assert ndb.release.files == [
        "results/scores.json",
        "results/release/cases/index.json",
        "results/release/cases/*/*.json",
        "results/release/speed.json",
    ]
    assert cfg.by_name("jubarte-app").deploy == (
        "cd jubarte-site && scripts/release.sh deploy {version}"
    )
    assert cfg.by_name("benchmarks-law").deploy == "pnpm run deploy"
    assert cfg.by_name("editors_trial").deploy is None
