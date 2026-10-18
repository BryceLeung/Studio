"""Temporary-project tests for graph invalidation and recoverable regeneration."""
import copy
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import stage_invalidation as invalidation
from scripts.validation import load_stages


WORKFLOW = Path(__file__).resolve().parents[1]


class InvalidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name).resolve()
        self.stages = copy.deepcopy(load_stages(WORKFLOW))
        # Ownership fixtures are explicit, not inferred from output inventories.
        for definition in self.stages.values():
            definition["generated"] = [
                {"folder": definition["folder"], "filename_regex": r".*\.json"},
                {"folder": definition["folder"] + "/nested", "filename_regex": r".*\.json"},
            ]
        self.loader = patch.object(invalidation, "load_stages", return_value=self.stages)
        self.loader.start()
        self.addCleanup(self.loader.stop)
        self.logs = []

    def folder(self, step, marker=True):
        directory = self.project / self.stages[step]["folder"]
        directory.mkdir(parents=True, exist_ok=True)
        if marker:
            (directory / "DONE").write_text(step)
        return directory

    def log(self, directory, title, items):
        self.logs.append((directory, title, items))

    def invalidate(self, step="3.2", **kwargs):
        return invalidation.invalidate(self.project, WORKFLOW, step, "changed", self.log, **kwargs)

    def prepare(self, paths, step="3.2", logger=None, **kwargs):
        return invalidation.prepare_regeneration(self.project, WORKFLOW, step, paths, "regenerate",
                                                 self.log if logger is None else logger, **kwargs)

    def test_all_graph_branches_preserve_independent_siblings(self):
        expected = {
            "1.1": {"1.1", "1.2", "1.3", "2.1", "3.1", "3.2", "3.3", "3.4", "3.5", "3.6"},
            "1.2": {"1.2", "1.3", "2.1", "3.1", "3.2", "3.3", "3.4", "3.5", "3.6"},
            "1.3": {"1.3", "2.1", "3.1", "3.2", "3.3", "3.4", "3.5", "3.6"},
            "2.1": {"2.1", "3.1", "3.2", "3.3", "3.4", "3.5", "3.6"},
            "3.1": {"3.1", "3.5", "3.6"},
            "3.2": {"3.2", "3.3", "3.4", "3.5", "3.6"},
            "3.3": {"3.3", "3.4", "3.5", "3.6"},
            "3.4": {"3.4", "3.6"}, "3.5": {"3.5", "3.6"}, "3.6": {"3.6"},
        }
        self.assertEqual(set(expected), set(self.stages))
        for failed, affected in expected.items():
            with self.subTest(failed=failed):
                folders = {step: self.folder(step) for step in self.stages}
                slots = self.invalidate(failed)
                self.assertEqual(set(slots), affected)
                for step, directory in folders.items():
                    self.assertEqual((directory / "DONE").exists(), step not in affected)
                    if step in affected:
                        self.assertEqual((slots[step] / "DONE").read_text(), step)
                        self.assertTrue(slots[step].is_absolute())

    def test_canonical_invalidation_preserves_independent_zero_tmp_preview_tree(self):
        self.folder("1.1")
        scratch = self.project / "3-Post Production/0-Tmp"
        scratch.mkdir(parents=True)
        (scratch / "preview.FCPXML").write_text("independent preview")
        (scratch / "DONE").write_text("scratch sentinel, not workflow completion")
        before = {path.name: path.read_bytes() for path in scratch.iterdir()}
        self.assertEqual(set(self.invalidate("1.1")), {"1.1"})
        self.assertEqual({path.name: path.read_bytes() for path in scratch.iterdir()}, before)

    def test_explicit_start_without_done_still_invalidates_descendants(self):
        own = self.folder("3.2", marker=False)
        child = self.folder("3.3")
        output = own / "transcript.json"
        output.write_text("keep")
        slots = self.invalidate()
        self.assertEqual(set(slots), {"3.2", "3.3"})
        self.assertFalse((child / "DONE").exists())
        self.assertEqual(output.read_text(), "keep")
        event = json.loads((slots["3.2"] / "EVENT.json").read_text())
        self.assertIs(event["had_done"], False)
        self.assertEqual({entry[0] for entry in self.logs}, {own, child})
        self.assertFalse((own.parent / "STALE").exists())
        self.assertFalse((self.project / self.stages["3.6"]["folder"]).exists())

    def test_pending_reuse_new_marker_new_slot_and_clear_is_pointer_only(self):
        own = self.folder("3.2")
        first = self.invalidate()["3.2"]
        self.assertEqual(self.invalidate()["3.2"], first)
        self.assertEqual(invalidation.read_pending(own, "3.2"), first)
        (own / "DONE").write_text("new")
        second = self.invalidate()["3.2"]
        self.assertNotEqual(first, second)
        self.assertEqual((first / "DONE").read_text(), "3.2")
        invalidation.clear_pending(own)
        self.assertIsNone(invalidation.read_pending(own))
        self.assertEqual((second / "DONE").read_text(), "new")
        third = self.invalidate()["3.2"]
        self.assertNotIn(third, (first, second))
        self.assertFalse(json.loads((third / "EVENT.json").read_text())["had_done"])

    def test_stage_level_markers_and_writer_exclusion(self):
        own = self.folder("3.4")
        child = self.folder("3.6")
        parent = own.parent
        (parent / "DONE").write_text("aggregate")
        lock = own / ".DONE.lock"
        lock.mkdir()
        slots = self.invalidate("3.4", include_self=False)
        self.assertEqual(set(slots), {"3.6", "stage:3"})
        self.assertEqual((slots["stage:3"] / "DONE").read_text(), "aggregate")
        self.assertTrue(lock.is_dir())
        self.assertTrue((own / "DONE").exists())
        self.assertFalse((child / "DONE").exists())

    def test_unknown_numbered_later_folders_conservative_and_stale_ignored(self):
        own = self.folder("3.4")
        unknown = self.project / "4-Unknown/1-Unspecified"
        unknown.mkdir(parents=True)
        (unknown / "DONE").write_text("legacy with no input references")
        nested = own / "STALE/3-Not a project stage/8-Not a step"
        nested.mkdir(parents=True)
        (nested / "DONE").write_text("historical")
        earlier = self.project / "3-Post Production/0-Unknown"
        earlier.mkdir()
        (earlier / "DONE").write_text("earlier")
        slots = self.invalidate("3.4")
        self.assertEqual(set(slots), {"3.4", "4.1"})
        self.assertEqual((nested / "DONE").read_text(), "historical")
        self.assertEqual((earlier / "DONE").read_text(), "earlier")
        with self.assertRaisesRegex(ValueError, "Unknown stage"):
            self.invalidate("4.1")

    def test_prepare_archives_selected_files_only_and_preserves_subpaths(self):
        own = self.folder("3.2")
        child = self.folder("3.3")
        first = own / "one.json"
        second = own / "nested/two.json"
        second.parent.mkdir()
        first.write_text("one")
        second.write_text("two")
        unrelated = own / "unselected.json"
        unrelated.write_text("untouched")
        slot = self.prepare([first, second, first, own / "absent.json"])
        self.assertEqual((slot / "one.json").read_text(), "one")
        self.assertEqual((slot / "nested/two.json").read_text(), "two")
        self.assertEqual((slot / "DONE").read_text(), "3.2")
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        self.assertEqual(unrelated.read_text(), "untouched")
        self.assertFalse((child / "DONE").exists())
        self.assertEqual(invalidation.read_pending(own), slot)

    def test_invalidation_then_prepare_reuses_pending_slot(self):
        own = self.folder("3.2")
        output = own / "one.json"
        output.write_text("output")
        slot = self.invalidate()["3.2"]
        self.assertEqual(self.prepare([output]), slot)
        output.write_text("new output")
        following = self.prepare([output])
        self.assertNotEqual(following, slot)
        self.assertEqual((following / "one.json").read_text(), "new output")
        self.assertEqual((slot / "one.json").read_text(), "output")

    def test_repeated_preparations_preserve_baseline_and_link_attempt_history(self):
        own = self.folder("3.2")
        child = self.folder("3.3")
        output = own / "one.json"
        output.write_text("first")
        first = self.prepare([output])
        child_slot = invalidation.read_pending(child)
        first_bytes = {path.name: path.read_bytes() for path in first.iterdir()}
        for value, previous in (("second", first), ("third", own / "STALE/2")):
            output.write_text(value)
            with patch.object(invalidation.completion, "archive_done", wraps=invalidation.completion.archive_done) as archive:
                slot = self.prepare([output])
            archive.assert_not_called()
            self.assertEqual((slot / "one.json").read_text(), value)
            self.assertEqual((slot / "DONE").read_text(), "3.2")
            self.assertNotEqual((slot / "DONE").stat().st_ino, (previous / "DONE").stat().st_ino)
            event = json.loads((slot / "EVENT.json").read_text())
            self.assertEqual(event["previous_slot"], int(previous.name))
            self.assertTrue(event["had_done"])
            self.assertEqual(event["artifacts"], ["one.json"])
            self.assertEqual(invalidation.read_pending(own), slot)
            self.assertEqual(self.invalidate()["3.2"], slot)
            self.assertEqual(invalidation.read_pending(child), child_slot)
        self.assertEqual({path.name: path.read_bytes() for path in first.iterdir()}, first_bytes)

    def test_repeated_prepare_without_done_allocates_one_new_slot_for_whole_set(self):
        own = self.folder("3.2", marker=False)
        first_file, second_file = own / "one.json", own / "nested/two.json"
        second_file.parent.mkdir()
        first_file.write_text("old first")
        first = self.prepare([first_file])
        first_file.write_text("new first")
        second_file.write_text("new second")
        slot = self.prepare([first_file, second_file])
        self.assertEqual((slot / "one.json").read_text(), "new first")
        self.assertEqual((slot / "nested/two.json").read_text(), "new second")
        self.assertFalse((slot / "DONE").exists())
        event = json.loads((slot / "EVENT.json").read_text())
        self.assertFalse(event["had_done"])
        self.assertEqual(event["previous_slot"], int(first.name))
        self.assertEqual(event["artifacts"], ["nested/two.json", "one.json"])
        self.assertEqual((first / "one.json").read_text(), "old first")
        self.assertEqual(invalidation.read_pending(own), slot)

    def test_noncolliding_prepares_and_marker_only_retries_keep_pending_slot(self):
        own = self.folder("3.2")
        first, second = own / "a.json", own / "b.json"
        first.write_text("first")
        slot = self.prepare([first])
        second.write_text("second")
        self.assertEqual(self.prepare([second]), slot)
        self.assertEqual(self.prepare([]), slot)
        self.assertEqual(self.invalidate()["3.2"], slot)
        self.assertEqual(json.loads((slot / "EVENT.json").read_text())["artifacts"], ["a.json", "b.json"])

    def test_repeated_prepare_move_failure_rolls_back_all_and_keeps_prior_history(self):
        own = self.folder("3.2")
        first, second = own / "a.json", own / "b.json"
        first.write_text("old first")
        previous = self.prepare([first])
        prior = {path.name: path.read_bytes() for path in previous.iterdir()}
        first.write_text("new first")
        second.write_text("new second")
        link = os.link

        def fail_second(source, destination):
            if source == second:
                raise OSError("second attempt move failure")
            return link(source, destination)

        with patch.object(invalidation.os, "link", fail_second):
            with self.assertRaisesRegex(OSError, "second attempt"):
                self.prepare([first, second])
        self.assertEqual(first.read_text(), "new first")
        self.assertEqual(second.read_text(), "new second")
        slot = invalidation.read_pending(own)
        self.assertNotEqual(slot, previous)
        self.assertFalse((slot / "a.json").exists())
        self.assertEqual((slot / "DONE").read_text(), "3.2")
        self.assertEqual({path.name: path.read_bytes() for path in previous.iterdir()}, prior)
        self.assertEqual(self.prepare([first, second]), slot)

    def test_repeated_prepare_baseline_copy_failure_keeps_previous_pointer(self):
        own = self.folder("3.2")
        output = own / "one.json"
        output.write_text("old")
        previous = self.prepare([output])
        output.write_text("new")
        with patch.object(invalidation.shutil, "copyfileobj", side_effect=OSError("copy failure")):
            with self.assertRaisesRegex(OSError, "copy failure"):
                self.prepare([output])
        self.assertEqual(invalidation.read_pending(own), previous)
        self.assertEqual(output.read_text(), "new")
        self.assertEqual((previous / "one.json").read_text(), "old")
        self.assertEqual(self.prepare([output]).name, "3")

    def test_repeated_prepare_baseline_copy_is_exclusive(self):
        own = self.folder("3.2")
        output = own / "one.json"
        output.write_text("old")
        previous = self.prepare([output])
        output.write_text("new")
        mkdir = Path.mkdir

        def inject_marker(path, *args, **kwargs):
            result = mkdir(path, *args, **kwargs)
            if path == own / "STALE/2":
                (path / "DONE").write_text("competing marker")
            return result

        with patch.object(Path, "mkdir", inject_marker):
            with self.assertRaises(FileExistsError):
                self.prepare([output])
        self.assertEqual((own / "STALE/2/DONE").read_text(), "competing marker")
        self.assertEqual(invalidation.read_pending(own), previous)
        self.assertEqual(output.read_text(), "new")

    def test_generated_ownership_inputs_media_and_approval_are_protected(self):
        own = self.folder("3.2")
        # Even an overly broad generated selector cannot grant media/input ownership.
        definition = self.stages["3.2"]
        definition["generated"] = [{"folder": definition["folder"], "filename_regex": ".*"},
                                   {"folder": definition["folder"] + "/APPROVED", "filename_regex": ".*"}]
        definition["inputs"].append({"folder": definition["folder"], "filename_regex": "input.json"})
        for relative in ("input.json", "raw.wav", "raw.mov", "raw.mp4", "DONE", "LOG.md",
                         ".DONE.lock", "STALE/a.json", "APPROVED/a.json", "not-owned/a.json"):
            with self.subTest(relative=relative):
                path = own / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("protected")
                with self.assertRaises(ValueError):
                    self.prepare([path])
                self.assertEqual(path.read_text(), "protected")
                self.assertFalse((own / "STALE/PENDING.json").exists())
        outside = self.project / "other.json"
        outside.write_text("outside")
        with self.assertRaises(ValueError):
            self.prepare([outside])

    def test_output_inventory_alone_does_not_grant_ownership(self):
        own = self.folder("3.2")
        output = own / "one.json"
        output.write_text("output")
        self.stages["3.2"]["generated"] = []
        with self.assertRaises(ValueError):
            self.prepare([output])
        self.assertTrue((own / "DONE").exists())

    def test_generated_event_and_pending_plan_names_outside_stale_are_allowed(self):
        own = self.folder("1.2")
        definition = self.stages["1.2"]
        definition["generated"] = [{"folder": definition["folder"] + "/working",
                                    "filename_regex": r".*\.json"}]
        working = own / "working"
        working.mkdir()
        paths = [working / "Event.json", working / "Pending.json"]
        for path in paths:
            path.write_text(path.name)
        slot = self.prepare(paths, step="1.2")
        for path in paths:
            self.assertEqual((slot / "working" / path.name).read_text(), path.name)

    def test_generated_project_render_input_is_still_owned_not_approved_media(self):
        own = self.folder("3.6")
        definition = self.stages["3.6"]
        definition["generated"] = [{"folder": definition["folder"], "filename_regex": r".*\.FCPXML"}]
        output = own / "Cut.FCPXML"
        output.write_text("generated project")
        # Real First Cut inventories declare XML as role=project render inputs.
        self.assertTrue(invalidation._matches(output, self.project, definition["inputs"]))
        slot = self.prepare([output], step="3.6")
        self.assertEqual((slot / "Cut.FCPXML").read_text(), "generated project")

    def test_multiple_file_preflight_rejects_all_before_any_mutation(self):
        own = self.folder("3.2")
        good = own / "good.json"
        good.write_text("good")
        bad = own / "bad.wav"
        bad.write_text("bad")
        with self.assertRaises(ValueError):
            self.prepare([good, bad])
        self.assertEqual(good.read_text(), "good")
        self.assertTrue((own / "DONE").exists())
        self.assertFalse((own / "STALE").exists())
        self.assertFalse(self.logs)

    def test_all_archive_destinations_preflight_before_moving_any_output(self):
        own = self.folder("3.2")
        slot = self.invalidate()["3.2"]
        first, second = own / "a.json", own / "b.json"
        first.write_text("first")
        second.write_text("second")
        (slot / "b.json").write_text("already archived")
        with self.assertRaisesRegex(ValueError, "already occupied"):
            self.prepare([first, second])
        self.assertEqual(first.read_text(), "first")
        self.assertEqual(second.read_text(), "second")
        self.assertFalse((slot / "a.json").exists())
        self.assertEqual((slot / "b.json").read_text(), "already archived")

    def test_move_failure_rolls_back_outputs_but_preserves_done_history(self):
        own = self.folder("3.2")
        files = [own / "a.json", own / "b.json"]
        for source in files:
            source.write_text(source.name)
        link = os.link

        def fail_second(source, destination):
            if source == files[1]:
                raise OSError("injected move failure")
            return link(source, destination)

        with patch.object(invalidation.os, "link", fail_second):
            with self.assertRaisesRegex(OSError, "injected"):
                self.prepare(files)
        slot = invalidation.read_pending(own)
        self.assertEqual((slot / "DONE").read_text(), "3.2")
        for source in files:
            self.assertEqual(source.read_text(), source.name)
            self.assertFalse((slot / source.name).exists())
        self.assertFalse((own / ".DONE.lock").exists())

    def test_destination_race_never_overwrites_archive(self):
        own = self.folder("3.2")
        output = own / "a.json"
        output.write_text("current output")
        link = os.link

        def publish_competing_archive(source, destination):
            if source == output:
                destination.write_text("competing archive")
            return link(source, destination)

        with patch.object(invalidation.os, "link", publish_competing_archive):
            with self.assertRaises(FileExistsError):
                self.prepare([output])
        self.assertEqual(output.read_text(), "current output")
        slot = invalidation.read_pending(own)
        self.assertEqual((slot / "a.json").read_text(), "competing archive")
        self.assertTrue((slot / "DONE").is_file())

    def test_new_attempt_destination_race_fails_without_allocating_again(self):
        own = self.folder("3.2")
        output = own / "a.json"
        output.write_text("old")
        previous = self.prepare([output])
        output.write_text("new")
        link = os.link

        def publish_competing_archive(source, destination):
            if source == output:
                destination.write_text("competing archive")
            return link(source, destination)

        with patch.object(invalidation.os, "link", publish_competing_archive):
            with self.assertRaises(FileExistsError):
                self.prepare([output])
        slot = invalidation.read_pending(own)
        self.assertEqual(slot.name, "2")
        self.assertEqual((slot / "a.json").read_text(), "competing archive")
        self.assertEqual((previous / "a.json").read_text(), "old")
        self.assertEqual(output.read_text(), "new")
        with self.assertRaisesRegex(ValueError, "already occupied"):
            self.prepare([output])
        self.assertFalse((own / "STALE/3").exists())

    def test_failed_unlink_restores_original_without_archive_duplicate(self):
        own = self.folder("3.2")
        output = own / "a.json"
        output.write_text("current output")
        unlink = Path.unlink

        def fail_source_unlink(path, *args, **kwargs):
            if path == output:
                raise OSError("injected source unlink failure")
            return unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", fail_source_unlink):
            with self.assertRaisesRegex(OSError, "injected source unlink"):
                self.prepare([output])
        self.assertEqual(output.read_text(), "current output")
        self.assertFalse((invalidation.read_pending(own) / "a.json").exists())

    def test_rollback_never_overwrites_new_current_file(self):
        own = self.folder("3.2")
        output = own / "a.json"
        output.write_text("original")

        def fail_log(*args):
            output.write_text("new current")
            raise OSError("log failure")

        with self.assertRaisesRegex(RuntimeError, "recovery"):
            self.prepare([output], logger=fail_log)
        self.assertEqual(output.read_text(), "new current")
        self.assertEqual((invalidation.read_pending(own) / "a.json").read_text(), "original")

    def test_log_failure_rolls_back_outputs(self):
        own = self.folder("3.2")
        output = own / "a.json"
        output.write_text("original")
        with self.assertRaisesRegex(OSError, "log failure"):
            self.prepare([output], logger=lambda *args: (_ for _ in ()).throw(OSError("log failure")))
        self.assertEqual(output.read_text(), "original")
        self.assertTrue((invalidation.read_pending(own) / "DONE").is_file())

    def test_lock_collision_preflight_releases_only_our_locks(self):
        own = self.folder("3.2")
        child = self.folder("3.6")
        lock = child / ".DONE.lock"
        lock.mkdir()
        token = lock / "owner"
        token.write_text("someone else")
        with self.assertRaisesRegex(ValueError, "already active"):
            self.invalidate()
        self.assertEqual(token.read_text(), "someone else")
        self.assertFalse((own / ".DONE.lock").exists())
        self.assertTrue((own / "DONE").exists())
        self.assertTrue((child / "DONE").exists())
        self.assertFalse((own / "STALE").exists())

    def test_lock_replacement_is_not_removed_during_cleanup(self):
        own = self.folder("3.2")
        lock = own / ".DONE.lock"

        def replace_lock(*args):
            lock.rename(own / "original-lock")
            lock.mkdir()
            (lock / "owner").write_text("new owner")

        invalidation.invalidate(self.project, WORKFLOW, "3.2", "changed", replace_lock)
        self.assertEqual((lock / "owner").read_text(), "new owner")

    def test_all_marker_and_log_paths_preflight_before_any_move(self):
        own = self.folder("3.2")
        child = self.folder("3.6")
        outside = self.project / "outside"
        outside.write_text("outside")
        for name in ("DONE", "LOG.md", "STALE", ".DONE.lock"):
            with self.subTest(name=name):
                path = child / name
                if path.exists():
                    path.unlink()
                path.symlink_to(outside)
                with self.assertRaises(ValueError):
                    self.invalidate()
                self.assertTrue((own / "DONE").exists())
                self.assertFalse((own / "STALE").exists())
                self.assertEqual(outside.read_text(), "outside")
                path.unlink()
                (child / "DONE").write_text("3.6")

    def test_symlinked_output_or_parent_is_never_followed(self):
        own = self.folder("3.2")
        external = self.project / "external"
        external.mkdir()
        (external / "out.json").write_text("external")
        link = own / "out.json"
        link.symlink_to(external / "out.json")
        with self.assertRaises(ValueError):
            self.prepare([link])
        (own / "nested").symlink_to(external, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.prepare([own / "nested/out.json"])
        self.assertTrue((own / "DONE").exists())

    def test_broken_symlink_slots_are_occupied_not_replaced(self):
        own = self.folder("3.2")
        stale = own / "STALE"
        stale.mkdir()
        (stale / "1").symlink_to(own / "missing")
        slot = self.invalidate()["3.2"]
        self.assertEqual(slot, stale / "2")
        self.assertTrue((stale / "1").is_symlink())
        self.assertFalse((own / "missing").exists())

    def test_pending_slot_symlink_is_rejected_before_other_markers_move(self):
        own = self.folder("3.2")
        child = self.folder("3.6")
        slot = self.invalidate()["3.2"]
        real_slot = slot.with_name("saved")
        slot.rename(real_slot)
        slot.symlink_to(real_slot, target_is_directory=True)
        (child / "DONE").write_text("child")
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.invalidate()
        self.assertTrue((child / "DONE").exists())
        self.assertTrue((real_slot / "DONE").exists())

    def test_unknown_later_stage_aggregate_without_step_folder_is_invalidated(self):
        self.folder("3.4")
        future = self.project / "4-Future"
        future.mkdir()
        (future / "DONE").write_text("future stage")
        slots = self.invalidate("3.4")
        self.assertEqual((slots["stage:4"] / "DONE").read_text(), "future stage")

    def test_archive_done_is_called_with_all_affected_locks_held(self):
        own = self.folder("3.2")
        child = self.folder("3.6")
        archive = invalidation.completion.archive_done

        def check_locks(directory):
            self.assertTrue((own / ".DONE.lock").is_dir())
            self.assertTrue((child / ".DONE.lock").is_dir())
            return archive(directory)

        with patch.object(invalidation.completion, "archive_done", side_effect=check_locks) as call:
            self.invalidate()
        self.assertEqual(call.call_count, 2)

    def test_read_and_clear_pending_reject_redirected_or_invalid_pointers(self):
        own = self.folder("3.2")
        slot = self.invalidate()["3.2"]
        pointer = own / "STALE/PENDING.json"
        valid = pointer.read_text()
        for value in (0, -1, "../outside", True):
            data = json.loads(valid)
            data["slot"] = value
            pointer.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                invalidation.read_pending(own)
            with self.assertRaises(ValueError):
                invalidation.clear_pending(own)
        pointer.write_text(valid)
        with self.assertRaises(ValueError):
            invalidation.read_pending(own, "3.1")
        (slot / "EVENT.json").unlink()
        (slot / "EVENT.json").symlink_to(self.project / "missing-event")
        with self.assertRaises(ValueError):
            invalidation.clear_pending(own)
        self.assertTrue(pointer.exists())

    def test_no_latest_number_guess_for_missing_pending(self):
        own = self.folder("3.2", marker=False)
        historic = own / "STALE/99"
        historic.mkdir(parents=True)
        (historic / "DONE").write_text("historical")
        self.assertIsNone(invalidation.read_pending(own))
        invalidation.clear_pending(own)
        slot = self.invalidate()["3.2"]
        self.assertEqual(slot.name, "100")
        self.assertEqual((historic / "DONE").read_text(), "historical")

    def test_deleted_archive_gaps_do_not_reorder_new_marker_or_attempt_history(self):
        own = self.folder("3.2")
        stale = own / "STALE"
        older = stale / "2"
        older.mkdir(parents=True)
        (older / "DONE").write_text("older")
        slot = self.invalidate()["3.2"]
        self.assertEqual(slot.name, "3")
        output = own / "a.json"
        output.write_text("first")
        self.assertEqual(self.prepare([output]), slot)
        (stale / "6").symlink_to(own / "missing")
        output.write_text("second")
        attempt = self.prepare([output])
        self.assertEqual(attempt.name, "7")
        self.assertEqual(json.loads((attempt / "EVENT.json").read_text())["previous_slot"], 3)
        self.assertFalse((stale / "1").exists())
        self.assertEqual((older / "DONE").read_text(), "older")

    def test_default_logging_uses_inprocess_append_helper(self):
        own = self.folder("3.2")
        invalidation.invalidate(self.project, WORKFLOW, "3.2", "source changed")
        text = (own / "LOG.md").read_text()
        self.assertIn("Completion invalidated", text)
        self.assertIn("source changed", text)
        self.assertIn("outputs preserved", text)

    def test_distinct_jobs_have_distinct_pointers_and_shared_explicit_baseline(self):
        own = self.folder("3.1")
        child = self.folder("3.5")
        (own.parent / "DONE").write_text("aggregate")
        slots = {}
        for job in ("clip/A", "clip/B"):
            output = own / (job[-1] + ".json")
            output.write_text(job)
            slots[job] = self.prepare([output], step="3.1", job_id=job)
            self.assertEqual((slots[job] / output.name).read_text(), job)
            digest = hashlib.sha256(job.encode("utf-8")).hexdigest()
            pointer = own / "STALE/PENDING" / f"{digest}.json"
            self.assertEqual(json.loads(pointer.read_text())["job_id"], job)
            self.assertEqual(invalidation.read_pending(own, "3.1", job_id=job), slots[job])
        baseline = invalidation.read_pending(own, "3.1")
        self.assertEqual(len({baseline, *slots.values()}), 3)
        for job, slot in slots.items():
            event = json.loads((slot / "EVENT.json").read_text())
            self.assertEqual(event["job_id"], job)
            self.assertEqual(event["baseline_slot"], int(baseline.name))
            self.assertEqual((slot / "DONE").read_text(), "3.1")
            self.assertNotEqual((slot / "DONE").stat().st_ino, (baseline / "DONE").stat().st_ino)
        self.assertNotIn("job_id", json.loads((baseline / "EVENT.json").read_text()))
        self.assertEqual(invalidation.read_all_pending(own), {None: baseline, **slots})
        self.assertEqual(set(invalidation.read_all_pending(child)), {None})
        self.assertEqual(set(invalidation.read_all_pending(own.parent)), {None})

    def test_scoped_same_job_collision_preserves_baseline_and_previous_slot(self):
        own = self.folder("3.2")
        output = own / "one.json"
        output.write_text("first")
        first = self.prepare([output], job_id="same")
        baseline = invalidation.read_pending(own)
        output.write_text("second")
        second = self.prepare([output], job_id="same")
        event = json.loads((second / "EVENT.json").read_text())
        self.assertEqual(event["previous_slot"], int(first.name))
        self.assertEqual(event["baseline_slot"], int(baseline.name))
        self.assertEqual((second / "DONE").read_text(), "3.2")
        self.assertEqual((first / "one.json").read_text(), "first")
        self.assertEqual((second / "one.json").read_text(), "second")
        self.assertEqual(self.invalidate(job_id="same")["3.2"], second)
        self.assertEqual(invalidation.read_pending(own), baseline)

    def test_scoped_uses_legacy_baseline_not_latest_archived_done(self):
        own = self.folder("3.2")
        baseline = self.invalidate()["3.2"]
        historic = own / "STALE/99"
        historic.mkdir()
        (historic / "DONE").write_text("not the baseline")
        slot = self.prepare([], job_id="new")
        self.assertEqual(slot.name, "100")
        self.assertEqual((slot / "DONE").read_text(), "3.2")
        self.assertEqual(json.loads((slot / "EVENT.json").read_text())["baseline_slot"], int(baseline.name))

    def test_scoped_without_explicit_baseline_never_recovers_historical_done(self):
        own = self.folder("3.2", marker=False)
        historic = own / "STALE/99"
        historic.mkdir(parents=True)
        (historic / "DONE").write_text("historical")
        slot = self.prepare([], job_id="new")
        baseline = invalidation.read_pending(own)
        self.assertEqual(baseline.name, "100")
        self.assertEqual(slot.name, "101")
        self.assertFalse((slot / "DONE").exists())
        self.assertFalse(json.loads((slot / "EVENT.json").read_text())["had_done"])

    def test_new_active_marker_starts_new_baseline_for_existing_job(self):
        own = self.folder("3.2")
        first = self.prepare([], job_id="same")
        (own / "DONE").write_text("new baseline")
        second = self.prepare([], job_id="same")
        baseline = invalidation.read_pending(own)
        self.assertNotEqual(first, second)
        self.assertEqual((first / "DONE").read_text(), "3.2")
        self.assertEqual((second / "DONE").read_text(), "new baseline")
        self.assertEqual(json.loads((second / "EVENT.json").read_text())["baseline_slot"], int(baseline.name))

    def test_all_pending_cleanup_prevalidates_scoped_records_before_any_unlink(self):
        own = self.folder("3.2")
        first = self.prepare([], job_id="one")
        second = self.prepare([], job_id="two")
        event = second / "EVENT.json"
        valid = event.read_text()
        data = json.loads(valid)
        data["job_id"] = "wrong"
        event.write_text(json.dumps(data))
        pointers = [own / "STALE/PENDING.json", *sorted((own / "STALE/PENDING").iterdir())]
        for action in (lambda: invalidation.read_all_pending(own),
                       lambda: invalidation.clear_pending(own),
                       lambda: self.invalidate(),
                       lambda: self.prepare([], job_id="one")):
            with self.assertRaises(ValueError):
                action()
            self.assertTrue(all(path.exists() for path in pointers))
        event.write_text(valid)
        invalidation.clear_pending(own, job_id="one")
        self.assertIsNone(invalidation.read_pending(own, job_id="one"))
        self.assertIsNotNone(invalidation.read_pending(own))
        self.assertEqual(invalidation.read_pending(own, job_id="two"), second)
        invalidation.clear_pending(own)
        self.assertEqual(invalidation.read_all_pending(own), {})
        self.assertTrue((first / "DONE").exists())
        self.assertTrue((second / "DONE").exists())
        self.assertFalse(any(path.exists() for path in pointers))

    def test_scoped_pointer_identity_filename_and_symlink_are_validated(self):
        own = self.folder("3.2")
        self.prepare([], job_id="one")
        pointer = next((own / "STALE/PENDING").iterdir())
        valid = pointer.read_text()
        data = json.loads(valid)
        data["job_id"] = "two"
        pointer.write_text(json.dumps(data))
        with self.assertRaises(ValueError):
            invalidation.read_pending(own, job_id="one")
        with self.assertRaises(ValueError):
            invalidation.read_all_pending(own)
        pointer.write_text(valid)
        renamed = pointer.with_name("wrong.json")
        pointer.rename(renamed)
        with self.assertRaises(ValueError):
            invalidation.clear_pending(own)
        renamed.rename(pointer)
        pointer.unlink()
        pointer.symlink_to(self.project / "missing")
        with self.assertRaisesRegex(ValueError, "symlink"):
            invalidation.read_all_pending(own)

    def test_scoped_baseline_copy_failure_preserves_legacy_pointer(self):
        own = self.folder("3.2")
        output = own / "one.json"
        output.write_text("original")
        with patch.object(invalidation.shutil, "copyfileobj", side_effect=OSError("copy failure")):
            with self.assertRaisesRegex(OSError, "copy failure"):
                self.prepare([output], job_id="one")
        baseline = invalidation.read_pending(own)
        self.assertEqual((baseline / "DONE").read_text(), "3.2")
        self.assertIsNone(invalidation.read_pending(own, job_id="one"))
        self.assertEqual(output.read_text(), "original")
        slot = self.prepare([output], job_id="one")
        self.assertEqual(slot.name, "3")
        self.assertEqual(json.loads((slot / "EVENT.json").read_text())["baseline_slot"], int(baseline.name))


if __name__ == "__main__":
    unittest.main()
