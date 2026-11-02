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
from bench_ops.config import Config, load_config
from bench_ops.gc import VerifiedManifest, fetch_verified, tracked_in, verdicts
from bench_ops.gitx import clone_or_pull, git, origin_url
from bench_ops.hf import HfHub, Witness, sha256_of, verify_manifest
from bench_ops.hooks import secrets_command, size_guard
from bench_ops.ledger import check_ledger
from bench_ops.models import LedgerEntry, Lock, Manifest, dumps, manifest_path
from bench_ops.publish import PublishError, run_script
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
            f"[red]no repos.toml at {e.filename}; set BENCH_ROOT or clone bench-ops to ~/bench[/]"
        )
        raise typer.Exit(2) from None


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
    table = RichTable(title="bench status")
    for col in ("repo", "role", "branch", "dirty", "stray worktrees", "release", "witness"):
        table.add_column(col, overflow="fold")
    stale = False
    broken = False
    for r in repos:
        rep = report(r, latest, witness)
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
) -> None:
    """Upload a producer's release to Hugging Face, verify it, then record it (plan 2).

    Runs the producer's own `before` scripts (its [repos.release], rev 3) in its checkout
    first — pages current, stores uploaded — stopping at the first failure; nothing here
    repeats what they already do. Resumable and idempotent (C3): rerunning after a failure
    adopts what is already on the Hub and creates only the missing records; every step
    prints `did` or `done`. Never pushes git: the ledger commit and the tag stay local
    until the printed push command is run by hand.
    """
    cfg = _config()
    try:
        repo = cfg.by_name(producer)
    except KeyError:
        known = ", ".join(r.name for r in cfg.repos)
        console.print(f"[red]unknown repo {producer!r}; known: {known}[/]")
        raise typer.Exit(1) from None
    try:
        m = publish_release(repo, tag, HfHub(), now=datetime.now(UTC), dry_run=dry_run)
    except PublishError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(1) from None
    except Exception as e:  # noqa: BLE001 -- a failed publish is a message, never a traceback (M6)
        console.print(f"[red]{type(e).__name__}: {e}[/]", markup=False, highlight=False)
        raise typer.Exit(1) from None
    console.print(dumps(m), markup=False, highlight=False)
    if not dry_run:
        console.print(
            f"push with: git -C {repo.local_path} push origin {repo.branch} && "
            f"git -C {repo.local_path} push origin {tag}"
        )


@app.command("deploy")
def deploy_cmd(
    repo_name: Annotated[str, typer.Argument(help="Consumer name in repos.toml.")],
    yes: Annotated[
        bool, typer.Option(help="Actually run the deploy; without it bench only prints it.")
    ] = False,
) -> None:
    """Run a consumer's deploy command with the version its bench.lock pins (plan 2 rev 3).

    Consumer only: producers release with `bench publish`. Checks the lock first (`bench fetch
    --check` verification is pending Plan 4), then substitutes {version} from the lock's tag of
    its first producer. Deploys are outward-facing: without --yes the command is printed, not
    run.
    """
    cfg = _config()
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

    lock_path = repo.local_path / "bench.lock"
    if not lock_path.exists():
        console.print(
            f"[red]no bench.lock at {lock_path}; release against a producer first[/]"
        )
        raise typer.Exit(1) from None
    console.print(
        "[yellow]fetch verification is pending Plan 4; only checked that bench.lock exists[/]"
    )
    if repo.deploy is None:
        console.print(f"[red]{repo.name} has no deploy command in repos.toml[/]")
        raise typer.Exit(1) from None

    command = repo.deploy
    if "{version}" in command:
        lock = Lock.parse(lock_path.read_text())
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
    served file is never deleted, and nothing under .git/ is touched. A directory argument
    is expanded to its files (an archived satellite's working tree); its .git directory
    stays, to be removed by hand once the verdict output has been read.
    """
    cfg = _config()
    hub = HfHub()
    roots = [r.local_path for r in cfg.repos]  # candidates must live in a linked repo (M2)
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
            vm = fetch_verified(entry, hub)
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
    passed = [p if p.is_absolute() else Path.cwd() / p for p in paths]
    targets: list[Path] = []
    for p in passed:
        # The directory stays a target (reported as skipped — gc deletes files, not dirs)
        # and its files are considered one by one: that is how an archived satellite is
        # collected, without making `bench gc out/` silently say nothing (N7).
        targets.append(p)
        if p.is_dir():
            targets += sorted(q for q in p.rglob("*") if q.is_file() or q.is_symlink())
    linked = [r.resolve() for r in roots]
    extra_roots: list[Path] = []
    for p in passed:  # an explicitly passed directory (an archived satellite) is its own root;
        anchor = p.resolve() if p.is_dir() else p.resolve().parent  # the two-witness rule
        if not any(anchor.is_relative_to(r) for r in linked):  # still gates every delete
            extra_roots.append(anchor)
    for v in verdicts(targets, verified, unverified, roots=roots + extra_roots, tracked=tracked):
        if yes and v.deletable:
            if not v.current():  # changed between hashing and this unlink (M1)
                console.print(f"[yellow]keep[/] {v.path}  ·  changed since hashing; not deleted")
                continue
            v.path.unlink()
            console.print(f"[red]deleted[/] {v.path}  ·  {v.reason}")
        else:
            style = "green" if v.deletable else "yellow"
            word = "deletable" if v.deletable else "keep"
            console.print(f"[{style}]{word}[/] {v.path}  ·  {v.reason}")


@app.command("work")
def work_cmd(
    older_than: Annotated[
        str, typer.Option(help="Removal TTL, e.g. 14d or 6h (age of the last commit).")
    ] = "14d",
    yes: Annotated[
        bool, typer.Option(help="Actually remove; without it bench only lists.")
    ] = False,
) -> None:
    """List the worktrees under BENCH_ROOT/.work and remove the stale ones (plan 6 B).

    A worktree leaves only when it is older than the TTL, clean, and merged into origin's
    default branch or pushed there — and only with --yes, through `git worktree remove`
    (never --force). Everything else is listed with the reason it stays. Origin refs are
    read as the checkout sees them: `bench sync` refreshes them.
    """
    cfg = _config()
    try:
        ttl = parse_duration(older_than)
    except ValueError as e:
        console.print(f"[red]{e}[/]")
        raise typer.Exit(2) from None
    now = datetime.now(UTC)
    infos = scan(cfg.root / ".work", cfg.repos, ttl=ttl, now=now)
    if not infos:
        console.print(f"no worktrees under {cfg.root / '.work'}")
        return
    for info in infos:
        age = f"{(now - info.last_commit).days}d" if info.last_commit is not None else "?"
        line = f"{info.path.name:<24} branch={info.branch:<18} age={age:<6} dirty={info.dirty}"
        if info.removable:
            if not yes:
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
) -> None:
    """Snapshot a satellite directory to the private archive dataset (plan 6 C).

    The whole tree goes to arthrod/neurotic_docx_bench-archive under archive/<name>/ — the
    working tree (untracked and modified files included) *and* the .git directory, so
    history survives. The HF tag is arc-<name> and one line lands in
    BENCH_ROOT/archive/index.jsonl; `bench gc <dir>` may delete local files once that
    manifest verifies on HF. Without --yes, only the summary is printed.
    """
    cfg = _config()
    try:
        m = archive_release(
            directory.resolve(), name, HfHub(), now=datetime.now(UTC), yes=yes,
            bench_root=cfg.root,
        )
    except PublishError as e:
        console.print(str(e), style="red", markup=False, highlight=False)
        raise typer.Exit(1) from None
    except Exception as e:  # noqa: BLE001 -- a failed archive is a message, never a traceback
        console.print(f"[red]{type(e).__name__}: {e}[/]", markup=False, highlight=False)
        raise typer.Exit(1) from None
    if yes:
        console.print(
            f"commit the ledger: git -C {cfg.root} add archive/index.jsonl && "
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


LEDGER = "release/index.jsonl"


def _git_text(repo: Path, *args: str, hook: bool = False) -> str | None:
    try:
        return git(repo, *args, hook=hook)
    except subprocess.CalledProcessError:
        return None


@hook_app.command("ledger")
def hook_ledger() -> None:
    """Refuse anything but an append to release/index.jsonl (D7/D14, C1/C2).

    Runs on every commit (lefthook must not glob-gate it: staged deletions and renames are
    exactly what `git diff --cached --name-status -M` catches). The staged blob is read
    from the index git will commit — GIT_INDEX_FILE is kept, so for a path-limited commit
    (`git commit -- release/index.jsonl`) the bytes validated are the bytes committed —
    and compared with HEAD's.
    """
    cwd = Path.cwd()
    staged = _git_text(cwd, "show", f":{LEDGER}", hook=True)
    if staged is None:  # not in the index: a deletion/rename, or nothing staged
        diff = _git_text(cwd, "diff", "--cached", "--name-status", "-M", "--", LEDGER,
                         hook=True) or ""
        if any(ln.split("\t")[0].startswith(("D", "R")) for ln in diff.splitlines()):
            console.print(f"[red]{LEDGER} may only grow: staged deletions and renames are refused[/]")
            raise typer.Exit(1)
        return
    problems = check_ledger(_git_text(cwd, "show", f"HEAD:{LEDGER}", hook=True), staged)
    for p in problems:
        console.print(f"[red]{p}[/]")
    if problems:
        console.print(f"[red]{LEDGER} is append-only (bench-ops docs/CONTRACT.md)[/]")
        raise typer.Exit(1)
