"""Shared completion definitions, confinement, and historical marker handling."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts import stage_completion as completion


class StageCompletionTests(unittest.TestCase):
    def test_same_manifest_normalizes_legacy_media_timestamps(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.wav"
            source.write_bytes(b"audio")
            fresh = completion.fingerprint(source, root, "project")
            old = {"scope": "project", "path": "source.wav", "bytes": source.stat().st_size,
                   "mtime_ns": source.stat().st_mtime_ns}
            previous = {"inputs": [old]}
            current = {"inputs": [fresh]}
            self.assertTrue(completion.same_manifest(previous, current, root, root, sections=("inputs",)))
            self.assertEqual(previous["inputs"], [old])
            old["unexpected"] = True
            self.assertFalse(completion.same_manifest(previous, current, root, root, sections=("inputs",)))

    def test_generated_ownership_is_explicit_and_excludes_intake_originals(self):
        from scripts.validation import load_stages
        definitions = load_stages()
        self.assertTrue(all("generated" in definition for definition in definitions.values()))
        self.assertEqual(definitions["2.1"]["generated"], [])
        for definition in definitions.values():
            for policy in definition["generated"]:
                folder = Path(policy["folder"])
                self.assertTrue(folder.is_relative_to(Path(definition["folder"])))
                self.assertNotIn("APPROVED", folder.parts)
                self.assertNotIn("Approved", folder.parts)

    def test_generated_policy_cannot_claim_other_stages_or_approved_files(self):
        from scripts.validation import load_stages
        definition = load_stages()["1.2"]
        for folder in ("2-Production/1-Footage Intake", "1-Pre Production/2-Cue Cards/APPROVED"):
            with self.subTest(folder=folder), TemporaryDirectory() as directory:
                file = Path(directory) / "dependencies.json"
                changed = dict(definition, generated=[{"folder": folder, "filename_regex": ".*"}])
                file.write_text(json.dumps(changed))
                with self.assertRaises(ValueError):
                    completion.load_definition(file)

    def test_transcribe_definition_is_machine_readable(self):
        definition = completion.load_definition(
            Path(__file__).resolve().parents[1] / ".agents/skills/studio/3-Post Production/2-Transcribe/dependencies.json")
        self.assertEqual(definition["step"], "3.2")
        self.assertEqual([item["step"] for item in definition["prerequisites"]], ["2.1"])
        self.assertEqual(definition["outputs"][0]["suffix"], ".json")

    def test_paths_cannot_escape_scoped_roots(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text("{}")
            entry = completion.fingerprint(source, root, "project")
            self.assertTrue(completion.matches_entry(entry, root, root))
            for bad in ("../source.json", "/source.json", "C:/source.json", "folder\\source.json"):
                with self.assertRaises(ValueError):
                    completion.matches_entry(dict(entry, path=bad), root, root)

    def test_archive_only_active_done_into_next_slot(self):
        with TemporaryDirectory() as directory:
            stage = Path(directory)
            (stage / "DONE").write_text(json.dumps({"step": "3.2"}))
            first = completion.archive_done(stage)
            self.assertEqual(first, stage / "STALE/1/DONE")
            self.assertIsNone(completion.archive_done(stage))
            (stage / "DONE").write_text("new")
            second = completion.archive_done(stage)
            self.assertEqual(second, stage / "STALE/2/DONE")
            self.assertEqual(first.read_text(), json.dumps({"step": "3.2"}))

    def test_dangling_symlink_slot_is_occupied_not_replaced(self):
        with TemporaryDirectory() as directory:
            stage = Path(directory)
            (stage / "STALE").mkdir()
            (stage / "STALE/1").symlink_to(stage / "absent", target_is_directory=True)
            (stage / "DONE").write_text("completion")
            saved = completion.archive_done(stage)
            self.assertEqual(saved, stage / "STALE/2/DONE")
            self.assertTrue((stage / "STALE/1").is_symlink())

    def test_deleted_archive_gaps_do_not_make_old_history_look_newest(self):
        with TemporaryDirectory() as directory:
            stage = Path(directory)
            (stage / "STALE/2").mkdir(parents=True)
            (stage / "STALE/2/DONE").write_text("older")
            (stage / "DONE").write_text("newest")
            saved = completion.archive_done(stage)
            self.assertEqual(saved, stage / "STALE/3/DONE")
            self.assertEqual(completion.latest_archived_done(stage), saved)

    def test_symlinked_step_cannot_archive_outside_project(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / "project"
            project.mkdir()
            external = root / "external"
            external.mkdir()
            (external / "DONE").write_text("outside")
            (project / "3-Post Production").symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, "symlink"):
                completion.invalidate_from(project, (3, 2), "test", lambda *args: None)
            self.assertEqual((external / "DONE").read_text(), "outside")


if __name__ == "__main__":
    unittest.main()
