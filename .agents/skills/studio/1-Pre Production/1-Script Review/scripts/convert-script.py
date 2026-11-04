"""Convert a Studio DOCX between one-column and ID/dialogue/directions layouts."""

import argparse
import importlib.util
from pathlib import Path
from zipfile import BadZipFile

from docx.opc.exceptions import PackageNotFoundError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Stage 1.1 script DOCX")
    parser.add_argument("--to", required=True, choices=("one-column", "three-column"))
    parser.add_argument("--output", type=Path, help="New .docx filename or path; never overwritten")
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("write_script", Path(__file__).with_name("write-script.py"))
    writer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(writer)
    try:
        target = writer.convert_script(args.source, args.to, args.output)
    except (OSError, ValueError, BadZipFile, PackageNotFoundError, KeyError) as error:
        parser.error(str(error))
    print(f"Created {args.to} script: {target}")
    print("Source preserved; no approval or completion marker written.")


if __name__ == "__main__":
    main()
