"""`bench`: the one command of the workspace."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table as RichTable

from bench_ops.archive import archive as archive_release
from bench_ops.checkout import CheckoutError, checkout_problems, release_tree, scratch_tree
from bench_ops.config import Config, load_config
from bench_ops.fetch import LOCK as LOCK_NAME
from bench_ops.fetch import FetchError, check_tree
from bench_ops.fetch import bump as bump_pin
from bench_ops.fetch import fetch as fetch_tree
from bench_ops.gc import (
    VerifiedManifest,
    fetch_verified,
    safe_unlink,
    tracked_in,
    verdicts,
)
from bench_ops.gitx import clean_env, clone_or_pull, git, origin_url
from bench_ops.hf import HfHub, Witness, sha256_of, verify_manifest
from bench_ops.hooks import ledger_problems, secrets_command, size_guard
from bench_ops.models import LedgerEntry, Lock, Manifest, dumps, manifest_path
from bench_ops.pipeline import (
    PipelineError,
    build_plan,
    execute_plan,
    log_path,
    preflight,
    word_probe,
)
from bench_ops.publish import PublishError, push_release, run_script, validate_tag
from bench_ops.publish import publish as publish_release
from bench_ops.status import latest_tags, report
from bench_ops.work import parse_duration, remove, scan

app = typer.Typer(no_args_is_help=True, add_completion=False)
hook_app = typer.Typer(no_args_is_help=True, help="Hooks run by lefthook (see lefthook/common.yml).")
app.add_typer(hook_app, name="hook")
console = Console()


def _config() -> Config:
    try:
        return load_config()
    except FileNotFoundError as e:
        console.print(
            f"[red]no repos.toml at {e.filename}; set BENCH_ROOT or clone bench-ops to ~/T/bench[/]"
        )
        raise typer.Exit(2) from None
    except ValidationError as e:
        console.print(f"[red]invalid repos.toml: {e}[/]", markup=False, highlight=False)
        raise typer.Exit(2) from None


def _word_probe(command: str, timeout_seconds: int) -> int:
    """The Word preflight probe; tests replace this function with an offline fake."""
    return word_probe(command, timeout_seconds)


INACTIVE = "HOOKS NOT ACTIVE"


def _install_hooks(repo: Path) -> str:
    if os.environ.get("BENCH_NO_LEFTHOOK") == "1" or not (repo / "lefthook.yml").exists():
        return "no lefthook.yml"
    if shutil.which("lefthook") is None:
        return "lefthook missing (brew install lefthook)"
    subprocess.run(["lefthook", "install"], cwd=repo, check=True, capture_output=True)
    # lefthook "continues anyway" when it can't fetch a remote config and has no cached copy,
    # leaving every shared job out. Check the merged config really has them.
    dump = subprocess.run(["lefthook", "dump"], cwd=repo, check=False, capture_output=True,
                          text=True).stdout
    if "size guard" not in dump:
        return (f"{INACTIVE}: this checkout's lefthook.yml pulls no shared jobs (branch without "
                "the bench-ops remote, or the remote could not be fetched)")
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
    inactive = False
    for r in repos:
        action = clone_or_pull(r.url, r.local_path, r.branch)
        hooks = _install_hooks(r.local_path)
        inactive |= hooks.startswith(INACTIVE)
        style = "red" if hooks.startswith(INACTIVE) else "default"
        console.print(f"{r.name:<16} {action:<20} {r.local_path}  ·  [{style}]{hooks}[/]")
    if inactive:
        raise typer.Exit(1)


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
        Path | None, typer.Option(help="Only the repo at this path (the pre-commit hook).")
    ] = None,
) -> None:
    """Branch, dirt, worktrees and release state of every linked repo."""
    cfg = _config()
    repos = cfg.repos
    if repo is not None:
        found = cfg.for_checkout(repo, origin_url(repo))
        if found is None:
            console.print(f"[yellow]{repo} is not a linked repo; nothing to check[/]")
            raise typer.Exit(0)
        repos = [found]
    latest = latest_tags(cfg.repos)
    witness = None if offline else _witness_via_hf
    pen = cfg.root / ".work"  # L5: the pen bench work drains is not stray debris
    table = RichTable(title="bench status")
    for col in ("repo", "role", "branch", "dirty", "stray worktrees", "release", "witness"):
        table.add_column(col, overflow="fold")
    stale = False
    broken = False
    for r in repos:
        rep = report(r, latest, witness, pen=pen)
        stale |= rep.stale
        broken |= rep.broken
        cell = f"BROKEN: {rep.witness}" if rep.broken else (rep.witness or "-")
        table.add_row(rep.name, rep.role, rep.branch, str(rep.dirty),
                      "\n".join(rep.stray_worktrees) or "-", rep.release, cell)
    console.print(table)
    if fail_on_stale:
        failed = False  # both messages, then one exit (N7): broken names the record,
        # stale names the consumer — neither may hide the other
        if broken:
            console.print(
                "[red]BROKEN: a producer's release is not intact on Hugging Face; "
                "repair the record before relying on it or deleting anything (gc refuses)[/]"
            )
            failed = True
        if stale:
            if os.environ.get("BENCH_ALLOW_STALE") == "1":
                console.print("[yellow]consumer lock is behind; allowed by BENCH_ALLOW_STALE=1[/]")
            else:
                console.print(
                    "[red]a consumer lock is behind its producer; bump bench.lock "
                    "or commit with BENCH_ALLOW_STALE=1[/]"
                )
                failed = True
        if failed:
            raise typer.Exit(1)


def _size_at_head(rel: str) -> int | None:
    """Size of the file in HEAD, or None when it is new (or there is no HEAD yet)."""
    try:
        size = int(git(Path.cwd(), "cat-file", "-s", f"HEAD:{rel}").strip())
        if size < 1024:  # possibly a Git LFS pointer: its "size" line is the real size
            blob = git(Path.cwd(), "cat-file", "-p", f"HEAD:{rel}")
            if blob.startswith("version https://git-lfs.github.com/spec/"):
                for line in blob.splitlines():
                    if line.startswith("size "):
                        return int(line.split()[1])
        return size
    except (subprocess.CalledProcessError, ValueError):
        return None


@app.command("publish")
def publish_cmd(
    producer: Annotated[str, typer.Argument(help="Producer name in repos.toml.")],
    tag: Annotated[str, typer.Argument(help="Release tag, e.g. ndb-v0.0.1.")],
    dry_run: Annotated[
        bool, typer.Option(help="Local checks and staging only: no hub calls, no writes.")
    ] = False,
    version: Annotated[
        str | None,
        typer.Option(help="X.Y.Z filled into {version} of the producer's before commands "
                          "(ndb: the jubarte release whose views this publishes)."),
    ] = None,
    push: Annotated[
        bool, typer.Option(help="Push release/<tag> and the tag, then open the PR.")
    ] = False,
) -> None:
    """Upload a producer's release to Hugging Face, verify it, then record it (plan 2).

    Works in a fresh worktree of origin/<branch> at <BENCH_ROOT>/.work/release-<tag>, on
    branch release/<tag> (plan 8): the configured checkout's branch and dirt never reach a
    release. Runs the producer's own `before` scripts (its [repos.release], rev 3) there
    first — pages current, stores uploaded — stopping at the first failure or at any tracked
    change they leave. Resumable and idempotent (C3): rerunning reuses the release worktree,
    adopts what is already on the Hub and creates only the missing records; every step
    prints `did` or `done`. With --push the branch and tag are pushed and the PR opened;
    without it the push commands are printed. --dry-run stages from the configured checkout
    and writes nothing.
    """
    cfg = _config()
    try:
        repo = cfg.by_name(producer)
    except KeyError:
        known = ", ".join(r.name for r in cfg.repos)
        console.print(f"[red]unknown repo {producer!r}; known: {known}[/]")
        raise typer.Exit(1) from None
    try:
        validate_tag(tag)  # before any worktree or fetch: a bad tag names nothing to make
        if not dry_run:
            tree = release_tree(repo, tag)
            console.print(f"release tree: {tree} (release/{tag} from origin/{repo.branch})")
            repo = repo.model_copy(update={"path": tree})
        m = publish_release(repo, tag, HfHub(), now=datetime.now(UTC), dry_run=dry_run,
                            version=version)
        console.print(dumps(m), markup=False, highlight=False)
        if dry_run:
            return
        if push:
            push_release(repo.local_path, tag, cfg.by_name(producer).branch)
        else:
            console.print(
                push_hint(repo.local_path, _current_branch(repo.local_path), repo.branch, tag)
            )
    except (PublishError, CheckoutError) as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from None
    except Exception as e:  # noqa: BLE001 -- a failed publish is a message, never a traceback (M6)
        console.print(f"[red]{type(e).__name__}: {e}[/]", markup=False, highlight=False)
        raise typer.Exit(1) from None


def _current_branch(path: Path) -> str:
    try:
        return git(path, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except subprocess.CalledProcessError:
        return "HEAD"


def push_hint(path: Path, current: str, default: str, tag: str) -> str:
    """The push command for the ledger commit and the tag (#11): the branch actually checked out."""
    if current == "HEAD":
        return (
            f"HEAD is detached in {path}: put the ledger commit on a branch, then push it and "
            f"`git -C {path} push origin {tag}`"
        )
    hint = f"push with: git -C {path} push origin HEAD:{current} && git -C {path} push origin {tag}"
    if current != default:
        hint += f"\nthen open a PR: (cd {path} && gh pr create --base {default} --head {current})"
    return hint


@app.command(
    "exec",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def exec_cmd(
    ctx: typer.Context,
    repo_name: Annotated[str, typer.Argument(help="Repo name in repos.toml.")],
    main: Annotated[
        bool,
        typer.Option("--main", help="Run in a fresh detached worktree of origin/<branch> "
                                    "(removed afterwards unless the command left files)."),
    ] = False,
) -> None:
    """Run a command in a repo's checkout, from anywhere (plan 8).

    `bench exec ndb -- uv run bench report --check` is `uv run bench report --check` in
    ndb's configured checkout; with --main it runs on what origin's branch holds now,
    whatever that checkout is on. The exit code is the command's.
    """
    cfg = _config()
    try:
        repo = cfg.by_name(repo_name)
    except KeyError:
        known = ", ".join(r.name for r in cfg.repos)
        console.print(f"[red]unknown repo {repo_name!r}; known: {known}[/]")
        raise typer.Exit(1) from None
    args = list(ctx.args)
    if not args:
        console.print("[red]no command; e.g. bench exec ndb -- uv run bench report --check[/]")
        raise typer.Exit(2)

    def run_in(cwd: Path) -> int:
        env = {k: v for k, v in clean_env().items() if k != "VIRTUAL_ENV"}
        return subprocess.run(args, cwd=str(cwd), env=env, check=False).returncode

    try:
        if main:
            with scratch_tree(repo) as tree:
                code = run_in(tree)
        else:
            code = run_in(repo.local_path)
    except CheckoutError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from None
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(127) from None
    raise typer.Exit(code)


@app.command("bump")
def bump_cmd(
    producer: Annotated[str, typer.Argument(help="Producer name in repos.toml.")],
    repo: Annotated[
        Path | None, typer.Option(help="The consumer checkout whose bench.lock is written (default: cwd).")
    ] = None,
) -> None:
    """Pin the producer's latest release in bench.lock: <producer>=<tag> manifest=<sha256>.

    Reads the producer's last release/index.jsonl line (from its checkout in repos.toml),
    creates bench.lock when missing, keeps every other producer's line, and prints
    old -> new. The manifest sha pins content: a moved HF tag cannot change what the
    consumer renders.
    """
    cfg = _config()
    try:
        name, old, new = bump_pin(producer, cfg, repo or Path.cwd())
    except FetchError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from None
    console.print(f"{name}: {old} -> {new}")


@app.command("fetch")
def fetch_cmd(
    repo: Annotated[
        Path | None, typer.Option(help="The consumer checkout to fetch into (default: cwd).")
    ] = None,
    check: Annotated[
        bool, typer.Option(help="Offline: verify .bench/ against bench.lock; download nothing.")
    ] = False,
) -> None:
    """Materialise every bench.lock pin under <repo>/.bench/<producer>/ (plan 4).

    Per lock line: the manifest is downloaded from the producer's HF dataset at the tag
    and refused unless it hashes to the lock's manifest= sha; then every file it lists is
    downloaded at the manifest's own pinned revision and verified. Files already present
    with the right hash are skipped, and the tree swap is atomic per producer (scratch
    directory, rename). Private datasets use the logged-in huggingface_hub token.
    --check is offline (the pre-commit hook and `bench deploy` use it): manifest.json
    must hash to the pin and every listed file to its entry; the first problem is named
    and the exit code is 1.
    """
    cfg = _config()
    root = repo or Path.cwd()
    if check:
        problems = check_tree(root)
        if problems:
            console.print(f"[red]{problems[0]}[/]")
            raise typer.Exit(1) from None
        console.print(f"ok: .bench/ matches {root / LOCK_NAME}")
        return
    try:
        fetch_tree(root, cfg, HfHub())
    except FetchError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from None
    except Exception as e:  # noqa: BLE001 -- a failed fetch is a message, never a traceback
        console.print(f"[red]{type(e).__name__}: {e}[/]", markup=False, highlight=False)
        raise typer.Exit(1) from None


@app.command("deploy")
def deploy_cmd(
    repo_name: Annotated[
        str, typer.Argument(help="Consumer name in repos.toml, or a configured pipeline name.")
    ],
    version: Annotated[
        str | None, typer.Argument(help="Pipeline version, e.g. 0.18.0 (pipeline only).")
    ] = None,
    yes: Annotated[
        bool, typer.Option(help="Actually run the pipeline; without it bench only prints it.")
    ] = False,
    deploy: Annotated[
        bool, typer.Option(help="Also run the pipeline's gated deploy step; --no-deploy is "
                              "the explicit default.")
    ] = False,
    from_step: Annotated[
        int, typer.Option("--from", min=1, help="Resume at this one-based pipeline step.")
    ] = 1,
    tag: Annotated[
        str | None, typer.Option(help="ndb-vX.Y.Z release tag override.")
    ] = None,
) -> None:
    """Run a consumer deploy or a configured release pipeline.

    `bench deploy <consumer>` keeps the plan-2 behavior: prove the fetched tree, resolve
    {version} from bench.lock, and run the consumer's deploy command only with --yes.
    `bench deploy <pipeline> <version>` prints the configured plan by default; --yes runs
    steps 1-5 and --deploy additionally runs the gated outward-facing deploy.
    """
    cfg = _config()
    repo_names = {repo.name for repo in cfg.repos}
    pipeline_names = set(cfg.pipelines)

    if repo_name in pipeline_names:
        if repo_name in repo_names:  # config validation normally refuses this first
            console.print(
                f"[red]{repo_name!r} names both a pipeline and a repo; "
                "pipeline and repo names must be distinct[/]"
            )
            raise typer.Exit(2) from None
        if version is None:
            console.print(
                f"[red]pipeline {repo_name!r} requires a version; "
                f"use `bench deploy {repo_name} <version>`[/]"
            )
            raise typer.Exit(2) from None
        _deploy_pipeline(
            cfg,
            repo_name,
            version,
            yes=yes,
            deploy=deploy,
            from_step=from_step,
            tag=tag,
        )
        return

    if repo_name not in repo_names:
        known_repos = ", ".join(sorted(repo_names))
        known_pipelines = ", ".join(sorted(pipeline_names)) or "(none)"
        console.print(
            f"[red]unknown repo or pipeline {repo_name!r}; it is neither a configured repo "
            f"nor a pipeline; repos: {known_repos}; pipelines: {known_pipelines}[/]"
        )
        raise typer.Exit(1) from None
    if version is not None:
        console.print(
            f"[red]{repo_name!r} is a consumer, not a pipeline; a version belongs only to "
            "`bench deploy <pipeline> <version>`[/]"
        )
        raise typer.Exit(2) from None
    if deploy or from_step != 1 or tag is not None:
        console.print(
            f"[red]--deploy, --from, and --tag belong to a pipeline, "
            f"not the consumer deploy {repo_name!r}[/]"
        )
        raise typer.Exit(2) from None

    try:
        repo = cfg.by_name(repo_name)
    except KeyError:
        known = ", ".join(r.name for r in cfg.repos)
        console.print(f"[red]unknown repo {repo_name!r}; known: {known}[/]")
        raise typer.Exit(1) from None
    if repo.role != "consumer":
        console.print(
            f"[red]{repo.name} is a {repo.role}; bench deploy runs a consumer's site, "
            "a producer releases with bench publish[/]"
        )
        raise typer.Exit(1) from None

    try:
        problems = checkout_problems(repo.local_path, repo.branch)  # plan 8
    except CheckoutError as e:
        problems = [str(e)]
    if problems:
        for problem in problems:
            console.print(f"[red]{problem}[/]")
        console.print(
            f"[red]{repo.name} deploys only from a clean {repo.branch} up to date with origin; "
            "commit, merge and `bench sync` first[/]"
        )
        raise typer.Exit(1) from None

    lock_path = repo.local_path / "bench.lock"
    if not lock_path.exists():
        console.print(
            f"[red]no bench.lock at {lock_path}; release against a producer first[/]"
        )
        raise typer.Exit(1) from None
    problems = check_tree(repo.local_path)  # plan 4: prove .bench/ against the lock first
    if problems:
        console.print(f"[red]fetch check failed: {problems[0]}[/]")
        console.print(f"[red]run `bench fetch --repo {repo.local_path}` before deploying[/]")
        raise typer.Exit(1) from None
    console.print("fetch check: ok — .bench/ matches bench.lock")
    if repo.deploy is None:
        console.print(f"[red]{repo.name} has no deploy command in repos.toml[/]")
        raise typer.Exit(1) from None

    command = repo.deploy
    if "{version}" in command:
        lock = Lock.parse(lock_path.read_text())  # check_tree above already proved it parses
        if not lock.tags:
            console.print(
                f"[red]{lock_path} pins no <producer>=<tag>; cannot substitute {{version}}[/]"
            )
            raise typer.Exit(1) from None
        producer = next(iter(lock.tags))  # the lock's first producer, in file order
        command = command.replace("{version}", lock.tags[producer])

    if not yes:
        console.print("[yellow]dry (no --yes): printing the deploy command, not running it[/]")
        console.print(command, markup=False, highlight=False)
        return
    console.print(f"deploying {repo.name}:")
    console.print(command, markup=False, highlight=False)
    code = run_script(command, repo.local_path)
    if code != 0:
        console.print(f"[red]deploy exited {code}[/]")
        raise typer.Exit(code)


def _deploy_pipeline(
    cfg: Config,
    pipeline_name: str,
    version: str,
    *,
    yes: bool,
    deploy: bool,
    from_step: int,
    tag: str | None,
) -> None:
    """Print and, only with --yes, run one configured release pipeline."""
    try:
        plan = build_plan(cfg, pipeline_name, version, tag, from_step=from_step)
    except PipelineError as e:
        console.print(f"[red]{e}[/]", markup=False, highlight=False)
        raise typer.Exit(1) from None
    if from_step > len(plan.steps):
        console.print(
            f"[red]--from {from_step} has no step; the pipeline has {len(plan.steps)}[/]"
        )
        raise typer.Exit(2) from None
    if yes and from_step == plan.deploy_step and not deploy:
        console.print(
            f"[red]step {from_step} is the deploy step and needs --deploy[/]"
        )
        raise typer.Exit(2) from None

    console.print(
        f"bench deploy {pipeline_name} {version} plan (ndb tag {plan.tag})",
        markup=False,
        highlight=False,
    )
    for step in plan.steps:
        if step.number < from_step:
            status = "skip (before --from)"
        elif step.number == plan.deploy_step and not deploy:
            status = "requires --deploy"
        elif not yes:
            status = "dry"
        else:
            status = "run"
        console.print(
            f"{step.number}. {step.name} [{step.repo}] {step.repo_path} — {status}",
            soft_wrap=True,
        )
        console.print(f"   {step.command}", markup=False, highlight=False)

    if not yes:
        console.print("[yellow]dry (no --yes): printing the plan, not running it[/]")
        return

    spec = cfg.pipelines[pipeline_name]
    try:
        preflight(cfg, spec, _word_probe)
    except PipelineError as e:
        console.print(f"[red]preflight refused: {e}[/]", markup=False, highlight=False)
        raise typer.Exit(1) from None

    log = log_path(cfg.root, pipeline_name, version, datetime.now(UTC))
    console.print(f"log: {log}")
    result = execute_plan(plan, run_script, log, from_step=from_step, deploy=deploy)
    if result.exit_code != 0:
        failed = result.failed_step
        where = f"step {failed.number} ({failed.name})" if failed is not None else "pipeline step"
        console.print(f"[red]{where} exited {result.exit_code}[/]")
        console.print(f"log: {log}", markup=False, highlight=False)
        raise typer.Exit(result.exit_code)
    console.print(f"pipeline complete: {log}", markup=False, highlight=False)


def _refuse_wild_gc_targets(targets: list[Path], linked: list[Path], bench_root: Path) -> None:
    """L4: gc's reach must stay bounded. A target that is the filesystem root, $HOME,
    BENCH_ROOT itself, or a parent of a linked repo makes every verified byte on the
    machine a candidate — refuse it before anything is hashed or deleted."""
    home = Path.home().resolve()
    root = bench_root.resolve()
    linked_resolved = [r.resolve() for r in linked]
    for t in targets:
        r = t.resolve()
        why: str | None = None
        if r == Path("/"):
            why = "the filesystem root"
        elif r == home:
            why = "$HOME"
        elif r == root:
            why = "BENCH_ROOT itself"
        else:
            why = next(
                (
                    f"a parent of the linked repo {lr}"
                    for lr in linked_resolved
                    if lr != r and lr.is_relative_to(r)
                ),
                None,
            )
        if why is not None:
            console.print(f"[red]refusing to gc {t}: it is {why}[/]")
            raise typer.Exit(1)


@app.command("gc")
def gc_cmd(
    paths: Annotated[list[Path], typer.Argument(help="Local files (or directories) to consider deleting.")],
    yes: Annotated[
        bool, typer.Option(help="Actually delete; without it gc only reports.")
    ] = False,
) -> None:
    """Delete local files whose bytes the Hub records in a verified manifest (plans 2 and 6).

    The Hub is the only authority: every ledger line — each producer's
    release/index.jsonl and BENCH_ROOT/archive/index.jsonl — is verified against it, a
    served file is never deleted, and nothing inside a git directory is touched, however
    that directory is named (its .git stays for hand removal once the verdict output has
    been read). A directory argument is expanded to its files; when the directory is not a
    linked repo, only files an archive of that very tree lists at the same path are
    candidates (M2: provenance, not bytes-that-match-anywhere).
    """
    cfg = _config()
    roots = [r.local_path for r in cfg.repos]  # candidates must live in a linked repo (M2)
    passed = [p if p.is_absolute() else Path.cwd() / p for p in paths]
    _refuse_wild_gc_targets(passed, roots, cfg.root)  # L4: before any hashing
    hub = HfHub()
    tracked = tracked_in(roots)  # and never be a file git tracks (M3)
    verified: list[VerifiedManifest] = []
    unverified: set[str] = set()  # fail closed: these shas are never deletable
    sources: list[tuple[Path, str]] = [
        *((r.local_path, "release/index.jsonl") for r in cfg.by_role("producer")),
        (cfg.root, "archive/index.jsonl"),  # plan 6 C: archive manifests are witnesses too
    ]
    for base, rel_ledger in sources:
        ledger = base / rel_ledger
        if not ledger.exists():
            continue
        lines = [ln for ln in ledger.read_text().splitlines() if ln.strip()]
        for i, line in enumerate(lines, 1):
            try:
                entry = LedgerEntry.from_line(line)
            except ValidationError:
                console.print(f"[red]{ledger}: line {i} is not a ledger entry[/]")
                raise typer.Exit(1) from None
            try:
                vm = fetch_verified(entry, hub)
            except Exception as e:  # noqa: BLE001 -- M7: one red line, never a traceback
                console.print(
                    f"witness failed for {entry.tag}: {type(e).__name__}: {e}",
                    style="red", markup=False, highlight=False,
                )
                raise typer.Exit(1) from None
            if vm is not None:
                verified.append(vm)
            else:
                local = base / manifest_path(entry.tag)
                if local.exists():  # untrusted bytes, but its shas must block deletes
                    try:
                        unverified.update(
                            f.sha256
                            for f in Manifest.model_validate_json(local.read_text()).files
                        )
                    except ValidationError:
                        pass
    archives: dict[Path, list[VerifiedManifest]] = {}  # M2: archived root -> its manifests
    for vm in verified:
        if vm.manifest.archived_root:
            archives.setdefault(Path(vm.manifest.archived_root).resolve(), []).append(vm)
    targets: list[Path] = []
    for p in passed:
        # The directory stays a target (reported as skipped — gc deletes files, not dirs)
        # and its files are considered one by one: that is how an archived satellite is
        # collected, without making `bench gc out/` silently say nothing (N7).
        targets.append(p)
        if p.is_dir():
            targets += sorted(q for q in p.rglob("*") if q.is_file() or q.is_symlink())
    for v in verdicts(targets, verified, unverified, roots=roots, tracked=tracked,
                      archives=archives):
        if yes and v.deletable:
            if safe_unlink(v):  # L7: re-opened, fstat'd, compared with the pinned stat
                console.print(f"[red]deleted[/] {v.path}  ·  {v.reason}")
            else:  # changed (or swapped for a symlink) between hashing and this unlink (M1)
                console.print(f"[yellow]keep[/] {v.path}  ·  changed since hashing; not deleted")
        else:
            style = "green" if v.deletable else "yellow"
            word = "deletable" if v.deletable else "keep"
            console.print(f"[{style}]{word}[/] {v.path}  ·  {v.reason}")


@app.command("work")
def work_cmd(
    older_than: Annotated[
        str, typer.Option(help="Removal TTL, e.g. 14d or 6h (worktree creation AND last commit).")
    ] = "14d",
    yes: Annotated[
        bool, typer.Option(help="Actually remove; without it bench only lists.")
    ] = False,
    offline: Annotated[
        bool, typer.Option(help="Skip the origin fetch; never removes anything.")
    ] = False,
) -> None:
    """List the worktrees under BENCH_ROOT/.work and remove the stale ones (plan 6 B).

    A worktree leaves only when it is older than the TTL on both clocks (its creation and
    its last commit), holds nothing git-ignored or untracked, is not locked, and its HEAD
    is reachable from origin's default branch or its branch was pushed there — verified
    against a fresh `git fetch --prune origin`, never a stale remote-tracking ref (M5).
    With --yes it goes through `git worktree remove` (never --force). Everything else is
    listed with the reason it stays. --offline skips the fetch and then never removes.
    """
    cfg = _config()
    try:
        ttl = parse_duration(older_than)
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(2) from None
    now = datetime.now(UTC)
    infos = scan(cfg.root / ".work", cfg.repos, ttl=ttl, now=now, fetch=not offline)
    if not infos:
        console.print(f"no worktrees under {cfg.root / '.work'}")
        return
    if offline:
        console.print("[yellow]offline: origin refs were not fetched; nothing is removed[/]")
    for info in infos:
        age = f"{(now - info.last_commit).days}d" if info.last_commit is not None else "?"
        line = f"{info.path.name:<24} branch={info.branch:<18} age={age:<6} dirty={info.dirty}"
        if info.removable:
            if offline:  # M5: without the fetch no verdict justifies a removal
                console.print(f"[yellow]would remove[/] {line}  ·  --offline never removes")
            elif not yes:
                console.print(
                    f"[yellow]would remove[/] {line}  ·  old, clean, merged or pushed (needs --yes)"
                )
            else:
                try:
                    remove(info)
                except (subprocess.CalledProcessError, ValueError) as e:
                    console.print(f"[yellow]keep[/] {line}  ·  git worktree remove refused: {e}")
                else:
                    console.print(f"[red]removed[/] {line}")
        else:
            console.print(f"[yellow]keep[/] {line}  ·  {'; '.join(info.reasons)}")


@app.command("archive")
def archive_cmd(
    directory: Annotated[Path, typer.Argument(help="The satellite directory to snapshot.")],
    name: Annotated[str, typer.Argument(help="Archive name, [a-z0-9-]+ (the tag is arc-<name>).")],
    yes: Annotated[
        bool, typer.Option(help="Actually publish; without it bench prints the summary only.")
    ] = False,
    exclude: Annotated[
        list[str] | None, typer.Option("--exclude", help="Drop staged paths matching this "
        "glob (repeatable), e.g. --exclude .env; recorded in the manifest.")
    ] = None,
) -> None:
    """Snapshot a satellite directory to the private archive dataset (plan 6 C).

    The whole tree goes to arthrod/neurotic_docx_bench-archive under archive/<name>/ — the
    working tree (untracked and modified files included) *and* the .git directory, so
    history survives; symlinks are recorded in an archive/<name>/symlinks.json sidecar.
    The dataset must exist and be private, and the staged set must pass the secrets scan
    (use --exclude to drop paths). The HF tag is arc-<name>; one line lands in
    BENCH_ROOT/archive/index.jsonl and a copy of the manifest in BENCH_ROOT/archive/<name>/.
    `bench gc <dir>` may then delete local files once that manifest verifies on HF — and
    only files this very archive lists. Resumable: a run that died after the manifest
    commit is finished, never redone. Without --yes, only the summary is printed.
    """
    cfg = _config()
    try:
        m = archive_release(
            directory.resolve(), name, HfHub(), now=datetime.now(UTC), yes=yes,
            bench_root=cfg.root, excludes=tuple(exclude or ()),
        )
    except PublishError as e:
        console.print(str(e), style="red", markup=False, highlight=False)
        raise typer.Exit(1) from None
    except Exception as e:  # noqa: BLE001 -- a failed archive is a message, never a traceback
        console.print(f"[red]{type(e).__name__}: {e}[/]", markup=False, highlight=False)
        raise typer.Exit(1) from None
    if yes:
        console.print(
            f"commit the ledger: git -C {cfg.root} add archive/index.jsonl "
            f"archive/{m.tag[4:]}/ && "
            f"git -C {cfg.root} commit -m 'archive: {m.tag}'"
        )
    else:
        total = sum(f.bytes for f in m.files)
        console.print(
            f"{m.tag}: {len(m.files)} files, {total} bytes (dry run — nothing written; "
            "rerun with --yes)"
        )


StagedFiles = Annotated[list[Path] | None, typer.Argument(help="Staged files (from lefthook).")]


@hook_app.command("size-guard")
def hook_size_guard(files: StagedFiles = None) -> None:
    """Refuse staged files over 2 MB and binaries under data directories."""
    problems = size_guard(files or [], root=Path.cwd(), previous_size=_size_at_head)
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


@hook_app.command("ledger")
def hook_ledger(files: StagedFiles = None) -> None:
    """Refuse anything but an append to the ledgers (D7/D14, C1/C2, M4).

    Without files it runs on every commit (lefthook must not glob-gate it: staged deletions
    and renames are exactly what `git diff --cached --name-status -M` catches) and guards
    every ledger it knows about — release/index.jsonl in the producers and
    archive/index.jsonl in BENCH_ROOT (the bench-ops repo; elsewhere the path is simply
    absent and the check passes). With files (the glob-scoped producer job) only the ledgers
    among them are compared; the deletion and rename check still runs for all of them.

    The staged blob is read from the index git will commit — GIT_INDEX_FILE is kept, so for
    a path-limited commit (`git commit -- release/index.jsonl`) the bytes validated are the
    bytes committed — and compared with HEAD's.
    """
    problems = ledger_problems(Path.cwd(), files)
    for p in problems:
        console.print(f"[red]{p}[/]")
    if problems:
        console.print("[red]the ledgers are append-only (bench-ops docs/CONTRACT.md)[/]")
        raise typer.Exit(1)
