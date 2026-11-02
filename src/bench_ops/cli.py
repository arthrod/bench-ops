"""`bench`: the one command of the workspace."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table as RichTable

from bench_ops.config import Config, load_config
from bench_ops.gitx import clone_or_pull
from bench_ops.hf import HfHub, Witness, sha256_of, verify_manifest
from bench_ops.hooks import secrets_command, size_guard
from bench_ops.models import LedgerEntry, Manifest
from bench_ops.status import latest_tags, report

app = typer.Typer(no_args_is_help=True, add_completion=False)
hook_app = typer.Typer(no_args_is_help=True, help="Hooks run by lefthook (see lefthook/common.yml).")
app.add_typer(hook_app, name="hook")
console = Console()


def _config() -> Config:
    try:
        return load_config()
    except FileNotFoundError as e:
        console.print(
            f"[red]no repos.toml at {e.filename}; set BENCH_ROOT or clone bench-ops to ~/bench[/]"
        )
        raise typer.Exit(2) from None


def _install_hooks(repo: Path) -> str:
    if os.environ.get("BENCH_NO_LEFTHOOK") == "1" or not (repo / "lefthook.yml").exists():
        return "no lefthook.yml"
    if shutil.which("lefthook") is None:
        return "lefthook missing (brew install lefthook)"
    subprocess.run(["lefthook", "install"], cwd=repo, check=True, capture_output=True)
    return "hooks installed"


@app.command()
def sync(
    only: Annotated[str | None, typer.Option(help="Only this repo (name in repos.toml).")] = None,
) -> None:
    """Clone or fast-forward every linked repo, create .work/ and .logs/, install hooks."""
    cfg = _config()
    (cfg.root / ".work").mkdir(exist_ok=True)
    (cfg.root / ".logs").mkdir(exist_ok=True)
    repos = cfg.repos
    if only:
        try:
            repos = [cfg.by_name(only)]
        except KeyError:
            known = ", ".join(r.name for r in cfg.repos)
            console.print(f"[red]unknown repo {only!r}; known: {known}[/]")
            raise typer.Exit(1) from None
    for r in repos:
        action = clone_or_pull(r.url, r.local_path, r.branch)
        hooks = _install_hooks(r.local_path)
        console.print(f"{r.name:<16} {action:<20} {r.local_path}  ·  {hooks}")


def _witness_via_hf(entry: LedgerEntry) -> Witness:
    """Fetch release/<tag>/manifest.json from HF, check it against the ledger, then every file."""
    hub = HfHub()
    path = hub.download(entry.hf.repo, entry.hf.revision, f"release/{entry.tag}/manifest.json")
    if sha256_of(path) != entry.manifest_sha256:
        return Witness(mismatched=[f"release/{entry.tag}/manifest.json"], checked=1)
    return verify_manifest(Manifest.model_validate_json(path.read_text()), hub)


@app.command()
def status(
    offline: Annotated[bool, typer.Option(help="Skip the Hugging Face witness.")] = False,
    fail_on_stale: Annotated[
        bool, typer.Option(help="Exit 1 when a consumer lock is behind its producer.")
    ] = False,
    repo: Annotated[
        Path | None, typer.Option(help="Only the repo at this path (pre-push hook).")
    ] = None,
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
    witness = None if offline else _witness_via_hf
    table = RichTable(title="bench status")
    for col in ("repo", "role", "branch", "dirty", "stray worktrees", "release", "witness"):
        table.add_column(col, overflow="fold")
    stale = False
    for r in repos:
        rep = report(r, latest, witness)
        stale |= rep.stale
        table.add_row(rep.name, rep.role, rep.branch, str(rep.dirty),
                      "\n".join(rep.stray_worktrees) or "-", rep.release, rep.witness or "-")
    console.print(table)
    if fail_on_stale and stale:
        if os.environ.get("BENCH_ALLOW_STALE") == "1":
            console.print("[yellow]consumer lock is behind; allowed by BENCH_ALLOW_STALE=1[/]")
            return
        console.print(
            "[red]a consumer lock is behind its producer; bump bench.lock "
            "or push with BENCH_ALLOW_STALE=1[/]"
        )
        raise typer.Exit(1)


StagedFiles = Annotated[list[Path] | None, typer.Argument(help="Staged files (from lefthook).")]


@hook_app.command("size-guard")
def hook_size_guard(files: StagedFiles = None) -> None:
    """Refuse staged files over 2 MB and binaries under data directories."""
    problems = size_guard(files or [], root=Path.cwd())
    for p in problems:
        console.print(f"[red]{p}[/]")
    if problems:
        console.print(
            "Data goes to Hugging Face; git keeps code and small ledgers (bench-ops docs/CONTRACT.md)."
        )
        raise typer.Exit(1)


@hook_app.command("secrets")
def hook_secrets(files: StagedFiles = None) -> None:
    """Scan staged files for secrets with ripsecrets."""
    if not files:
        return
    argv = secrets_command(files)
    if argv is None:
        console.print("[red]ripsecrets is not installed: brew install ripsecrets[/]")
        raise typer.Exit(1)
    rc = subprocess.run(argv, check=False).returncode
    if rc:
        raise typer.Exit(rc)
