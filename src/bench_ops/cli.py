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
            known = ", ".join(r.name for r in cfg.repos)
            console.print(f"[red]unknown repo {only!r}; known: {known}[/]")
            raise typer.Exit(1) from None
    for r in repos:
        action = clone_or_pull(r.url, r.local_path, r.branch)
        hooks = _install_hooks(r.local_path)
        console.print(f"{r.name:<16} {action:<20} {r.local_path}  ·  {hooks}")
