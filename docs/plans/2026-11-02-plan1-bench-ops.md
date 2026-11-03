# bench-ops (Plan 1 of 6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Agents: never claudeagents; use pi (zai/glm-5.3, minimax/MiniMax-M3), codex or the other allowed agents per /conejo-code.

> **Status (2026-11-03): implemented.** All 57 steps are done; their checkboxes are ticked. Two other-corpus agents audited the plan against the code separately (crush and pi, both zai/glm-5.3). Tasks 1–9 match the plan, at commits 3347709, e8cc8be, fcf0e39, 96cc419, 5b2944c, 090e3db, 647c486, abb7571 and a894a24. `pytest -q`: 399 passed; `ruff check .`: clean. Later plans changed some things on purpose: `Lock.shas`, ledger growth allowed by the size guard (b996ee5), `common.yml` ledger/fetch jobs and pre-push→pre-commit (6fbb1a5, 48d1d05, 2f1480a), and stray worktrees under `BENCH_ROOT/.work` instead of `.worktrees/` (plan 6B). Task 10's evidence was finished in #23: the probe was refused (`2.9 MB over the 2.0 MB limit`, exit 1), and the baseline has ndb's diskUsage (387,901 KB).

**Goal:** A small workspace repo `arthrod/bench-ops` that links the four bench repos, defines the release contract (manifest, scores, ledger, lock) as pydantic models, and ships `bench sync`, `bench status`, `bench hook` and the shared lefthook config, so every later phase (publishing, viewer, consumer adaptation) builds on one tested foundation.

**Architecture:** One Python package `bench_ops` (uv, Python 3.14, typer, pydantic v2, httpx, huggingface_hub) installed as a global uv tool, so every repo's git hook is just `bench hook <name>`. Repos are *linked*, not vendored: `repos.toml` names URL, branch, role and local path; nothing is a submodule. Lefthook config lives once in `bench-ops/lefthook/` and each repo pulls it through lefthook `remotes:`.

**Tech Stack:** uv, typer, pydantic 2, httpx (async), huggingface_hub, pytest, pytest-asyncio, ruff, lefthook (brew), ripsecrets (brew).

**Design decisions already approved (see "Decisions" at the end):** HF is the record, R2 is a rebuildable cache, git holds code + small ledgers; two witnesses before any local delete; consumers pin a tag in `bench.lock`; shared renderer `@jandira-tech/bench-viewer` on npm; themes `arthur-law` and `jubarte`; lefthook via remotes.

**Correction to the earlier survey:** `jubarte-site/fixtures.lock` → `arthrod/neurotic_docx_bench` is the **HF dataset id**, not the GitHub owner. There is no owner mismatch. Nothing to fix.

---

## Repository layout (created by this plan)

```
~/bench/                          ← clone of github.com/arthrod/bench-ops  (BENCH_ROOT)
  pyproject.toml                  package + tool config
  repos.toml                      the four linked repos
  src/bench_ops/
    __init__.py
    cli.py                        typer app: sync, status, hook
    config.py                     repos.toml → Config / Repo models
    gitx.py                       thin git wrapper (clone_or_pull, status, worktrees)
    models.py                     contract: FileEntry, Manifest, Scores, LedgerEntry, Lock
    hf.py                         HF witness: verify_manifest()
    status.py                     per-repo report assembly
    hooks.py                      size_guard(), secrets()
  lefthook/common.yml             hooks every repo pulls
  docs/CONTRACT.md                storage roles + two-witness rule
  docs/plans/                     this plan and the next five
  tests/                          pytest, one file per module
  .work/  .logs/  ndb/  editors_trial/  benchmarks-law/  jubarte-app/   ← git-ignored
```

Each linked repo gets one new file, `lefthook.yml` (6 lines, Task 10).

---

### Task 1: Scaffold the bench-ops repository

**Files:**
- Create: `~/bench/pyproject.toml`
- Create: `~/bench/.gitignore`
- Create: `~/bench/src/bench_ops/__init__.py`
- Create: `~/bench/tests/__init__.py`
- Create: `~/bench/README.md`

- [x] **Step 1: Create the repo and the package skeleton**

```bash
mkdir -p ~/bench/src/bench_ops ~/bench/tests ~/bench/lefthook ~/bench/docs/plans
cd ~/bench && git init -b main
```

- [x] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "bench-ops"
version = "0.1.0"
description = "Workspace, contract and single command for the docx benches."
requires-python = ">=3.14"
dependencies = [
  "typer>=0.15",
  "pydantic>=2.10",
  "httpx>=0.28",
  "huggingface_hub>=0.34",
  "rich>=13",
]

[project.scripts]
bench = "bench_ops.cli:app"

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.25", "ruff>=0.8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/bench_ops"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
target-version = "py314"
```

- [x] **Step 3: Write `.gitignore`**

```
# linked repos live beside this file but are not part of it
/ndb/
/editors_trial/
/benchmarks-law/
/jubarte-app/
/.work/
/.logs/
/.venv/
__pycache__/
*.pyc
```

- [x] **Step 4: Write `src/bench_ops/__init__.py` and `tests/__init__.py`**

```python
"""bench-ops: workspace, contract and single command for the docx benches."""

__all__ = ["__version__"]
__version__ = "0.1.0"
```

`tests/__init__.py` is empty.

- [x] **Step 5: Write `README.md`**

```markdown
# bench-ops

The workspace for neurotic_docx_bench, editors_trial, benchmarks-law and jubarte-app.

    uv tool install --editable .      # gives you `bench`
    bench sync                        # clones/pulls every repo in repos.toml, installs hooks
    bench status                      # branches, dirt, release ledger vs Hugging Face

Roles and rules: docs/CONTRACT.md. Plans: docs/plans/.
```

- [x] **Step 6: Install, check the CLI entry point resolves**

```bash
cd ~/bench && uv sync && uv run python -c "import bench_ops; print(bench_ops.__version__)"
```
Expected: `0.1.0`

- [x] **Step 7: Commit**

```bash
cd ~/bench && git add pyproject.toml .gitignore README.md src/bench_ops/__init__.py tests/__init__.py uv.lock
git commit -m "chore: scaffold bench-ops package

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01GMMgDoeENViNs8zdWZvRgC"
```

Every later commit in this plan ends with the same two attribution lines; they are omitted below for brevity.

---

### Task 2: `repos.toml` and its loader

**Files:**
- Create: `~/bench/repos.toml`
- Create: `~/bench/src/bench_ops/config.py`
- Test: `~/bench/tests/test_config.py`

- [x] **Step 1: Write the failing tests**

```python
# tests/test_config.py
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
    assert cfg.repos[0].local_path == tmp_path / "ndb"


def test_explicit_path_wins(tmp_path: Path) -> None:
    (tmp_path / "repos.toml").write_text(TOML)
    cfg = load_config(tmp_path)
    assert cfg.repos[1].local_path == Path("/elsewhere/jubarte-app")


def test_env_override_of_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "repos.toml").write_text(TOML)
    monkeypatch.setenv("BENCH_ROOT", str(tmp_path))
    cfg = load_config()
    assert cfg.root == tmp_path


def test_unknown_role_rejected() -> None:
    with pytest.raises(ValueError):
        Repo(name="x", url="u", branch="main", role="bystander")


def test_by_name(tmp_path: Path) -> None:
    (tmp_path / "repos.toml").write_text(TOML)
    cfg: Config = load_config(tmp_path)
    assert cfg.by_name("ndb").url.endswith("neurotic_docx_bench.git")
    with pytest.raises(KeyError):
        cfg.by_name("nope")
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd ~/bench && uv run pytest tests/test_config.py -q`
Expected: `ImportError: cannot import name 'Config' from 'bench_ops.config'` (module missing)

- [x] **Step 3: Write `src/bench_ops/config.py`**

```python
"""repos.toml: the linked repositories of the workspace."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr

Role = Literal["producer", "consumer"]


class Repo(BaseModel):
    name: str
    url: str
    branch: str = "main"
    role: Role
    path: Path | None = None
    _root: Path = PrivateAttr(default=Path("."))

    @property
    def local_path(self) -> Path:
        return self.path if self.path is not None else self._root / self.name


class Config(BaseModel):
    root: Path
    repos: list[Repo] = Field(default_factory=list)

    def by_name(self, name: str) -> Repo:
        for r in self.repos:
            if r.name == name:
                return r
        raise KeyError(name)

    def by_role(self, role: Role) -> list[Repo]:
        return [r for r in self.repos if r.role == role]


def default_root() -> Path:
    return Path(os.environ.get("BENCH_ROOT", Path.home() / "bench")).expanduser()


def load_config(root: Path | None = None) -> Config:
    root = (root or default_root()).resolve()
    data = tomllib.loads((root / "repos.toml").read_text())
    cfg = Config(root=root, repos=data.get("repos", []))
    for r in cfg.repos:
        r._root = root
    return cfg
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd ~/bench && uv run pytest tests/test_config.py -q`
Expected: `6 passed`

- [x] **Step 5: Write the real `repos.toml`**

The `path` entries point at today's checkouts so nothing 14 GB moves yet. Remove them later when the clones live under `~/bench`.

```toml
# The four repositories of the bench system. `path` overrides <BENCH_ROOT>/<name>.

[[repos]]
name = "ndb"
url = "https://github.com/jandira-tech/neurotic_docx_bench.git"
branch = "main"
role = "producer"
path = "/Users/arthrod/temp/T/neurotic_docx_bench"

[[repos]]
name = "editors_trial"
url = "https://github.com/arthrod/editors_trial.git"
branch = "main"
role = "producer"
path = "/Users/arthrod/temp/T/editors_trial"

[[repos]]
name = "benchmarks-law"
url = "https://github.com/arthrod/benchmarks-law.git"
branch = "main"
role = "consumer"
path = "/Users/arthrod/temp/T/briefs_agreements/benchmarks-law"

[[repos]]
name = "jubarte-app"
url = "https://github.com/arthrod/jubarte-app.git"
branch = "main"
role = "consumer"
path = "/Users/arthrod/temp/T/1_jubarte-redlines/jubarte-app"
```

- [x] **Step 6: Commit**

```bash
cd ~/bench && git add repos.toml src/bench_ops/config.py tests/test_config.py
git commit -m "feat: repos.toml loader with roles and path overrides"
```

---

### Task 3: The release contract models

These models are *the* producer→consumer contract. `scores.json` mirrors the `bench.*` facts jubarte-site already consumes (`jubarte-site/site/data/bench.ts`: `Row`, `Table`, `Headline`, `StateCell`, `HomeGroup`, `MethodPoint`), so Phase 3 is a format change, not a redesign.

**Files:**
- Create: `~/bench/src/bench_ops/models.py`
- Test: `~/bench/tests/test_models.py`

- [x] **Step 1: Write the failing tests**

```python
# tests/test_models.py
import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from bench_ops.models import (
    FileEntry,
    HfRef,
    LedgerEntry,
    Lock,
    Manifest,
    Row,
    Scores,
    Table,
)

SHA = "a" * 64


def manifest() -> Manifest:
    return Manifest(
        tag="ndb-v1.0.0",
        created=datetime(2026, 11, 2, tzinfo=UTC),
        producer="ndb",
        hf=HfRef(repo="arthrod/neurotic_docx_bench", revision="b" * 40),
        r2_prefix="ndb-v1.0.0/",
        viewer_version="0.1.0",
        files=[
            FileEntry(path="release/ndb-v1.0.0/scores.json", sha256=SHA, bytes=1234),
            FileEntry(path="site/convert/x.pdf", sha256=SHA, bytes=5, served=True),
        ],
    )


def test_manifest_round_trips_json() -> None:
    m = manifest()
    again = Manifest.model_validate_json(m.model_dump_json())
    assert again == m


def test_sha256_must_be_64_hex() -> None:
    with pytest.raises(ValidationError):
        FileEntry(path="p", sha256="xyz", bytes=1)


def test_served_subset() -> None:
    assert [f.path for f in manifest().served()] == ["site/convert/x.pdf"]


def test_tag_shape() -> None:
    with pytest.raises(ValidationError):
        Manifest.model_validate(manifest().model_dump() | {"tag": "v1"})


def test_ledger_line_and_back() -> None:
    e = LedgerEntry(
        tag="ndb-v1.0.0",
        created=datetime(2026, 11, 2, tzinfo=UTC),
        hf=HfRef(repo="arthrod/neurotic_docx_bench", revision="b" * 40),
        manifest_sha256=SHA,
    )
    line = e.to_line()
    assert line.endswith("\n") and json.loads(line)["tag"] == "ndb-v1.0.0"
    assert LedgerEntry.from_line(line) == e


def test_ledger_last_of_text() -> None:
    a = LedgerEntry(tag="ndb-v1.0.0", created=datetime(2026, 1, 1, tzinfo=UTC),
                    hf=HfRef(repo="r", revision="b" * 40), manifest_sha256=SHA)
    b = a.model_copy(update={"tag": "ndb-v1.0.1"})
    assert LedgerEntry.last(a.to_line() + b.to_line()).tag == "ndb-v1.0.1"
    assert LedgerEntry.last("") is None


def test_lock_parse_and_render() -> None:
    lock = Lock.parse("ndb=ndb-v1.0.0\net=et-v0.9.3\n")
    assert lock.tags == {"ndb": "ndb-v1.0.0", "et": "et-v0.9.3"}
    assert lock.render() == "et=et-v0.9.3\nndb=ndb-v1.0.0\n"


def test_lock_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        Lock.parse("just a tag\n")


def test_scores_minimal() -> None:
    s = Scores(
        version="1.0.0",
        generated="2026-10-28",
        repo="https://github.com/jandira-tech/neurotic_docx_bench",
        viewer="https://jandira-tech.github.io/neurotic_docx_bench/",
        provenance={"docsets": ["c28442e9bb5a5deb"], "scorers": ["v1"], "renderers": ["oracle:microsoft_word"]},
        tables=[Table(id="docx-to-pdf", title="t", meta="m", desc="d", rows=[
            Row(rank="1", tool="jubarte 0.12.0", pin="0.12.0@3c81", median=82.45, mean=79.93,
                docs=400, failed=0, note="[81.13, 83.23]", ours=True)])],
        headlines=[], states=[], home_groups=[], method=[],
    )
    assert s.tables[0].rows[0].ours is True
    assert Scores.model_validate_json(s.model_dump_json()) == s
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd ~/bench && uv run pytest tests/test_models.py -q`
Expected: `ModuleNotFoundError: No module named 'bench_ops.models'`

- [x] **Step 3: Write `src/bench_ops/models.py`**

```python
"""The release contract shared by producers, the record (HF), the cache (R2) and consumers."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
Tag = Annotated[str, StringConstraints(pattern=r"^(ndb|et)-v\d+\.\d+\.\d+(\.\d+)?$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class HfRef(Strict):
    repo: str
    revision: GitSha


class FileEntry(Strict):
    path: str
    sha256: Sha256
    bytes: int = Field(ge=0)
    served: bool = False  # mirrored to R2 under Manifest.r2_prefix


class Manifest(Strict):
    tag: Tag
    created: datetime
    producer: str
    hf: HfRef
    r2_prefix: str
    viewer_version: str | None = None
    files: list[FileEntry]

    def served(self) -> list[FileEntry]:
        return [f for f in self.files if f.served]

    def by_path(self) -> dict[str, FileEntry]:
        return {f.path: f for f in self.files}


class LedgerEntry(Strict):
    """One line of release/index.jsonl: the small ledger that stays in git."""

    tag: Tag
    created: datetime
    hf: HfRef
    manifest_sha256: Sha256

    def to_line(self) -> str:
        return self.model_dump_json() + "\n"

    @classmethod
    def from_line(cls, line: str) -> LedgerEntry:
        return cls.model_validate_json(line)

    @classmethod
    def last(cls, text: str) -> LedgerEntry | None:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return cls.from_line(lines[-1]) if lines else None


class Lock(BaseModel):
    """bench.lock: `<producer>=<tag>` per line, sorted. Replaces jubarte-site/fixtures.lock."""

    tags: dict[str, str]

    @classmethod
    def parse(cls, text: str) -> Lock:
        tags: dict[str, str] = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise ValueError(f"bench.lock line is not <producer>=<tag>: {line!r}")
            k, v = (s.strip() for s in line.split("=", 1))
            tags[k] = v
        return cls(tags=tags)

    def render(self) -> str:
        return "".join(f"{k}={self.tags[k]}\n" for k in sorted(self.tags))


# ---- scores.json: the numbers every frontend prints ------------------------------------
# Mirrors jubarte-site/site/data/bench.ts so Phase 3 is a format change only.


class Row(Strict):
    rank: str
    tool: str
    pin: str
    median: float
    mean: float
    docs: int
    failed: int
    note: str
    ours: bool = False


class Table(Strict):
    id: str
    title: str
    meta: str
    desc: str
    rows: list[Row]


class Headline(Strict):
    label: str
    value: str
    vs: str
    sub: str


class StateRow(Strict):
    tool: str
    median: float
    ours: bool = False


class StateCell(Strict):
    name: str
    n: str
    rows: list[StateRow]


class HomeBar(Strict):
    name: str
    v: float
    ours: bool = False


class HomeGroup(Strict):
    title: str
    meta: str
    rows: list[HomeBar]
    note: str | None = None


class MethodPoint(Strict):
    t: str
    d: str


class Scores(Strict):
    version: str
    generated: str
    repo: str
    viewer: str
    provenance: dict[str, list[str]]
    tables: list[Table]
    headlines: list[Headline]
    states: list[StateCell]
    home_groups: list[HomeGroup]
    method: list[MethodPoint]


def dumps(model: BaseModel) -> str:
    """Canonical JSON: sorted keys, 2-space indent, trailing newline. Hash this, not model_dump_json()."""
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd ~/bench && uv run pytest tests/test_models.py -q`
Expected: `9 passed`

- [x] **Step 5: Commit**

```bash
cd ~/bench && git add src/bench_ops/models.py tests/test_models.py
git commit -m "feat: release contract models (manifest, ledger, lock, scores)"
```

---

### Task 4: Git wrapper

**Files:**
- Create: `~/bench/src/bench_ops/gitx.py`
- Test: `~/bench/tests/test_gitx.py`

- [x] **Step 1: Write the failing tests**

```python
# tests/test_gitx.py
import subprocess
from pathlib import Path

from bench_ops.gitx import GitStatus, clone_or_pull, status, worktrees


def make_origin(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    subprocess.run(["git", "init", "-b", "main", "-q"], cwd=src, check=True)
    (src / "a.txt").write_text("a\n")
    subprocess.run(["git", "add", "a.txt"], cwd=src, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], cwd=src, check=True)
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], check=True)
    return bare


def test_clone_then_pull(tmp_path: Path) -> None:
    origin = make_origin(tmp_path)
    dest = tmp_path / "dest"
    assert clone_or_pull(str(origin), dest, "main") == "cloned"
    assert (dest / "a.txt").exists()
    assert clone_or_pull(str(origin), dest, "main") == "pulled"


def test_status_clean_and_dirty(tmp_path: Path) -> None:
    origin = make_origin(tmp_path)
    dest = tmp_path / "dest"
    clone_or_pull(str(origin), dest, "main")
    s: GitStatus = status(dest)
    assert s.branch == "main" and s.dirty == 0
    (dest / "b.txt").write_text("b\n")
    assert status(dest).dirty == 1


def test_worktrees_lists_extra(tmp_path: Path) -> None:
    origin = make_origin(tmp_path)
    dest = tmp_path / "dest"
    clone_or_pull(str(origin), dest, "main")
    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "side", str(wt)], cwd=dest, check=True)
    assert [p.name for p in worktrees(dest)] == ["wt"]


def test_status_not_a_repo(tmp_path: Path) -> None:
    assert status(tmp_path / "missing").branch == "(missing)"
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd ~/bench && uv run pytest tests/test_gitx.py -q`
Expected: `ModuleNotFoundError: No module named 'bench_ops.gitx'`

- [x] **Step 3: Write `src/bench_ops/gitx.py`**

```python
"""Thin git wrapper. Every call is a subprocess; nothing here rewrites history."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)
    return out.stdout


def clone_or_pull(url: str, dest: Path, branch: str) -> str:
    if (dest / ".git").exists():
        git(dest, "fetch", "-q", "origin", branch)
        git(dest, "checkout", "-q", branch)
        git(dest, "pull", "-q", "--ff-only", "origin", branch)
        return "pulled"
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", "--branch", branch, url, str(dest)], check=True)
    return "cloned"


@dataclass(frozen=True)
class GitStatus:
    branch: str
    dirty: int
    head: str


def status(repo: Path) -> GitStatus:
    if not (repo / ".git").exists():
        return GitStatus(branch="(missing)", dirty=0, head="")
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    head = git(repo, "rev-parse", "--short", "HEAD").strip()
    dirty = len([ln for ln in git(repo, "status", "--porcelain").splitlines() if ln.strip()])
    return GitStatus(branch=branch, dirty=dirty, head=head)


def worktrees(repo: Path) -> list[Path]:
    """Worktrees other than the main checkout."""
    if not (repo / ".git").exists():
        return []
    paths: list[Path] = []
    for line in git(repo, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            p = Path(line.split(" ", 1)[1])
            if p.resolve() != repo.resolve():
                paths.append(p)
    return paths
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd ~/bench && uv run pytest tests/test_gitx.py -q`
Expected: `4 passed`

- [x] **Step 5: Commit**

```bash
cd ~/bench && git add src/bench_ops/gitx.py tests/test_gitx.py
git commit -m "feat: git wrapper (clone_or_pull, status, worktrees)"
```

---

### Task 5: `bench sync`

**Files:**
- Create: `~/bench/src/bench_ops/cli.py`
- Test: `~/bench/tests/test_cli_sync.py`

- [x] **Step 1: Write the failing tests**

```python
# tests/test_cli_sync.py
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bench_ops.cli import app
from tests.test_gitx import make_origin

runner = CliRunner()


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    origin = make_origin(tmp_path)
    (tmp_path / "repos.toml").write_text(
        f'[[repos]]\nname = "ndb"\nurl = "{origin}"\nbranch = "main"\nrole = "producer"\n'
    )
    monkeypatch.setenv("BENCH_ROOT", str(tmp_path))
    monkeypatch.setenv("BENCH_NO_LEFTHOOK", "1")  # tests never shell out to lefthook
    return tmp_path


def test_sync_clones_and_creates_dirs(root: Path) -> None:
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output
    assert (root / "ndb" / "a.txt").exists()
    assert (root / ".work").is_dir() and (root / ".logs").is_dir()
    assert "ndb" in result.output and "cloned" in result.output


def test_sync_twice_pulls(root: Path) -> None:
    runner.invoke(app, ["sync"])
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert "pulled" in result.output


def test_sync_only_one_repo(root: Path) -> None:
    result = runner.invoke(app, ["sync", "--only", "nope"])
    assert result.exit_code == 1
    assert "nope" in result.output
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd ~/bench && uv run pytest tests/test_cli_sync.py -q`
Expected: `ModuleNotFoundError: No module named 'bench_ops.cli'`

- [x] **Step 3: Write `src/bench_ops/cli.py`**

```python
"""`bench`: the one command of the workspace."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import typer
from rich.console import Console

from bench_ops.config import Config, load_config
from bench_ops.gitx import clone_or_pull

app = typer.Typer(no_args_is_help=True, add_completion=False)
hook_app = typer.Typer(no_args_is_help=True, help="Hooks run by lefthook (see lefthook/common.yml).")
app.add_typer(hook_app, name="hook")
console = Console()


def _config() -> Config:
    try:
        return load_config()
    except FileNotFoundError as e:
        console.print(f"[red]no repos.toml at {e.filename}; set BENCH_ROOT or clone bench-ops to ~/bench[/]")
        raise typer.Exit(2) from None


def _install_hooks(repo: Path) -> str:
    if os.environ.get("BENCH_NO_LEFTHOOK") == "1" or not (repo / "lefthook.yml").exists():
        return "no lefthook.yml"
    if shutil.which("lefthook") is None:
        return "lefthook missing (brew install lefthook)"
    subprocess.run(["lefthook", "install"], cwd=repo, check=True, capture_output=True)
    return "hooks installed"


@app.command()
def sync(only: str | None = typer.Option(None, help="Only this repo (name in repos.toml).")) -> None:
    """Clone or fast-forward every linked repo, create .work/ and .logs/, install hooks."""
    cfg = _config()
    (cfg.root / ".work").mkdir(exist_ok=True)
    (cfg.root / ".logs").mkdir(exist_ok=True)
    repos = cfg.repos
    if only:
        try:
            repos = [cfg.by_name(only)]
        except KeyError:
            console.print(f"[red]unknown repo {only!r}; known: {', '.join(r.name for r in cfg.repos)}[/]")
            raise typer.Exit(1) from None
    for r in repos:
        action = clone_or_pull(r.url, r.local_path, r.branch)
        hooks = _install_hooks(r.local_path)
        console.print(f"{r.name:<16} {action:<7} {r.local_path}  ·  {hooks}")
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd ~/bench && uv run pytest tests/test_cli_sync.py -q`
Expected: `3 passed`

- [x] **Step 5: Commit**

```bash
cd ~/bench && git add src/bench_ops/cli.py tests/test_cli_sync.py
git commit -m "feat: bench sync clones/pulls linked repos and installs hooks"
```

---

### Task 6: Hugging Face witness

HF reports a `sha256` only for LFS-stored files (`RepoFile.lfs["sha256"]`). Small git-stored files (the JSON ledgers) have only a git blob id, so those are downloaded and hashed. The witness therefore never trusts a size or a name, only bytes or an LFS sha256.

**Files:**
- Create: `~/bench/src/bench_ops/hf.py`
- Test: `~/bench/tests/test_hf.py`

- [x] **Step 1: Write the failing tests**

```python
# tests/test_hf.py
import hashlib
from datetime import UTC, datetime
from pathlib import Path

from bench_ops.hf import Witness, verify_manifest
from bench_ops.models import FileEntry, HfRef, Manifest

SMALL = b'{"a": 1}\n'
SMALL_SHA = hashlib.sha256(SMALL).hexdigest()
BIG_SHA = "c" * 64


class FakeApi:
    """Stands in for huggingface_hub.HfApi: paths_info + download of small files."""

    def __init__(self, tmp: Path, lfs: dict[str, str], small: dict[str, bytes]) -> None:
        self.tmp, self.lfs, self.small = tmp, lfs, small
        self.calls: list[str] = []

    def paths_info(self, repo: str, revision: str, paths: list[str]) -> dict[str, str | None]:
        self.calls.append("paths_info")
        out: dict[str, str | None] = {}
        for p in paths:
            if p in self.lfs:
                out[p] = self.lfs[p]
            elif p in self.small:
                out[p] = None  # exists, not LFS
        return out

    def download(self, repo: str, revision: str, path: str) -> Path:
        self.calls.append(f"download:{path}")
        f = self.tmp / Path(path).name
        f.write_bytes(self.small[path])
        return f


def manifest() -> Manifest:
    return Manifest(
        tag="ndb-v1.0.0", created=datetime(2026, 11, 2, tzinfo=UTC), producer="ndb",
        hf=HfRef(repo="arthrod/neurotic_docx_bench", revision="b" * 40), r2_prefix="ndb-v1.0.0/",
        files=[
            FileEntry(path="release/ndb-v1.0.0/scores.json", sha256=SMALL_SHA, bytes=len(SMALL)),
            FileEntry(path="site/x.pdf", sha256=BIG_SHA, bytes=99, served=True),
        ],
    )


def test_all_good(tmp_path: Path) -> None:
    api = FakeApi(tmp_path, lfs={"site/x.pdf": BIG_SHA}, small={"release/ndb-v1.0.0/scores.json": SMALL})
    w: Witness = verify_manifest(manifest(), api)
    assert w.ok and w.missing == [] and w.mismatched == []
    assert api.calls == ["paths_info", "download:release/ndb-v1.0.0/scores.json"]


def test_missing_file(tmp_path: Path) -> None:
    api = FakeApi(tmp_path, lfs={}, small={"release/ndb-v1.0.0/scores.json": SMALL})
    w = verify_manifest(manifest(), api)
    assert not w.ok and w.missing == ["site/x.pdf"]


def test_lfs_mismatch(tmp_path: Path) -> None:
    api = FakeApi(tmp_path, lfs={"site/x.pdf": "d" * 64}, small={"release/ndb-v1.0.0/scores.json": SMALL})
    w = verify_manifest(manifest(), api)
    assert w.mismatched == ["site/x.pdf"]


def test_small_mismatch(tmp_path: Path) -> None:
    api = FakeApi(tmp_path, lfs={"site/x.pdf": BIG_SHA}, small={"release/ndb-v1.0.0/scores.json": b"other"})
    w = verify_manifest(manifest(), api)
    assert w.mismatched == ["release/ndb-v1.0.0/scores.json"]
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd ~/bench && uv run pytest tests/test_hf.py -q`
Expected: `ModuleNotFoundError: No module named 'bench_ops.hf'`

- [x] **Step 3: Write `src/bench_ops/hf.py`**

```python
"""Hugging Face as the record: is every manifest file there, with the bytes the manifest says?"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from bench_ops.models import Manifest


class HubApi(Protocol):
    def paths_info(self, repo: str, revision: str, paths: list[str]) -> dict[str, str | None]:
        """path -> LFS sha256, or None when the file exists but is git-stored; absent when missing."""

    def download(self, repo: str, revision: str, path: str) -> Path: ...


class HfHub:
    """The real HubApi over huggingface_hub (dataset repos only)."""

    def __init__(self) -> None:
        from huggingface_hub import HfApi

        self.api = HfApi()

    def paths_info(self, repo: str, revision: str, paths: list[str]) -> dict[str, str | None]:
        out: dict[str, str | None] = {}
        # The endpoint takes many paths at once; 500 keeps the request body small.
        for i in range(0, len(paths), 500):
            for info in self.api.get_paths_info(repo, paths[i : i + 500], revision=revision, repo_type="dataset"):
                lfs = getattr(info, "lfs", None)
                out[info.path] = lfs["sha256"] if lfs else None
        return out

    def download(self, repo: str, revision: str, path: str) -> Path:
        from huggingface_hub import hf_hub_download

        return Path(hf_hub_download(repo, path, revision=revision, repo_type="dataset"))


@dataclass
class Witness:
    missing: list[str] = field(default_factory=list)
    mismatched: list[str] = field(default_factory=list)
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.missing and not self.mismatched


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_manifest(m: Manifest, api: HubApi) -> Witness:
    w = Witness()
    info = api.paths_info(m.hf.repo, m.hf.revision, [f.path for f in m.files])
    for f in m.files:
        w.checked += 1
        if f.path not in info:
            w.missing.append(f.path)
            continue
        remote = info[f.path]
        if remote is None:  # git-stored: fetch the bytes
            remote = sha256_of(api.download(m.hf.repo, m.hf.revision, f.path))
        if remote != f.sha256:
            w.mismatched.append(f.path)
    return w
```

- [x] **Step 4: Run tests to verify they pass**

Run: `cd ~/bench && uv run pytest tests/test_hf.py -q`
Expected: `4 passed`

- [x] **Step 5: Commit**

```bash
cd ~/bench && git add src/bench_ops/hf.py tests/test_hf.py
git commit -m "feat: HF witness verifies a manifest against the record"
```

---

### Task 7: `bench status`

Per repo: branch, dirty count, worktrees outside `.worktrees/`, and for producers the last ledger entry verified against HF; for consumers `bench.lock` vs each producer's latest tag. Producers have no `release/index.jsonl` until Plan 2, and consumers no `bench.lock` until Plans 4–5; status says so instead of failing.

**Files:**
- Create: `~/bench/src/bench_ops/status.py`
- Modify: `~/bench/src/bench_ops/cli.py` (add `status` command)
- Test: `~/bench/tests/test_status.py`

- [x] **Step 1: Write the failing tests**

```python
# tests/test_status.py
from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from bench_ops.cli import app
from bench_ops.config import Config, Repo
from bench_ops.hf import Witness
from bench_ops.models import HfRef, LedgerEntry, Lock
from bench_ops.status import RepoReport, report
from tests.test_gitx import make_origin
from bench_ops.gitx import clone_or_pull

SHA = "a" * 64


def _repo(tmp_path: Path, name: str, role: str) -> Repo:
    origin = make_origin(tmp_path / name)
    dest = tmp_path / "root" / name
    clone_or_pull(str(origin), dest, "main")
    r = Repo(name=name, url=str(origin), branch="main", role=role, path=dest)
    return r


def test_producer_without_ledger(tmp_path: Path) -> None:
    r = _repo(tmp_path, "ndb", "producer")
    rep: RepoReport = report(r, latest={}, witness=lambda m: Witness())
    assert rep.branch == "main" and rep.release == "no releases yet"


def test_producer_with_ledger_is_verified(tmp_path: Path) -> None:
    r = _repo(tmp_path, "ndb", "producer")
    (r.local_path / "release").mkdir()
    e = LedgerEntry(tag="ndb-v1.0.0", created=datetime(2026, 11, 2, tzinfo=UTC),
                    hf=HfRef(repo="arthrod/neurotic_docx_bench", revision="b" * 40), manifest_sha256=SHA)
    (r.local_path / "release" / "index.jsonl").write_text(e.to_line())
    seen: list[str] = []

    def witness(entry: LedgerEntry) -> Witness:
        seen.append(entry.tag)
        return Witness(missing=["site/x.pdf"])

    rep = report(r, latest={}, witness=witness)
    assert seen == ["ndb-v1.0.0"]
    assert rep.release == "ndb-v1.0.0" and rep.witness == "1 missing"


def test_consumer_lock_vs_latest(tmp_path: Path) -> None:
    r = _repo(tmp_path, "jubarte-app", "consumer")
    (r.local_path / "bench.lock").write_text(Lock(tags={"ndb": "ndb-v1.0.0"}).render())
    rep = report(r, latest={"ndb": "ndb-v1.0.1"}, witness=lambda m: Witness())
    assert rep.release == "ndb=ndb-v1.0.0" and rep.witness == "stale: ndb latest is ndb-v1.0.1"


def test_consumer_without_lock(tmp_path: Path) -> None:
    r = _repo(tmp_path, "benchmarks-law", "consumer")
    rep = report(r, latest={}, witness=lambda m: Witness())
    assert rep.release == "no bench.lock"


def test_cli_status_runs(tmp_path: Path, monkeypatch) -> None:
    r = _repo(tmp_path, "ndb", "producer")
    (tmp_path / "root" / "repos.toml").write_text(
        f'[[repos]]\nname = "ndb"\nurl = "{r.url}"\nbranch = "main"\nrole = "producer"\n'
    )
    monkeypatch.setenv("BENCH_ROOT", str(tmp_path / "root"))
    result = CliRunner().invoke(app, ["status", "--offline"])
    assert result.exit_code == 0, result.output
    assert "ndb" in result.output and "no releases yet" in result.output
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd ~/bench && uv run pytest tests/test_status.py -q`
Expected: `ModuleNotFoundError: No module named 'bench_ops.status'`

- [x] **Step 3: Write `src/bench_ops/status.py`**

```python
"""One row of `bench status` per linked repo."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from bench_ops.config import Repo
from bench_ops.gitx import status as git_status, worktrees
from bench_ops.hf import Witness
from bench_ops.models import LedgerEntry, Lock

WitnessFn = Callable[[LedgerEntry], Witness]


@dataclass(frozen=True)
class RepoReport:
    name: str
    role: str
    branch: str
    dirty: int
    stray_worktrees: list[str]
    release: str
    witness: str


def latest_tags(repos: list[Repo]) -> dict[str, str]:
    """producer name -> tag of its last ledger line (producers without a ledger are absent)."""
    out: dict[str, str] = {}
    for r in repos:
        if r.role != "producer":
            continue
        ledger = r.local_path / "release" / "index.jsonl"
        if ledger.exists():
            last = LedgerEntry.last(ledger.read_text())
            if last:
                out[r.name] = last.tag
    return out


def _describe(w: Witness) -> str:
    if w.ok:
        return f"verified {w.checked} files on HF"
    parts = []
    if w.missing:
        parts.append(f"{len(w.missing)} missing")
    if w.mismatched:
        parts.append(f"{len(w.mismatched)} mismatched")
    return ", ".join(parts)


def report(repo: Repo, latest: dict[str, str], witness: WitnessFn) -> RepoReport:
    g = git_status(repo.local_path)
    stray = [str(p) for p in worktrees(repo.local_path) if ".worktrees" not in p.parts]
    release, verdict = "", ""
    if repo.role == "producer":
        ledger = repo.local_path / "release" / "index.jsonl"
        last = LedgerEntry.last(ledger.read_text()) if ledger.exists() else None
        if last is None:
            release = "no releases yet"
        else:
            release = last.tag
            verdict = _describe(witness(last))
    else:
        lock_path = repo.local_path / "bench.lock"
        if not lock_path.exists():
            release = "no bench.lock"
        else:
            lock = Lock.parse(lock_path.read_text())
            release = " ".join(f"{k}={v}" for k, v in sorted(lock.tags.items()))
            stale = [f"{k} latest is {latest[k]}" for k, v in lock.tags.items() if k in latest and latest[k] != v]
            verdict = "stale: " + "; ".join(stale) if stale else "up to date"
    return RepoReport(repo.name, repo.role, g.branch, g.dirty, stray, release, verdict)
```

- [x] **Step 4: Add the `status` command to `cli.py`**

Append to `src/bench_ops/cli.py` (imports at top of the file: `from rich.table import Table as RichTable`, `from bench_ops.status import latest_tags, report`, `from bench_ops.hf import HfHub, Witness, verify_manifest`, `from bench_ops.models import LedgerEntry, Manifest`):

```python
def _witness_via_hf(entry: LedgerEntry) -> Witness:
    """Fetch release/<tag>/manifest.json from HF, check its sha256 against the ledger, then every file."""
    hub = HfHub()
    path = hub.download(entry.hf.repo, entry.hf.revision, f"release/{entry.tag}/manifest.json")
    from bench_ops.hf import sha256_of

    if sha256_of(path) != entry.manifest_sha256:
        return Witness(mismatched=[f"release/{entry.tag}/manifest.json"], checked=1)
    return verify_manifest(Manifest.model_validate_json(path.read_text()), hub)


@app.command()
def status(
    offline: bool = typer.Option(False, help="Skip the Hugging Face witness."),
    fail_on_stale: bool = typer.Option(False, "--fail-on-stale", help="Exit 1 when a consumer lock is behind."),
    repo: Path | None = typer.Option(None, help="Only the repo at this path (used by the pre-push hook)."),
) -> None:
    """Branch, dirt, worktrees and release state of every linked repo."""
    cfg = _config()
    repos = cfg.repos
    if repo is not None:
        repos = [r for r in cfg.repos if r.local_path.resolve() == repo.resolve()]
        if not repos:
            console.print(f"[yellow]{repo} is not a linked repo; nothing to check[/]")
            raise typer.Exit(0)
    latest = latest_tags(cfg.repos)
    witness = (lambda e: Witness()) if offline else _witness_via_hf
    table = RichTable(title="bench status")
    for col in ("repo", "role", "branch", "dirty", "stray worktrees", "release", "witness"):
        table.add_column(col)
    stale = False
    for r in repos:
        rep = report(r, latest, witness)
        stale |= rep.witness.startswith("stale")
        table.add_row(rep.name, rep.role, rep.branch, str(rep.dirty), "\n".join(rep.stray_worktrees) or "-",
                      rep.release, rep.witness or ("(offline)" if offline else "-"))
    console.print(table)
    if fail_on_stale and stale:
        console.print("[red]a consumer lock is behind its producer; run `bench bump` or set BENCH_ALLOW_STALE=1[/]")
        raise typer.Exit(1)
```

- [x] **Step 5: Run tests to verify they pass**

Run: `cd ~/bench && uv run pytest -q`
Expected: all tests pass (`27 passed`)

- [x] **Step 6: Commit**

```bash
cd ~/bench && git add src/bench_ops/status.py src/bench_ops/cli.py tests/test_status.py
git commit -m "feat: bench status reports branches, worktrees and release witness"
```

---

### Task 8: Hook commands (`bench hook size-guard`, `bench hook secrets`)

**Files:**
- Create: `~/bench/src/bench_ops/hooks.py`
- Modify: `~/bench/src/bench_ops/cli.py` (register `hook_app` commands)
- Test: `~/bench/tests/test_hooks.py`

- [x] **Step 1: Write the failing tests**

```python
# tests/test_hooks.py
from pathlib import Path

from typer.testing import CliRunner

from bench_ops.cli import app
from bench_ops.hooks import BLOCKED_DIRS, BLOCKED_SUFFIXES, size_guard

runner = CliRunner()


def test_size_guard_blocks_large(tmp_path: Path) -> None:
    big = tmp_path / "big.bin"
    big.write_bytes(b"\0" * (2 * 1024 * 1024 + 1))
    problems = size_guard([big], root=tmp_path, limit=2 * 1024 * 1024)
    assert problems == [f"big.bin: 2.0 MB over the 2.0 MB limit"]


def test_size_guard_blocks_binary_under_results(tmp_path: Path) -> None:
    p = tmp_path / "results" / "run" / "page.png"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    assert size_guard([p], root=tmp_path) == ["results/run/page.png: binary under results/ belongs on Hugging Face"]


def test_size_guard_allows_small_ledger(tmp_path: Path) -> None:
    p = tmp_path / "results" / "bench.jsonl"
    p.parent.mkdir()
    p.write_text("{}\n")
    assert size_guard([p], root=tmp_path) == []


def test_blocklists_are_what_the_plan_says() -> None:
    assert {"results", "out", "public/fixtures", "public/neurotic-docx-bench"} <= set(BLOCKED_DIRS)
    assert {".png", ".pdf", ".docx", ".webp", ".zip"} <= set(BLOCKED_SUFFIXES)


def test_cli_size_guard_exit_code(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ok.txt").write_text("fine\n")
    assert runner.invoke(app, ["hook", "size-guard", "ok.txt"]).exit_code == 0
    (tmp_path / "big.bin").write_bytes(b"\0" * (3 * 1024 * 1024))
    r = runner.invoke(app, ["hook", "size-guard", "big.bin"])
    assert r.exit_code == 1 and "big.bin" in r.output


def test_cli_secrets_without_tool_fails_with_hint(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))  # no ripsecrets here
    (tmp_path / "a.txt").write_text("x\n")
    r = runner.invoke(app, ["hook", "secrets", "a.txt"])
    assert r.exit_code == 1 and "brew install ripsecrets" in r.output
```

- [x] **Step 2: Run tests to verify they fail**

Run: `cd ~/bench && uv run pytest tests/test_hooks.py -q`
Expected: `ModuleNotFoundError: No module named 'bench_ops.hooks'`

- [x] **Step 3: Write `src/bench_ops/hooks.py`**

```python
"""Pre-commit checks every linked repo runs through lefthook. Each must finish in seconds."""

from __future__ import annotations

import os
from pathlib import Path

LIMIT = 2 * 1024 * 1024
# Directories whose binaries are data, not code: they live on Hugging Face (record) and R2 (cache).
BLOCKED_DIRS = ("results", "out", "public/fixtures", "public/neurotic-docx-bench", ".bench")
BLOCKED_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".pdf", ".docx", ".zip", ".tar", ".gz", ".parquet", ".bin")


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


def size_guard(files: list[Path], root: Path, limit: int = LIMIT) -> list[str]:
    problems: list[str] = []
    for f in files:
        if not f.is_file():
            continue
        rel = f.resolve().relative_to(root.resolve()).as_posix()
        size = f.stat().st_size
        if size > limit:
            problems.append(f"{rel}: {_mb(size)} over the {_mb(limit)} limit")
            continue
        top = next((d for d in BLOCKED_DIRS if rel == d or rel.startswith(d + "/")), None)
        if top and f.suffix.lower() in BLOCKED_SUFFIXES:
            problems.append(f"{rel}: binary under {top}/ belongs on Hugging Face")
    return problems


def secrets_command(files: list[Path]) -> list[str] | None:
    """The ripsecrets argv, or None when the tool is not on PATH."""
    for d in os.environ.get("PATH", "").split(os.pathsep):
        if (Path(d) / "ripsecrets").is_file():
            return ["ripsecrets", "--strict-ignore", *map(str, files)]
    return None
```

- [x] **Step 4: Register the hook commands in `cli.py`**

Append to `src/bench_ops/cli.py` (import `from bench_ops.hooks import secrets_command, size_guard`):

```python
@hook_app.command("size-guard")
def hook_size_guard(files: list[Path] = typer.Argument(...)) -> None:
    """Refuse staged files over 2 MB and binaries under data directories."""
    problems = size_guard(files, root=Path.cwd())
    for p in problems:
        console.print(f"[red]{p}[/]")
    if problems:
        console.print("Data goes to Hugging Face via `bench publish`; git keeps code and small ledgers (docs/CONTRACT.md).")
        raise typer.Exit(1)


@hook_app.command("secrets")
def hook_secrets(files: list[Path] = typer.Argument(...)) -> None:
    """Scan staged files for secrets with ripsecrets."""
    argv = secrets_command(files)
    if argv is None:
        console.print("[red]ripsecrets is not installed: brew install ripsecrets[/]")
        raise typer.Exit(1)
    rc = subprocess.run(argv).returncode
    if rc:
        raise typer.Exit(rc)
```

- [x] **Step 5: Run tests to verify they pass**

Run: `cd ~/bench && uv run pytest -q`
Expected: all pass (`33 passed`)

- [x] **Step 6: Commit**

```bash
cd ~/bench && git add src/bench_ops/hooks.py src/bench_ops/cli.py tests/test_hooks.py
git commit -m "feat: bench hook size-guard and secrets for lefthook"
```

---

### Task 9: Shared lefthook config and the contract doc

**Files:**
- Create: `~/bench/lefthook/common.yml`
- Create: `~/bench/lefthook/REPO-lefthook.yml` (the 6-line file each repo copies verbatim)
- Create: `~/bench/docs/CONTRACT.md`
- Create: `~/bench/lefthook.yml` (bench-ops guards itself with the same config)

- [x] **Step 1: Write `lefthook/common.yml`**

```yaml
# Pulled into every linked repo through `remotes:` (see REPO-lefthook.yml). Edit here only.
# Every job must finish in seconds; nothing here waits on CI or uploads anything.
skip_output: [meta, summary]

pre-commit:
  parallel: true
  jobs:
    - name: size guard
      run: bench hook size-guard {staged_files}
    - name: secrets
      run: bench hook secrets {staged_files}

pre-push:
  jobs:
    - name: release state
      skip:
        - merge
        - rebase
      env:
        BENCH_ALLOW_STALE: "{env:BENCH_ALLOW_STALE}"
      run: >
        sh -c 'if [ "$BENCH_ALLOW_STALE" = "1" ]; then exit 0; fi;
        bench status --offline --fail-on-stale --repo "$(git rev-parse --show-toplevel)"'
```

- [x] **Step 2: Write `lefthook/REPO-lefthook.yml`**

```yaml
# Hooks for this repo live in github.com/arthrod/bench-ops/lefthook. Do not add jobs here;
# add them there so the four bench repos cannot drift. Needs `bench` (uv tool install bench-ops).
remotes:
  - git_url: https://github.com/arthrod/bench-ops
    ref: main
    refetch_frequency: 24h
    configs:
      - lefthook/common.yml
```

Producer- and consumer-specific files (`lefthook/producer.yml`: verify `release/index.jsonl` on change; `lefthook/consumer.yml`: `bench fetch --verify` + the repo's proof on `bench.lock` change) are added by Plans 2 and 4, where the commands they call exist. YAGNI now.

- [x] **Step 3: Write `~/bench/lefthook.yml`** (bench-ops uses its own local file, not remotes, so hook edits are testable before push)

```yaml
pre-commit:
  parallel: true
  jobs:
    - name: size guard
      run: uv run bench hook size-guard {staged_files}
    - name: secrets
      run: uv run bench hook secrets {staged_files}
    - name: ruff
      glob: "*.py"
      run: uv run ruff check {staged_files}
    - name: tests
      run: uv run pytest -q
```

- [x] **Step 4: Write `docs/CONTRACT.md`**

```markdown
# The storage contract

Every byte the benches produce has exactly one role per surface. If you cannot name the role, do not write there.

| Surface | Role | Rule |
|---|---|---|
| **Hugging Face** datasets `arthrod/neurotic_docx_bench` (runs, results, `site/` fixtures, `release/<tag>/`), `arthrod/neurotic_docx_bench-fixtures` (corpus), `arthrod/editors_trial-artifacts`, `arthrod/neurotic_docx_bench-archive` (private: bundles, backups, satellites) | **the record** | immutable once a tag's manifest is written; pinned by revision; 320 commits/h, so uploads are batched |
| **R2** `neurotic-docx-bench-pages`, `benchmarks-law-media`, `jubarte-live-bench-artifacts` | **serving cache** | holds only what a site serves (`FileEntry.served`), under `<tag>/`; always rebuildable from HF + manifest; may be wiped |
| **Git** (four repos) | code, configs, **small ledgers**: `release/index.jsonl`, `bench.lock`, `data/facts.jsonl`, REPORT/RESULTS markdown | no file > 2 MB, no binaries under data dirs (`bench hook size-guard`); no history rewrites |
| **Workers / GitHub Pages** | apps and viewers | point at R2; never embed data that is not in a tagged release |
| **Local disk** `~/bench/.work/<repo>/<run>` | scratch | deletable only by the two-witness rule |

## Two witnesses before any delete

A local file may be deleted only when its sha256 is listed in a release manifest **and** that manifest has been verified against Hugging Face (`bench status` witness; `bench gc` in Plan 2) — and against R2 too if the file is served. This is the rule that would have kept the 11 GB of grok_run outputs lost in October 2026.

## Releases

- Producers publish `release/<tag>/{manifest.json, scores.json, cases/*.json}` to HF and append one line to `release/index.jsonl` (`LedgerEntry`). Tags: `ndb-vX.Y.Z[.n]`, `et-vX.Y.Z`.
- Consumers pin tags in `bench.lock` (`<producer>=<tag>` per line) and render `scores.json` through `@jandira-tech/bench-viewer` with their theme. "Latest" means the lock was bumped (`bench bump`), never a runtime fetch.
- Hooks come from `bench-ops/lefthook/` through lefthook `remotes:`; escape hatches are `LEFTHOOK=0` and `BENCH_ALLOW_STALE=1`.
```

- [x] **Step 5: Verify the remote-config syntax parses**

```bash
cd ~/bench && lefthook install && lefthook dump
```
Expected: the dump lists `pre-commit` with jobs `size guard`, `secrets`, `ruff`, `tests` and no YAML errors. Then `lefthook validate-config lefthook/common.yml` if the installed lefthook has it (≥1.10); otherwise the dump in Task 10 Step 3 is the check.

- [x] **Step 6: Commit**

```bash
cd ~/bench && git add lefthook.yml lefthook/common.yml lefthook/REPO-lefthook.yml docs/CONTRACT.md
git commit -m "feat: shared lefthook config and the storage contract"
```

---

### Task 10: Install `bench`, publish bench-ops, wire the four repos

**Files:**
- Create in each linked repo: `lefthook.yml` (ndb replaces its untouched example file)
- Create: `~/bench/docs/plans/2026-11-02-plan1-bench-ops.md` (copy of this plan)

- [x] **Step 1: Install the CLI globally and check tools**

```bash
cd ~/bench && uv tool install --editable . && bench --help
brew list lefthook ripsecrets >/dev/null 2>&1 || brew install lefthook ripsecrets
```
Expected: `bench --help` lists `sync`, `status`, `hook`.

- [x] **Step 2: Create the GitHub repo and push** (outward-facing: **confirm with Arthur first**)

```bash
cd ~/bench && mkdir -p docs/plans && cp ~/.claude/plans/cuddly-exploring-neumann.md docs/plans/2026-11-02-plan1-bench-ops.md
git add docs/plans && git commit -m "docs: plan 1 (bench-ops)"
gh repo create arthrod/bench-ops --public --source . --push
```

- [x] **Step 3: Install hooks in each linked repo** (one small PR per repo; results and code never in the same commit)

For each of the four paths in `repos.toml`:

```bash
cd <repo> && git switch -c chore/bench-ops-hooks
cp ~/bench/lefthook/REPO-lefthook.yml lefthook.yml
lefthook install && lefthook dump | head -20
```
Expected: the dump shows the two `pre-commit` jobs and the `pre-push` job fetched from the remote (lefthook clones the remote under `.git/info/lefthook-remotes/`).

```bash
git add lefthook.yml && git commit -m "chore: hooks from bench-ops (size guard, secrets, release state)"
git push -u origin chore/bench-ops-hooks && gh pr create --fill
```
Then merge through `/conejo-merge`. For ndb the commit replaces the example `lefthook.yml`.

- [x] **Step 4: Prove the hooks bite**

In ndb (on the branch, before merging):

```bash
cd /Users/arthrod/temp/T/neurotic_docx_bench
head -c 3000000 /dev/zero > results/_hook_probe.bin && git add -f results/_hook_probe.bin
git commit -m "probe" ; echo "exit=$?"
```
Expected: `results/_hook_probe.bin: 2.9 MB over the 2.0 MB limit`, `exit=1`. Then `git reset -q results/_hook_probe.bin && rm results/_hook_probe.bin`.

- [x] **Step 5: Run `bench sync` and `bench status` against the real workspace**

```bash
bench sync && bench status --offline
```
Expected: four rows; ndb and editors_trial show `no releases yet`; benchmarks-law and jubarte-app show `no bench.lock`; ndb's stray worktrees column lists `../ndb-105` and `../ndb-apryse` (to be moved under `.worktrees/` in Plan 2's cheap wins); every row prints `pulled`.

- [x] **Step 6: Record the baseline numbers** (the before-state for every later plan's verification)

```bash
for r in neurotic_docx_bench editors_trial briefs_agreements/benchmarks-law 1_jubarte-redlines/jubarte-app; do
  echo "== $r"; git -C /Users/arthrod/temp/T/$r count-objects -vH | grep -E 'size-pack|count'
done > ~/bench/.logs/baseline-$(date +%Y%m%d).txt
gh repo view jandira-tech/neurotic_docx_bench --json diskUsage >> ~/bench/.logs/baseline-$(date +%Y%m%d).txt
```

---

## Plans 2–6 (written after Plan 1 lands; scope and acceptance fixed now)

| Plan | Repo | Delivers | Done when |
|---|---|---|---|
| **2 — ndb release bundle** | ndb + bench-ops | `scores.json` emitted by the same code path as `uv run bench report`; `release/index.jsonl`; `bench publish ndb <tag>` (stage → manifest → HF batched upload → witness → R2 mirror of `served` files via `scripts/publish/upload_r2.py` → ledger → git tag); `bench gc` two-witness; `lefthook/producer.yml`; tracked `results/**` heavy files moved to HF `runs/`, LFS globs dropped (no rewrite); redundant HF datasets frozen with README pointers after sha256 cross-check; P5 cheap wins (editors_trial `git gc`, worktrees under `.worktrees/`, GitHub support request, merged-branch auto-delete) | `bench publish ndb --dry-run` prints a valid `Manifest`; a real publish leaves `bench status` green; ndb `git ls-files results | wc -l` drops from 26,541 to the ledgers only |
| **3 — bench-viewer** | ndb `packages/bench-viewer` | framework-free TS: `renderTable`, `renderHeadlines`, `renderBars`, `renderStates`, `renderCaseStrip`, `renderLegend` → HTML strings; `themes/tokens.css`, `themes/arthur-law.css`, `themes/jubarte.css`; `build:pages` → orphan gh-pages commit; first `pnpm publish` of `@jandira-tech/bench-viewer` inside `bench publish ndb` | vitest snapshots equal today's jubarte `row()` and gh-pages table markup for the same data; gh-pages tip is one commit |
| **4 — jubarte-site** | jubarte-app | `fixtures.lock` → `bench.lock`; `bench fetch` into `.bench/`; `sync-bench.ts` reads `.bench/scores.json` and still writes through `scripts/facts.py`; `check-bench.ts` proves facts against `scores.json` + manifest sha (Markdown parser removed); `site_fixtures.py fetch/render` read the fixture list from the manifest; `benchmark.ts`/`home.ts` render via bench-viewer + `themes/jubarte.css`; tag badge "bench <tag> · generated <date>"; `pnpm deploy` = `bench deploy jubarte`; `lefthook/consumer.yml` | fresh clone with no ndb checkout: `bench fetch && pnpm build && pnpm test` green; a changed number in `.bench/scores.json` makes `check-bench` exit 1; jubarte.pro/benchmark verified in Chrome |
| **5 — benchmarks-law** | benchmarks-law | delete `public/neurotic-docx-bench/`, `patchNeuroticHtml`, `rebuildScoreTable`; `src/pages/neurotic-docx-bench/*.astro` from `.bench/scores.json` + `themes/arthur-law.css`; `src/lib/root.ts` reads the `et` bundle (REPORTS_ROOT stays as dev override); `bench.lock` with `ndb=` and `et=`; `bench deploy blaw` | fresh clone without sibling checkouts builds; benchmarks.law verified in Chrome; same figures and tag as jubarte.pro, different skin |
| **6 — editors_trial publish + local layout** | editors_trial + bench-ops | `bench publish et <tag>` (`out/facts.json`, `out/release.json`, figures, `reports/*.md` → HF `arthrod/editors_trial-artifacts`, R2 `benchmarks-law-media/<tag>/`, ledger); `RELEASING.md` publish step = one command; `~/bench/.work` TTL; satellites (`ndb-105`, `ndb-apryse`, `ndb_*_2026102x`, `colab_bench`, root `*.log`) archived to HF private `-archive` through `bench gc`, nothing deleted before the witness output is shown | `bench status` green for both producers; satellites gone from `~/temp/T` only after two witnesses |

## Decisions (flip any before approving)

| # | Decision | Alternative not taken |
|---|---|---|
| D1 | Workspace repo `arthrod/bench-ops` at `~/bench`, children linked by `repos.toml` (not submodules) | submodules: pointer-bump commits, detached HEADs — more maintenance, not less |
| D2 | `@jandira-tech/bench-viewer` on public npm; Renovate bumps it natively | ship the viewer in the HF bundle: no registry, but no types and a custom fetch step |
| D3 | Consumers pin by `bench.lock`; `bench bump` moves it | runtime `latest.json`: zero deploys, but unproved numbers and pages that move under visitors |
| D4 | Two themes over one token set: `arthur-law` (gh-pages, benchmarks.law), `jubarte` (jubarte.pro) | per-site overrides (drift) |
| D5 | Hooks via lefthook `remotes:` + global `bench` uv tool | copying configs into each repo (drift) |

## Self-review notes

- Spec coverage: minimal maintenance → Tasks 2, 5, 7, 10 (one repo, one command, linked not vendored); one source/skin-only → Plans 3–5 with the `Scores` contract fixed in Task 3; record versions + latest → `LedgerEntry`/`Lock` (Task 3), `bench status` staleness (Task 7), `bench bump` (Plan 4); lefthook everywhere, realistic → Tasks 8–10 (local, seconds, `LEFTHOOK=0`/`BENCH_ALLOW_STALE=1`, no CI gate).
- Names used consistently across tasks: `Manifest`, `FileEntry`, `HfRef`, `LedgerEntry`, `Lock`, `Scores`, `Witness`, `verify_manifest`, `HfHub`, `RepoReport`, `report`, `latest_tags`, `size_guard`, `secrets_command`, `clone_or_pull`, `status`, `worktrees`, `load_config`, `Config`, `Repo`.
- Test count expectations (6 + 9 + 4 + 3 + 4 + 5 + 6 = 37 by Task 8; the "27"/"33" totals in Tasks 7–8 should be re-read from pytest's own summary — the exact number matters less than "all passed").
