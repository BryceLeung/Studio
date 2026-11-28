"""Resolve and record edited First Cut exports without unpacking bundles."""
import argparse
import json
from pathlib import Path
import re
import sys


def export_stem(xml):
    return xml.parent.stem if xml.parent.suffix.casefold() == ".fcpxmld" else xml.stem


def matches_rule(path, rule):
    folder = rule["folder"]
    if path.parent.as_posix() == folder:
        return bool(re.fullmatch(rule["filename_regex"], path.name, re.I))
    return (rule.get("bundle_member") == path.name and path.parent.parent.as_posix() == folder
            and bool(re.fullmatch(r".+\.fcpxmld", path.parent.name, re.I)))


def exports(project, rule):
    """Return a stem-keyed inventory of the actual XML files, not directories."""
    folder = project
    for part in Path(rule["folder"]).parts:
        folder = folder / part
        if folder.is_symlink():
            raise ValueError(f"Redirected First Cut approval directory: {folder}")
    result = {}
    if not folder.is_dir():
        return result
    for export in sorted(folder.iterdir()):
        bundle = "bundle_member" in rule and bool(re.fullmatch(r".+\.fcpxmld", export.name, re.I))
        if not bundle and not matches_rule(export.relative_to(project), rule):
            continue
        xml = export / rule["bundle_member"] if bundle else export
        if export.is_symlink() or xml.is_symlink() or not xml.is_file():
            raise ValueError(f"Expected a regular approved First Cut XML file: {xml}")
        key = export.stem.casefold()
        if key in result:
            raise ValueError(f"Duplicate First Cut approval for stem: {export.stem}")
        result[key] = xml
    return result


def build_manifest(project, workflow, step):
    """Supplement the shared flat-file inventory with bundle XML evidence."""
    from scripts import stage_completion as completion
    from scripts.validation import load_stages

    manifest = completion.build_manifest(project, workflow, step)
    definition = load_stages(workflow)[step]
    section = {"3.6": "outputs", "3.7": "inputs"}[step]
    rule = next(rule for rule in definition[section] if "bundle_member" in rule)
    paths = exports(project, rule)
    saved = {row["path"]: row for row in manifest[section]}
    for xml in paths.values():
        entry = completion.fingerprint(xml, project, "project")
        saved[entry["path"]] = entry
    manifest[section] = [saved[path] for path in sorted(saved)]
    if step == "3.6":
        from studio.first_cut_plan import SHORTLIST, parse_shortlist, selection, variants

        manifest["extract_selections"] = {
            path.as_posix(): selection(extract) for path, _, _, extract in variants(
                parse_shortlist((project / SHORTLIST).read_text(encoding="utf-8-sig")))
        }
    return manifest


def main():
    from scripts import stage_completion as completion

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--stage", required=True, choices=("3.6", "3.7"))
    args = parser.parse_args()
    try:
        project = args.project_root.resolve(strict=True)
        manifest = build_manifest(project, completion.ROOT, args.stage)
        recorded = completion.record_done(project, completion.ROOT, args.stage, manifest)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Completion failed: {error}\n")
    print(json.dumps(recorded, indent=2))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
