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


# ---- pipelines: strict, frozen, ordered release steps -------------------------------------


def test_pipeline_step_forbids_unknown_keys() -> None:
    assert hasattr(config, "PipelineStep"), "issue #14: PipelineStep is missing from config.py"
    with pytest.raises(ValidationError):
        config.PipelineStep(name="x", repo="ndb", run="echo", commnd="typo")


def test_pipeline_spec_forbids_unknown_keys_and_is_frozen() -> None:
    assert hasattr(config, "PipelineSpec"), "issue #14: PipelineSpec is missing from config.py"
    with pytest.raises(ValidationError):
        config.PipelineSpec(steps=[], stepz=[])
    spec = config.PipelineSpec(steps=[])
    with pytest.raises(ValidationError):
        spec.steps = []  # type: ignore[misc]


def test_pipeline_section_loads(tmp_path: Path) -> None:
    (tmp_path / "repos.toml").write_text(
        TOML
        + '''
[pipelines.jubarte]
preflight_repo = "ndb"
deploy_step = 1
word_timeout_seconds = 5
required_ancestors = ["0123456789012345678901234567890123456789"]

[[pipelines.jubarte.steps]]
name = "score"
repo = "ndb"
run = "uv run python -m neurotic_docx_bench.jubarte_release_info {version}"
'''
    )
    cfg = load_config(tmp_path)
    pipeline = getattr(cfg, "pipelines", {}).get("jubarte")
    assert pipeline is not None, "issue #14: Config.pipelines is missing"
    assert pipeline.preflight_repo == "ndb"
    assert pipeline.deploy_step == 1
    assert pipeline.word_timeout_seconds == 5
    assert pipeline.required_ancestors == ["0123456789012345678901234567890123456789"]
    assert pipeline.steps[0].name == "score"
    assert pipeline.steps[0].repo == "ndb"
    assert pipeline.steps[0].run.endswith("{version}")


def test_pipeline_rejects_an_unknown_step_repo(tmp_path: Path) -> None:
    (tmp_path / "repos.toml").write_text(
        TOML
        + '''
[pipelines.jubarte]
preflight_repo = "ndb"

[[pipelines.jubarte.steps]]
name = "score"
repo = "missing"
run = "echo"
'''
    )
    with pytest.raises(ValidationError, match="missing"):
        load_config(tmp_path)


def test_pipeline_name_cannot_name_both_a_pipeline_and_a_repo(tmp_path: Path) -> None:
    (tmp_path / "repos.toml").write_text(
        TOML
        + '''
[pipelines.ndb]
preflight_repo = "ndb"

[[pipelines.ndb.steps]]
name = "score"
repo = "ndb"
run = "echo"
'''
    )
    with pytest.raises(ValidationError, match="both"):
        load_config(tmp_path)


def test_pipeline_name_must_be_a_safe_slug(tmp_path: Path) -> None:
    (tmp_path / "repos.toml").write_text(
        TOML
        + '''
[pipelines."../escape"]
preflight_repo = "ndb"

[[pipelines."../escape".steps]]
name = "score"
repo = "ndb"
run = "echo"
'''
    )
    with pytest.raises(ValidationError, match="pipeline name"):
        load_config(tmp_path)


def test_workspace_repos_toml_pins_the_jubarte_pipeline() -> None:
    """The real D3 order and the merged ndb fixes are configuration, not code."""
    cfg = load_config(Path(__file__).resolve().parent.parent)
    pipelines = getattr(cfg, "pipelines", {})
    pipeline = pipelines.get("jubarte")
    assert pipeline is not None, "issue #14: [pipelines.jubarte] is missing"
    assert pipeline.preflight_repo == "ndb"
    assert pipeline.publish_step == 4
    assert pipeline.deploy_step == 6
    assert pipeline.word_timeout_seconds > 0
    assert pipeline.required_ancestors == [
        "31e42a32b17744dd8b61f06ddedbbceaae49b9cf",  # ndb #102
        "1dbbdfd341976cf42bc9fed4c12645b723ef034b",  # ndb #104
    ]
    assert [step.name for step in pipeline.steps] == [
        "release-info",
        "report-and-release-views",
        "stage-pages-and-upload-r2",
        "publish-ndb",
        "bump-and-fetch-jubarte-app",
        "deploy-jubarte-app",
    ]
    assert [step.repo for step in pipeline.steps] == [
        "ndb",
        "ndb",
        "ndb",
        "ndb",
        "jubarte-app",
        "jubarte-app",
    ]
    assert pipeline.steps[0].run == (
        "uv run python -m neurotic_docx_bench.jubarte_release_info {version}"
    )
    assert pipeline.steps[1].run == "uv run bench report && uv run scripts/release_views.py"
    assert pipeline.steps[3].run == "bench publish ndb {tag}"
    assert pipeline.steps[4].run == "bench bump ndb && bench fetch"
    assert pipeline.steps[5].run == "bench deploy jubarte-app --yes"
