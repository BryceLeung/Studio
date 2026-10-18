"""Read-only recomputation of persisted Take Selection scores."""
import importlib.util
import json
from pathlib import Path
import sys

from scripts.validation import StageValidation


def load_helpers():
    search_path, bytecode = sys.path[:], sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location(
            "_studio_validate_34_scoring", Path(__file__).with_name("score_takes.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except ImportError as error:
        raise NotImplementedError(f"Take Selection validation dependency unavailable: {error}") from error
    finally:
        sys.path[:] = search_path
        sys.dont_write_bytecode = bytecode


class Validation(StageValidation):
    def validate_stage(self):
        scoring = load_helpers()
        find = scoring.find
        for folder in (find.APPROVED, find.TRANSCRIBED, find.OUTPUT, scoring.STAGE):
            self.confined(self.project / folder)
        inputs, outputs, latest, count = [], [], [], 0
        for script_path, transcript_paths in find.discover(self.project):
            script_path = self.confined(script_path)
            transcript_paths = [self.confined(p) for p in transcript_paths]
            versions = find.mapping.takes_versions(self.project, script_path.stem + "__Takes")
            if not versions or sum(v == versions[-1][0] for v, _ in versions) != 1:
                raise ValueError(f"Missing or ambiguous latest Takes version: {script_path.stem}")
            path = self.confined(versions[-1][1])
            check = find.check_mapping(
                self.project, path, find.sources.load_script(self.project, script_path),
                [find.sources.load_transcript(self.project, p) for p in transcript_paths])
            if not check["coverage"]["complete"]:
                raise ValueError(f"Incomplete reviewed Takes coverage: {path}")
            target = self.confined(self.project / scoring.STAGE / (script_path.stem + "__Scores.v1.json"))
            persisted = json.loads(target.read_text(encoding="utf-8-sig"))
            reference = scoring.checked_reference(self.project, path, persisted["source_mapping"])
            document = scoring.load_mapping(self.project, path)
            expected = scoring.score_document(document, reference)
            scoring.validate_scores(document, persisted)
            if persisted != expected:
                raise ValueError(f"Persisted take scores differ from current scoring: {target}")
            inputs.extend((path, script_path, *transcript_paths))
            outputs.append(target)
            latest.append(path.relative_to(self.project).as_posix())
            count += expected["take_count"]
        self.require_inventory("inputs", inputs, exact=True)
        self.require_inventory("outputs", outputs, exact=True)
        conditions = self.manifest.get("conditions", {})
        if not isinstance(conditions, dict) or not {"latest_mapping_paths", "every_take_scored", "take_count"} <= conditions.keys():
            raise NotImplementedError("Take Selection DONE lacks scoring coverage evidence.")
        if (conditions.get("latest_mapping_paths") != latest
                or conditions.get("every_take_scored") is not True
                or conditions.get("take_count") != count):
            raise ValueError("Take Selection completion conditions do not describe current scores.")
