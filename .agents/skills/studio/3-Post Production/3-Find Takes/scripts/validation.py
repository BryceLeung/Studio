"""Read-only validation of current reviewed Takes mappings."""
import importlib.util
from pathlib import Path
import sys

from scripts.validation import StageValidation


def load_helpers():
    # The existing helper imports add the workflow root to sys.path.
    search_path, bytecode = sys.path[:], sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec = importlib.util.spec_from_file_location(
            "_studio_validate_33_find", Path(__file__).with_name("find_takes.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except ImportError as error:
        raise NotImplementedError(f"Find Takes validation dependency unavailable: {error}") from error
    finally:
        sys.path[:] = search_path
        sys.dont_write_bytecode = bytecode


class Validation(StageValidation):
    def validate_stage(self):
        find = load_helpers()
        for folder in (find.APPROVED, find.TRANSCRIBED, find.OUTPUT):
            self.confined(self.project / folder)
        inventory = find.discover(self.project)
        inputs, outputs, latest = [], [], []
        for script_path, transcript_paths in inventory:
            script_path = self.confined(script_path)
            transcript_paths = [self.confined(path) for path in transcript_paths]
            versions = find.mapping.takes_versions(self.project, script_path.stem + "__Takes")
            if not versions or sum(v == versions[-1][0] for v, _ in versions) != 1:
                raise ValueError(f"Missing or ambiguous latest Takes version: {script_path.stem}")
            version, path = versions[-1]
            path = self.confined(path)
            check = find.check_mapping(
                self.project, path, find.sources.load_script(self.project, script_path),
                [find.sources.load_transcript(self.project, p) for p in transcript_paths])
            if not check["coverage"]["complete"]:
                raise ValueError(f"Incomplete reviewed Takes coverage: {path}")
            inputs.extend((script_path, *transcript_paths))
            outputs.append(path)
            latest.append({"script": script_path.relative_to(self.project).as_posix(),
                           "version": version, "path": path.relative_to(self.project).as_posix()})
        self.require_inventory("inputs", inputs, exact=True)
        self.require_inventory("outputs", outputs, exact=True)
        conditions = self.manifest.get("conditions", {})
        expected = {
            "approved_scripts": [p.relative_to(self.project).as_posix() for p, _ in inventory],
            "matching_transcripts": [p.relative_to(self.project).as_posix() for _, paths in inventory for p in paths],
            "latest_versions": latest,
            "non_flub_coverage_or_explicit_script_discard": True,
        }
        if not isinstance(conditions, dict) or not expected.keys() <= conditions.keys():
            raise NotImplementedError("Find Takes DONE lacks current mapping coverage evidence.")
        if any(conditions.get(key) != value for key, value in expected.items()):
            raise ValueError("Find Takes completion conditions do not describe current mappings.")
