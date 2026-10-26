# vnpipe

A pipeline that turns public-domain classic novels and plays into Ren'Py visual novels:
Project Gutenberg text → parsed JSON → LLM-annotated JSON → Ren'Py project.

`vnpipe` is work-agnostic. Everything specific to a text (source, scope, cast, staging)
lives in a *work repo* in any directory, in a `work.toml`.
Example: [faust-vn](https://github.com/Dasemu/faust-vn). A sibling checkout is not required.

## Develop vnpipe

```bash
git clone https://github.com/Dasemu/vnpipe.git
cd vnpipe
uv sync --locked
uv run pytest -q
uv run vnpipe --help
```

## Generate an existing work

Follow the work repository's dependency configuration. For faust-vn, from its root:

```bash
uv sync --locked
uv run vnpipe --work . renpy
```

Its Git dependency is pinned by its lockfile; no local vnpipe clone or source-path
edit is needed. Existing committed annotated data can be generated directly:
fetch/parse/annotate/translation commands are for developing the content.
`--work DIR` selects the directory containing `work.toml` (default: current directory).

For desktop execution, web and Android prerequisites, follow the
[faust-vn running guide](https://github.com/Dasemu/faust-vn/blob/main/docs/running.md)
or the [generic guide](docs/running.md). Generating a project does not launch it.

To test an unpublished vnpipe checkout from a work root, use
`uv run --with-editable /absolute/path/to/vnpipe vnpipe --work . renpy`.

## Docs

- [Pipeline overview](docs/pipeline.md)
- [Annotation stage](docs/annotation.md)
- [Work configuration (`work.toml`)](docs/work-config.md)
- [Adaptation rules](docs/adaptation-rules.md)
- [Running, linting and building](docs/running.md)

## License

[MIT](LICENSE).
