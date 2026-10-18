#!/usr/bin/env python3
"""Compatibility entry point for the read-only Studio validator."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.validation import audit, check_marker, load_stages, main, order_stages  # noqa: E402,F401


if __name__ == "__main__":
    main()
