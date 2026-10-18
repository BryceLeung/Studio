#!/usr/bin/env python3
"""Read-only stage validation; stage validators extend StageValidation."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if __name__ == "__main__":
    sys.modules["scripts.validation"] = sys.modules[__name__]
from scripts import stage_completion as completion  # noqa: E402


def load_stages(workflow=ROOT):
    stages = {}
    skill_root = Path(workflow) / ".agents/skills/studio"
    for file in skill_root.glob("*/*/dependencies.json"):
        definition = completion.load_definition(file)
        step = definition["step"]
        if step in stages or file.parent.relative_to(skill_root).as_posix() != definition["folder"]:
            raise ValueError(f"Duplicate or misplaced stage definition: {file}")
        stages[step] = definition
    if not stages:
        raise ValueError(f"No stage definitions in {skill_root}")
    for step, definition in stages.items():
        for prerequisite in definition["prerequisites"]:
            parent = stages.get(prerequisite["step"])
            if parent is None or parent["folder"] != prerequisite["folder"]:
                raise ValueError(f"Unresolved prerequisite {prerequisite} for {step}")
    return stages


def order_stages(stages, target=None):
    if target is not None and target not in stages:
        raise ValueError(f"Unknown stage: {target}")
    order, visiting, visited = [], set(), set()

    def visit(step):
        if step in visiting:
            raise ValueError(f"Cycle in stage prerequisites at {step}")
        if step in visited:
            return
        visiting.add(step)
        for parent in stages[step]["prerequisites"]:
            visit(parent["step"])
        visiting.remove(step)
        visited.add(step)
        order.append(step)

    for step in ([target] if target else sorted(stages, key=lambda name: tuple(map(int, name.split("."))))):
        visit(step)
    return order


def inventory_problem(project, inventory, recorded=None):
    folder = (project / completion.relative_path(inventory["folder"])).resolve()
    if not folder.is_relative_to(project):
        return f"Inventory escapes project: {inventory['folder']}"
    pattern = re.compile(inventory["filename_regex"], re.I)
    found = {path.relative_to(project).as_posix() for path in folder.iterdir()
             if path.is_file() and pattern.fullmatch(path.name)} if folder.is_dir() else set()
    count = len(found)
    if count < inventory["minimum"] or ("maximum" in inventory and count > inventory["maximum"]):
        return f"Inventory {inventory['folder']} / {inventory['filename_regex']}: found {count}"
    if recorded is not None and inventory.get("complete_set"):
        saved = {entry["path"] for entry in recorded if entry.get("scope") == "project"
                 and completion.relative_path(entry["path"]).parent.as_posix() == inventory["folder"]
                 and pattern.fullmatch(Path(entry["path"]).name)}
        if found != saved:
            return f"Inventory differs from DONE: {inventory['folder']} (added={sorted(found - saved)}, removed={sorted(saved - found)})"
    return None


def recorded_fingerprints(manifest):
    """Read both list and nested legacy manifest sections; never infer success."""
    entries = []

    def walk(value):
        if isinstance(value, dict):
            if "path" in value and any(key in value for key in ("sha256", "method", "mtime_ns", "mtime_s")):
                entries.append(value)
            else:
                for child in value.values():
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    for section in ("inputs", "outputs", "dependencies"):
        walk(manifest.get(section))
    return entries


def check_common(project, workflow, definition, manifest=None):
    project = Path(project).resolve(strict=True)
    workflow = Path(workflow).resolve(strict=True)
    marker = project / completion.relative_path(definition["folder"]) / "DONE"
    if not marker.resolve().is_relative_to(project):
        return "unverifiable", "DONE escapes the project root"
    if manifest is None and not marker.is_file():
        return "missing", "No active DONE"
    try:
        if manifest is None:
            manifest = json.loads(marker.read_text(encoding="utf-8-sig"))
        if not isinstance(manifest, dict):
            return "unverifiable", "DONE is not a JSON object"
        if manifest.get("step") not in (None, definition["step"]):
            return "stale", "DONE belongs to a different stage"
        entries = recorded_fingerprints(manifest)
        if not entries:
            return "unverifiable", "DONE contains no readable file fingerprints"
        for entry in entries:
            if entry.get("scope") == "workflow" and entry["path"] == ".agents/skills/studio/SKILL.md":
                continue
            if "scope" not in entry:
                return "unverifiable", f"Unscoped legacy fingerprint: {entry['path']}"
            if not completion.matches_entry(entry, project, workflow):
                return "stale", f"Fingerprint differs or file missing: {entry['path']}"
        for section in ("inputs", "outputs"):
            for inventory in definition[section]:
                saved = recorded_fingerprints({section: manifest.get(section)})
                problem = inventory_problem(project, inventory, saved)
                if problem:
                    return "stale", problem
                recorded = {entry["path"] for entry in saved if entry.get("scope") == "project"
                            and completion.relative_path(entry["path"]).parent.as_posix() == inventory["folder"]
                            and re.fullmatch(inventory["filename_regex"], Path(entry["path"]).name, re.I)}
                if len(recorded) < inventory["minimum"]:
                    return "unverifiable", f"DONE lacks evidence for inventory: {inventory['folder']}"
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError) as error:
        return "unverifiable", f"Cannot validate DONE: {error}"
    return "current", "Recorded fingerprints and inventories match"


class StageValidation:
    """Common checks cannot be bypassed by a stage's validate_stage override."""

    def __init__(self, project, workflow, definition):
        self.project = Path(project).resolve(strict=True)
        self.workflow = Path(workflow).resolve(strict=True)
        self.definition = definition
        self.manifest = None

    def confined(self, path):
        path = Path(path)
        path = (self.project / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_relative_to(self.project):
            raise ValueError(f"Path escapes project: {path}")
        return path

    def require_inventory(self, section, paths, exact=False):
        """Require evidence for all selected files, preserving other input categories."""
        expected = {self.confined(path).relative_to(self.project).as_posix() for path in paths}
        saved = {entry["path"] for entry in recorded_fingerprints({section: self.manifest.get(section)})
                 if entry.get("scope") == "project"}
        if not expected.issubset(saved):
            raise ValueError(f"DONE lacks {section} fingerprints: {sorted(expected - saved)}")
        if exact and saved != expected:
            raise ValueError(f"DONE records obsolete {section} files: {sorted(saved - expected)}")

    def validate_stage(self):
        if self.definition.get("requires_stage_check"):
            raise NotImplementedError("Stage-specific validation has not been implemented")

    def validate(self, manifest=None):
        policy = self.definition.get("validation", {})
        if "skip_reason" in policy:
            return "skipped", policy["skip_reason"]
        status, reason = check_common(self.project, self.workflow, self.definition, manifest)
        if status != "current":
            return status, reason
        try:
            marker = self.confined(Path(self.definition["folder"]) / "DONE")
            self.manifest = manifest if manifest is not None else json.loads(marker.read_text(encoding="utf-8-sig"))
            if self.manifest.get("completion_mode") == "manual-override":
                from scripts.stage_override import validate_override
                return validate_override(self.project, self.workflow, self.definition, self.manifest)
            self.validate_stage()
        except NotImplementedError as error:
            return "unverifiable", str(error)
        except ImportError as error:
            return "unverifiable", f"Stage validator dependency unavailable: {error}"
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError) as error:
            return "stale", f"Stage validation failed: {error}"
        except Exception as error:
            return "unverifiable", f"Stage validator failed unexpectedly: {error}"
        return "current", "Recorded evidence and stage completion checks pass"


def check_marker(project, workflow, definition, manifest=None):
    validator = StageValidation
    root = Path(project).resolve(strict=True)
    marker = root / completion.relative_path(definition["folder"]) / "DONE"
    if not marker.resolve().is_relative_to(root):
        return "unverifiable", "DONE escapes the project root"
    if manifest is None and marker.is_file():
        try:
            manifest = json.loads(marker.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, ValueError) as error:
            return "unverifiable", f"Cannot read DONE: {error}"
    if isinstance(manifest, dict) and manifest.get("completion_mode") == "manual-override":
        from scripts.stage_override import validate_override
        return validate_override(root, workflow, definition, manifest)
    if "skip_reason" in definition.get("validation", {}):
        return StageValidation(project, workflow, definition).validate(manifest)
    if manifest is None:
        return "missing", "No active DONE"
    script = definition.get("validation", {}).get("script")
    if script:
        root = Path(workflow).resolve(strict=True)
        path = (root / ".agents/skills/studio" / completion.relative_path(definition["folder"])
                / completion.relative_path(script)).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return "unverifiable", "Stage validator is missing or escapes workflow root"
        try:
            old_path, old_bytecode = sys.path[:], sys.dont_write_bytecode
            sys.dont_write_bytecode = True
            spec = importlib.util.spec_from_file_location("studio_validation_" + definition["step"].replace(".", "_"), path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            validator = module.Validation
            if not issubclass(validator, StageValidation):
                return "unverifiable", "Stage validator must extend StageValidation"
        except Exception as error:
            return "unverifiable", f"Cannot load stage validator: {error}"
        finally:
            sys.path[:] = old_path
            sys.dont_write_bytecode = old_bytecode
    try:
        return StageValidation.validate(validator(project, workflow, definition), manifest)
    except Exception as error:
        return "unverifiable", f"Cannot initialize stage validator: {error}"


def validate_evidence(project, workflow, definition, manifest):
    if not isinstance(manifest, dict):
        return "unverifiable", "Completion evidence must be a JSON object"
    return check_marker(project, workflow, definition, manifest)


def audit(project, workflow=ROOT, target=None, stages=None, *, prerequisites_only=False):
    project = Path(project).resolve(strict=True)
    workflow = Path(workflow).resolve(strict=True)
    stages = load_stages(workflow) if stages is None else stages
    result = {}
    for step in order_stages(stages, target):
        if prerequisites_only and step == target:
            continue
        status, reason = check_marker(project, workflow, stages[step])
        local_status = status
        blockers = [item["step"] for item in stages[step]["prerequisites"]
                    if result[item["step"]]["status"] != "current"]
        if blockers and status != "skipped":
            status, reason = "blocked", f"Prerequisite evidence not current: {', '.join(blockers)}; local marker: {status} ({reason})"
        result[step] = {"status": status, "reason": reason, "local_status": local_status,
                        "blocked_by": blockers}
    return result


class PrerequisiteError(ValueError):
    def __init__(self, step, reason):
        self.step = step
        super().__init__(f"Stage {step} is not current: {reason}")


def require_prerequisites(project, workflow, step):
    result = audit(project, workflow, step, prerequisites_only=True)
    for ancestor, outcome in result.items():
        if outcome["status"] != "current":
            raise PrerequisiteError(ancestor, outcome["reason"])
    return result


def require_current(project, workflow, step):
    result = audit(project, workflow, step)
    for ancestor, outcome in result.items():
        if outcome["status"] != "current":
            raise PrerequisiteError(ancestor, outcome["reason"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--stage", help="Stage ID to audit with its prerequisites (default: all)")
    parser.add_argument("--prerequisites", action="store_true", help="Check ancestors only; requires --stage")
    args = parser.parse_args()
    if args.prerequisites and not args.stage:
        parser.error("--prerequisites requires --stage")
    try:
        result = audit(args.project_root, target=args.stage, prerequisites_only=args.prerequisites)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, f"Stage definitions: {error}\n")
    print(json.dumps(result, indent=2))
    if any(item["status"] != "current" for item in result.values()):
        parser.exit(1)


if __name__ == "__main__":
    main()
