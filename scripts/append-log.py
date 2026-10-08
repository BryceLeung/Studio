"""Append one structured entry to an existing Studio LOG.md file."""

import argparse
from datetime import date
import os
from pathlib import Path


def single_line(value: str) -> str:
    if not value.strip() or "\n" in value or "\r" in value:
        raise argparse.ArgumentTypeError("value must be one non-empty line")
    return value.strip()


def append_entry(log_path: Path, title: str, items: list[str], entry_date: date) -> None:
    log_path = log_path.resolve(strict=True)
    if log_path.name != "LOG.md" or not log_path.is_file():
        raise ValueError(f"Expected an existing LOG.md file: {log_path}")

    heading = f"## {entry_date.isoformat()} — {title}"
    entry = "\n" + heading + "\n\n" + "\n".join(f"- {item}" for item in items) + "\n"
    descriptor = os.open(log_path, os.O_WRONLY | os.O_APPEND)
    try:
        os.write(descriptor, entry.encode("utf-8"))
    finally:
        os.close(descriptor)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log", type=Path, help="Existing Studio step LOG.md")
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
