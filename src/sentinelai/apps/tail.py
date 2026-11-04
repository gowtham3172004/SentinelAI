"""Incremental reading of growing log files with rotation handling.

The cursor ({"offset", "inode", "head", "head_len"}) is stored with each batch, in the same
transaction as the counts it produced, so a restart resumes exactly where the last committed
batch ended: nothing lost, nothing counted twice. A partial last line is left for the next
read. Rotation is detected by a changed inode, a file shorter than the offset, or a changed
already-consumed prefix (copy-truncate rotation keeps the inode).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HEAD_BYTES = 256


@dataclass(frozen=True)
class Chunk:
    lines: list[str]
    cursor: dict[str, Any]
    rotated: bool


def _prefix_hash(path: Path, length: int) -> str:
    with path.open("rb") as handle:
        return hashlib.sha256(handle.read(length)).hexdigest()[:16]


def read_new_lines(path: Path, cursor: dict[str, Any], *, max_lines: int = 1000) -> Chunk:
    stat = os.stat(path)
    offset = int(cursor.get("offset", 0))
    head_len = int(cursor.get("head_len", 0))
    rotated = bool(cursor) and (
        cursor.get("inode") != stat.st_ino
        or stat.st_size < offset
        or (head_len > 0 and _prefix_hash(path, head_len) != cursor.get("head"))
    )
    if rotated:
        offset = 0
    lines: list[str] = []
    with path.open("rb") as handle:
        handle.seek(offset)
        while len(lines) < max_lines:
            line = handle.readline()
            if not line or not line.endswith(b"\n"):
                break  # end of file, or a partial line still being written
            offset += len(line)
            lines.append(line.decode("utf-8", "replace"))
    head_len = min(HEAD_BYTES, offset)  # fingerprint only bytes already consumed (immutable)
    new_cursor = {"offset": offset, "inode": stat.st_ino, "head_len": head_len,
                  "head": _prefix_hash(path, head_len)}  # fmt: skip
    return Chunk(lines, new_cursor, rotated)
