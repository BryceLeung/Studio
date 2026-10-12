#!/usr/bin/env python3
"""Run flat, script-named benchmark cases and score human-reviewed Takes files."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

import find_takes


def rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def metrics(correct: int, predicted: int, expected: int) -> dict:
    precision, recall = rate(correct, predicted), rate(correct, expected)
    return {"correct": correct, "predicted": predicted, "expected": expected,
            "precision": precision, "recall": recall,
            "f1": round(2 * precision * recall / (precision + recall), 6) if precision + recall else 0.0}


def link_key(link: dict) -> tuple:
    return (*find_takes.mapping.range_key(link["script"]), *find_takes.mapping.range_key(link["transcript"]))


def assignments(groups: list[dict], links: list[dict], discarded: list[dict], word_count: int) -> list:
    values = ["unreviewed"] * word_count
    for group in discarded:
        for index in range(group["word_start"], group["word_end"] + 1):
            values[index] = "discarded"
    for link in links:
        for index in range(link["transcript"]["word_start"], link["transcript"]["word_end"] + 1):
            values[index] = find_takes.mapping.range_key(link["script"])
    return values


def compare(project: Path, reference_path: Path, prediction_path: Path) -> dict:
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    find_takes.mapping.validate_document(project, reference, verify_inputs=False)
    for panel in ("script", "transcript"):
        reference_source = reference["sources"][panel]
        path = find_takes.sources.project_path(project, reference_source["source"]["path"])
        if find_takes.sha256(path) != reference_source["source"]["sha256"]:
            raise ValueError(f"Benchmark {panel} bytes differ from the reviewed source.")
        actual = (find_takes.sources.load_script if panel == "script" else find_takes.sources.load_transcript)(project, path)
        fields = ("index", "text") if panel == "script" else ("index", "text", "start_ms", "end_ms")
        if len(actual["words"]) != len(reference_source["words"]) or any(
                any(word.get(field) != saved.get(field) for field in fields)
                for word, saved in zip(actual["words"], reference_source["words"])):
            raise ValueError("Benchmark source word indexes, text, or timings differ from the review.")
    source = reference["sources"]["script"]
    prediction = json.loads(prediction_path.read_text(encoding="utf-8"))
    transcript = reference["sources"]["transcript"]
    recording = next(r for r in prediction["recordings"]
                     if r["transcript"]["source"]["path"] == transcript["source"]["path"])
    expected = {link_key(link): link for link in reference["links"]}
    predicted = {link_key(link): link for link in recording["links"]}
    common = expected.keys() & predicted.keys()
    total_words = len(transcript["words"])
    human_words = assignments(reference["groupings"]["transcript"], reference["links"], reference["discarded"], total_words)
    predicted_words = assignments(recording["groupings"], recording["links"], recording["discarded"], total_words)
    correct_words = sum(a == b for a, b in zip(human_words, predicted_words))
    expected_discards = {i for i, value in enumerate(human_words) if value == "discarded"}
    predicted_discards = {i for i, value in enumerate(predicted_words) if value == "discarded"}

    def describe(link: dict) -> dict:
        return {**link, "script_text": find_takes.mapping.range_text(source, link["script"]),
                "transcript_text": find_takes.mapping.range_text(transcript, link["transcript"])}

    return {
        "reference": f"{project.name}/{reference_path.name}",
        "reference_sha256": find_takes.sha256(reference_path),
        "script": source["source"], "transcript": transcript["source"],
        "algorithm": prediction["algorithm"],
        "exact_take_ranges": metrics(len(common), len(predicted), len(expected)),
        "classification_agreement_on_exact_ranges": {
            "correct": sum(expected[key]["classification"] == predicted[key]["classification"] for key in common),
            "compared": len(common)},
        "word_assignments": {"correct": correct_words, "total": total_words, "accuracy": rate(correct_words, total_words)},
        "discarded_words": metrics(len(expected_discards & predicted_discards), len(predicted_discards), len(expected_discards)),
        "script_group_coverage": {
            "expected": len({find_takes.mapping.range_key(link["script"]) for link in expected.values()}),
            "predicted": len({find_takes.mapping.range_key(link["script"]) for link in predicted.values()}),
            "total": len(reference["groupings"]["script"])},
        "missing_or_different": [describe(expected[key]) for key in sorted(expected.keys() - predicted.keys())],
        "extra_or_different": [describe(predicted[key]) for key in sorted(predicted.keys() - expected.keys())],
        "word_errors": [{"index": i, "word": transcript["words"][i]["text"],
                         "expected": human_words[i], "predicted": predicted_words[i]}
                        for i in range(total_words) if human_words[i] != predicted_words[i]],
    }


def comparison_lines(report: dict) -> list[str]:
    exact = report["exact_take_ranges"]
    words = report["word_assignments"]
    quality = report["classification_agreement_on_exact_ranges"]
    lines = [
        f"Reference: `{report['reference']}`; recording: `{Path(report['transcript']['path']).name}`.", "",
        f"- Exact script/transcript word ranges: {exact['correct']} of {exact['expected']} reference takes; {exact['predicted']} predicted takes.",
        f"- Take precision: {exact['precision']:.1%}; recall: {exact['recall']:.1%}; F1: {exact['f1']:.1%}.",
        f"- Word assignments (script grouping or discard): {words['correct']} / {words['total']} ({words['accuracy']:.1%}).",
        f"- Quality labels agree on {quality['correct']} / {quality['compared']} exact-range matches.",
        "", "### Missing or different reference takes", "",
    ]
    for link in report["missing_or_different"]:
        lines.append(f"- Script words {link['script']['word_start']}–{link['script']['word_end']}, transcript words {link['transcript']['word_start']}–{link['transcript']['word_end']}: {link['transcript_text']}")
    if not report["missing_or_different"]:
        lines.append("None.")
    lines.extend(("", "### Extra or different predictions", ""))
    for link in report["extra_or_different"]:
        lines.append(f"- Script words {link['script']['word_start']}–{link['script']['word_end']}, transcript words {link['transcript']['word_start']}–{link['transcript']['word_end']}: {link['transcript_text']}")
    if not report["extra_or_different"]:
        lines.append("None.")
    return lines


def summary(report: dict) -> str:
    lines = ["# Find Takes benchmark", ""]
    for case in report["cases"]:
        lines.extend((f"## {case['case']}", ""))
        lines.extend(comparison_lines(case))
        lines.append("")
    lines.extend(("Scores are text similarity measures, not probabilities. The first comparison report remains preserved under reports/initial/.", ""))
    return "\n".join(lines)


def load_cases(fixture: Path, name: str | None = None) -> tuple[dict, list[dict]]:
    manifest = json.loads((fixture / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "studio.find-takes.benchmark" or manifest.get("schema_version") != 2:
        raise ValueError("Unsupported benchmark manifest.")
    cases = manifest["cases"]
    if name is not None:
        cases = [case for case in cases if case["name"] == name]
    if not cases:
        raise ValueError("No matching benchmark cases.")
    seen = set()
    for case in cases:
        if (not isinstance(case["name"], str) or case["name"] in ("", ".", "..")
                or Path(case["name"]).name != case["name"] or "\\" in case["name"] or case["name"] in seen):
            raise ValueError("Each benchmark needs a unique script-named case directory.")
        seen.add(case["name"])
        expected_takes = re.fullmatch(re.escape(case["name"]) + r"__Takes\.v[0-9]+\.json", case["takes"])
        if case["script"] != f"{case['name']}.docx" or not expected_takes or not case["transcripts"]:
            raise ValueError("Benchmark filenames must match the case's script name.")
        for filename in case["transcripts"]:
            if not re.fullmatch(re.escape(case["name"]) + r"__C\d{2,}__lav\.json", filename):
                raise ValueError("Unexpected benchmark transcript filename.")
        if case["takes_role"] != "human-reviewed":
            raise ValueError("Only human-reviewed Takes files belong in benchmarks.")
        if case["transcripts"] != case["reviewed_transcripts"]:
            raise ValueError("Every benchmark transcript must have a human-reviewed mapping.")
    return manifest, cases


def generate_case(fixture: Path, case: dict, output_dir: Path) -> dict:
    project = (fixture / case["name"]).resolve(strict=True)
    script = find_takes.sources.load_script(project, project / case["script"])
    transcripts = [find_takes.sources.load_transcript(project, project / name) for name in case["transcripts"]]
    algorithm = find_takes.provenance()
    document = {
        "schema": find_takes.SCHEMA, "schema_version": find_takes.VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "algorithm": algorithm, "script": script,
        "script_groupings": find_takes.mapping.initial_groupings(script),
        "recordings": [find_takes.match_recording(script, transcript) for transcript in transcripts],
    }
    find_takes.validate_takes(project, document, script, transcripts, algorithm)
    target = output_dir / case["name"] / case["takes"]
    find_takes.write_json(target, document)
    return {"path": target.relative_to(output_dir).as_posix(), "status": "generated",
            "takes": sum(len(recording["links"]) for recording in document["recordings"])}


def run(fixture: Path, output_dir: Path, case_name: str | None = None) -> dict:
    fixture = fixture.resolve(strict=True)
    output_dir = output_dir.resolve()
    if output_dir == fixture or fixture in output_dir.parents:
        raise ValueError("Predictions must be outside the frozen benchmark directory.")
    manifest, cases = load_cases(fixture, case_name)
    # Generate every prediction before reading any expected mappings.
    generated = [generate_case(fixture, case, output_dir) for case in cases]
    results = []
    fingerprints = {entry["path"]: entry["sha256"] for entry in manifest["files"]}
    for case in cases:
        project = fixture / case["name"]
        for filename in (case["script"], *case["transcripts"], case["takes"]):
            relative = f"{case['name']}/{filename}"
            if find_takes.sha256(fixture / relative) != fingerprints[relative]:
                raise ValueError(f"Frozen benchmark file changed: {relative}")
        reference_path = project / case["takes"]
        reference = json.loads(reference_path.read_text(encoding="utf-8"))
        reviewed = [reference["sources"]["transcript"]["source"]["path"]]
        if reviewed != case["reviewed_transcripts"]:
            raise ValueError("Reviewed transcript inventory differs from the benchmark manifest.")
        result = compare(project, reference_path, output_dir / case["name"] / case["takes"])
        results.append({"case": case["name"], "status": "compared", **result})
    return {"generation": {"scripts": len(cases), "transcripts": sum(len(case["transcripts"]) for case in cases),
                           "outputs": generated}, "cases": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=Path(__file__).resolve().parents[1] / "benchmarks")
    parser.add_argument("--case", help="Run only the named script's benchmark case.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        fixture = args.fixture.resolve(strict=True)
        report_path = args.report.resolve()
        if report_path == fixture or fixture in report_path.parents:
            raise ValueError("Comparison reports must be outside the frozen benchmark directory.")
        report = run(args.fixture, args.output_dir, args.case)
        find_takes.write_json(args.report, report)
        args.report.with_suffix(".md").write_text(summary(report), encoding="utf-8")
        print(summary(report))
        return 0
    except (OSError, ValueError, KeyError, TypeError, StopIteration) as error:
        print(f"Benchmark: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
