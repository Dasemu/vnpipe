"""Command-line entry point.

Each pipeline stage lives in its own module exposing ``register(subparsers)``,
which adds a subcommand whose handler takes the parsed ``argparse.Namespace``.
"""

import argparse
import importlib
from pathlib import Path

STAGE_MODULES = ["text", "annotate", "assets", "renpy_gen"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vnpipe", description=__doc__)
    parser.add_argument(
        "--work",
        type=Path,
        default=Path("."),
        help="Work directory containing work.toml (default: current directory)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in STAGE_MODULES:
        try:
            module = importlib.import_module(f"vnpipe.{name}")
        except ModuleNotFoundError as exc:
            if exc.name != f"vnpipe.{name}":
                raise
            continue
        module.register(sub)
    args = parser.parse_args(argv)
    return args.handler(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
