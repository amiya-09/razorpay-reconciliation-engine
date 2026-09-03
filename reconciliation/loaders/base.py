"""Shared file-reading and value-parsing helpers for the source loaders.

Both CSV and JSON are supported per source: the dataset generator (Milestone 2)
emits JSON (native types), but CSV is kept as an input format too since it's
the more realistic shape for bank/gateway exports and costs nothing extra here.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterator, Union


def read_rows(path: Union[str, Path]) -> Iterator[dict[str, Any]]:
    path = Path(path)
    if path.suffix == ".json":
        data = json.loads(path.read_text())
        yield from data
    elif path.suffix == ".csv":
        with path.open(newline="") as f:
            yield from csv.DictReader(f)
    else:
        raise ValueError(f"Unsupported file type: {path.suffix!r} (expected .csv or .json)")


_TRUE_STRINGS = {"true", "1", "yes"}
_FALSE_STRINGS = {"false", "0", "no"}


def parse_optional_bool(value: Any) -> "bool | None":
    """CSV gives us strings, JSON gives us native bools/None — normalize both."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in _TRUE_STRINGS:
        return True
    if normalized in _FALSE_STRINGS:
        return False
    raise ValueError(f"Cannot parse boolean from {value!r}")


def blank_to_none(value: Any) -> Any:
    """CSV empty cells come back as '' rather than missing keys; JSON gives None directly."""
    if value == "":
        return None
    return value
