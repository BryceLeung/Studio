"""Read-only validation of required camera intervals and persisted face paths."""
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
            "_studio_validate_35_tracking", Path(__file__).with_name("face_tracking.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except ImportError as error:
        raise NotImplementedError(f"Face Tracking validation dependency unavailable: {error}") from error
    finally:
        sys.path[:] = search_path
        sys.dont_write_bytecode = bytecode


class Validation(StageValidation):
    def validate_stage(self):
        track = load_helpers()
        for folder in (track.SHORTLIST.parent, track.SYNC, track.INTAKE,
                       track.find.APPROVED, track.find.TRANSCRIBED, track.find.OUTPUT, track.STAGE):
            self.confined(self.project / folder)
        shortlist = self.confined(self.project / track.SHORTLIST)
        if not shortlist.is_file():
            raise ValueError(f"Approved shortlist is missing: {track.SHORTLIST}")
        extracts = track.approved_passages(shortlist.read_text(encoding="utf-8-sig")) if shortlist.is_file() else []
        # Preflight source references before required_ranges reads mappings and sync JSON.
        self.confined(self.project / track.find.OUTPUT / "DONE")
        inventory = track.find.discover(self.project) if extracts else []
        for script, transcripts in inventory:
            self.confined(script)
            for transcript in transcripts:
                self.confined(transcript)
            versions = track.find.mapping.takes_versions(self.project, script.stem + "__Takes")
            if not versions or sum(v == versions[-1][0] for v, _ in versions) != 1:
                raise ValueError(f"Missing or ambiguous latest Takes version: {script.stem}")
            self.confined(versions[-1][1])
        for _, source, _ in extracts:
            if Path(source).stem not in {script.stem for script, _ in inventory}:
                raise ValueError(f"Extract source has no approved script: {source}")
            for sync in (self.project / track.SYNC).glob(Path(source).stem + "__C*.json"):
                sync = self.confined(sync)
                document = json.loads(sync.read_text(encoding="utf-8-sig"))
                for clip in document["clips"].values():
                    self.confined(self.project / clip["path"])
        ranges, references = track.required_ranges(self.project, extracts)
        inputs = [track.fingerprint(shortlist, self.project)] if shortlist.is_file() else []
        inputs.extend(json.loads(item) for item in references)
        dependencies = [Path(track.__file__), Path(track.__file__).parents[1] / "SKILL.md",
                        track.FIND_SCRIPT, track.ROOT / "studio/tools/line/mapping.py",
                        track.ROOT / "scripts/append-log.py", track.ROOT / "studio/fingerprints.py"]
        identity = {"schema": "studio.face-path", "schema_version": 1,
                    "sample_fps": track.SAMPLE_FPS, "padding_ms": track.PADDING_MS,
                    "model_sha256": track.MODEL_SHA256, "inputs": inputs,
                    "dependencies": [{"scope": "workflow", "path": p.relative_to(track.ROOT).as_posix(),
                                      "sha256": track.find.sha256(p)} for p in dependencies]}
        outputs, path_outputs = [], []
        for (media, role), spans in sorted(ranges.items(), key=lambda item: (str(item[0][0]), item[0][1])):
            media = self.confined(media)
            path = self.confined(self.project / track.STAGE / (media.stem + "__FacePath.v1.json"))
            header = dict(identity, media=track.media_fingerprint(media, self.project), role=role,
                          ranges_ms=[[round(float(a * 1000)), round(float(b * 1000))] for a, b in spans])
            track.validate_path(json.loads(path.read_text(encoding="utf-8-sig")), header)
            outputs.append(path)
            path_outputs.append(track.fingerprint(path, self.project))
        index = self.confined(self.project / track.STAGE / "Face Paths.json")
        expected = {"schema": "studio.face-path-index", "schema_version": 1, "inputs": inputs,
                    "dependencies": identity["dependencies"], "paths": path_outputs,
                    "no_extracts": not extracts}
        if json.loads(index.read_text(encoding="utf-8-sig")) != expected:
            raise ValueError("Face path index differs from required camera paths.")
        self.require_inventory("inputs", [self.confined(self.project / item["path"]) for item in inputs], exact=True)
        self.require_inventory("outputs", [*outputs, index], exact=True)
        conditions = self.manifest.get("conditions", {})
        if not isinstance(conditions, dict) or not {"no_extracts", "face_paths"} <= conditions.keys():
            raise NotImplementedError("Face Tracking DONE lacks required path coverage evidence.")
        if (conditions.get("no_extracts") is not (not extracts)
                or conditions.get("face_paths") != [item["path"] for item in path_outputs]):
            raise ValueError("Face Tracking completion conditions do not describe required paths.")
