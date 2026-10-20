"""Voice track: a source-language reading played under the translated text.

Stages (all deterministic and cached on disk; see docs/voice.md):

    fetch  download the reading's audio sections and the source-language text
    asr    word-level transcription of each audio section on a GPU host -> voice/asr/NN.json
    build  parse the source text, align it with the ASR words, map its speeches onto the game's
           speeches by speaker sequence -> voice/map.json + voice/report/<scene>.txt
    cut    ffmpeg: one normalised clip per mapped speech -> voice/clips/<first line id>.ogg

``vnpipe renpy`` then plays the clips (``voice`` statements); speeches with no clip stay silent.
"""

import argparse
import json
import shutil
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from vnpipe.config import Work, load_work
from vnpipe.voice_align import (
    Target,
    align,
    find_sections,
    map_speeches,
    pad_bounds,
    parse_speeches,
    plausible_duration,
)

CLIPS_DIR = "voice/clips"
MAP_FILE = "voice/map.json"
MIN_COVERAGE = 0.6  # share of a speech's words found in the audio, below which it stays silent
LOUDNESS = "I=-18:TP=-2:LRA=11"
ASR_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "asr.py"


# --- configuration -------------------------------------------------------------------------


def voice_cfg(work: Work) -> dict[str, Any]:
    cfg = work.data.get("voice")
    if not cfg:
        raise SystemExit("this work has no [voice] table in work.toml")
    return cfg


def selected_sections(work: Work, scenes: str | None) -> list[dict[str, Any]]:
    """The ``[[voice.sections]]`` entries to process: ``--scenes a,b``, else ``[voice].scenes``,
    else all of them."""
    cfg = voice_cfg(work)
    wanted = scenes.split(",") if scenes else cfg.get("scenes")
    sections = cfg["sections"]
    known = {s["scene"] for s in sections}
    for scene in wanted or []:
        if scene not in known:
            raise SystemExit(f"voice: scene {scene!r} has no [[voice.sections]] entry")
    return [s for s in sections if not wanted or s["scene"] in wanted]


def asr_path(work: Work, section: dict[str, Any]) -> Path:
    return work.path(f"voice/asr/{section['nn']:02d}.json")


def audio_path(work: Work, section: dict[str, Any]) -> Path:
    return work.path(voice_cfg(work)["audio_dir"]) / f"{section['nn']:02d}.mp3"


# --- fetch ---------------------------------------------------------------------------------


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "vnpipe/0.1"})
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        payload = response.read()
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(payload)
    tmp.replace(dest)
    print(f"fetch: wrote {dest} ({len(payload)} bytes)")


def cmd_fetch(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    cfg = voice_cfg(work)
    text = work.path(cfg["text_file"])
    if not text.exists():
        download(cfg["text_url"], text)
    for section in selected_sections(work, args.scenes):
        dest = audio_path(work, section)
        if not dest.exists():
            download(cfg["audio_url"].format(nn=section["nn"]), dest)
    return 0


# --- asr -----------------------------------------------------------------------------------


def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)  # noqa: S603
    if result.returncode:
        raise SystemExit(f"voice: {' '.join(cmd[:3])} failed:\n{result.stderr[-800:]}")
    return result


def cmd_asr(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    cfg = voice_cfg(work).get("asr", {})
    host = args.host or cfg.get("host")
    if not host:
        raise SystemExit("voice asr: pass --host or set [voice.asr].host")
    remote = cfg.get("workdir", ".") + "/vnpipe-asr"
    sections = selected_sections(work, args.scenes)
    todo = [s for s in sections if args.force or not asr_path(work, s).exists()]
    if not todo:
        print("asr: all sections already transcribed")
        return 0
    ssh = ["ssh", "-o", "BatchMode=yes", host]
    scp = ["scp", "-o", "BatchMode=yes"]
    run([*ssh, f"mkdir -p {remote}/in {remote}/out"])
    run([*scp, str(ASR_SCRIPT), f"{host}:{remote}/asr.py"])
    names = []
    for section in todo:
        src = audio_path(work, section)
        if not src.is_file():
            raise SystemExit(f"asr: {src} missing; run `vnpipe voice fetch`")
        run([*scp, str(src), f"{host}:{remote}/in/{src.name}"])
        names.append(f"in/{src.name}")
    devices = cfg.get("devices", [0])
    groups: list[list[str]] = [[] for _ in devices]
    loads = [0] * len(devices)
    for name in sorted(
        names, key=lambda n: -work.path(f"{voice_cfg(work)['audio_dir']}/{n[3:]}").stat().st_size
    ):
        i = loads.index(min(loads))  # biggest first onto the least loaded device
        groups[i].append(name)
        loads[i] += work.path(f"{voice_cfg(work)['audio_dir']}/{name[3:]}").stat().st_size

    def transcribe(device: int, files: list[str]) -> str:
        script = (
            f"cd {cfg.get('workdir', '.')} && {cfg.get('prelude', 'true')} && "
            f"{cfg.get('python', 'python')} vnpipe-asr/asr.py --out vnpipe-asr/out "
            f"--model {cfg.get('model', 'medium')} --language {cfg.get('language', 'de')} "
            f"--device-index {device} " + " ".join(f"vnpipe-asr/{n}" for n in files)
        )
        return run([*ssh, script]).stdout.strip()

    jobs = [(d, g) for d, g in zip(devices, groups, strict=True) if g]
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        for output in pool.map(lambda job: transcribe(*job), jobs):
            print(output)
    for section in todo:
        dest = asr_path(work, section)
        dest.parent.mkdir(parents=True, exist_ok=True)
        run([*scp, f"{host}:{remote}/out/{section['nn']:02d}.json", str(dest)])
        print(f"asr: wrote {dest}")
    return 0


# --- build ---------------------------------------------------------------------------------


def game_targets(scene: dict[str, Any]) -> list[Target]:
    """Speeches of a game scene that have a speaker, in order."""
    found: dict[int, Target] = {}
    for line in scene["lines"]:
        speaker = line["ann"]["speaker"]
        if line["kind"] not in ("verse", "prose") or not speaker:
            continue
        target = found.setdefault(line["speech"], Target(line["speech"], speaker, 0, line["id"]))
        target.words += len(line["text"].split())
    return [found[k] for k in sorted(found)]


def load_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def build_scene(
    work: Work,
    scene: dict[str, Any],
    section: dict[str, Any],
    german: str,
    asr: dict[str, Any],
) -> dict[str, Any]:
    """Align one scene; returns its map entry."""
    cfg = voice_cfg(work)
    speakers = {**cfg["speakers"], **cfg.get("scene_speakers", {}).get(scene["id"], {})}
    source = parse_speeches(
        german, speakers, section.get("default_speaker"), section.get("skip_paragraphs", 0)
    )
    words = asr["words"]
    spans = align(source, words)
    targets = game_targets(scene)
    mapping = map_speeches(source, targets, frozenset(cfg.get("ensembles", [])))
    total_tokens = sum(len(s.tokens) for s in source)
    matched_tokens = sum(sp.matched for sp in spans if sp)
    used: set[int] = set()
    entries = []
    for target in targets:
        entry: dict[str, Any] = {
            "speech": target.speech,
            "first_line": target.first_line,
            "speaker": target.speaker,
            "words": target.words,
            "source": None,
            "status": "unmapped",
            "file": None,
        }
        j = mapping.get(target.speech)
        if j is not None:
            used.add(j)
            sp, span = source[j], spans[j]
            entry["source"] = j
            if span is None or span.coverage < MIN_COVERAGE:
                entry["status"] = "low-coverage"
                entry["coverage"] = round(span.coverage, 2) if span else 0.0
            else:
                start, end = pad_bounds(span, words)
                entry.update(
                    coverage=round(span.coverage, 2),
                    start=round(start, 2),
                    end=round(end, 2),
                    status="ok"
                    if plausible_duration(span.end - span.start, sp.text)
                    else "implausible-duration",
                )
        if entry["status"] == "ok":
            entry["file"] = f"voice/{target.first_line}.ogg"
        entries.append(entry)
    voiced = sum(e["status"] == "ok" for e in entries)
    result = {
        "section": section["nn"],
        "speeches": len(entries),
        "voiced": voiced,
        "source_speeches": len(source),
        "asr_coverage": round(matched_tokens / total_tokens, 3) if total_tokens else 0.0,
        "entries": entries,
        "unmapped_source": [
            {
                "index": sp.index,
                "label": sp.label,
                "speaker": sp.speaker,
                "tokens": len(sp.tokens),
                "coverage": round(spans[sp.index].coverage, 2) if spans[sp.index] else None,
            }
            for sp in source
            if sp.index not in used
        ],
    }
    return result


def report_text(scene_id: str, result: dict[str, Any]) -> str:
    out = [
        f"scene {scene_id}  (audio section {result['section']})",
        f"voiced {result['voiced']}/{result['speeches']} game speeches",
        f"source speeches {result['source_speeches']}, "
        f"ASR coverage {result['asr_coverage']:.1%} of source words",
        "",
        "game speeches:",
    ]
    for e in result["entries"]:
        extra = ""
        if e["source"] is not None:
            extra = f" <- source #{e['source']} cov {e.get('coverage', 0):.2f}"
            if "start" in e:
                extra += f" {e['start']:.1f}-{e['end']:.1f}s"
        out.append(f"  {e['status']:<20} {e['first_line']} {e['speaker']} ({e['words']}w){extra}")
    out += ["", "source speeches without a game speech:"]
    for sp in result["unmapped_source"]:
        cov = "not found" if sp["coverage"] is None else f"cov {sp['coverage']:.2f}"
        who = f"{sp['label']} -> {sp['speaker']}"
        out.append(f"  #{sp['index']} {who} ({sp['tokens']} tokens, {cov})")
    return "\n".join(out) + "\n"


def cmd_build(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    cfg = voice_cfg(work)
    text_path = work.path(cfg["text_file"])
    if not text_path.is_file():
        raise SystemExit("voice build: source text missing; run `vnpipe voice fetch`")
    annotated = json.loads(work.path("data/annotated.json").read_text(encoding="utf-8"))
    scenes = {s["id"]: s for s in annotated["scenes"]}
    all_sections = cfg["sections"]
    texts = find_sections(
        text_path.read_text(encoding="utf-8"), [s["heading"] for s in all_sections]
    )
    by_scene = {s["scene"]: t for s, t in zip(all_sections, texts, strict=True)}
    voice_map = load_json(work.path(MAP_FILE), {"version": 1, "scenes": {}})
    for section in selected_sections(work, args.scenes):
        scene_id = section["scene"]
        path = asr_path(work, section)
        if not path.is_file():
            raise SystemExit(f"voice build: {path} missing; run `vnpipe voice asr`")
        asr = json.loads(path.read_text(encoding="utf-8"))
        result = build_scene(work, scenes[scene_id], section, by_scene[scene_id], asr)
        voice_map["scenes"][scene_id] = result
        write_report(work, scene_id, result)
        print(
            f"build: {scene_id}: voiced {result['voiced']}/{result['speeches']}, "
            f"ASR coverage {result['asr_coverage']:.1%}"
        )
    voice_map["scenes"] = dict(sorted(voice_map["scenes"].items()))
    write_map(work, voice_map)
    return 0


def write_report(work: Work, scene_id: str, result: dict[str, Any]) -> None:
    path = work.path(f"voice/report/{scene_id}.txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report_text(scene_id, result), encoding="utf-8", newline="\n")


def write_map(work: Work, voice_map: dict[str, Any]) -> None:
    path = work.path(MAP_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(voice_map, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n"
    )


# --- cut -----------------------------------------------------------------------------------


def probe_duration(path: Path) -> float:
    out = run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]
    ).stdout
    return float(out.strip())


def cut_clip(src: Path, start: float, end: float, dest: Path, chain: str = "") -> None:
    """Optional ``chain`` (ffmpeg ``-af`` filters, e.g. a noise cleanup), then two-pass
    loudnorm (linear, so short clips are not pumped); mono Opus ~40 kbps."""
    pre = f"{chain}," if chain else ""
    seek = ["-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", str(src)]
    first = run(
        ["ffmpeg", "-hide_banner", "-nostats", *seek]
        + ["-af", f"{pre}loudnorm={LOUDNESS}:print_format=json", "-f", "null", "-"]
    ).stderr
    stats, _ = json.JSONDecoder().raw_decode(first[first.rindex("{") :])
    if stats["input_i"] in ("-inf", "inf"):
        raise ValueError("silent clip")
    measured = (
        f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
        f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
        f"offset={stats['target_offset']}:linear=true"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part.ogg")
    run(
        ["ffmpeg", "-hide_banner", "-nostats", "-y", *seek]
        + ["-af", f"{pre}loudnorm={LOUDNESS}:{measured}", "-ac", "1", "-ar", "48000"]
        + ["-c:a", "libopus", "-b:a", "40k", "-application", "voip", str(tmp)]
    )
    tmp.replace(dest)


def cmd_cut(args: argparse.Namespace) -> int:
    work = load_work(args.work)
    voice_map = load_json(work.path(MAP_FILE), None)
    if voice_map is None:
        raise SystemExit("voice cut: voice/map.json missing; run `vnpipe voice build`")
    clips = work.path(CLIPS_DIR)
    filters: dict[str, str] = voice_cfg(work).get("filters", {})
    for section in selected_sections(work, args.scenes):
        scene_id = section["scene"]
        result = voice_map["scenes"].get(scene_id)
        if result is None:
            raise SystemExit(f"voice cut: {scene_id} not built; run `vnpipe voice build`")
        src = audio_path(work, section)
        made = reused = 0
        for entry in result["entries"]:
            dest = clips / f"{entry['first_line']}.ogg"
            if entry["status"] not in ("ok", "cut-failed"):
                dest.unlink(missing_ok=True)
                continue
            chain = filters.get(entry["speaker"], "")
            key = f"{src.name}:{entry['start']}:{entry['end']}:{LOUDNESS}:{chain}"
            if dest.is_file() and entry.get("cut_key") == key:
                reused += 1
                continue
            expected = entry["end"] - entry["start"]
            try:
                cut_clip(src, entry["start"], entry["end"], dest, chain)
                if abs(probe_duration(dest) - expected) > 0.3:
                    raise ValueError("duration mismatch")
            except (ValueError, SystemExit, KeyError) as exc:
                dest.unlink(missing_ok=True)
                entry.update(status="cut-failed", file=None, error=str(exc)[:200])
                entry.pop("cut_key", None)
                continue
            entry.update(status="ok", file=f"voice/{entry['first_line']}.ogg", cut_key=key)
            entry.pop("error", None)
            made += 1
        result["voiced"] = sum(e["status"] == "ok" for e in result["entries"])
        print(f"cut: {scene_id}: {made} new, {reused} cached, {result['voiced']} voiced")
        write_report(work, scene_id, result)
    write_map(work, voice_map)
    return 0


# --- used by `vnpipe renpy` ----------------------------------------------------------------


def voice_files(work: Work) -> dict[str, dict[int, str]]:
    """scene id -> {speech index: path relative to ``game/``} for clips that exist."""
    voice_map = load_json(work.path(MAP_FILE), None)
    if voice_map is None:
        return {}
    found: dict[str, dict[int, str]] = {}
    for scene_id, result in voice_map["scenes"].items():
        for entry in result["entries"]:
            if entry.get("file") and work.path(f"{CLIPS_DIR}/{entry['first_line']}.ogg").is_file():
                found.setdefault(scene_id, {})[entry["speech"]] = entry["file"]
    return found


def copy_voice(work: Work, out: Path) -> list[Path]:
    """Copy ``<work>/voice/clips/*.ogg`` into ``game/voice/`` (never into ``game/audio/``)."""
    dest_root = out / "game" / "voice"
    shutil.rmtree(dest_root, ignore_errors=True)
    written = []
    for src in sorted(work.path(CLIPS_DIR).glob("*.ogg")):
        dest = dest_root / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        written.append(dest)
    return written


# --- CLI -----------------------------------------------------------------------------------


def register(subparsers: argparse._SubParsersAction) -> None:
    voice = subparsers.add_parser("voice", help="Voice track from a source-language reading")
    sub = voice.add_subparsers(dest="voice_command", required=True)

    def add(name: str, help_: str, handler: Any) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_)
        p.add_argument("--scenes", help="Comma-separated scene ids (default: [voice].scenes)")
        p.set_defaults(handler=handler)
        return p

    add("fetch", "Download the audio sections and the source text", cmd_fetch)
    asr = add("asr", "Transcribe audio sections with word timestamps on a GPU host", cmd_asr)
    asr.add_argument("--host", help="ssh host (default: [voice.asr].host)")
    asr.add_argument("--force", action="store_true", help="Redo sections already transcribed")
    add("build", "Align source text with ASR and map speeches onto the game text", cmd_build)
    add("cut", "Cut normalised clips from the mapped speeches", cmd_cut)
