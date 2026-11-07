"""Append one structured entry to a Studio LOG.md file, creating it if needed."""

import argparse
from datetime import date
import os
from pathlib import Path


def single_line(value: str) -> str:
    if not value.strip() or "\n" in value or "\r" in value:
        raise argparse.ArgumentTypeError("value must be one non-empty line")
    return value.strip()


def append_entry(log_path: Path, title: str, items: list[str], entry_date: date) -> None:
    log_path = log_path.resolve()
    if log_path.name != "LOG.md" or (log_path.exists() and not log_path.is_file()):
        raise ValueError(f"Expected a LOG.md file: {log_path}")
    log_path.parent.mkdir(parents=True, exist_ok=True)

    heading = f"## {entry_date.isoformat()} — {title}"
    entry = "\n" + heading + "\n\n" + "\n".join(f"- {item}" for item in items) + "\n"
    descriptor = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o666)
    try:
        os.write(descriptor, entry.encode("utf-8"))
    finally:
        os.close(descriptor)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="Studio step LOG.md (created if missing)")
    parser.add_argument("--title", required=True, type=single_line, help="Entry heading")
    parser.add_argument(
        "--item", required=True, action="append", type=single_line,
        help="Bullet item to append; repeat for each item",
    )
    parser.add_argument("--date", type=date.fromisoformat, default=date.today(), help="Entry date (YYYY-MM-DD)")
    args = parser.parse_args()

    try:
        append_entry(args.log, args.title, args.item, args.date)
    except (OSError, ValueError) as error:
        parser.error(str(error))

    print(f"Appended entry to {args.log.resolve()}")


if __name__ == "__main__":
    main()
