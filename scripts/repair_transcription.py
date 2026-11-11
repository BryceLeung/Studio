#!/usr/bin/env python3
"""Run a user-requested local repair with the stage-local WhisperX interpreter."""

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.tools.line.repair import RepairError, repair_transcription  # noqa: E402


def _interrupt(_signum, _frame):
    raise KeyboardInterrupt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("filename", help="Takes mapping (preferred) or native WhisperX transcript")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--group", type=int, required=True, help="One-based global Line transcript display group")
    parser.add_argument("--model-dir")
    parser.add_argument("--padding", type=float, default=5.0)
    parser.add_argument("--timing-threshold", type=float, default=0.5)
    parser.add_argument("--anchor-words", type=int, default=3)
    parser.add_argument("--anchor-tolerance", type=float, default=0.5)
    parser.add_argument("--result", type=Path, help="Also write final machine JSON to this new file")
    args = parser.parse_args(argv)
    signal.signal(signal.SIGTERM, _interrupt)
    code = 0
    try:
        with redirect_stdout(sys.stderr):
            result = repair_transcription(args.project_root, args.filename, args.group, model_dir=args.model_dir,
                                          padding=args.padding, timing_threshold=args.timing_threshold,
                                          anchor_words=args.anchor_words, anchor_tolerance=args.anchor_tolerance,
                                          progress=lambda message: print(message, file=sys.stderr, flush=True))
    except KeyboardInterrupt:
        result = {"status": "cancelled", "error": "Repair cancelled", "transcript_path": None, "mapping_path": None}
        code = 130
    except (OSError, ValueError, TypeError, KeyError, ImportError) as error:
        result = {"status": "error", "error": str(error), "report_path": getattr(error, "report_path", None),
                  "transcript_path": None, "mapping_path": None}
        code = 1
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
    if args.result:
        try:
            with args.result.open("x", encoding="utf-8") as handle:
                handle.write(encoded + "\n")
        except OSError as error:
            print(f"Cannot write result file: {error}", file=sys.stderr)
            code = 1
    print(encoded, flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
