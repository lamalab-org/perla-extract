"""Write inspectable extraction artifacts without exposing partial files."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path


def _replace_after_contention(source: Path, target: Path) -> None:
    """Publish a completed file despite brief same-target contention on Windows."""

    for attempt in range(20):
        try:
            source.replace(target)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.01)


def write_json_atomic(path: Path, value: object) -> None:
    """Replace a JSON artifact only after its complete contents are written and closed.

    A writer-specific temporary file avoids collisions when two extraction jobs
    share a cache directory. Closing it before replacement also keeps this usable
    on platforms that do not permit replacing an open file. This prevents partial
    readers; it does not claim power-loss durability because no filesystem sync is
    requested for these reproducible artifacts.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        _replace_after_contention(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_json_exclusive(path: Path, value: object) -> None:
    """Publish a complete JSON artifact only when its final path is still absent.

    Immutable revision files use path creation as their compare-and-swap operation.
    Building the bytes in a sibling temporary file and then hard-linking it avoids
    both partial reads and an overwrite race between concurrent reviewers.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
