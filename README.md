# vnpipe

A pipeline that turns public-domain classic novels and plays into Ren'Py visual novels:
Project Gutenberg text → parsed JSON → LLM-annotated JSON → Ren'Py project.

`vnpipe` is work-agnostic. Everything specific to a text (source, scope, cast, staging)
lives in a *work repo* next to this one, in a `work.toml`. Example: `faust-vn`.

## Quick start

```bash
uv sync
uv run pytest -q
uv run vnpipe --help
```

In a work repo (a directory with `work.toml`), depend on this package and run
`uv run vnpipe parse`, `uv run vnpipe annotate ...`, `uv run vnpipe renpy`.
`--work DIR` selects another work directory (default: the current directory).

## Docs

- [Pipeline overview](docs/pipeline.md)
- [Annotation stage](docs/annotation.md)
- [Work configuration (`work.toml`)](docs/work-config.md)
- [Adaptation rules](docs/adaptation-rules.md)
- [Running, linting and building](docs/running.md)
