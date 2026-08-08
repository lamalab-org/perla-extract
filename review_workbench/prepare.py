#!/usr/bin/env python3
"""Create a minimal Vercel deployment directory for the review workbench."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKBENCH_ROOT = REPO_ROOT / "review_workbench"
DEFAULT_OUTPUT = WORKBENCH_ROOT / ".vercel-build"


def copy_file(relative: str, output: Path, target_relative: str | None = None) -> None:
    source = REPO_ROOT / relative
    target = output / (target_relative or relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def prepare(output: Path) -> Path:
    output = output.resolve()
    git_dir = (REPO_ROOT / ".git").resolve()
    if output == REPO_ROOT or output == git_dir or git_dir in output.parents:
        raise ValueError("Refusing unsafe deployment output directory")
    vercel_dir = output / ".vercel"
    saved_vercel_files = {
        path.relative_to(vercel_dir): path.read_bytes()
        for path in vercel_dir.rglob("*")
        if path.is_file()
    } if vercel_dir.exists() else {}
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    for relative, contents in saved_vercel_files.items():
        target = vercel_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents)

    for relative in (
        "review_workbench/__init__.py",
        "review_workbench/auth.py",
        "review_workbench/review_collaboration.py",
        "review_workbench/review_evidence.py",
        "review_workbench/server.py",
        "src/perla_extract/__init__.py",
        "src/perla_extract/ground_truth.py",
        "src/perla_extract/pydantic_model_reduced.py",
    ):
        copy_file(relative, output)

    copy_file("review_workbench/api/index.py", output, "api/index.py")
    shutil.copytree(
        WORKBENCH_ROOT / "review_app",
        output / "review_workbench" / "review_app",
    )
    shutil.copytree(
        REPO_ROOT / "src" / "perla_extract" / "data" / "ground_truth",
        output / "src" / "perla_extract" / "data" / "ground_truth",
    )
    shutil.copy2(WORKBENCH_ROOT / "pyproject.toml", output / "pyproject.toml")
    shutil.copy2(WORKBENCH_ROOT / "vercel.json", output / "vercel.json")
    return output


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(prepare(args.output))


if __name__ == "__main__":
    main()
