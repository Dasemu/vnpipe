"""Standalone word-level ASR for `vnpipe voice asr` (runs on a GPU host, not in the pipeline).

Depends only on faster-whisper. Writes one JSON per input audio file:
{"audio", "model", "language", "duration", "words": [[word, start, end, prob], ...]}

    python asr.py --out OUT_DIR [--model medium] [--language de] FILE.mp3 ...
"""

import argparse
import json
from pathlib import Path

from faster_whisper import WhisperModel


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="medium")
    parser.add_argument("--language", default="de")
    parser.add_argument("--device-index", type=int, default=0)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(
        args.model, device="cuda", device_index=args.device_index, compute_type="int8_float16"
    )
    for path in args.files:
        segments, info = model.transcribe(
            str(path), language=args.language, word_timestamps=True, vad_filter=True
        )
        words = [
            [w.word.strip(), round(w.start, 2), round(w.end, 2), round(w.probability, 3)]
            for seg in segments
            for w in seg.words
        ]
        payload = {
            "audio": path.name,
            "model": args.model,
            "language": args.language,
            "duration": round(info.duration, 2),
            "words": words,
        }
        dest = args.out / f"{path.stem}.json"
        dest.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{path.name}: {len(words)} words -> {dest}", flush=True)


if __name__ == "__main__":
    main()
