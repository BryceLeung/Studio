"""Print a project's real directory path, resolving symlinks and junctions."""

import argparse
from pathlib import Path


def resolve_project(path: Path) -> Path:
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError(f"Project path is not a directory: {resolved}")
    return resolved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="Connected project link or project directory")
    args = parser.parse_args()
    try:
        resolved = resolve_project(args.path)
    except (OSError, RuntimeError, ValueError) as error:
        parser.error(str(error))
    print(resolved)


if __name__ == "__main__":
    main()
