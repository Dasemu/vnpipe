import json
from pathlib import Path

from vnpipe import renpy_gen
from vnpipe import voice as voice_mod
from vnpipe.config import load_work

FIXTURES = Path(__file__).parent / "fixtures"


def test_game_targets_and_voice_emission(tmp_path):
    work = load_work(FIXTURES / "mini_work")
    annotated = json.loads((FIXTURES / "annotated_mini.json").read_text(encoding="utf-8"))
    talk = next(s for s in annotated["scenes"] if s["id"] == "talk")
    targets = voice_mod.game_targets(talk)
    assert [(t.speech, t.speaker, t.first_line) for t in targets][:3] == [
        (0, "director", "talk_0002"),
        (1, "poet", "talk_0008"),
        (2, "director", "talk_0009"),
    ]
    voice = {
        "lyric": {0: "voice/lyric_0001.ogg"},
        "talk": {2: "voice/talk_0009.ogg"},
    }
    script = renpy_gen.render_script(work, annotated, voice=voice)
    lines = [ln.strip() for ln in script.splitlines()]
    # a speech spanning several boxes: `voice` once, then `voice sustain`
    lyric = lines[lines.index("label scene_lyric:") : lines.index("label scene_talk:")]
    assert lyric.count('voice "voice/lyric_0001.ogg"') == 1
    assert lyric.count("voice sustain") >= 1
    first_say = next(i for i, ln in enumerate(lyric) if ln.startswith("c_"))
    assert lyric[first_say - 1] == 'voice "voice/lyric_0001.ogg"'
    # a stage direction inside the voiced speech keeps the clip going
    talk_block = lines[lines.index("label scene_talk:") :]
    assert talk_block.count('voice "voice/talk_0009.ogg"') == 1
    i = talk_block.index('voice "voice/talk_0009.ogg"')
    assert talk_block[i + 1].startswith("c_director")
    assert "voice sustain" in talk_block[i + 2 : i + 6]
    # speeches without a clip emit nothing
    assert script.count("voice ") == script.count('voice "') + script.count("voice sustain")
    plain = renpy_gen.render_script(work, annotated)
    assert 'voice "' not in plain and "voice sustain" not in plain


def test_voice_clips_are_copied_outside_audio_and_credited(tmp_path):
    root = tmp_path / "w"
    (root / "voice/clips").mkdir(parents=True)
    (root / "voice/clips/talk_0009.ogg").write_bytes(b"OggS")
    (root / "voice/map.json").write_text(
        json.dumps(
            {
                "version": 1,
                "scenes": {
                    "talk": {
                        "entries": [
                            {"speech": 2, "first_line": "talk_0009", "file": "voice/talk_0009.ogg"}
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    base = load_work(FIXTURES / "mini_work")
    data = json.loads(json.dumps(base.data))
    data["voice"] = {"credits": ["Lectura de [LibriVox]"]}
    work = type(base)(root=root, data=data)
    assert voice_mod.voice_files(work) == {"talk": {2: "voice/talk_0009.ogg"}}
    written = voice_mod.copy_voice(work, tmp_path / "out")
    assert [p.relative_to(tmp_path / "out").as_posix() for p in written] == [
        "game/voice/talk_0009.ogg"
    ]
    assert dict(renpy_gen.credits_sections(work, None))["Voces"] == ["Lectura de [[LibriVox]"]
    (root / "voice/clips/talk_0009.ogg").unlink()  # no clip, no credit, no voice
    assert voice_mod.voice_files(work) == {}
    assert "Voces" not in dict(renpy_gen.credits_sections(work, None))


def test_cut_clip_applies_the_speaker_chain_before_loudnorm(monkeypatch, tmp_path):
    calls = []

    class Result:
        stderr = json.dumps(
            {
                "input_i": "-20",
                "input_tp": "-3",
                "input_lra": "5",
                "input_thresh": "-30",
                "target_offset": "0.1",
            }
        )

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Result()

    monkeypatch.setattr(voice_mod, "run", fake_run)
    (tmp_path / "x.part.ogg").write_bytes(b"")
    voice_mod.cut_clip(Path("a.mp3"), 1.0, 3.0, tmp_path / "x.ogg", "highpass=f=80")
    filters = [c[c.index("-af") + 1] for c in calls]
    assert len(filters) == 2
    assert all(f.startswith("highpass=f=80,loudnorm=") for f in filters)
    calls.clear()
    (tmp_path / "x.part.ogg").write_bytes(b"")
    voice_mod.cut_clip(Path("a.mp3"), 1.0, 3.0, tmp_path / "x.ogg")
    assert all(c[c.index("-af") + 1].startswith("loudnorm=") for c in calls)
