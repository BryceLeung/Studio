"""Read-only, declaration-driven evidence for explicitly authorized overrides.

No stage code is imported: inventories describe the entire candidate universe,
not just the files a stage's normal validator would select.
"""
import json
from pathlib import Path
import re
import sys


CRITICAL_FILES = (
    "scripts/validation.py", "scripts/stage_completion.py",
    "scripts/stage_invalidation.py", "scripts/stage_override.py",
    "scripts/override_snapshot.py", "studio/fingerprints.py",
    ".agents/skills/studio-override/SKILL.md",
)
ROOT_SKILL = ".agents/skills/studio/SKILL.md"
HELPERS = r"(?!test(?:_|\.py$))(?!.*_test\.py$).+\.py"
REQUIREMENTS = r"requirements.*\.txt"
SECTIONS = ("inputs", "outputs", "dependencies")


def _excluded(path):
    # Names such as Approved/Log.docx or scripts/debug.py can be real inputs.
    # Exclude lifecycle metadata, not declared data based on a naming heuristic.
    path = Path(path)
    return any(part.casefold() == "stale" for part in path.parts) or path.name.casefold() in {"done", "log.md"}


def _confined(root, value, relative_path, *, folder=False):
    relative = Path() if folder and value == "." else relative_path(value)
    if relative.as_posix() != value:
        raise ValueError(f"Noncanonical snapshot path: {value!r}")
    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError(f"Symlink in snapshot path: {value}")
    if not candidate.resolve().is_relative_to(root):
        raise ValueError(f"Snapshot path escapes root: {value}")
    return candidate


def _roots(project, workflow):
    roots = {}
    for scope, value in (("project", project), ("workflow", workflow)):
        path = Path(value)
        if path.is_symlink():
            raise ValueError(f"Symlinked {scope} root")
        roots[scope] = path.resolve(strict=True)
        if not roots[scope].is_dir():
            raise ValueError(f"Not a {scope} directory")
    return roots


def _shared():
    # Shared modules are stdlib-only, but may adjust sys.path on first import.
    old_path, old_bytecode = sys.path[:], sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        from scripts import stage_completion as completion
        from scripts.validation import load_stages, order_stages
    finally:
        sys.path[:] = old_path
        sys.dont_write_bytecode = old_bytecode
    return completion, load_stages, order_stages


def collect_snapshot(project, workflow, step):
    """Collect canonical fingerprints and exact inventory policies; never write."""
    completion, load_stages, order_stages = _shared()

    roots = _roots(project, workflow)
    relative_path = completion.relative_path
    skill_root = _confined(roots["workflow"], ".agents/skills/studio", relative_path, folder=True)
    for path in skill_root.glob("*/*/dependencies.json"):
        _confined(roots["workflow"], path.relative_to(roots["workflow"]).as_posix(), relative_path)
    stages = load_stages(roots["workflow"])
    order = order_stages(stages, step)
    watches = []

    def watch(scope, section, rule):
        folder, regex = rule["folder"], rule["filename_regex"]
        minimum, maximum = rule.get("minimum", 0), rule.get("maximum")
        if (type(minimum) is not int or minimum < 0
                or (maximum is not None and (type(maximum) is not int or maximum < minimum))):
            raise ValueError("Invalid snapshot inventory bounds")
        if not isinstance(regex, str):
            raise ValueError("Invalid snapshot filename pattern")
        pattern = re.compile(regex, re.I)
        directory = _confined(roots[scope], folder, relative_path, folder=True)
        if _excluded(folder):
            raise ValueError(f"Protected snapshot folder: {folder}")
        paths = []
        if directory.exists() and not directory.is_dir():
            raise ValueError(f"Inventory folder is not a directory: {folder}")
        if directory.is_dir():
            for path in directory.iterdir():
                name = path.relative_to(roots[scope]).as_posix()
                if not pattern.fullmatch(path.name) or _excluded(name) or (scope == "workflow" and name.casefold() == ROOT_SKILL.casefold()):
                    continue
                # Check before is_file(), which follows symlinks (including broken ones).
                candidate = _confined(roots[scope], name, relative_path)
                if candidate.is_file():
                    paths.append(name)
        paths.sort()
        if len(paths) < minimum or (maximum is not None and len(paths) > maximum):
            raise ValueError(f"Inventory {folder} / {regex}: found {len(paths)}, expected {minimum}..{maximum}")
        item = {"scope": scope, "folder": folder, "filename_regex": regex,
                "section": section, "paths": paths, "minimum": minimum}
        if maximum is not None:
            item["maximum"] = maximum
        watches.append(item)

    def dependency(path):
        if path.casefold() == ROOT_SKILL.casefold():
            return
        if _excluded(path):
            raise ValueError(f"Protected workflow dependency: {path}")
        relative = relative_path(path)
        if not _confined(roots["workflow"], path, relative_path).is_file():
            raise ValueError(f"Required workflow dependency is missing: {path}")
        watch("workflow", "dependencies", {"folder": relative.parent.as_posix(),
              "filename_regex": re.escape(relative.name), "minimum": 1, "maximum": 1})

    for ancestor in order:
        definition = stages[ancestor]
        for section in ("inputs", "outputs"):
            destination = "outputs" if ancestor == step and section == "outputs" else "inputs"
            for rule in definition[section]:
                watch("project", destination, rule)
        extra = definition.get("override_watches", [])
        if not isinstance(extra, list):
            raise ValueError("override_watches must be a list")
        for rule in extra:
            watch("project", "inputs", rule)
        stage = (Path(".agents/skills/studio") / relative_path(definition["folder"])).as_posix()
        dependency(stage + "/SKILL.md")
        dependency(stage + "/dependencies.json")
        script = definition.get("validation", {}).get("script")
        if script:
            dependency(stage + "/" + relative_path(script).as_posix())
        for path in definition.get("workflow_dependencies", []):
            dependency(path)
        for regex in (HELPERS, REQUIREMENTS):
            watch("workflow", "dependencies", {"folder": stage + "/scripts", "filename_regex": regex})
    for path in CRITICAL_FILES:
        dependency(path)
    for folder in (".", "scripts"):
        for regex in ((HELPERS, REQUIREMENTS) if folder == "scripts" else (REQUIREMENTS,)):
            watch("workflow", "dependencies", {"folder": folder, "filename_regex": regex})
    watch("workflow", "dependencies", {"folder": ".", "filename_regex": r"pyproject\.toml", "maximum": 1})

    # Sorting serialized rules also deduplicates overlapping ancestor declarations.
    watches = [json.loads(value) for value in sorted({json.dumps(item, sort_keys=True) for item in watches})]
    selected = {section: set() for section in SECTIONS}
    for item in watches:
        selected[item["section"]].update((item["scope"], path) for path in item["paths"])
    selected["inputs"] -= selected["outputs"]
    result = {section: [completion.fingerprint(_confined(roots[scope], path, relative_path), roots[scope], scope)
                        for scope, path in sorted(selected[section])] for section in SECTIONS}
    result["snapshot"] = {"schema": "studio.override.snapshot", "schema_version": 1, "watches": watches}
    return result


def validate_snapshot(project, workflow, definition, manifest):
    """Compare saved evidence with a fresh declaration-driven snapshot."""
    try:
        if not isinstance(manifest, dict) or not isinstance(definition, dict):
            raise ValueError("Snapshot manifest and stage definition must be objects")
        step = definition["step"]
        if manifest.get("step", step) != step:
            raise ValueError("Snapshot belongs to a different stage")
        snapshot = manifest["snapshot"]
        if (not isinstance(snapshot, dict) or set(snapshot) != {"schema", "schema_version", "watches"}
                or snapshot["schema"] != "studio.override.snapshot"
                or type(snapshot["schema_version"]) is not int or snapshot["schema_version"] != 1
                or not isinstance(snapshot["watches"], list)):
            raise ValueError("Unsupported override snapshot schema")
        # Validate syntax before comparing; a malformed record is not stale evidence.
        relative_path = _shared()[0].relative_path
        for watch in snapshot["watches"]:
            if (not isinstance(watch, dict)
                    or not {"scope", "folder", "filename_regex", "section", "paths", "minimum"} <= set(watch)
                    or set(watch) - {"scope", "folder", "filename_regex", "section", "paths", "minimum", "maximum"}
                    or watch["scope"] not in ("project", "workflow")
                    or watch["section"] not in SECTIONS or not isinstance(watch["paths"], list)
                    or type(watch["minimum"]) is not int or watch["minimum"] < 0
                    or ("maximum" in watch and (type(watch["maximum"]) is not int or watch["maximum"] < watch["minimum"]))):
                raise ValueError("Malformed override watch")
            if watch["folder"] != ".":
                relative_path(watch["folder"])
            re.compile(watch["filename_regex"], re.I)
            for path in watch["paths"]:
                relative_path(path)
                if _excluded(path):
                    raise ValueError("Protected file in snapshot")
        for section in SECTIONS:
            if not isinstance(manifest.get(section), list):
                raise ValueError(f"Missing snapshot {section} fingerprints")
            for entry in manifest[section]:
                if not isinstance(entry, dict) or entry.get("scope") not in ("project", "workflow"):
                    raise ValueError("Malformed snapshot fingerprint")
                relative_path(entry["path"])
                if _excluded(entry["path"]):
                    raise ValueError("Protected fingerprint path")
                method = entry.get("method")
                if method == "sha256":
                    if not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
                        raise ValueError("Malformed SHA256 fingerprint")
                elif method == "media-stat":
                    if type(entry.get("size_bytes")) is not int or entry["size_bytes"] < 0 or type(entry.get("mtime_s")) is not int:
                        raise ValueError("Malformed media fingerprint")
                else:
                    raise ValueError("Unsupported snapshot fingerprint method")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, re.error) as error:
        return "unverifiable", f"Invalid override snapshot: {error}"
    try:
        expected = collect_snapshot(project, workflow, step)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError, re.error) as error:
        return "stale", f"Cannot recollect override snapshot: {error}"
    if snapshot != expected["snapshot"]:
        return "stale", "Override inventory membership or watch policies changed"
    for section in SECTIONS:
        if manifest[section] != expected[section]:
            return "stale", f"Override {section} fingerprints changed"
    return "current", "Frozen override inventories and fingerprints match"
