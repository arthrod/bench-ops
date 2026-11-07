"""Plan 6 part A: editors_trial goes through bench publish — the release section, the stage
globs, et-vX.Y.Z tags, and the rule that publishing never creates Hugging Face repos."""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError
from typer.testing import CliRunner

from bench_ops import config
from bench_ops.cli import app
from bench_ops.config import load_config
from bench_ops.models import Manifest, Tag
from bench_ops.publish import PublishError, publish, stage
from tests.test_publish import CREATED, FakeHub

HF_REPO = "arthrod/editors_trial-artifacts"
TAG = "et-v0.9.3"
ET_FILES = [
    "out/facts.json",
    "out/release.json",
    "out/demos/**",  # each live editor: index.html, SOURCE.txt, document.docx, assets
    "out/demo-files/*.docx",  # the sanitized copy the site links beside the demo
    "reports/*.md",
    "reports/*.docx",
    "?reports/figures/**",  # figures may be absent in a given release (M1)
]

TREE = {
    "out/facts.json": b'{"facts": 1}',
    "out/release.json": b'{"release": 1}',
    "out/other.json": b"not in the bundle",
    "out/demos/superdoc/index.html": b"<html></html>",
    "out/demos/superdoc/SOURCE.txt": b"Repository: https://example.test\nCommit: abcdef0\n",
    "out/demo-files/superdoc.docx": b"PK\x03\x04demo",
    "out/demo-files/notes.txt": b"not in the bundle",
    "reports/alpha.md": b"# alpha",
    "reports/bravo.md": b"# bravo",
    "reports/tool.docx": b"PK\x03\x04docx",
    "reports/figures/overview.png": b"\x89PNG",
    "reports/figures/sub/detail.png": b"\x89PNG sub",
    "reports/notes.txt": b"not in the bundle",
    ".env": b"SECRET=1",
}
EXPECTED = sorted(
    [
        "out/facts.json",
        "out/release.json",
        "out/demos/superdoc/index.html",
        "out/demos/superdoc/SOURCE.txt",
        "out/demo-files/superdoc.docx",
        "reports/alpha.md",
        "reports/bravo.md",
        "reports/tool.docx",
        "reports/figures/overview.png",
        "reports/figures/sub/detail.png",
    ]
)


def _sh(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "core.hooksPath=/dev/null", *args],
        check=True,
        capture_output=True,
    )


def _et_tree(tmp_path: Path) -> Path:
    """An editors_trial-shaped checkout: the bundle files, near misses, and a .env."""
    root = tmp_path / "editors_trial"
    root.mkdir()
    subprocess.run(["git", "init", "-b", "main", "-q"], cwd=root, check=True)
    for rel, data in TREE.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    _sh(root, "add", "out", "reports")
    _sh(root, "commit", "-qm", "init")
    return root


def _repo(root: Path) -> config.Repo:
    return config.Repo(
        name="editors_trial",
        url=str(root),
        branch="main",
        role="producer",
        path=root,
        release=config.ReleaseSpec(hf_repo=HF_REPO, files=ET_FILES),
    )


# ---- stage over the editors_trial globs ---------------------------------------------------


def test_stage_picks_exactly_the_editors_trial_bundle(tmp_path: Path) -> None:
    staged = stage(_et_tree(tmp_path), ET_FILES)
    assert [s.rel for s in staged] == EXPECTED  # neither out/other.json nor reports/notes.txt


@pytest.mark.parametrize("globs", [["**/*"], ["out/*"], [".env"]])
def test_stage_refuses_secret_looking_files(tmp_path: Path, globs: list[str]) -> None:
    """A .env (what the secrets/size guards exist for) must never reach a public dataset."""
    root = _et_tree(tmp_path)
    (root / "out" / ".env").write_bytes(b"EMDASH_TOKEN=1")
    with pytest.raises(PublishError):
        stage(root, globs)


# ---- the tag pattern ----------------------------------------------------------------------


def test_the_tag_pattern_accepts_et_versions() -> None:
    t = TypeAdapter(Tag)
    assert t.validate_python("et-v0.9.3") == "et-v0.9.3"
    assert t.validate_python("et-v1.20.3") == "et-v1.20.3"
    for bad in ("et-v0.9", "et-0.9.3", "editors_trial-v0.9.3", "et-vX.Y.Z"):
        with pytest.raises(ValidationError):
            t.validate_python(bad)


def test_publish_validates_et_tags_before_any_hub_call(tmp_path: Path) -> None:
    hub = FakeHub(tmp_path)
    with pytest.raises(PublishError, match="et-v"):
        publish(_repo(_et_tree(tmp_path)), "et-v0.9", hub, now=CREATED, dry_run=False)
    assert hub.calls == []


# ---- the workspace config ------------------------------------------------------------------


def test_workspace_repos_toml_gives_editors_trial_the_release_section() -> None:
    cfg = load_config(Path(__file__).resolve().parent.parent)
    et = cfg.by_name("editors_trial")
    assert et.release is not None, "plan 6: editors_trial needs [repos.release] in repos.toml"
    assert et.release.hf_repo == HF_REPO
    assert et.release.files == ET_FILES
    assert et.release.private is True  # the trial documents are not public (H2)
    assert cfg.by_name("ndb").release.private is False  # the public record dataset
    assert et.release.served == []  # benchmarks.law reads the bundle at build time
    assert et.release.before == []  # facts.py/render_reports.py stay in RELEASING.md for now


# ---- the CLI -------------------------------------------------------------------------------


def _bench_root(tmp_path: Path, root: Path) -> Path:
    bench_root = tmp_path / "root"
    bench_root.mkdir(exist_ok=True)
    (bench_root / "repos.toml").write_text(
        f'[[repos]]\nname = "editors_trial"\n'
        f'url = "https://github.com/arthrod/editors_trial.git"\n'
        f'branch = "main"\nrole = "producer"\npath = "{root}"\n\n'
        f"[repos.release]\nhf_repo = \"{HF_REPO}\"\nfiles = {json.dumps(ET_FILES)}\n"
    )
    return bench_root


def test_cli_publish_editors_trial_dry_run_prints_a_valid_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _et_tree(tmp_path)
    monkeypatch.setenv("BENCH_ROOT", str(_bench_root(tmp_path, root)))
    result = CliRunner().invoke(app, ["publish", "editors_trial", TAG, "--dry-run"])
    assert result.exit_code == 0, result.output
    m = Manifest.model_validate(json.loads(result.output))
    assert m.tag == TAG and m.producer == "editors_trial"
    assert [f.path for f in m.files] == [f"release/{TAG}/{rel}" for rel in EXPECTED]
    assert not (root / "release").exists()  # no ledger, no manifest, no tag
    assert not any(f.served for f in m.files)


# ---- publishing never creates Hugging Face repos -------------------------------------------


def test_publish_refuses_when_the_hub_repo_does_not_exist(tmp_path: Path) -> None:
    root = _et_tree(tmp_path)
    hub = FakeHub(tmp_path)
    hub.missing_repo = True
    with pytest.raises(PublishError) as e:
        publish(_repo(root), TAG, hub, now=CREATED, dry_run=False)
    msg = str(e.value)
    assert "\n" not in msg  # one line, actionable
    assert HF_REPO in msg
    assert "private" in msg and "create" in msg
    assert not any(c.startswith(("commit:", "tag:")) for c in hub.calls)
    assert not (root / "release").exists()


def test_publish_dry_run_ignores_whether_the_hub_repo_exists(tmp_path: Path) -> None:
    """The check is hub traffic, so a dry run never trips it (no hub calls at all)."""
    root = _et_tree(tmp_path)
    hub = FakeHub(tmp_path)
    hub.missing_repo = True
    m = publish(_repo(root), TAG, hub, now=datetime(2026, 11, 3, tzinfo=UTC), dry_run=True)
    assert hub.calls == []
    assert m.tag == TAG
