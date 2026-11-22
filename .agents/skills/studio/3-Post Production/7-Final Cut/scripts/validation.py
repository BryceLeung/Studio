"""Rendered movies in APPROVED attest that the Final Cut projects were rendered."""
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from scripts import stage_completion as completion
from scripts.validation import StageValidation, recorded_fingerprints
from studio.final_cut_animations import verify_sources
from studio.first_cut_exports import export_stem, exports, matches_rule
from studio.fingerprints import matches, matches_identity


class Validation(StageValidation):
    def validate_stage(self):
        sources = exports(self.project, self.definition["inputs"][0])
        self.require_inventory("inputs", sources.values())
        projects = self.files(self.definition["outputs"][0])
        report, = self.files(self.definition["outputs"][1])
        movies = self.files(self.definition["outputs"][2])
        expected = {key: export_stem(path) for key, path in sources.items()}
        generated = {path.stem.casefold() for path in projects}
        if len(generated) != len(projects):
            raise ValueError("Duplicate Final Cut project stems")
        if expected.keys() - generated or generated - expected.keys():
            raise ValueError(f"Final Cut project stems differ from approved First Cut XML: "
                             f"missing={sorted(expected.keys() - generated)}, extra={sorted(generated - expected.keys())}")
        self.validate_review(json.loads(report.read_text(encoding="utf-8-sig")), sources, projects)
        for path in projects:
            try:
                root = ET.parse(path).getroot()
            except ET.ParseError as error:
                raise ValueError(f"Invalid Final Cut FCPXML: {path.name}: {error}") from error
            if root.tag != "fcpxml" or len(list(root.iter("project"))) != 1:
                raise ValueError(f"Final Cut FCPXML must contain one project: {path.name}")
            verify_sources(root, self.project, path)

        if any(path.stat().st_size == 0 for path in movies):
            raise ValueError("Approved rendered movies must not be empty")
        approved = {path.stem.casefold() for path in movies}
        if expected.keys() - approved:
            raise ValueError(f"Missing approved rendered movies for projects: {sorted(expected.keys() - approved)}")
        if approved - expected.keys():
            raise ValueError(f"Unexpected approved rendered movies: {sorted(approved - expected.keys())}")
        self.validate_render_provenance(movies, expected)

    def files(self, rule):
        folder = self.confined(rule["folder"])
        paths = sorted(path for path in folder.iterdir()
                       if path.is_file() and re.fullmatch(rule["filename_regex"], path.name, re.I))
        if any(path.is_symlink() for path in paths):
            raise ValueError(f"Redirected file in Final Cut inventory: {rule['folder']}")
        return [self.confined(path) for path in paths]

    def validate_review(self, review, sources, projects):
        if (not isinstance(review, dict) or not isinstance(review.get("projects"), list)
                or not isinstance(review.get("browser"), list)):
            raise ValueError("Final Cut REVIEW.json must record project and animation provenance")
        output_by_stem = {path.stem.casefold(): path for path in projects}
        expected = {source.relative_to(self.project).as_posix(): (source, output_by_stem[key])
                    for key, source in sources.items()}
        for row in review["projects"]:
            source_output = expected.pop(row["input"], None)
            if source_output is None:
                raise ValueError(f"Unexpected or duplicate Final Cut review project: {row['input']}")
            source, output = source_output
            if (row["output"] != output.name or not matches(source, row["input_sha256"])
                    or not matches(output, row["output_sha256"])):
                raise ValueError(f"Final Cut generation evidence changed; regenerate {output.name}")
        if expected:
            raise ValueError(f"Final Cut REVIEW.json lacks projects: {sorted(expected)}")

        animation = next(item for item in self.definition["prerequisites"] if item["step"] == "2.3")
        marker = self.confined(Path(animation["folder"]) / "DONE")
        current = json.loads(marker.read_text(encoding="utf-8-sig"))
        if current.get("completion_mode") == "manual-override":
            return  # Override output watches do not attest 2.3's stage-owned media hashes.
        approved = {(entry["path"], entry["sha256"]) for entry in current["outputs"]}
        staged = {(row["path"], row["sha256"]) for row in review["browser"]}
        if staged != approved or len(staged) != len(review["browser"]):
            raise ValueError("Final Cut animation inventory differs from current 2.3 approvals; regenerate Final Cut")

    def render_inputs(self, manifest):
        """The render consumes generated XML, not REVIEW.json or workflow code."""
        sources = self.definition["inputs"]
        projects = self.definition["outputs"][0]
        return self.fingerprints(manifest, "inputs", sources) + self.fingerprints(manifest, "outputs", [projects])

    @staticmethod
    def fingerprints(manifest, section, rules):
        return [entry for entry in recorded_fingerprints({section: manifest.get(section)})
                 if entry.get("scope") == "project" and any(
                     matches_rule(completion.relative_path(entry["path"]), rule) for rule in rules)]

    def validate_render_provenance(self, movies, expected):
        """Preserved approved files are not new render evidence after inputs change."""
        directory = self.confined(self.definition["folder"])
        marker = directory / "DONE"
        if marker.is_symlink():
            raise ValueError("Redirected Final Cut completion history")
        if not marker.exists():
            marker = completion.latest_archived_done(directory)
        if marker is None:
            return
        previous = json.loads(self.confined(marker).read_text(encoding="utf-8-sig"))
        if not isinstance(previous, dict) or not isinstance(previous.get("outputs"), list):
            raise NotImplementedError("Final Cut history needs review before accepting existing renders")
        target = self.definition["outputs"][2]
        old_movies = []
        for entry in previous["outputs"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise NotImplementedError("Malformed historical Final Cut output evidence needs review")
            path = completion.relative_path(entry["path"])
            if not re.fullmatch(target["filename_regex"], path.name, re.I):
                continue
            if entry.get("scope") != "project" or path.parent.as_posix() != target["folder"]:
                raise NotImplementedError("Historical render approval has an unknown scope or location")
            method = entry.get("method", "sha256" if "sha256" in entry else "legacy-media-stat")
            if method == "sha256":
                digest = entry.get("sha256")
                if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                    raise NotImplementedError("Historical render hash needs review")
            elif method in ("media-stat", "legacy-media-stat"):
                timestamp = next((entry[key] for key in ("mtime_s", "mtime_ns") if key in entry), None)
                values = [entry.get("size_bytes", entry.get("bytes")), timestamp]
                if any(type(value) is not int or value < 0 for value in values):
                    raise NotImplementedError("Historical render identity needs review")
            else:
                raise NotImplementedError("Historical render fingerprint method needs review")
            old_movies.append(entry)
        if not old_movies:
            return
        old_inputs = self.render_inputs(previous)
        if not old_inputs:
            raise NotImplementedError("Previous render approval lacks usable input provenance")
        current_inputs = self.render_inputs(self.manifest)
        old_paths = {entry["path"] for entry in old_inputs}
        current_paths = {entry["path"] for entry in current_inputs}
        if old_paths == current_paths and all(completion.matches_entry(entry, self.project, self.workflow)
                                              for entry in old_inputs):
            return
        for stem, name in expected.items():
            prior = [entry for entry in old_movies if Path(entry["path"]).stem.casefold() == stem]
            current = [movie for movie in movies if movie.stem.casefold() == stem]
            if prior and not any(all(not matches_identity(movie, entry) for entry in prior) for movie in current):
                raise ValueError(f"Generation inputs changed; a new approved rendered movie is required for {name}")
