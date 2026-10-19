# Lessons

Recurring problems and what worked. Read before starting; add an entry when a mistake repeats.

- The sandbox masks `.git/config` (and `.git/config.lock`); git commands that write config
  (`git remote add`) must run outside the sandbox.
- `~/.cache/uv` is read-only inside the sandbox: export `UV_CACHE_DIR=$TMPDIR/uv-cache`
  before any `uv` command.
- The sandbox mounts char-device placeholders (`.claude/`, `.mcp.json`, dotfiles) in the
  working dir; they are gitignored, never `git add` them.
- `renpy lint` passing does not mean the game runs: a main menu once crashed on missing GUI
  images. Always run the end-to-end playthrough (`vnpipe renpy --with-test` + `renpy.sh test`,
  see docs/running.md).
- Ren'Py SDK commands write to `~/.renpy`: prefix them with `HOME=$TMPDIR/renpy-home`
  inside the sandbox. `web_build` leaves `progressive_download.txt` in the project (gitignored).
- Small-model annotators label whole speeches with one emotion and lose stage presence
  across chunks; fix with `work.toml` overrides (pinned cast, emotion ranges), not by
  hand-editing annotated.json.
