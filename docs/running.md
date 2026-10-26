# Running, linting and building the game

The Ren'Py project in `<work>/vn/` is generated; never edit it by hand. Commands below run
from the work root (the directory with `work.toml`); `RENPY_SDK` is the SDK directory
(e.g. `export RENPY_SDK=/absolute/path/to/renpy-sdk`).
Examples use Bash on Linux x86-64; other hosts need their SDK launcher and bundled Python.
Install the work's locked dependencies first (`uv sync --locked`). A sibling vnpipe
checkout is not required. For a complete clean-clone example, see the
[faust-vn guide](https://github.com/Dasemu/faust-vn/blob/main/docs/running.md).

## 1. Ren'Py SDK

The SDK is not committed. Install **Ren'Py 8.5.3** (SDK
archive from <https://www.renpy.org/dl/8.5.3/>, `renpy-8.5.3-sdk.tar.bz2`) so that
`$RENPY_SDK/renpy.sh` exists:

```sh
tar -xjf renpy-8.5.3-sdk.tar.bz2 && mv renpy-8.5.3-sdk "$RENPY_SDK"
```

The SDK provides desktop distribution templates. For web, download
<https://www.renpy.org/dl/8.5.3/renpy-8.5.3-web.zip> and unzip it into
`"$RENPY_SDK"` so that `"$RENPY_SDK/web"` exists. RAPT is a separate add-on (below).

## 2. Regenerate the project

```sh
uv run vnpipe renpy                 # from data/annotated.json
uv run vnpipe renpy --from-parsed   # trivial annotations built from parsed.json
uv run vnpipe renpy --out some/dir  # default output: <work>/vn
```

Generation is deterministic: running it twice gives identical files (no timestamps).
It rewrites the generated scripts, GUI, fonts and images. When the work has an
`audio/` directory, its Ogg files are copied into `vn/game/audio/`.
Keep audio sources in the work's `audio/` directory, not the generated output;
configure music and credits in `work.toml` and `audio/credits.toml`.

Images come from `assets/assets.json` (see "Art styles" below); anything missing or
unusable is replaced by a generated placeholder so the game always runs. The generator also
writes `game/styles.rpy` (the first-launch style picker and `persistent.art_style`).

### Art styles and the asset manifest (v2)

`assets/assets.json` declares the art styles once and gives each asset one *rendition* per
style:

```json
{
  "default_style": "painterly",
  "styles": {"painterly": {"label": "Pictórico", "base_prompt": "...",
             "templates": {"background": "...", "sprite": "...", "variant": "..."}}},
  "assets": [{"id": "sprite_faust_neutral", "kind": "sprite", "character": "faust",
              "expression": "neutral", "prompt": "...", "reference": null,
              "renditions": {"painterly": {"source": "chatgpt-web", "generation_id": "...",
                "raw_file": "raw/painterly/sprite_faust_neutral.png",
                "file": "sprites/painterly/faust_neutral.webp", "sha256": "...",
                "status": "approved", "notes": ""}}}]
}
```

The prompt sent is `styles[style].base_prompt` + the kind template + the asset prompt.
Files live in `<kind dir>/<style>/<name>.webp` (raw: `raw/<style>/<asset id>.png`).
Asset commands take `--style` (default: `default_style`):

```sh
vnpipe assets prompt|import|process|placeholders|sheet [--style S] ...
vnpipe assets status [--style S]       # asset x style matrix + approved/pending/missing counts
vnpipe assets check [--style S] [IDS]  # automatic checks, exit 1 on any failure
vnpipe assets migrate STYLE [--label L]  # one-off: v1 manifest -> v2 (old data becomes STYLE)
```

`process` runs the checks on what it processed. Checks: a variant's sha256 differs from its
reference's (same style); a sprite's opaque coverage is 15-85% (measured on the keyed raw
image when it exists, else on the trimmed file) and its bbox does not touch the top edge
(raw only); a background is 1920x1080.

In the game, each image is a `ConditionSwitch` on `persistent.art_style` over the styles
with a `processed`/`approved` rendition; the default style is the fallback (placeholder if
it has none). An image with a single usable style is a plain image. First launch shows a
style picker before the first scene (one button per style label); Preferences has a radio
group to change it. A change applies on the next interaction without moving sprites.

### GUI template and licences

`src/vnpipe/renpy_templates/` (in this repo) holds the default Ren'Py GUI (`gui.rpy`, `screens.rpy`,
`options.rpy` and `gui/*.png`). It was generated once with the SDK's own command and then
edited minimally (dark palette, ivory text, accent `#c9a25b`, Roboto Slab font, NVL
tweaks, `@@TOKEN@@` placeholders for title/build name/about text/menu background):

```sh
"$RENPY_SDK"/renpy.sh "$RENPY_SDK/launcher" generate_gui OUT --width 1920 --height 1080 \
  --accent '#c9a25b' --boring '#14110e' --template "$RENPY_SDK/gui" --start
```

Since then the menus were redesigned around the scene title cards (gold rule with a diamond,
size-76 titles): a book-title main menu, a navigation column with the work's title, framed
panels, and a save/load register (one row per slot with thumbnail, scene title, the line on
screen and the date; Roman-numbered pages; autosave and quick save pages only when loading;
a delete button per slot). The scene and line come from `config.save_json_callbacks`
(`menus.rpy`). The ornament images (`gui/vn/*.png`, check/radio marks, bars) are drawn by
`gui_extra_images()` in `renpy_gen.py`. `tl/spanish/` holds the Spanish UI strings: Ren'Py's
SDK translation (corrected and completed) plus every `_()` string of the screens; a test
fails when a screen string has no translation. To check a project for untranslated strings,
run `renpy.sh <copy of vn> translate spanish` on a throw-away copy and look for new `old`
entries after the "TODO: Translation updated" line.

These files are part of Ren'Py (MIT licence, Tom Rothamel and contributors). The font
`game/fonts/RobotoSlab-*.ttf` is Roboto Slab (Apache 2.0), taken from the SDK
documentation assets.

## 3. Lint

```sh
"$RENPY_SDK"/renpy.sh vn lint
```

The expected result is a statistics block with no warnings above it.

## 3b. End-to-end playthrough test

Lint does not catch runtime errors (missing GUI images, display bugs). `--with-test` also
writes `game/e2e_test.rpy`, a Ren'Py `testcase` that clicks "Start", advances through the
whole game up to the final card (`label the_end`) and back to the main menu (then opens and
closes the Scenes, Credits, Load, Preferences and About screens; a save made at the first
scene must carry its title), failing on any
exception (`traceback.txt`). Use a throw-away output directory so the test file never ends
up in `vn/` or in a build:

```sh
uv run vnpipe renpy --with-test --out build/e2e
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy RENPY_RENDERER=sw \
  "$RENPY_SDK"/renpy.sh build/e2e test
```

The headless run needs the software renderer (`RENPY_RENDERER=sw`, no GL under the dummy
video driver); matrix tints are therefore not rendered but all game logic is exercised.
`tests/test_renpy_gen.py` runs this for the fixture work when the SDK exists (set
`RENPY_SDK`; slow: about a minute). On a desktop with a display, drop the three variables.

## 4. Play

```sh
"$RENPY_SDK"/renpy.sh vn
```

## 5. Builds (output under `build/`, gitignored)

```sh
# Web (HTML5)
"$RENPY_SDK"/renpy.sh "$RENPY_SDK/launcher" web_build vn --destination build/web
rm -f vn/progressive_download.txt
uv run python -m http.server --bind 127.0.0.1 --directory build/web 8000     # then open http://localhost:8000

# Linux desktop
"$RENPY_SDK"/renpy.sh "$RENPY_SDK/launcher" distribute vn --package linux --destination build/dist
```

```sh
# Android APK (needs the `[android]` table in work.toml, RAPT in the SDK and JDK 21)
"$RENPY_SDK"/renpy.sh "$RENPY_SDK/launcher" android_build vn --destination build/dist
```

Use JDK 21 (`JAVA_HOME` and `PATH`) and ASCII-only absolute paths for the work and SDK.
Install RAPT and the Android SDK once:

```bash
curl -fsSL -o /tmp/vnpipe-rapt.zip https://www.renpy.org/dl/8.5.3/renpy-8.5.3-rapt.zip
unzip -q /tmp/vnpipe-rapt.zip -d "$RENPY_SDK"
(
  cd "$RENPY_SDK/rapt"
  ../lib/py3-linux-x86_64/python -c '
import runpy, sys
sys.path[:0] = [".."]
sys.argv = ["android.py", "installsdk"]
runpy.run_path("android.py", run_name="__main__")
'
)
```

Review and accept the Android SDK licences when prompted, then run `android_build`
above. For updates, restore the original `vn/android.keystore` and `vn/bundle.keystore`
before building. A first independent build creates keys when absent: back them up,
keep them out of git and reuse them for every update. Transfer the resulting APK
from `build/dist` to the device and install it with permission for that installation source.

`--package` also accepts `win` and `mac`; omit it to build every package. The web build
also leaves a `build/web.zip` next to the folder. A web build writes
`vn/progressive_download.txt`; delete it (it is not part of the generated project).
