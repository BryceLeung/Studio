"""Validate and manifest Studio production footage for substage 2.1 on Windows, macOS, or Linux."""

from argparse import ArgumentParser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import re


STEP_ID = "2.1"
SKILL_DIR = Path(__file__).resolve().parents[3]
ROOT_SKILL = SKILL_DIR / "SKILL.md"
STEP_SKILL = SKILL_DIR / "2-Production" / "1-Footage Intake" / "SKILL.md"
VALIDATOR = Path(__file__).resolve()
APPROVED_PARTS = ("1-Pre Production", "1-Script Review", "Approved")
STEP_PARTS = ("2-Production", "1-Footage Intake")

ROLES = ("camA", "camB", "lav")
ROLE_EXTENSIONS = {
    "camA": {"mp4", "mov"},
    "camB": {"mp4", "mov"},
    "lav": {"wav", "mp3", "m4a"},
}
MEDIA_EXTENSIONS = {"mp4", "mov", "wav", "mp3", "m4a"}
FILENAME = re.compile(
    r"^(?P<script>.+?)__T(?P<take>\d{2})__(?P<role>camA|camB|lav)\.(?P<ext>mp4|mov|wav|mp3|m4a)$"
)
IGNORED_NAMES = {"log.md", "done", "desktop.ini", "thumbs.db", "ehthumbs.db"}
PENDING_SUFFIXES = {".tmp", ".partial", ".crdownload", ".filepart"}


@dataclass
class Take:
    script: str
    number: str
    roles: dict = field(default_factory=dict)
    duplicates: list = field(default_factory=list)

    @property
    def label(self) -> str:
        return f"{self.script} T{self.number}"

    def missing(self) -> list:
        return [role for role in ROLES if role not in self.roles]

    def is_complete(self) -> bool:
        return not self.missing() and not self.duplicates


def digest(path: Path) -> str:
    accumulator = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            accumulator.update(block)
    return accumulator.hexdigest().upper()


def fingerprint(path: Path, threshold_mb: int, sample_mb: int, full_hash: bool) -> tuple:
    size = path.stat().st_size
    if not full_hash and size > threshold_mb * 1024 * 1024:
        span = sample_mb * 1024 * 1024
        accumulator = sha256()
        with path.open("rb") as handle:
            accumulator.update(handle.read(span))
            if size > span:
                handle.seek(max(span, size - span))
                accumulator.update(handle.read(span))
        return accumulator.hexdigest().upper(), f"sha256-sampled(first+last {sample_mb} MiB)"
    return digest(path), "sha256"


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def diagnose(name: str) -> str:
    suffix = Path(name).suffix
    extension = suffix[1:] if suffix else ""
    if not extension:
        return "missing a file extension"
    if extension != extension.lower():
        return f"extension '.{extension}' must be lowercase"
    if extension not in MEDIA_EXTENSIONS:
        accepted = ", ".join(sorted(MEDIA_EXTENSIONS))
        return f"'.{extension}' is not an accepted media extension; expected one of {accepted}"
    fields = name[: -len(suffix)].split("__")
    if len(fields) < 3:
        return "missing the '__T<nn>__<role>' separator; expected <Script>__T<nn>__<role>.<ext>"
    if len(fields) > 3:
        return "too many '__' separators; the script name must not contain '__'"
    _, take, role = fields
    if not take:
        return "missing the take number"
    if take[0] != "T":
        return f"take field '{take}' must start with 'T'"
    digits = take[1:]
    if not digits.isdigit():
        return f"take field '{take}' must be 'T' followed by digits"
    if len(digits) != 2:
        return f"take field '{take}' must be zero-padded to two digits, e.g. 'T{int(digits):02d}'"
    if role not in ROLES:
        if role.lower() in ROLES:
            return f"role '{role}' must be lowercase"
        return f"unknown role '{role}'; expected one of {', '.join(ROLES)}"
    if extension not in ROLE_EXTENSIONS[role]:
        expected = ", ".join(sorted(ROLE_EXTENSIONS[role]))
        return f"'.{extension}' is not a valid extension for role '{role}'; expected {expected}"
    return "does not match <Script>__T<nn>__<role>.<ext>"


def collect(step_dir: Path, approved: list) -> tuple:
    violations = []
    takes = {}
    for path in sorted(step_dir.iterdir()):
        name = path.name
        if path.is_dir() or name.lower() in IGNORED_NAMES or name.startswith("."):
            continue
        if name.startswith("~$") or Path(name).suffix.lower() in PENDING_SUFFIXES:
            violations.append((name, "looks like an in-progress transfer; wait for the copy to finish"))
            continue
        match = FILENAME.match(name)
        if not match:
            violations.append((name, diagnose(name)))
            continue
        script, take, role, extension = match["script"], match["take"], match["role"], match["ext"]
        if "__" in script:
            violations.append((name, "script name contains '__'; the name must not include the separator"))
            continue
        if script not in approved:
            violations.append((name, f"'{script}' is not an approved script"))
            continue
        if extension not in ROLE_EXTENSIONS[role]:
            expected = ", ".join(sorted(ROLE_EXTENSIONS[role]))
            violations.append((name, f"'.{extension}' is not valid for role '{role}'; expected {expected}"))
            continue
        record = takes.setdefault((script, take), Take(script, take))
        if role in record.roles:
            record.duplicates.append(path)
        else:
            record.roles[role] = path

    for record in sorted(takes.values(), key=lambda item: (item.script, item.number)):
        if record.missing():
            reason = f"incomplete take; missing {'/'.join(record.missing())}"
            violations.append((f"[take] {record.label}", reason))
        for path in record.duplicates:
            violations.append((path.name, f"duplicate role for {record.label}; a take has one file per role"))
    return violations, takes


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="OneDrive Studio project folder")
    parser.add_argument("--write-manifest", action="store_true", help="Write the DONE manifest when validation passes")
    parser.add_argument("--accept-missing", action="append", default=[], metavar="SCRIPT",
                        help="Approved script allowed to have no footage; repeatable, and the reason must be recorded in LOG.md")
    parser.add_argument("--hash-threshold-mb", type=int, default=256,
                        help="Files larger than this use a sampled hash; default 256")
    parser.add_argument("--sample-mb", type=int, default=8, help="MiB sampled from each end of a large file; default 8")
    parser.add_argument("--full-hash", action="store_true", help="Hash every file completely regardless of size")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing DONE manifest after confirming the change")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print the report as JSON")
    args = parser.parse_args()

    if args.hash_threshold_mb < 0:
        parser.error("--hash-threshold-mb must not be negative")
    if args.sample_mb < 1:
        parser.error("--sample-mb must be at least 1")

    project = args.project.resolve(strict=True)
    approved_dir = project.joinpath(*APPROVED_PARTS)
    step_dir = project.joinpath(*STEP_PARTS)
    if not approved_dir.is_dir():
        parser.error(f"Approved script folder does not exist: {approved_dir}")
    if not step_dir.is_dir():
        parser.error(f"Footage step folder does not exist: {step_dir}")

    approved = [path.stem for path in sorted(approved_dir.glob("*.docx"))]
    if not approved:
        parser.error(f"No approved Word scripts found in {approved_dir}")
    unknown = [name for name in args.accept_missing if name not in approved]
    if unknown:
        parser.error(f"--accept-missing names no approved script: {', '.join(unknown)}")
    accepted = [name for name in args.accept_missing]

    violations, takes = collect(step_dir, approved)
    complete = {record.script for record in takes.values() if record.is_complete()}
    for name in approved:
        if name not in complete and name not in accepted:
            violations.append((name, "no complete take present"))

    outputs = []
    if not violations:
        for record in sorted(takes.values(), key=lambda item: (item.script, item.number)):
            for role in ROLES:
                path = record.roles[role]
                checksum, method = fingerprint(path, args.hash_threshold_mb, args.sample_mb, args.full_hash)
                outputs.append({
                    "path": str(path),
                    "sha256": checksum,
                    "bytes": path.stat().st_size,
                    "hash_method": method,
                    "script": record.script,
                    "take": f"T{record.number}",
                    "role": role,
                })

    ordered = sorted(takes.values(), key=lambda item: (item.script, item.number))
    report = {
        "project": project.name,
        "step": STEP_ID,
        "approved_docx_names": approved,
        "accepted_missing": accepted,
        "takes": [
            {"script": record.script, "take": f"T{record.number}",
             "roles": [role for role in ROLES if role in record.roles],
             "missing": record.missing(),
             "complete": record.is_complete()}
            for record in ordered
        ],
        "violations": [{"subject": subject, "reason": reason} for subject, reason in violations],
        "valid": not violations,
    }

    if args.as_json:
        print(json.dumps(report, indent=4))
    else:
        print(f"Footage Intake {STEP_ID} - {project.name}")
        print()
        print(f"Approved scripts ({len(approved)})")
        for name in approved:
            note = "  (no footage accepted)" if name in accepted else ""
            print(f"  {name}{note}")
        print()
        if ordered:
            print(f"Takes ({len(ordered)})")
            for record in ordered:
                cells = [f"{role:4}" if role in record.roles else " -- " for role in ROLES]
                state = "OK" if record.is_complete() else "MISSING " + "/".join(record.missing())
                total = sum(record.roles[role].stat().st_size for role in record.roles)
                print(f"  {record.label:38} {' '.join(cells)}  {state:14} {human_size(total)}")
        else:
            print("Takes (0)")
        print()
        if violations:
            print(f"FAIL - {len(violations)} violation(s)")
            for subject, reason in violations:
                print(f"  - {subject}: {reason}")
        else:
            print(f"PASS - {len(outputs)} file(s) validated, all takes complete")

    if violations:
        raise SystemExit(1)

    if not args.write_manifest:
        return

    manifest_path = step_dir / "DONE"
    manifest = {
        "project": project.name,
        "step": STEP_ID,
        "completed_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "dependencies": [
            {"path": str(path), "sha256": digest(path)} for path in (ROOT_SKILL, STEP_SKILL, VALIDATOR)
        ],
        "inputs": [
            {"path": str(approved_dir / f"{name}.docx"), "sha256": digest(approved_dir / f"{name}.docx")}
            for name in approved
        ],
        "outputs": outputs,
        "conditions": {
            "approved_docx_names": approved,
            "media_file_count": len(outputs),
            "take_count": len(ordered),
            "take_inventory": [
                {"script": record.script, "take": f"T{record.number}",
                 "roles": [role for role in ROLES if role in record.roles]}
                for record in ordered
            ],
            "violation_count": 0,
            "accepted_missing": accepted,
            "naming_convention": "<Script>__T<nn>__<role>.<ext>",
            "hash_method": outputs[0]["hash_method"] if outputs else "none",
        },
    }
    if manifest_path.exists() and not args.overwrite:
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("outputs") != manifest["outputs"]:
            raise SystemExit(
                f"{manifest_path} already exists and records different footage; "
                "confirm the change, then rerun with --overwrite"
            )
    manifest_path.write_text(json.dumps(manifest, indent=4), encoding="utf-8")
    print(f"\nWrote {manifest_path}")


if __name__ == "__main__":
    main()
