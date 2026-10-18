"""Rendered movies in APPROVED attest that each project was edited and rendered."""
import json
from pathlib import Path
import re

from scripts import stage_completion as completion
from scripts.validation import StageValidation, recorded_fingerprints
from studio.fingerprints import matches_identity


class Validation(StageValidation):
    def validate_stage(self):
        source = self.definition["inputs"][0]
        target = self.definition["outputs"][0]
        scores = [path for path in self.confined(source["folder"]).iterdir()
                  if path.is_file() and re.fullmatch(source["filename_regex"], path.name, re.I)]
        folder = self.confined(target["folder"])
        movies = [self.confined(path) for path in folder.iterdir()
                  if path.is_file() and re.fullmatch(target["filename_regex"], path.name, re.I)]
        if any(path.stat().st_size == 0 for path in movies):
            raise ValueError("Approved rendered movies must not be empty")
        approved = {path.stem.casefold() for path in movies}
        expected = {re.sub(r"__Scores\.v1\.json$", "", score.name, flags=re.I) for score in scores}
        for inventory in self.definition["inputs"][1:]:
            if inventory.get("role") != "project":
                continue
            projects = self.confined(inventory["folder"])
            if projects.is_dir():
                expected.update(path.stem for path in projects.iterdir()
                                if path.is_file() and re.fullmatch(inventory["filename_regex"], path.name, re.I))
        missing = [name for name in expected if name.casefold() not in approved]
        if missing:
            raise ValueError(f"Missing approved rendered movies for projects: {sorted(missing)}")
        self.validate_render_provenance(movies, expected)

    def validate_render_provenance(self, movies, expected):
        """Preserved approved files are not new render evidence after inputs change."""
        directory = self.confined(self.definition["folder"])
        marker = directory / "DONE"
        if not marker.exists():
            marker = completion.latest_archived_done(directory)
        if marker is None:
            return
        if marker.is_symlink():
            raise ValueError("Redirected First Cut completion history")
        previous = json.loads(self.confined(marker).read_text(encoding="utf-8-sig"))
        if not isinstance(previous, dict) or not isinstance(previous.get("outputs"), list):
            raise NotImplementedError("First Cut history needs review before accepting existing renders")
        target = self.definition["outputs"][0]
        old_movies = []
        for entry in previous["outputs"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise NotImplementedError("Malformed historical First Cut output evidence needs review")
            path = completion.relative_path(entry["path"])
            if not re.fullmatch(target["filename_regex"], path.name, re.I):
                continue
            if entry.get("scope") != "project" or path.parent.as_posix() != target["folder"]:
                raise NotImplementedError("Historical render approval has an unknown scope or location")
            method = entry.get("method", "sha256" if "sha256" in entry else "legacy-media-stat")
            if method == "sha256":
                if not isinstance(entry.get("sha256"), str) or not re.fullmatch(r"[0-9a-fA-F]{64}", entry["sha256"]):
                    raise NotImplementedError("Historical render hash needs review")
            elif method in ("media-stat", "legacy-media-stat"):
                values = ([entry.get("size_bytes"), entry.get("mtime_s")] if method == "media-stat"
                          else [entry.get("size_bytes", entry.get("bytes")), entry.get("mtime_ns")])
                if any(type(value) is not int or value < 0 for value in values):
                    raise NotImplementedError("Historical render identity needs review")
            else:
                raise NotImplementedError("Historical render fingerprint method needs review")
            old_movies.append(entry)
        if not old_movies:
            return  # An older XML-only completion is not a prior render approval.
        old_inputs = recorded_fingerprints({"inputs": previous.get("inputs")})
        if not old_inputs:
            raise NotImplementedError("Previous render approval lacks usable input provenance")
        if previous.get("completion_mode") == "manual-override":
            # Override watches also freeze ancestor-only artifacts (for example
            # cue decks); those are not newly introduced render dependencies.
            old_inputs = [entry for entry in old_inputs if entry.get("scope") == "project" and any(
                completion.relative_path(entry["path"]).parent.as_posix() == rule["folder"]
                and re.fullmatch(rule["filename_regex"], Path(entry["path"]).name, re.I)
                for rule in self.definition["inputs"])]
        current_inputs = recorded_fingerprints({"inputs": self.manifest.get("inputs")})
        old_paths = {(entry.get("scope"), entry["path"]) for entry in old_inputs}
        current_paths = {(entry.get("scope"), entry["path"]) for entry in current_inputs}
        changed = old_paths != current_paths or any(
            not completion.matches_entry(entry, self.project, self.workflow) for entry in old_inputs)
        if not changed:
            return
        for name in expected:
            prior = [entry for entry in old_movies if Path(entry["path"]).stem.casefold() == name.casefold()]
            current = [movie for movie in movies if movie.stem.casefold() == name.casefold()]
            if prior and not any(all(not matches_identity(movie, entry) for entry in prior) for movie in current):
                raise ValueError(f"Generation inputs changed; a new approved rendered movie is required for {name}")
