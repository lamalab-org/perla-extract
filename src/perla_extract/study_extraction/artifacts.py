"""Write inspectable extraction artifacts without exposing partial files."""

from __future__ import annotations

import json
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
    """Replace a JSON artifact only after its complete contents reach disk.

    A writer-specific temporary file avoids collisions when two extraction jobs
    share a cache directory. Closing it before replacement also keeps this usable
    on platforms that do not permit replacing an open file.
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
