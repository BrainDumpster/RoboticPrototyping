"""Write one CSV row per reading, flushed straight away so a crash never loses the log."""

import csv
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from types import TracebackType

from .config import REPO_ROOT

LOG_DIR = REPO_ROOT / "logs"


class CsvLog:
    def __init__(self, prefix: str, fields: Sequence[str], directory: Path = LOG_DIR):
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = Path(directory) / f"{prefix}_{stamp}.csv"
        self.fields = list(fields)

    def __enter__(self) -> "CsvLog":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("w", newline="")
        self._writer = csv.DictWriter(self._file, fieldnames=self.fields)
        self._writer.writeheader()
        return self

    def write(self, row: dict) -> None:
        self._writer.writerow(row)
        self._file.flush()

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self._file.close()
        print(f"Log saved to {self.path}")  # also on Ctrl+C or an error, so the data is never lost track of
