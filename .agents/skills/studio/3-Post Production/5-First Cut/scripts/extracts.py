#!/usr/bin/env python3
"""Map approved extract passages to source script groups and scored take identities."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re

import first_cut as cut


def parse_shortlist(text):
    extracts = []
    for match in re.finditer(r"^## (\d+)\. ([^\n]+)\n(.*?)(?=^## |\Z)", text, re.M | re.S):
        number, title, body = match.groups()
        status = re.search(r"\*\*Review status:\*\* (.+)", body)
        if not status or status.group(1).strip() != "Approved":
            continue
        source = re.search(r"\*\*Source:\*\* (.+)", body)
        if not source or "### Provisional spoken script" not in body:
            raise ValueError(f"Extract {number} lacks source or spoken script.")
        passages, jump = [], False
        spoken = body.split("### Provisional spoken script", 1)[1]
        for paragraph in re.split(r"\n\s*\n", spoken.strip()):
            if paragraph.strip() == "> [PROPOSED JUMP]":
                jump = True
                continue
            if paragraph.startswith(">"):
                raise ValueError(f"Unknown extract directive: {paragraph}")
            passages.append({"text": paragraph.strip(), "jump_before": jump})
            jump = False
        if not passages or jump:
            raise ValueError(f"Empty extract or trailing jump: {number}")
        extracts.append({"number": int(number), "title": title.strip(),
                         "source": source.group(1).strip(), "passages": passages})
    if not extracts:
        raise ValueError("No approved extracts found.")
    return extracts


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
            "passages": [dict(passage, script={"word_start": start, "word_end": end})
                         for passage, (start, end) in zip(extract["passages"], spans)],
            "groups": groups, "issues": issues,
            "omissions": omissions,
            "mapping_source": deepcopy(scores["source_mapping"])}


def map_all(project, shortlist, score_paths):
    supplied = {Path(p).name.removesuffix("__Scores.v1.json") + ".docx": (project / p).resolve(strict=True)
                for p in score_paths}
    contexts = {}
    for source, path in supplied.items():
        scores = cut.read_json(path)
        document = cut.scoring.load_mapping(project, project / scores["source_mapping"]["path"])
        expected = cut.scoring.score_document(document, cut.scoring.fingerprint(project / scores["source_mapping"]["path"], project, "project"))
        if expected != scores:
            raise ValueError(f"Stale scoring output: {path}")
        contexts[source] = (document, scores)
    result = []
    for extract in parse_shortlist(shortlist.read_text(encoding="utf-8-sig")):
        if extract["source"] not in contexts:
            raise ValueError(f"Missing score file for {extract['source']}")
        result.append(map_extract(extract, *contexts[extract["source"]]))
    return {"schema": "studio.extract-mappings", "schema_version": 1,
            "shortlist": cut.scoring.fingerprint(shortlist, project, "project"), "extracts": result}


def generate(project, shortlist, score_paths, padding_ms, omit_discarded):
    directory = project / cut.STAGE / "Extracts"
    cut.find.append_log(project / cut.STAGE, "Extract projects started", [
        f"Shortlist: {shortlist}; padding_ms={padding_ms}; omit_discarded={omit_discarded}."])
    try:
        # Read-only validation: an extract run must not alter upstream completion.
        for folder in (cut.find.APPROVED.parent, cut.EXTRACTS, cut.INTAKE, cut.SYNC, cut.find.OUTPUT, cut.scoring.STAGE):
            cut.validate_done(project, folder)
        cut.find.validate_approved(project)
        mappings = map_all(project, shortlist, score_paths)
        planned, reports, inputs, contexts = [], [], [], {}
        for mapped in mappings["extracts"]:
            discarded_sentences = {g["sentence"] for g in mapped["groups"] if g["discarded"]}
            unresolved = [issue for issue in mapped["issues"]
                          if not (omit_discarded and issue["sentence"] in discarded_sentences
                                  and issue["reason"] in ("script_explicitly_discarded", "no_mapped_takes"))]
            if unresolved:
                raise ValueError(f"Extract {mapped['number']} requires review: {unresolved}")
            mapped["omitted_discarded_sentences"] = sorted(discarded_sentences) if omit_discarded else []
            if mapped["source"] not in contexts:
                score_path = next((project / p).resolve(strict=True) for p in score_paths
                                  if Path(p).name == Path(mapped["source"]).stem + "__Scores.v1.json")
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
                inputs.extend(cut.scoring.fingerprint(p, project, "project") for p in (score_path, source_path, script, *transcripts))
                syncs, probes = {}, {}
                for number in sorted({t["recording_number"] for t in scores["takes"]}):
                    sync_path = project / cut.SYNC / f"{script.stem}__C{number:02d}.json"
                    sync = cut.read_json(sync_path)
                    if (sync.get("reference_role") != "camA" or
                            sync.get("offset_convention") != "positive_means_file_starts_later_than_camA" or
                            set(sync["clips"]) != set(cut.ROLES) or sync["clips"]["camA"]["offset_ms"] != 0):
                        raise ValueError("Unsupported sync convention.")
                    syncs[number], probes[number] = sync, {}
                    inputs.append(cut.scoring.fingerprint(sync_path, project, "project"))
                    for role, clip in sync["clips"].items():
                        path = (project / clip["path"]).resolve(strict=True)
                        actual = cut.media_fingerprint(path, project)
                        if (not path.is_relative_to(project / cut.INTAKE) or actual["bytes"] != clip["size_bytes"]
                                or actual["mtime_ns"] != clip["mtime_ns"] or type(clip["offset_ms"]) is not int):
                            raise ValueError(f"Changed/invalid sync source: {path}")
                        probes[number][role] = cut.probe(path)
                        inputs.append(actual)
                contexts[mapped["source"]] = document, scores, syncs, probes
            included = {cut.find.mapping.range_key(g["script"]) for g in mapped["groups"] if not g["discarded"]}
            name = f"{mapped['number']:02d}-{mapped['title']}"
            safe_name = re.sub(r'[<>:"/\\|?*]', "-", name)
            output = directory / f"{safe_name}.FCPXML"
            xml, report = cut.build_xml(project, output, name, *contexts[mapped["source"]], padding_ms, included)
            report["extract_number"] = mapped["number"]
            report["source_script"] = mapped["source"]
            report["omitted_discarded_sentences"] = mapped["omitted_discarded_sentences"]
            reports.append(report)
            planned.append((output, xml))
        mappings["projects"] = reports
        mappings["inputs"] = inputs
        mappings["dependencies"] = [cut.scoring.fingerprint(p, cut.ROOT, "workflow") for p in
                                    (Path(__file__), Path(cut.__file__), Path(__file__).parents[1] / "SKILL.md")]
        mappings["review_required"] = "Import and review all six extract projects in Final Cut."
        mapping_path = directory / "Extract Mappings.json"
        mapping_bytes = (json.dumps(mappings, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        planned.append((mapping_path, mapping_bytes))
        for path, content in planned:
            if path.exists() and path.read_bytes() != content:
                raise ValueError(f"Existing extract output differs; inspect before replacing: {path}")
        directory.mkdir(parents=True, exist_ok=True)
        for path, content in planned:
            if not path.exists():
                with path.open("xb") as stream:
                    stream.write(content)
            if path.read_bytes() != content:
                raise ValueError(f"Persisted output mismatch: {path}")
        cut.find.append_log(project / cut.STAGE, "Extract projects ready for review", [
            "Outputs: " + json.dumps([cut.scoring.fingerprint(p, project, "project") for p, _ in planned]),
            "Extract 6 omits previously discarded script sentence 109 with user's explicit authorization; shortlist and source mappings preserved.",
            "All extract sentences matched verbatim, in unique source order, to reviewed script group ranges and scored takes.",
            "No DONE written; import/playback review pending."])
        return reports
    except (OSError, ValueError, TypeError, KeyError) as error:
        cut.find.append_log(project / cut.STAGE, "Extract projects failed", [str(error), "Existing source files preserved."])
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--shortlist", required=True, type=Path)
    parser.add_argument("--scores", required=True, nargs="+", type=Path)
    parser.add_argument("--generate", action="store_true", help="Write separate FCPXML projects and exact sentence/take mapping JSON")
    parser.add_argument("--padding-ms", type=int, default=50)
    parser.add_argument("--omit-discarded", action="store_true", help="Explicitly authorize omission of already discarded script groups")
    args = parser.parse_args()
    project = args.project_root.resolve(strict=True)
    report = map_all(project, (project / args.shortlist).resolve(strict=True), args.scores)
    for extract in report["extracts"]:
        print(json.dumps({"number": extract["number"], "title": extract["title"],
                          "sentences": len(extract["groups"]), "take_alternatives": sum(len(g["takes"]) for g in extract["groups"]),
                          "issues": extract["issues"]}, ensure_ascii=False))
    if args.generate:
        if args.padding_ms < 0:
            parser.error("Padding must be nonnegative.")
        generated = generate(project, (project / args.shortlist).resolve(strict=True), args.scores,
                             args.padding_ms, args.omit_discarded)
        print(json.dumps([{k: v for k, v in p.items() if k != "selected"} for p in generated], indent=2))


if __name__ == "__main__":
    main()
