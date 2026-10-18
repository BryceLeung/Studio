"""Read-only Sync checks use dummy media, never decoding or probing it."""
import importlib.util
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import stage_completion as completion


SOURCE = Path(__file__).with_name("validation.py")
SPEC = importlib.util.spec_from_file_location("test_sync_stage_validation", SOURCE)
validation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validation)
ROOT = next(path for path in SOURCE.resolve().parents if (path / "studio/fingerprints.py").is_file())


def snapshot(root):
    return {path.relative_to(root).as_posix(): (path.stat().st_mtime_ns,
            path.read_bytes() if path.is_file() else None) for path in root.rglob("*")}


@unittest.skipIf(sys.version_info < (3, 10), "Existing Sync generator requires Python 3.10+")
class SyncValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.definition = json.loads(SOURCE.parents[1].joinpath("dependencies.json").read_text())
        self.footage = self.project / self.definition["inputs"][0]["folder"]
        self.destination = self.project / self.definition["folder"]
        self.footage.mkdir(parents=True)
        self.destination.mkdir(parents=True)
        for role, suffix in (("camA", "mov"), ("camB", "mp4"), ("lav", "wav")):
            (self.footage / f"A__C01__{role}.{suffix}").write_bytes(b"media")
        self.helper, pins = validation.read_helpers()
        dependencies = {name: "fixture" for name in (
            "python_version", "audio_offset_finder_version", "numpy_version", "scipy_version",
            "librosa_version", "ffmpeg_version")}
        dependencies.update(audio_offset_finder_revision=pins.BBC_REVISION,
                            imageio_ffmpeg_version=pins.IMAGEIO_FFMPEG_VERSION)
        files = self.helper.discover_shots(self.footage, None)["A__C01"]
        self.document = {
            "schema_version": 1, "shot": "A__C01", "reference_role": "camA",
            "offset_convention": self.helper.OFFSET_CONVENTION,
            "generator": self.helper.generator_identity(dependencies),
            "clips": {role: {**self.helper.file_identity(path, self.project),
                             "offset_ms": 0 if role == "camA" else -12,
                             "standard_score": None if role == "camA" else 2.0}
                      for role, path in files.items()},
        }
        self.output = self.destination / "A__C01.json"
        self.write_document()
        self.validator = validation.Validation(self.project, ROOT, self.definition)
        self.validator.manifest = {
            "inputs": [completion.fingerprint(path, self.project, "project") for path in files.values()],
            "outputs": [completion.fingerprint(self.output, self.project, "project")],
        }

    def write_document(self):
        self.output.write_text(json.dumps(self.document))

    def check_unchanged(self, error=None):
        before, workflow = snapshot(self.project), snapshot(SOURCE.parent)
        path, bytecode = sys.path[:], sys.dont_write_bytecode
        previous = sys.modules.get("install_dependencies")
        with patch("subprocess.run", side_effect=AssertionError("No subprocess allowed")):
            if error:
                with self.assertRaises(error):
                    self.validator.validate_stage()
            else:
                self.assertIsNone(self.validator.validate_stage())
        self.assertEqual(snapshot(self.project), before)
        self.assertEqual(snapshot(SOURCE.parent), workflow)
        self.assertEqual(sys.path, path)
        self.assertEqual(sys.dont_write_bytecode, bytecode)
        self.assertIs(sys.modules.get("install_dependencies"), previous)

    def test_matching_json_success_is_read_only(self):
        self.check_unchanged()

    def test_selected_shot_requires_prerequisites_but_never_records_completion(self):
        self.output.unlink()
        dependencies = self.document["generator"]["dependencies"] | {"ffmpeg_executable": "fixture"}
        estimates = {role: {"offset_ms": clip["offset_ms"], "standard_score": clip["standard_score"]}
                     for role, clip in self.document["clips"].items()}
        with patch.object(sys, "argv", ["generate_offsets.py", str(self.project), "--shot", "A__C01"]), \
                patch.object(self.helper, "ENVIRONMENT", Path(sys.prefix)), \
                patch.object(self.helper, "require_prerequisites") as prerequisites, \
                patch.object(self.helper, "dependency_info", return_value=dependencies), \
                patch.object(self.helper, "measure_shot", return_value=estimates), \
                patch.object(self.helper.subprocess, "run", side_effect=AssertionError("No subprocess allowed")):
            self.assertEqual(self.helper.main(), 0)
        prerequisites.assert_called_once_with(self.project.resolve(), ROOT, "3.1")
        self.assertTrue(self.output.is_file())
        self.assertFalse((self.destination / "DONE").exists())

    def test_prerequisite_failure_precedes_discovery_and_dependency_probes(self):
        before = snapshot(self.project)
        with patch.object(sys, "argv", ["generate_offsets.py", str(self.project)]), \
                patch.object(self.helper, "ENVIRONMENT", Path(sys.prefix)), \
                patch.object(self.helper, "require_prerequisites", side_effect=ValueError("stale ancestor")) as prerequisites, \
                patch.object(self.helper, "discover_shots") as discover, \
                patch.object(self.helper, "dependency_info") as dependencies:
            self.assertEqual(self.helper.main(), 1)
        prerequisites.assert_called_once_with(self.project.resolve(), ROOT, "3.1")
        discover.assert_not_called()
        dependencies.assert_not_called()
        self.assertEqual(snapshot(self.project), before)

    def run_generator(self, *options, measurement_error=None):
        dependencies = self.document["generator"]["dependencies"] | {"ffmpeg_executable": "fixture"}
        estimates = {role: {"offset_ms": clip["offset_ms"], "standard_score": clip["standard_score"]}
                     for role, clip in self.document["clips"].items()}
        with patch.object(sys, "argv", ["generate_offsets.py", str(self.project), *options]), \
                patch.object(self.helper, "ENVIRONMENT", Path(sys.prefix)), \
                patch.object(self.helper, "require_prerequisites"), \
                patch.object(self.helper, "dependency_info", return_value=dependencies), \
                patch.object(self.helper, "measure_shot", return_value=estimates,
                             side_effect=measurement_error) as measurement:
            status = self.helper.main()
        return status, measurement

    def markers(self, own=True):
        self.downstream = self.project / "3-Post Production/6-First Cut"
        for folder in ([self.destination, self.downstream] if own else [self.downstream]):
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "DONE").write_text("previous marker")

    def test_default_matching_reuse_keeps_done_and_outputs_untouched(self):
        self.markers()
        before = snapshot(self.project)
        status, measurement = self.run_generator()
        self.assertEqual(status, 0)
        measurement.assert_not_called()
        self.assertEqual(snapshot(self.project), before)

    def test_default_stale_refusal_invalidates_markers_but_never_moves_outputs(self):
        self.markers()
        self.output.write_text('{"human": "edited"}')
        before = self.output.read_bytes(), self.output.stat().st_mtime_ns
        status, measurement = self.run_generator()
        self.assertEqual(status, 1)
        measurement.assert_not_called()
        self.assertEqual((self.output.read_bytes(), self.output.stat().st_mtime_ns), before)
        self.assertFalse((self.destination / "DONE").exists())
        self.assertFalse((self.downstream / "DONE").exists())
        self.assertFalse(list(self.destination.glob("STALE/*/A__C01.json")))

    def test_overwrite_archives_only_selected_json_after_successful_measurement(self):
        self.markers()
        original = self.output.read_bytes()
        unrelated = self.destination / "B__C01.json"
        unrelated.write_bytes(b"unselected human output")
        media = snapshot(self.footage)
        status, measurement = self.run_generator("--overwrite", "--shot", "A__C01")
        self.assertEqual(status, 0)
        measurement.assert_called_once()
        archived = list(self.destination.glob("STALE/*/A__C01.json"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_bytes(), original)
        self.assertTrue(archived[0].with_name("DONE").is_file())
        self.assertEqual(unrelated.read_bytes(), b"unselected human output")
        self.assertEqual(snapshot(self.footage), media)
        self.assertFalse((self.destination / "DONE").exists())
        self.assertFalse((self.downstream / "DONE").exists())

    def test_failed_measurement_preserves_output_but_invalidates_markers(self):
        self.markers()
        original = self.output.read_bytes(), self.output.stat().st_mtime_ns
        status, _ = self.run_generator("--overwrite", measurement_error=RuntimeError("no correlation"))
        self.assertEqual(status, 1)
        self.assertEqual((self.output.read_bytes(), self.output.stat().st_mtime_ns), original)
        self.assertFalse(list(self.destination.glob("STALE/*/A__C01.json")))
        self.assertFalse((self.destination / "DONE").exists())
        self.assertFalse((self.downstream / "DONE").exists())

    def test_invalid_measurement_result_never_archives_previous_output(self):
        self.markers()
        original = self.output.read_bytes()
        self.document["clips"]["lav"]["standard_score"] = float("nan")
        status, _ = self.run_generator("--overwrite")
        self.assertEqual(status, 1)
        self.assertEqual(self.output.read_bytes(), original)
        self.assertFalse(list(self.destination.glob("STALE/*/A__C01.json")))
        self.assertFalse((self.destination / "DONE").exists())

    def test_discovery_failure_invalidates_markers_without_moving_any_media(self):
        self.markers()
        (self.footage / "A__C01__lav.wav").unlink()
        media = snapshot(self.footage)
        original = self.output.read_bytes()
        status, measurement = self.run_generator()
        self.assertEqual(status, 1)
        measurement.assert_not_called()
        self.assertEqual(snapshot(self.footage), media)
        self.assertEqual(self.output.read_bytes(), original)
        self.assertFalse((self.destination / "DONE").exists())
        self.assertFalse((self.downstream / "DONE").exists())

    def test_new_output_invalidates_descendants_without_own_done(self):
        self.output.unlink()
        self.markers(own=False)
        status, _ = self.run_generator("--shot", "A__C01")
        self.assertEqual(status, 0)
        self.assertTrue(self.output.is_file())
        self.assertFalse((self.downstream / "DONE").exists())
        self.assertFalse((self.destination / "DONE").exists())

    def test_late_current_file_is_not_overwritten_after_archive_preparation(self):
        self.markers()
        original = self.output.read_bytes()
        prepare = self.helper.prepare_regeneration
        slots = []

        def prepare_then_publish_other_file(*args, **kwargs):
            slot = prepare(*args, **kwargs)
            slots.append(slot)
            self.output.write_bytes(b"another writer's new estimate")
            return slot

        with patch.object(self.helper, "prepare_regeneration", side_effect=prepare_then_publish_other_file):
            status, _ = self.run_generator("--overwrite")
        self.assertEqual(status, 1)
        self.assertEqual(self.output.read_bytes(), b"another writer's new estimate")
        self.assertEqual((slots[0] / self.output.name).read_bytes(), original)

    def test_common_validation_accepts_complete_manifest_without_writes(self):
        (self.destination / "DONE").write_text(json.dumps(self.validator.manifest))
        before = snapshot(self.project)
        self.assertEqual(self.validator.validate()[0], "current")
        self.assertEqual(snapshot(self.project), before)

    def test_invalid_offsets_and_scores_fail_read_only(self):
        for role, field, value in (("camA", "offset_ms", 1), ("camB", "offset_ms", True),
                                   ("lav", "standard_score", 0), ("lav", "standard_score", float("nan"))):
            with self.subTest(role=role, field=field, value=value):
                original = self.document["clips"][role][field]
                self.document["clips"][role][field] = value
                self.write_document()
                self.check_unchanged(ValueError)
                self.document["clips"][role][field] = original

    def test_generator_settings_and_hashes_must_match(self):
        for key, value in (("sample_rate_hz", 1), ("scripts_sha256", {}), ("fingerprints_sha256", "old")):
            with self.subTest(key=key):
                original = self.document["generator"][key]
                self.document["generator"][key] = value
                self.write_document()
                self.check_unchanged(ValueError)
                self.document["generator"][key] = original

    def test_pinned_dependency_identity_must_match(self):
        self.document["generator"]["dependencies"]["audio_offset_finder_revision"] = "old"
        self.write_document()
        self.check_unchanged(ValueError)

    def test_changed_source_identity_fails(self):
        (self.footage / "A__C01__lav.wav").write_bytes(b"changed source")
        self.check_unchanged(ValueError)

    def test_missing_duplicate_or_incomplete_group_fails(self):
        self.output.unlink()
        self.check_unchanged(ValueError)
        self.write_document()
        (self.footage / "A__C01__camA.mp4").write_bytes(b"duplicate")
        self.check_unchanged(ValueError)
        (self.footage / "A__C01__camA.mp4").unlink()
        (self.footage / "A__C02__lav.wav").write_bytes(b"incomplete")
        self.check_unchanged(ValueError)

    def test_extra_json_and_invalid_json_fail(self):
        extra = self.destination / "Other.json"
        extra.write_text("{}")
        self.check_unchanged(ValueError)
        extra.unlink()
        self.output.write_text("not JSON")
        self.check_unchanged(ValueError)


if __name__ == "__main__":
    unittest.main()
