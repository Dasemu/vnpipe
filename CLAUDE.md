# CLAUDE.md

`vnpipe`: work-agnostic pipeline that turns a public-domain text into a Ren'Py visual novel.
Works (texts) live in their own repos, e.g. `../Faust-VN`.

## Rules
- Repo content in English; game text comes from the source and is never rewritten.
- Nothing work-specific in `src/vnpipe`: no hard-coded work ids, paths, characters or text.
  Work details go in the work's `work.toml`.
- Every stage must be deterministic and idempotent; LLM/image outputs are cached by input hash.
- Data contracts live in `src/vnpipe/schemas/*.schema.json`; change schema + tests together.
- Tests use only `tests/fixtures/`; never depend on a real work's data.

## Commands
- `uv sync` — install. `uv run pytest` — tests. `uv run ruff check . && uv run ruff format --check .` — lint.
- `uv run vnpipe --help` — CLI (`--work` defaults to the current directory). Stages: `fetch`,
  `parse`, `annotate {run,status,prompt,merge}`, `assets {...}`, `voice {fetch,asr,build,cut}`
  (`docs/voice.md`), `translate {fetch,build,status}` (`docs/translations.md`),
  `renpy [--with-test]` (default output `<work>/vn`).
- `scripts/asr.py` runs on a GPU host (faster-whisper), never as a vnpipe dependency.
- Ren'Py SDK: set `RENPY_SDK` to the unpacked SDK directory (tests that need it are skipped
  otherwise; fallback `tools/renpy-sdk`). See `docs/running.md`.
- In the sandbox: `export UV_CACHE_DIR=$TMPDIR/uv-cache`.

## Branches and versions
- `development` is the integration branch; `main` receives reviewed merges from it.
  Feature work branches off `development` (`feat/...`, `fix/...`).
- `VERSION` is bumped by `.github/workflows/version.yml` on push. Do not edit it by hand.
- CI (`.github/workflows/ci.yml`) runs ruff and pytest.

## Layout
`src/vnpipe/` code, `schemas/`, `prompts/`, `renpy_templates/`; `tests/`; `docs/`
(see `docs/pipeline.md`).
