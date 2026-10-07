"""Check that every approved script has a non-empty camera/audio footage set."""

from argparse import ArgumentParser
from pathlib import Path
import re


APPROVED_PARTS = ("1-Pre Production", "1-Script Review", "Approved")
FOOTAGE_PARTS = ("2-Production", "1-Footage Intake")
ROLES = ("camA", "camB", "lav")
ROLE_EXTENSIONS = {
    "camA": {"mov", "mp4"},
    "camB": {"mov", "mp4"},
    "lav": {"wav"},
}
FILENAME = re.compile(
    r"^(?P<script>.+?)__T(?P<take>\d{2})__(?P<role>camA|camB|lav)\.(?P<ext>[^.]+)$"
)
BAD_SEPARATOR = re.compile(
    r"^(?P<script>.+?)__T(?P<take>\d{2})_(?P<role>camA|camB|lav)\.[^.]+$"
)


def inspect_footage(footage_dir: Path) -> tuple[dict[str, list[str]], list[str]]:
    """Return complete take numbers and actionable file problems."""
    takes: dict[tuple[str, str], set[str]] = {}
    problems = []

    for path in footage_dir.iterdir():
        if not path.is_file():
            continue
        match = FILENAME.fullmatch(path.name)
        if match:
            extension = match["ext"].lower()
            if extension not in ROLE_EXTENSIONS[match["role"]]:
                continue
            if path.stat().st_size == 0:
                problems.append(f"{path.name}: file is empty")
                continue
            key = (match["script"], match["take"])
            takes.setdefault(key, set()).add(match["role"])
            continue
        bad_separator = BAD_SEPARATOR.fullmatch(path.name)
        if bad_separator:
            expected = (
                f"{bad_separator['script']}__T{bad_separator['take']}__"
                f"{bad_separator['role']}{path.suffix}"
            )
            problems.append(f"{path.name}: expected {expected}")

    complete: dict[str, list[str]] = {}
    for (script, take), roles in takes.items():
        if roles == set(ROLES):
            complete.setdefault(script, []).append(f"T{take}")
    return complete, problems


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="Studio project folder")
    args = parser.parse_args()

    project = args.project.resolve(strict=True)
    approved_dir = project.joinpath(*APPROVED_PARTS)
    footage_dir = project.joinpath(*FOOTAGE_PARTS)

    if not approved_dir.is_dir():
        parser.error(f"Approved script folder does not exist: {approved_dir}")
    if not footage_dir.is_dir():
        parser.error(f"Footage folder does not exist: {footage_dir}")

    approved = sorted(path.stem for path in approved_dir.glob("*.docx"))
    if not approved:
        parser.error(f"No approved scripts found in {approved_dir}")

    complete, problems = inspect_footage(footage_dir)
    missing = [script for script in approved if script not in complete]

    print(f"Footage Intake - {project.name}")
    for script in approved:
        takes = ", ".join(sorted(complete.get(script, [])))
        print(f"  {'OK' if takes else 'MISSING'}  {script}{f' ({takes})' if takes else ''}")

    if missing or problems:
        print("\nFAIL - footage validation found problems")
        for problem in problems:
            print(f"  - {problem}")
        raise SystemExit(1)

    print("\nPASS - every approved script has a non-empty camA/camB/lav set")


if __name__ == "__main__":
    main()
