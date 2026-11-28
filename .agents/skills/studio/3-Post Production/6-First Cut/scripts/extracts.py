#!/usr/bin/env python3
"""Map approved extract passages to source script groups and scored take identities."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

import first_cut as cut
from scripts.validation import StageValidation
from scripts.stage_completion import fingerprint, matches_entry
from studio.first_cut_plan import parse_shortlist, selection, variants


def match_passages(extract, script):
    tokens = [w["text"] for w in script["words"]]
    alternatives = []
    for passage in extract["passages"]:
        wanted = passage["text"].split()
        starts = [i for i in range(len(tokens) - len(wanted) + 1) if tokens[i:i + len(wanted)] == wanted]
        if not starts:
            raise ValueError(f"Extract {extract['number']} passage does not match approved wording: {passage['text']}")
        alternatives.append([(i, i + len(wanted) - 1) for i in starts])
    solutions = []
    def walk(index, chosen):
        if len(solutions) > 1:
            return
        if index == len(alternatives):
            solutions.append(chosen)
            return
        for start, end in alternatives[index]:
            if chosen:
                last = chosen[-1][1]
                if start <= last:
                    continue
            walk(index + 1, chosen + [(start, end)])
    walk(0, [])
    if len(solutions) != 1:
        raise ValueError(f"Extract {extract['number']} has {len(solutions)} unique ordered matches; review needed.")
    return solutions[0]


def map_extract(extract, document, scores):
    spans = match_passages(extract, document["script"])
    included = {i for start, end in spans for i in range(start, end + 1)}
    discarded = {cut.find.mapping.range_key(g) for g in document.get("script_discarded", [])}
    groups, issues = [], []
    omissions = [{"script": {"word_start": left[1] + 1, "word_end": right[0] - 1},
                  "marked_jump": extract["passages"][index + 1]["jump_before"]}
                 for index, (left, right) in enumerate(zip(spans, spans[1:])) if right[0] > left[1] + 1]
    for index, group in enumerate(document["script_groupings"]):
        group_words = set(range(group["word_start"], group["word_end"] + 1))
        overlap = included & group_words
        if not overlap:
            continue
        candidates = sorted((deepcopy(t) for t in scores["takes"] if t["script"] == group),
                            key=lambda t: t["score"], reverse=True)
        is_discarded = cut.find.mapping.range_key(group) in discarded
        if overlap != group_words:
            issues.append({"sentence": index + 1, "reason": "extract_crops_reviewed_script_group", "script": group})
        if is_discarded:
            issues.append({"sentence": index + 1, "reason": "script_explicitly_discarded", "script": group})
        if not candidates:
            issues.append({"sentence": index + 1, "reason": "no_mapped_takes", "script": group})
        groups.append({"sentence": index + 1, "script": deepcopy(group),
                       "text": cut.find.mapping.range_text(document["script"], group),
                       "discarded": is_discarded, "takes": candidates,
                       "selected_take_key": candidates[0]["take_key"] if candidates and not is_discarded else None})
    if sum(len(set(range(g["script"]["word_start"], g["script"]["word_end"] + 1)) & included) for g in groups) != len(included):
        raise ValueError("Incomplete extract-to-script range coverage.")
    return {"number": extract["number"], "title": extract["title"], "source": extract["source"],
            "formats": extract.get("formats", []),
            "passages": [dict(passage, script={"word_start": start, "word_end": end})
                         for passage, (start, end) in zip(extract["passages"], spans)],
            "groups": groups, "issues": issues,
            "omissions": omissions,
            "mapping_source": deepcopy(scores["source_mapping"])}


def score_paths_by_source(project, score_paths):
    supplied = {}
    for path in score_paths:
        match = cut.scoring.score_versions.NAME.fullmatch(Path(path).name)
        if not match:
            raise ValueError(f"Expected a numbered score file: {path}")
        source = match[1].casefold() + ".docx"
        if source in supplied:
            raise ValueError(f"Pass only one score version for {match[1]}.")
        supplied[source] = (project / path).resolve(strict=True)
    return supplied


def map_all(project, shortlist, score_paths):
    supplied = score_paths_by_source(project, score_paths)
    contexts = {}
    for source, path in supplied.items():
        scores = cut.read_json(path)
        document = cut.scoring.load_mapping(project, project / scores["source_mapping"]["path"])
        reference = cut.scoring.checked_reference(project, project / scores["source_mapping"]["path"], scores["source_mapping"])
        expected = cut.scoring.score_document(document, reference)
        if not cut.scoring.scores_match(scores, expected):
            raise ValueError(f"Stale scoring output: {path}")
        contexts[source] = (document, scores)
    result = []
    for extract in parse_shortlist(shortlist.read_text(encoding="utf-8-sig")):
        key = extract["source"].casefold()
        if key not in contexts:
            raise ValueError(f"Missing score file for {extract['source']}")
        result.append(map_extract(extract, *contexts[key]))
    return {"schema": "studio.extract-mappings", "schema_version": 1,
            "shortlist": cut.scoring.fingerprint(shortlist, project, "project"), "extracts": result}


def generate(project, shortlist, score_paths, padding_ms, omit_discarded, regenerate=False):
    approved_shortlist = project / cut.EXTRACTS / "APPROVED" / "Short-Form Excerpt Shortlist.md"
    if shortlist.resolve() != approved_shortlist.resolve():
        raise ValueError(f"Extract generation requires the approved shortlist: {approved_shortlist}")
    cut.require_prerequisites(project, cut.ROOT, "3.6")
    guard = StageValidation(project, cut.ROOT, {})
    cut.find.append_log(project / cut.STAGE, "Extract projects started", [
        f"Shortlist: {shortlist}; padding_ms={padding_ms}; omit_discarded={omit_discarded}."])
    try:
        supplied = score_paths_by_source(project, score_paths)
        latest = cut.scoring.score_versions.latest_scores(project / cut.scoring.STAGE)
        for source, path in supplied.items():
            if latest.get(Path(source).stem, (None, None))[1] != path:
                raise ValueError(f"Pass the latest stage 3.4 scores for {source}.")
        mappings = map_all(project, shortlist, score_paths)
        planned, reports, contexts, batches = [], [], {}, {}
        face_paths, face_inputs = None, []
        for relative, name, portrait, mapped in variants(mappings["extracts"]):
            for candidate in (relative, relative.parent / "Extract Mappings.json"):
                if any((project / part).is_symlink() for part in (candidate, *candidate.parents)):
                    raise ValueError(f"Redirected extract artifact: {candidate}")
            output = guard.confined(relative)
            mapping_path = guard.confined(relative.parent / "Extract Mappings.json")
            if mapping_path not in batches:
                batch = cut.read_json(mapping_path) if mapping_path.exists() else {
                    "schema": mappings["schema"], "schema_version": mappings["schema_version"],
                    "shortlist": mappings["shortlist"], "projects": [], "extracts": []}
                if (batch.get("schema") != "studio.extract-mappings" or batch.get("schema_version") != 1
                        or not all(isinstance(batch.get(key), list) for key in ("projects", "extracts"))):
                    raise ValueError(f"Invalid extract provenance: {mapping_path}")
                batches[mapping_path] = (batch, [])
            batch, updates = batches[mapping_path]
            prior = [row for row in batch["projects"] if row["extract_number"] == mapped["number"]]
            saved = [row for row in batch["extracts"] if row["number"] == mapped["number"]]
            if output.exists() and not regenerate:
                if (len(prior) != 1 or len(saved) != 1 or prior[0]["script"] != name
                        or selection(saved[0]) != selection(mapped)):
                    raise ValueError(f"Existing extract needs provenance review: {output}")
                if "draft" in prior[0] and (prior[0]["draft"]["path"] != relative.as_posix()
                                            or not matches_entry(prior[0]["draft"], project, cut.ROOT)):
                    raise ValueError(f"Existing extract draft changed: {output}")
                if "draft" in prior[0]:
                    inputs = prior[0].get("generation_inputs", [])
                    score = supplied[mapped["source"].casefold()].relative_to(project).as_posix()
                    if (score not in {item["path"] for item in inputs}
                            or not all(matches_entry(item, project, cut.ROOT) for item in inputs)):
                        raise ValueError(f"Existing extract uses changed generation inputs: {output}")
                continue
            if portrait and face_paths is None:
                face_paths, face_inputs = cut.portrait.load_paths(cut, project)
            discarded_sentences = {g["sentence"] for g in mapped["groups"] if g["discarded"]}
            unresolved = [issue for issue in mapped["issues"]
                          if not (omit_discarded and issue["sentence"] in discarded_sentences
                                  and issue["reason"] in ("script_explicitly_discarded", "no_mapped_takes"))]
            if unresolved:
                raise ValueError(f"Extract {mapped['number']} requires review: {unresolved}")
            mapped["omitted_discarded_sentences"] = sorted(discarded_sentences) if omit_discarded else []
            if mapped["source"] not in contexts:
                score_path = supplied[mapped["source"].casefold()]
                scores = cut.read_json(score_path)
                source_path = project / scores["source_mapping"]["path"]
                versions = cut.find.mapping.takes_versions(project, Path(mapped["source"]).stem + "__Takes")
                if not versions or source_path.resolve() != versions[-1][1].resolve():
                    raise ValueError("Extract scores must reference the latest mapping version.")
                document = cut.scoring.load_mapping(project, source_path)
                script = project / document["script"]["source"]["path"]
                transcripts = [project / r["transcript"]["source"]["path"] for r in document["recordings"]]
                check = cut.find.check_mapping(project, source_path, cut.find.sources.load_script(project, script),
                                              [cut.find.sources.load_transcript(project, p) for p in transcripts])
                if not check["coverage"]["complete"]:
                    raise ValueError(f"Incomplete source mapping: {source_path}")
                inputs = [fingerprint(p, project, "project") for p in (score_path, source_path, script, *transcripts)]
                syncs, probes = {}, {}
                for number in sorted({t["recording_number"] for t in scores["takes"]}):
                    sync_path = project / cut.SYNC / f"{script.stem}__C{number:02d}.json"
                    sync = cut.read_json(sync_path)
                    if (sync.get("reference_role") != "camA" or
                            sync.get("offset_convention") != "positive_means_file_starts_later_than_camA" or
                            set(sync["clips"]) != set(cut.ROLES) or sync["clips"]["camA"]["offset_ms"] != 0):
                        raise ValueError("Unsupported sync convention.")
                    syncs[number], probes[number] = sync, {}
                    inputs.append(fingerprint(sync_path, project, "project"))
                    for role, clip in sync["clips"].items():
                        path = (project / clip["path"]).resolve(strict=True)
                        actual = cut.media_fingerprint(path, project)
                        if (not path.is_relative_to(project / cut.INTAKE) or not cut.matches_identity(path, clip)
                                or type(clip["offset_ms"]) is not int):
                            raise ValueError(f"Changed/invalid sync source: {path}")
                        probes[number][role] = cut.probe(path)
                        inputs.append(actual)
                contexts[mapped["source"]] = (document, scores, syncs, probes), inputs
            included = {cut.find.mapping.range_key(g["script"]) for g in mapped["groups"] if not g["discarded"]}
            context, inputs = contexts[mapped["source"]]
            xml, report = cut.build_xml(project, output, name, *context, padding_ms, included,
                                         face_paths=face_paths if portrait else None)
            report["extract_number"] = mapped["number"]
            report["source_script"] = mapped["source"]
            report["omitted_discarded_sentences"] = mapped["omitted_discarded_sentences"]
            report["draft"] = {"scope": "project", "path": relative.as_posix(), "method": "sha256",
                               "sha256": cut.find.fingerprints.digest_bytes(xml, text=True)}
            source_prefix = Path(mapped["source"]).stem.casefold() + "__"
            report["generation_inputs"] = inputs + ([item for item in face_inputs
                                                     if Path(item["path"]).name.casefold().startswith(source_prefix)]
                                                    if portrait else [])
            reports.append(report)
            planned.append((output, xml))
            updates.append((mapped, report))
        for batch, updates in batches.values():
            numbers = {mapped["number"] for mapped, _ in updates}
            batch["projects"] = [row for row in batch["projects"] if row["extract_number"] not in numbers]
            batch["extracts"] = [row for row in batch["extracts"] if row["number"] not in numbers]
            for mapped, report in updates:
                batch["projects"].append(report)
                batch["extracts"].append(mapped)
        if not planned:
            return []
        if regenerate:
            from scripts.stage_invalidation import prepare_regeneration
            replacing = [p for p, _ in planned if p.exists()]
            replacing += [p for p, (_, updates) in batches.items() if updates and p.exists()]
            if replacing:
                prepare_regeneration(project, cut.ROOT, "3.6", replacing,
                                     "Explicit extract project regeneration.", append_log=cut.find.append_log)
        cut.find.invalidate(project, (3, 6), "Extract projects changed. Edited FCPXML in APPROVED required.")
        # Publish provenance first. If an XML write is interrupted, a retry can
        # fill the missing draft without confusing it with a legacy draft.
        for path, (batch, updates) in batches.items():
            if updates:
                cut.find.write_json(path, batch, overwrite=path.exists())
        for path, content in planned:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(content)
            if path.read_bytes() != content:
                raise ValueError(f"Persisted output mismatch: {path}")
        (project / cut.STAGE / "APPROVED").mkdir(exist_ok=True)
        cut.find.append_log(project / cut.STAGE, "Extract projects ready for review", [
            "Outputs: " + json.dumps([cut.scoring.fingerprint(p, project, "project") for p, _ in planned]),
            "Omitted discarded sentences: " + json.dumps({p["extract_number"]: p["omitted_discarded_sentences"] for p in reports}),
            "All extract sentences matched verbatim, in unique source order, to reviewed script group ranges and scored takes.",
            "No DONE written; edit the extract projects in Final Cut, then export same-stem edited FCPXML to "
            "3-Post Production/6-First Cut/APPROVED/."])
        return reports
    except (OSError, ValueError, TypeError, KeyError, cut.subprocess.SubprocessError) as error:
        cut.find.invalidate(project, (3, 6), f"Extract projects failed: {error}")
        cut.find.append_log(project / cut.STAGE, "Extract projects failed", [str(error), "Existing source files preserved."])
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--shortlist", required=True, type=Path)
    parser.add_argument("--scores", required=True, nargs="+", type=Path)
    parser.add_argument("--generate", action="store_true", help="Write separate FCPXML projects and exact sentence/take mapping JSON")
    parser.add_argument("--regenerate", action="store_true", help="Archive selected generated extract outputs before replacing")
    parser.add_argument("--padding-ms", type=int, default=50)
    parser.add_argument("--omit-discarded", action="store_true", help="Explicitly authorize omission of already discarded script groups")
    args = parser.parse_args()
    project = args.project_root.resolve(strict=True)
    if args.generate:
        if args.padding_ms < 0:
            parser.error("Padding must be nonnegative.")
        generated = generate(project, (project / args.shortlist).resolve(strict=True), args.scores,
                             args.padding_ms, args.omit_discarded, regenerate=args.regenerate)
        print(json.dumps([{k: v for k, v in p.items() if k != "selected"} for p in generated], indent=2))
    else:
        report = map_all(project, (project / args.shortlist).resolve(strict=True), args.scores)
        for extract in report["extracts"]:
            print(json.dumps({"number": extract["number"], "title": extract["title"],
                               "formats": extract["formats"], "sentences": len(extract["groups"]), "take_alternatives": sum(len(g["takes"]) for g in extract["groups"]),
                              "issues": extract["issues"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
