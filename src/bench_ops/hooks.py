"""Pre-commit checks every linked repo runs through lefthook. Each must finish in seconds."""

from __future__ import annotations

import shutil
from pathlib import Path

LIMIT = 2 * 1024 * 1024
# Directories whose binaries are data, not code: they live on Hugging Face (record) and R2 (cache).
BLOCKED_DIRS = ("results", "out", "public/fixtures", "public/neurotic-docx-bench", ".bench")
BLOCKED_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".webp", ".pdf", ".docx", ".zip", ".tar", ".gz", ".parquet", ".bin",
)


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


def size_guard(files: list[Path], root: Path, limit: int = LIMIT) -> list[str]:
    """Problems with the staged files; paths are relative to the repo root, as lefthook passes them."""
    problems: list[str] = []
    for f in files:
        path = f if f.is_absolute() else root / f
        if not path.is_file():  # deleted or renamed away
            continue
        rel = path.resolve().relative_to(root.resolve()).as_posix()
        size = path.stat().st_size
        if size > limit:
            problems.append(f"{rel}: {_mb(size)} over the {_mb(limit)} limit")
            continue
        top = next((d for d in BLOCKED_DIRS if rel.startswith(d + "/")), None)
        if top and path.suffix.lower() in BLOCKED_SUFFIXES:
            problems.append(f"{rel}: binary under {top}/ belongs on Hugging Face")
    return problems


def secrets_command(files: list[Path]) -> list[str] | None:
    """The ripsecrets argv, or None when the tool is not on PATH."""
    exe = shutil.which("ripsecrets")
    return None if exe is None else [exe, "--strict-ignore", *map(str, files)]
