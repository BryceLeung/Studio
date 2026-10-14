#!/usr/bin/env python3
"""Generate a disposable extract subtitle preview without running the 3.6 stage."""
import argparse
import json
from pathlib import Path

import extracts
import first_cut as cut


def preview(project, shortlist, scores_paths, number, output):
    project = project.resolve(strict=True)
    output = output.resolve()
    if not output.parent.is_dir() or not output.is_relative_to(project / "3-Post Production/0-Tmp"):
        raise ValueError("Preview output must be inside an existing project 3-Post Production/0-Tmp directory.")
    if output.exists():
        raise ValueError(f"Preview exists; preserve it and choose another name: {output}")
    mapped = next((m for m in extracts.map_all(project, shortlist, scores_paths)["extracts"]
                   if m["number"] == number), None)
    if mapped is None:
        raise ValueError(f"No approved extract {number}")
    unresolved = [i for i in mapped["issues"] if i["reason"] != "script_explicitly_discarded"]
    if unresolved:
        raise ValueError(f"Extract has unresolved mapping issues: {unresolved}")
    scores_path = next((project / p).resolve(strict=True) for p in scores_paths
                       if Path(p).name == Path(mapped["source"]).stem + "__Scores.v1.json")
    scores = cut.read_json(scores_path)
    document = cut.scoring.load_mapping(project, project / scores["source_mapping"]["path"])
    syncs, probes = {}, {}
    for recording in sorted({t["recording_number"] for t in scores["takes"]}):
        sync = cut.read_json(project / cut.SYNC / f"{Path(mapped['source']).stem}__C{recording:02d}.json")
        if (sync.get("offset_convention") != "positive_means_file_starts_later_than_camA"
                or sync.get("reference_role") != "camA" or set(sync["clips"]) != set(cut.ROLES)):
            raise ValueError(f"Unsupported sync for recording {recording}")
        syncs[recording] = sync
        probes[recording] = {}
        for role, clip in sync["clips"].items():
            media = (project / clip["path"]).resolve(strict=True)
            fingerprint = cut.media_fingerprint(media, project)
            # Windows sync manifests have whole-second mtimes; OneDrive on macOS
            # may report a different fractional second for the same file.
            if (fingerprint["bytes"] != clip["size_bytes"] or
                    fingerprint["mtime_ns"] // 1_000_000_000 != clip["mtime_ns"] // 1_000_000_000):
                raise ValueError(f"Media changed since sync: {media}")
            probes[recording][role] = cut.probe(media)
    included = {cut.find.mapping.range_key(g["script"]) for g in mapped["groups"] if not g["discarded"]}
    xml, report = cut.build_xml(project, output, f"{number:02d}-{mapped['title']} - Subtitle Preview",
                                document, scores, syncs, probes, 50, included)
    if not report["subtitle_titles"]:
        raise ValueError("No subtitle titles generated.")
    with output.open("xb") as stream:
        stream.write(xml)
    cut.find.append_log(output.parent, "First Cut subtitle preview", [
        f"Extract {number}; independent preview, not a completed 3.6 run or a replacement for existing edits.",
        f"Output: {output.relative_to(project)}; {report['takes']} alternatives; {report['subtitle_titles']} title states.",
        "For this cross-platform preview, media fingerprints matched byte size and whole-second mtime; original sync timestamps retain Windows precision.",
        "Import into Final Cut and review word sync, title rendering, and audition switching; no DONE written."])
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--shortlist", required=True, type=Path)
    parser.add_argument("--scores", required=True, nargs="+", type=Path)
    parser.add_argument("--extract", type=int, default=1)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    project = args.project_root.resolve(strict=True)
    report = preview(project, (project / args.shortlist).resolve(strict=True), args.scores, args.extract,
                     (project / args.output).resolve())
    print(json.dumps({key: report[key] for key in ("script", "takes", "auditions", "subtitle_titles")}, indent=2))


if __name__ == "__main__":
    main()
