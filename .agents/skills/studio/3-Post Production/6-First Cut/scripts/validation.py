"""Edited single-project FCPXML exports attest First Cut approval."""
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from scripts import stage_completion as completion
from scripts.validation import StageValidation, recorded_fingerprints
from studio.first_cut_exports import export_stem, exports, matches_rule
from studio.first_cut_plan import SHORTLIST, parse_shortlist, selection, variants
from studio.fingerprints import matches_identity
from studio.score_versions import latest_scores


class Validation(StageValidation):
    def validate_stage(self):
        source = self.definition["inputs"][0]
        scores = latest_scores(self.confined(source["folder"]))
        expected = {}
        for rule in self.definition["inputs"]:
            if rule.get("role") != "project":
                continue
            stems = self.by_stem(self.files(rule))
            duplicates = expected.keys() & stems.keys()
            if duplicates:
                raise ValueError(f"Duplicate generated First Cut stems: {sorted(duplicates)}")
            if rule["folder"] == self.definition["folder"]:
                if scores.keys() - stems.keys():
                    raise ValueError(f"Missing generated First Cut projects for scores: {sorted(scores.keys() - stems.keys())}")
                if stems.keys() - scores.keys():
                    raise ValueError(f"Unexpected generated full-script projects: {sorted(stems.keys() - scores.keys())}")
            expected.update(stems)

        extracts = {path.stem.casefold(): (path, name, portrait, selection(extract))
                    for path, name, portrait, extract in variants(
                        parse_shortlist(self.confined(SHORTLIST).read_text(encoding="utf-8-sig")))}
        for stem, (path, _, _, _) in extracts.items():
            if stem not in expected or expected[stem].relative_to(self.project) != path:
                raise ValueError(f"Missing generated First Cut project: {path}")
        unexpected = [str(path.relative_to(self.project)) for stem, path in expected.items()
                      if path.parent != self.confined(self.definition["folder"]) and stem not in extracts]
        if unexpected:
            raise ValueError(f"Unexpected generated extract projects: {unexpected}")
        selections = {path.as_posix(): chosen for path, _, _, chosen in extracts.values()}
        if self.manifest.get("extract_selections", selections) != selections:
            raise ValueError("Recorded extract selections differ from the approved shortlist")

        target = self.definition["outputs"][0]
        approved = exports(self.project, target)
        if expected.keys() - approved.keys():
            raise ValueError(f"Missing approved First Cut FCPXML for projects: {sorted(expected.keys() - approved.keys())}")
        if approved.keys() - expected.keys():
            raise ValueError(f"Unexpected approved First Cut FCPXML: {sorted(approved.keys() - expected.keys())}")
        self.require_inventory("outputs", approved.values(), exact=True)
        for path in approved.values():
            try:
                root = ET.parse(path).getroot()
            except ET.ParseError as error:
                raise ValueError(f"Invalid approved First Cut FCPXML: {path.relative_to(self.project)}: {error}") from error
            projects = list(root.iter("project"))
            if root.tag != "fcpxml" or len(projects) != 1 or projects[0].find("sequence/spine") is None:
                raise ValueError(f"Approved FCPXML must contain one project with a timeline: {path.relative_to(self.project)}")
        self.validate_edit_provenance(approved, target, expected, extracts, scores)

    def files(self, rule):
        folder = self.confined(rule["folder"])
        if folder.is_dir():
            for path in sorted(folder.iterdir()):
                if path.is_file() and re.fullmatch(rule["filename_regex"], path.name, re.I):
                    if path.is_symlink():
                        raise ValueError(f"Redirected First Cut file: {path}")
                    yield self.confined(path)

    @staticmethod
    def by_stem(paths):
        result = {}
        for path in paths:
            stem = path.stem.casefold()
            if stem in result:
                raise ValueError(f"Duplicate First Cut project stem: {path.stem}")
            result[stem] = path
        return result

    def report(self, extract):
        path, name, _, chosen = extract
        report = self.confined(path.parent / "Extract Mappings.json")
        if not report.is_file():
            return None, None
        content = json.loads(report.read_text(encoding="utf-8-sig"))
        rows = [r for r in content["projects"] if r["script"] == name and r["extract_number"] == chosen["number"]
                and r["source_script"] == chosen["source"]]
        mappings = [r for r in content["extracts"] if r["number"] == chosen["number"]]
        if len(rows) != 1 or len(mappings) != 1 or selection(mappings[0]) != chosen:
            raise ValueError(f"First Cut extract provenance does not match the shortlist: {path.stem}")
        return content, rows[0]

    def validate_edit_provenance(self, approved, target, drafts, extracts, scores):
        """An unchanged approval is not a new edit after generation inputs change."""
        directory = self.confined(self.definition["folder"])
        marker = directory / "DONE"
        if marker.is_symlink():
            raise ValueError("Redirected First Cut completion history")
        if not marker.exists():
            marker = completion.latest_archived_done(directory)
        previous = (json.loads(self.confined(marker).read_text(encoding="utf-8-sig"))
                    if marker is not None else {"outputs": []})
        if not isinstance(previous, dict) or not isinstance(previous.get("outputs"), list):
            raise NotImplementedError("First Cut history needs review before accepting existing edits")
        old_edits = []
        for entry in previous["outputs"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise NotImplementedError("Malformed historical First Cut output evidence needs review")
            path = completion.relative_path(entry["path"])
            if not matches_rule(path, target):
                continue  # Movie-only history did not record approval of edited XML.
            digest = entry.get("sha256")
            if (entry.get("scope") != "project" or entry.get("method", "sha256") != "sha256"
                    or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)):
                raise NotImplementedError("Historical First Cut edit fingerprint needs review")
            old_edits.append(entry)
        old_inputs = recorded_fingerprints({"inputs": previous.get("inputs", [])})
        if previous.get("completion_mode") == "manual-override":
            old_inputs = [entry for entry in old_inputs if entry.get("scope") == "project" and any(
                completion.relative_path(entry["path"]).parent.as_posix() == rule["folder"]
                and re.fullmatch(rule["filename_regex"], Path(entry["path"]).name, re.I)
                for rule in self.definition["inputs"])]
        if old_edits and not old_inputs:
            raise NotImplementedError("Previous First Cut edits lack usable input provenance")
        current_inputs = recorded_fingerprints({"inputs": self.manifest.get("inputs")})
        old_by_path = {entry["path"]: entry for entry in old_inputs if entry.get("scope") == "project"}
        reports = {}
        for stem, extract in extracts.items():
            report, row = reports[stem] = self.report(extract)
            source = Path(extract[3]["source"]).stem.casefold()
            if source not in scores:
                raise ValueError(f"Missing current scores for First Cut project: {drafts[stem].stem}")
            if row is not None and "draft" in row:
                draft = row["draft"]
                inputs = row.get("generation_inputs")
                score_path = scores[source][1].relative_to(self.project).as_posix()
                current = self.relevant_inputs(current_inputs, source, extract[2])
                if (draft.get("scope") != "project" or draft["path"] != extract[0].as_posix()
                        or not isinstance(inputs, list) or not inputs
                        or score_path not in {item["path"] for item in inputs}
                        or any(key in current and current[key]["path"] != item["path"]
                               for key, item in self.relevant_inputs(inputs, source, extract[2]).items())
                        or not all(completion.matches_entry(item, self.project, self.workflow)
                                   for item in (draft, *inputs))):
                    raise ValueError(f"Generated First Cut inputs or draft changed; inspect before replacing: {drafts[stem].stem}")
            if marker is not None and extract[0].as_posix() not in old_by_path and (row is None or "draft" not in row):
                raise ValueError(f"Missing generation provenance for new First Cut project: {drafts[stem].stem}")

        for entry in old_edits:
            stem = export_stem(Path(entry["path"])).casefold()
            if stem not in approved or not matches_identity(approved[stem], entry):
                continue
            path = drafts[stem].relative_to(self.project).as_posix()
            extract = extracts.get(stem)
            source = Path(extract[3]["source"]).stem if extract else drafts[stem].stem
            portrait = bool(extract and extract[2])
            old_relevant = self.relevant_inputs(old_inputs, source, portrait)
            current_relevant = self.relevant_inputs(current_inputs, source, portrait)
            draft = old_by_path.get(path)
            reason = None
            if draft is None or not completion.matches_entry(draft, self.project, self.workflow):
                reason = "draft changed or is missing from the previous approval evidence"
            elif not old_relevant or {item["path"] for item in old_relevant.values()} != {
                item["path"] for item in current_relevant.values()
            } or not all(
                completion.matches_entry(item, self.project, self.workflow) for item in old_relevant.values()
            ):
                reason = "relevant generation inputs changed"
            elif extract:
                report, _ = reports[stem]
                old_shortlist = old_by_path.get(SHORTLIST.as_posix())
                saved_selections = previous.get("extract_selections", {})
                if path in saved_selections:
                    if saved_selections[path] != extract[3]:
                        reason = "approved extract selection changed"
                elif not (old_shortlist and (completion.matches_entry(old_shortlist, self.project, self.workflow)
                          or (report and report.get("shortlist", {}).get("sha256") == old_shortlist.get("sha256")))):
                    reason = "legacy evidence cannot verify the approved extract selection"
            if reason:
                raise ValueError(f"A new approved First Cut edit is required for {export_stem(approved[stem])}: {reason}")

    @staticmethod
    def relevant_inputs(entries, source, portrait):
        """Use the current version of each source-specific input, not its version history."""
        by_key = {}
        prefix = source.casefold()
        for entry in entries:
            if entry.get("scope") != "project":
                continue
            path = Path(entry["path"])
            if path.parent.is_relative_to(SHORTLIST.parent) or "6-First Cut" in path.parts:
                continue
            if path.stem.casefold() != prefix and not path.name.casefold().startswith(prefix + "__"):
                # The shared 3.5 gate verifies the index. Each portrait consumes
                # its own camera paths, not the index or other scripts' paths.
                continue
            if not portrait and "5-Face Tracking" in path.parts:
                continue
            match = re.fullmatch(r"(.+)\.v([1-9]\d*)\.json", path.name, re.I)
            key = (path.parent.as_posix(), (match[1] + ".json" if match else path.name).casefold())
            version = int(match[2]) if match else 0
            if key not in by_key or by_key[key][0] < version:
                by_key[key] = version, entry
        return {key: entry for key, (_, entry) in by_key.items()}
