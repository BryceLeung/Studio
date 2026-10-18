"""Intake validation uses temporary projects and never changes their snapshots."""
import importlib.util
import json
from pathlib import Path
import sys
import runpy
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from scripts import stage_completion as completion


SOURCE = Path(__file__).with_name("validation.py")
SPEC = importlib.util.spec_from_file_location("test_intake_stage_validation", SOURCE)
validation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validation)
ROOT = next(path for path in SOURCE.resolve().parents if (path / "studio/fingerprints.py").is_file())


def snapshot(root):
    return {path.relative_to(root).as_posix(): (path.stat().st_mtime_ns,
            path.read_bytes() if path.is_file() else None) for path in root.rglob("*")}


class IntakeValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name) / "project"
        self.definition = json.loads(SOURCE.parents[1].joinpath("dependencies.json").read_text())
        self.approved = self.project / self.definition["inputs"][0]["folder"]
        self.footage = self.project / self.definition["folder"]
        self.approved.mkdir(parents=True)
        self.footage.mkdir(parents=True)
        (self.approved / "A.docx").write_bytes(b"approved")
        for role, suffix in (("camA", "mov"), ("camB", "mp4"), ("lav", "wav")):
            (self.footage / f"A__C01__{role}.{suffix}").write_bytes(b"media")
        self.validator = validation.Validation(self.project, ROOT, self.definition)
        self.record()

    def record(self):
        self.validator.manifest = {
            "inputs": [completion.fingerprint(path, self.project, "project") for path in self.approved.glob("*.docx")],
            "outputs": [completion.fingerprint(path, self.project, "project")
                        for path in self.footage.iterdir() if path.is_file()],
        }

    def check_unchanged(self, error=None):
        before, path = snapshot(self.project), sys.path[:]
        if error:
            with self.assertRaises(error):
                self.validator.validate_stage()
        else:
            self.assertIsNone(self.validator.validate_stage())
        self.assertEqual(snapshot(self.project), before)
        self.assertEqual(sys.path, path)

    def test_complete_group_success_is_read_only(self):
        self.check_unchanged()

    def test_cli_requires_shared_prerequisites_without_requiring_target_done(self):
        before = snapshot(self.project)
        with patch("scripts.validation.require_prerequisites") as prerequisites, \
                patch.object(sys, "argv", ["validate-footage.py", str(self.project)]):
            runpy.run_path(str(SOURCE.with_name("validate-footage.py")), run_name="__main__")
        prerequisites.assert_called_once_with(self.project.resolve(), ROOT, "2.1")
        self.assertEqual(snapshot(self.project), before)

    def test_cli_inspection_failure_archives_markers_not_original_footage(self):
        own = self.footage / "DONE"
        downstream = self.project / "3-Post Production/2-Transcribe/DONE"
        upstream = self.approved.parent / "DONE"
        for marker in (own, downstream, upstream):
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("original marker")
        media = {path: (path.read_bytes(), path.stat().st_mtime_ns)
                 for path in self.footage.glob("*.*")}
        (self.approved / "B.docx").write_bytes(b"missing footage")
        with patch("scripts.validation.require_prerequisites"), \
                patch.object(sys, "argv", ["validate-footage.py", str(self.project)]):
            with self.assertRaises(SystemExit) as failure:
                runpy.run_path(str(SOURCE.with_name("validate-footage.py")), run_name="__main__")
        self.assertEqual(failure.exception.code, 1)
        self.assertFalse(own.exists())
        self.assertFalse(downstream.exists())
        self.assertEqual(upstream.read_text(), "original marker")
        self.assertEqual(media, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in media})

    def test_cli_prerequisite_rejection_leaves_all_markers_untouched(self):
        (self.footage / "DONE").write_text("own marker")
        before = snapshot(self.project)
        with patch("scripts.validation.require_prerequisites", side_effect=ValueError("upstream")), \
                patch.object(sys, "argv", ["validate-footage.py", str(self.project)]):
            with self.assertRaisesRegex(ValueError, "upstream"):
                runpy.run_path(str(SOURCE.with_name("validate-footage.py")), run_name="__main__")
        self.assertEqual(snapshot(self.project), before)

    def test_pure_inspection_does_not_reenter_prerequisites(self):
        with patch("scripts.validation.require_prerequisites", side_effect=AssertionError("Recursive gate")):
            self.check_unchanged()

    def test_common_validation_accepts_complete_manifest_without_writes(self):
        (self.footage / "DONE").write_text(json.dumps(self.validator.manifest))
        before = snapshot(self.project)
        self.assertEqual(self.validator.validate()[0], "current")
        self.assertEqual(snapshot(self.project), before)

    def test_missing_role_or_empty_file_fails_read_only(self):
        media = self.footage / "A__C01__lav.wav"
        media.unlink()
        self.check_unchanged(ValueError)
        media.write_bytes(b"")
        self.check_unchanged(ValueError)

    def test_every_approved_script_needs_group(self):
        (self.approved / "B.docx").write_bytes(b"approved")
        self.record()
        self.check_unchanged(ValueError)

    def test_invalid_separator_fails_even_with_complete_group(self):
        (self.footage / "A__C02_lav.wav").write_bytes(b"audio")
        self.check_unchanged(ValueError)

    def test_new_inventory_without_fingerprint_fails(self):
        for role, suffix in (("camA", "mov"), ("camB", "mov"), ("lav", "wav")):
            (self.footage / f"A__C02__{role}.{suffix}").write_bytes(b"media")
        self.check_unchanged(ValueError)

    def test_symlink_escape_fails_read_only(self):
        outside = self.project.parent / "outside.wav"
        outside.write_bytes(b"audio")
        media = self.footage / "A__C01__lav.wav"
        media.unlink()
        media.symlink_to(outside)
        self.check_unchanged(ValueError)


@unittest.skipIf(sys.version_info < (3, 10), "Existing cue-card builder requires Python 3.10+")
class CueCardEntryTests(unittest.TestCase):
    def test_both_generators_use_shared_prerequisites_without_target_done(self):
        scripts = ROOT / ".agents/skills/studio/1-Pre Production/2-Cue Cards/scripts"
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            approved = project / "1-Pre Production/1-Script Review/Approved"
            approved.mkdir(parents=True)
            (approved / "A.docx").write_bytes(b"fixture Word script")
            docx = MagicMock()
            docx.Document.return_value.paragraphs = [MagicMock(text=text) for text in
                                                   ("Example", "Spoken script", "A short spoken script.")]
            with patch("scripts.validation.require_prerequisites") as prerequisites, \
                    patch.dict(sys.modules, {"docx": docx}), \
                    patch.object(sys, "argv", ["make-plans.py", str(project)]):
                runpy.run_path(str(scripts / "make-plans.py"), run_name="__main__")
            prerequisites.assert_called_once_with(project, ROOT, "1.2")
            plan = project / "1-Pre Production/2-Cue Cards/working/A.json"
            self.assertTrue(plan.is_file())

            spec = importlib.util.spec_from_file_location("test_build_cue_cards", scripts / "build-cue-cards.py")
            builder = importlib.util.module_from_spec(spec)
            modules = {name: MagicMock() for name in ("PIL", "pptx", "pptx.dml.color", "pptx.enum.shapes",
                                                     "pptx.enum.text", "pptx.util")}
            with patch.dict(sys.modules, modules):
                spec.loader.exec_module(builder)
            with patch.object(builder, "require_prerequisites") as prerequisites, \
                    patch.object(sys, "argv", ["build-cue-cards.py", str(project)]), \
                    patch.object(builder, "metric_font", return_value=object()), \
                    patch.object(builder, "build_deck", side_effect=lambda plan, font, output:
                                 (output.write_bytes(b"rendered deck"), 1)[1]) as build:
                builder.main()
            prerequisites.assert_called_once_with(project, ROOT, "1.2")
            build.assert_called_once()
            self.assertFalse(list(project.rglob("DONE")))

    def test_rejected_prerequisites_do_not_create_plans(self):
        script = ROOT / ".agents/skills/studio/1-Pre Production/2-Cue Cards/scripts/make-plans.py"
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            with patch("scripts.validation.require_prerequisites", side_effect=ValueError("stale ancestor")), \
                    patch.dict(sys.modules, {"docx": MagicMock()}), \
                    patch.object(sys, "argv", ["make-plans.py", str(project)]):
                with self.assertRaisesRegex(ValueError, "stale ancestor"):
                    runpy.run_path(str(script), run_name="__main__")
            self.assertEqual(list(project.iterdir()), [])

    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name).resolve()
        self.scripts = ROOT / ".agents/skills/studio/1-Pre Production/2-Cue Cards/scripts"
        self.approved = self.project / "1-Pre Production/1-Script Review/Approved"
        self.approved.mkdir(parents=True)
        (self.approved / "A.docx").write_bytes(b"Word fixture")
        self.cue = self.project / "1-Pre Production/2-Cue Cards"
        self.downstream = self.project / "1-Pre Production/3-Extracts"

    def plans(self, *options, spoken="Original spoken words."):
        docx = MagicMock()
        docx.Document.return_value.paragraphs = [MagicMock(text=text) for text in
                                               ("Example", "Spoken script", spoken)]
        with patch("scripts.validation.require_prerequisites"), \
                patch.dict(sys.modules, {"docx": docx}), \
                patch.object(sys, "argv", ["make-plans.py", str(self.project), *options]):
            runpy.run_path(str(self.scripts / "make-plans.py"), run_name="__main__")

    def markers(self):
        for folder in (self.cue, self.downstream):
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "DONE").write_text("marker")

    def builder(self):
        spec = importlib.util.spec_from_file_location("cue_lifecycle_builder", self.scripts / "build-cue-cards.py")
        builder = importlib.util.module_from_spec(spec)
        modules = {name: MagicMock() for name in ("PIL", "pptx", "pptx.dml.color", "pptx.enum.shapes",
                                                 "pptx.enum.text", "pptx.util")}
        with patch.dict(sys.modules, modules):
            spec.loader.exec_module(builder)
        return builder

    def build(self, builder, *options, fail=False):
        def render(plan, font, output):
            output.write_bytes(b"new deck")
            if fail:
                raise RuntimeError("renderer failure")
            return 1
        with patch.object(builder, "require_prerequisites"), \
                patch.object(builder, "metric_font", return_value=object()), \
                patch.object(builder, "build_deck", side_effect=render), \
                patch.object(sys, "argv", ["build-cue-cards.py", str(self.project), *options]):
            builder.main()

    def test_unchanged_plan_reuses_bytes_mtime_and_done(self):
        self.plans()
        self.markers()
        before = snapshot(self.project)
        self.plans()
        self.assertEqual(snapshot(self.project), before)

    def test_changed_plan_refuses_without_overwrite_preserving_human_edits(self):
        self.plans()
        plan = self.cue / "working/A.json"
        plan.write_text('{"human": "edited"}')
        self.markers()
        with self.assertRaises(FileExistsError):
            self.plans()
        self.assertEqual(plan.read_text(), '{"human": "edited"}')
        self.assertTrue((self.cue / "DONE").exists())
        self.assertTrue((self.downstream / "DONE").exists())
        self.assertFalse(list(self.cue.glob("STALE/*/working/*.json")))
        self.plans("--overwrite")
        archived = list(self.cue.glob("STALE/*/working/A.json"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_text(), '{"human": "edited"}')
        self.assertEqual(archived[0].parent.parent.joinpath("DONE").read_text(), "marker")

    def test_new_plan_invalidates_descendants_without_own_done(self):
        self.downstream.mkdir(parents=True)
        (self.downstream / "DONE").write_text("dependent marker")
        self.plans()
        self.assertFalse((self.downstream / "DONE").exists())
        self.assertTrue((self.cue / "working/A.json").is_file())

    def test_failed_planning_invalidates_markers_without_archiving_existing_plan(self):
        self.plans()
        plan = self.cue / "working/A.json"
        before = plan.read_bytes(), plan.stat().st_mtime_ns
        self.markers()
        with self.assertRaisesRegex(ValueError, "No spoken script"):
            self.plans("--overwrite", spoken="")
        self.assertEqual((plan.read_bytes(), plan.stat().st_mtime_ns), before)
        self.assertFalse((self.cue / "DONE").exists())
        self.assertFalse((self.downstream / "DONE").exists())
        self.assertFalse(list(self.cue.glob("STALE/*/working/*.json")))

    def test_deck_overwrite_archives_only_after_render_succeeds(self):
        self.plans()
        deck = self.cue / "A.pptx"
        deck.write_bytes(b"human deck")
        self.markers()
        builder = self.builder()
        with self.assertRaisesRegex(RuntimeError, "renderer failure"):
            self.build(builder, "--overwrite", fail=True)
        self.assertEqual(deck.read_bytes(), b"human deck")
        self.assertFalse(list(self.cue.glob("STALE/*/*.pptx")))
        self.build(builder, "--overwrite")
        self.assertEqual(deck.read_bytes(), b"new deck")
        archived = list(self.cue.glob("STALE/*/A.pptx"))
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0].read_bytes(), b"human deck")
        self.assertTrue(archived[0].with_name("DONE").is_file())

    def test_output_root_success_and_failure_never_mutate_canonical_state(self):
        self.plans()
        (self.cue / "A.pptx").write_bytes(b"canonical deck")
        self.markers()
        before = snapshot(self.cue), snapshot(self.downstream)
        builder = self.builder()
        with TemporaryDirectory() as preview:
            self.build(builder, "--output-root", preview)
            self.assertEqual((Path(preview) / "A.pptx").read_bytes(), b"new deck")
            with self.assertRaisesRegex(RuntimeError, "renderer failure"):
                self.build(builder, "--output-root", preview, "--overwrite", fail=True)
        self.assertEqual((snapshot(self.cue), snapshot(self.downstream)), before)

    def test_new_deck_invalidates_descendants_without_own_done(self):
        self.plans()
        self.downstream.mkdir(parents=True, exist_ok=True)
        (self.downstream / "DONE").write_text("dependent marker")
        self.build(self.builder())
        self.assertFalse((self.downstream / "DONE").exists())
        self.assertEqual((self.cue / "A.pptx").read_bytes(), b"new deck")

    def test_preview_cannot_overwrite_canonical_deck_through_symlink(self):
        self.plans()
        deck = self.cue / "A.pptx"
        deck.write_bytes(b"canonical deck")
        self.markers()
        before = snapshot(self.project)
        with TemporaryDirectory() as preview:
            (Path(preview) / "A.pptx").symlink_to(deck)
            with self.assertRaisesRegex(ValueError, "aliases a canonical deck"):
                self.build(self.builder(), "--output-root", preview, "--overwrite")
        self.assertEqual(snapshot(self.project), before)

    def test_deck_refusal_preserves_human_output_and_current_markers(self):
        self.plans()
        deck = self.cue / "A.pptx"
        deck.write_bytes(b"human deck")
        self.markers()
        with self.assertRaises(FileExistsError):
            self.build(self.builder())
        self.assertEqual(deck.read_bytes(), b"human deck")
        self.assertTrue((self.cue / "DONE").exists())
        self.assertTrue((self.downstream / "DONE").exists())
        self.assertFalse(list(self.cue.glob("STALE/*/*.pptx")))

    def test_preview_cannot_write_inside_archives_or_other_canonical_stages(self):
        self.plans()
        self.markers()
        archive = self.cue / "STALE/99"
        archive.mkdir(parents=True)
        protected = archive / "A.pptx"
        protected.write_bytes(b"irreplaceable archive")
        before = snapshot(self.project)
        for destination in (archive, self.downstream / "preview"):
            with self.subTest(destination=destination), self.assertRaises(SystemExit):
                self.build(self.builder(), "--output-root", str(destination), "--overwrite")
        self.assertEqual(snapshot(self.project), before)

    def test_preview_cannot_overwrite_a_hardlinked_canonical_deck(self):
        import os
        self.plans()
        deck = self.cue / "A.pptx"
        deck.write_bytes(b"canonical deck")
        self.markers()
        before = snapshot(self.project)
        with TemporaryDirectory() as preview:
            os.link(deck, Path(preview) / "A.pptx")
            with self.assertRaisesRegex(ValueError, "aliases a canonical deck"):
                self.build(self.builder(), "--output-root", preview, "--overwrite")
        self.assertEqual(snapshot(self.project), before)

    def test_preview_cannot_overwrite_another_projects_canonical_deck(self):
        self.plans()
        with TemporaryDirectory() as directory:
            other = Path(directory) / "Other Project/1-Pre Production/2-Cue Cards"
            other.mkdir(parents=True)
            deck = other / "A.pptx"
            deck.write_bytes(b"other project's canonical deck")
            before = snapshot(other)
            with self.assertRaises(SystemExit):
                self.build(self.builder(), "--output-root", str(other), "--overwrite")
            self.assertEqual(snapshot(other), before)

    def test_preview_atomic_replacement_does_not_follow_other_file_aliases(self):
        import os
        self.plans()
        other = self.project / "other-source.txt"
        other.write_bytes(b"preserve other file")
        self.markers()
        before = snapshot(self.project)
        with TemporaryDirectory() as preview:
            target = Path(preview) / "A.pptx"
            os.link(other, target)
            self.build(self.builder(), "--output-root", preview, "--overwrite")
            self.assertEqual(target.read_bytes(), b"new deck")
            self.assertFalse(target.samefile(other))
        self.assertEqual(snapshot(self.project), before)


if __name__ == "__main__":
    unittest.main()
