"""Load a work's configuration (<work root>/work.toml)."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Work:
    root: Path
    data: dict[str, Any]

    @property
    def id(self) -> str:
        return self.data["work"]["id"]

    @property
    def cast(self) -> dict[str, dict[str, Any]]:
        return self.data["cast"]

    @property
    def speakers(self) -> dict[str, str]:
        return self.data["speakers"]

    @property
    def scenes(self) -> list[dict[str, Any]]:
        return self.data["scenes"]

    def path(self, relative: str) -> Path:
        return self.root / relative


def load_work(root: Path) -> Work:
    root = Path(root)
    with (root / "work.toml").open("rb") as fh:
        return Work(root=root, data=tomllib.load(fh))
