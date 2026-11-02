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
