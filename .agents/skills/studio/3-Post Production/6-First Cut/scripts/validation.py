"""Edited single-project FCPXML exports attest First Cut approval."""
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from scripts import stage_completion as completion
from scripts.validation import StageValidation, recorded_fingerprints
from studio.fingerprints import matches_identity


class Validation(StageValidation):
    def validate_stage(self):
        source = self.definition["inputs"][0]
        scores = {re.sub(r"__Scores\.v1\.json$", "", path.name, flags=re.I).casefold()
                  for path in self.files(source)}
        expected = set()
        for rule in self.definition["inputs"]:
            if rule.get("role") != "project":
                continue
            stems = self.by_stem(self.files(rule))
            duplicates = expected.intersection(stems)
            if duplicates:
                raise ValueError(f"Duplicate generated First Cut stems: {sorted(duplicates)}")
            if rule["folder"] == self.definition["folder"] and scores - stems.keys():
                raise ValueError(f"Missing generated First Cut projects for scores: {sorted(scores - stems.keys())}")
            expected.update(stems)

        target = self.definition["outputs"][0]
        approved = self.by_stem(self.files(target))
        if expected - approved.keys():
            raise ValueError(f"Missing approved First Cut FCPXML for projects: {sorted(expected - approved.keys())}")
        if approved.keys() - expected:
            raise ValueError(f"Unexpected approved First Cut FCPXML: {sorted(approved.keys() - expected)}")
        for path in approved.values():
            try:
                root = ET.parse(path).getroot()
            except ET.ParseError as error:
                raise ValueError(f"Invalid approved First Cut FCPXML: {path.name}: {error}") from error
            projects = list(root.iter("project"))
            if root.tag != "fcpxml" or len(projects) != 1 or projects[0].find("sequence/spine") is None:
                raise ValueError(f"Approved FCPXML must contain one project with a timeline: {path.name}")
        self.validate_edit_provenance(approved, target)

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

    def validate_edit_provenance(self, approved, target):
        """An unchanged approval is not a new edit after generation inputs change."""
        directory = self.confined(self.definition["folder"])
        marker = directory / "DONE"
        if marker.is_symlink():
            raise ValueError("Redirected First Cut completion history")
        if not marker.exists():
            marker = completion.latest_archived_done(directory)
        if marker is None:
            return
        previous = json.loads(self.confined(marker).read_text(encoding="utf-8-sig"))
        if not isinstance(previous, dict) or not isinstance(previous.get("outputs"), list):
            raise NotImplementedError("First Cut history needs review before accepting existing edits")
        old_edits = []
        for entry in previous["outputs"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise NotImplementedError("Malformed historical First Cut output evidence needs review")
            path = completion.relative_path(entry["path"])
            if path.parent.as_posix() != target["folder"] or not re.fullmatch(target["filename_regex"], path.name, re.I):
                continue  # Movie-only history did not record approval of edited XML.
            digest = entry.get("sha256")
            if (entry.get("scope") != "project" or entry.get("method", "sha256") != "sha256"
                    or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest)):
                raise NotImplementedError("Historical First Cut edit fingerprint needs review")
            old_edits.append(entry)
        if not old_edits:
            return

        old_inputs = recorded_fingerprints({"inputs": previous.get("inputs")})
        if previous.get("completion_mode") == "manual-override":
            old_inputs = [entry for entry in old_inputs if entry.get("scope") == "project" and any(
                completion.relative_path(entry["path"]).parent.as_posix() == rule["folder"]
                and re.fullmatch(rule["filename_regex"], Path(entry["path"]).name, re.I)
                for rule in self.definition["inputs"])]
        if not old_inputs:
            raise NotImplementedError("Previous First Cut edits lack usable input provenance")
        current_inputs = recorded_fingerprints({"inputs": self.manifest.get("inputs")})
        key = lambda entry: (entry.get("scope"), entry["path"])
        if ({key(entry) for entry in old_inputs} == {key(entry) for entry in current_inputs}
                and all(completion.matches_entry(entry, self.project, self.workflow) for entry in old_inputs)):
            return
        for entry in old_edits:
            stem = Path(entry["path"]).stem.casefold()
            if stem in approved and matches_identity(approved[stem], entry):
                raise ValueError(f"Generation inputs changed; a new approved First Cut edit is required for {approved[stem].stem}")
