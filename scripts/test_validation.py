"""Shared read-only validation, approval gates, and fail-closed evidence checks."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts import stage_completion, validation


class CommonValidationTests(unittest.TestCase):
    def test_defined_human_gates_are_no_longer_skipped(self):
        definitions = validation.load_stages()
        with TemporaryDirectory() as directory:
            project = Path(directory)
            for step in ("1.2", "1.3", "3.6"):
                result = validation.check_marker(project, validation.ROOT, definitions[step])
                self.assertEqual(result[0], "missing")
            self.assertEqual(list(project.iterdir()), [])

    def gate_fixture(self, project, step):
        definition = validation.load_stages()[step]
        source = project / definition["inputs"][0]["folder"] / ("A__Scores.v1.json" if step == "3.6" else "A.docx")
        source.parent.mkdir(parents=True)
        source.write_bytes(b"source")
        if step == "3.6":
            for relative in (
                "1-Pre Production/1-Script Review/Approved/A.docx",
                "1-Pre Production/3-Extracts/APPROVED/Short-Form Excerpt Shortlist.md",
                "2-Production/1-Footage Intake/A__C01__camA.mov",
                "2-Production/1-Footage Intake/A__C01__camB.mp4",
                "2-Production/1-Footage Intake/A__C01__lav.wav",
                "3-Post Production/1-Sync/A__C01.json",
                "3-Post Production/2-Transcribe/A__C01__lav.json",
                "3-Post Production/3-Find Takes/A__Takes.v1.json",
                "3-Post Production/5-Face Tracking/Face Paths.json",
            ):
                dependency = project / relative
                dependency.parent.mkdir(parents=True, exist_ok=True)
                dependency.write_bytes(b"generation source")
        name = {"1.2": "A.pptx", "1.3": "Short-Form Excerpt Shortlist.md", "3.6": "A.mov"}[step]
        output = project / definition["outputs"][0]["folder"] / name
        output.parent.mkdir(parents=True)
        output.write_bytes(b"approved output")
        marker = project / definition["folder"] / "DONE"
        self.record_gate(project, definition, [source], [output])
        return definition, source, output, marker

    def record_gate(self, project, definition, sources, outputs):
        if definition["step"] == "3.6":
            import re
            sources = set(sources)
            for inventory in definition["inputs"]:
                folder = project / inventory["folder"]
                if folder.is_dir():
                    sources.update(path for path in folder.iterdir()
                                   if path.is_file() and re.fullmatch(inventory["filename_regex"], path.name, re.I))
            sources = sorted(sources)
        marker = project / definition["folder"] / "DONE"
        marker.write_text(json.dumps({"step": definition["step"], "dependencies": [],
                                      "inputs": [stage_completion.fingerprint(path, project, "project") for path in sources],
                                      "outputs": [stage_completion.fingerprint(path, project, "project") for path in outputs]}))

    def test_gate_presence_and_input_fingerprints_validate_without_review_tools(self):
        for step in ("1.2", "1.3", "3.6"):
            with self.subTest(step=step), TemporaryDirectory() as directory:
                project = Path(directory).resolve()
                definition, source, output, marker = self.gate_fixture(project, step)
                before = {path.relative_to(project): (path.read_bytes(), path.stat().st_mtime_ns)
                          for path in project.rglob("*") if path.is_file()}
                self.assertEqual(validation.check_marker(project, validation.ROOT, definition)[0], "current")
                after = {path.relative_to(project): (path.read_bytes(), path.stat().st_mtime_ns)
                         for path in project.rglob("*") if path.is_file()}
                self.assertEqual(before, after)
                source.write_bytes(b"changed source")
                self.assertEqual(validation.check_marker(project, validation.ROOT, definition)[0], "stale")
                self.assertEqual(marker.read_bytes(), before[marker.relative_to(project)][0])
                self.assertTrue(output.is_file())

    def test_unapproved_root_files_cannot_satisfy_approval(self):
        for step in ("1.3", "3.6"):
            with self.subTest(step=step), TemporaryDirectory() as directory:
                project = Path(directory).resolve()
                definition, source, output, marker = self.gate_fixture(project, step)
                draft = project / definition["folder"] / output.name
                output.rename(draft)
                self.record_gate(project, definition, [source], [draft])
                self.assertEqual(validation.check_marker(project, validation.ROOT, definition)[0], "stale")
                self.assertTrue(marker.is_file())
                self.assertFalse((marker.parent / "STALE").exists())

    def test_all_source_scripts_need_corresponding_decks_or_approved_movies(self):
        for step in ("1.2", "3.6"):
            with self.subTest(step=step), TemporaryDirectory() as directory:
                project = Path(directory).resolve()
                definition, source, output, _ = self.gate_fixture(project, step)
                other = source.with_name("B__Scores.v1.json" if step == "3.6" else "B.docx")
                other.write_bytes(b"another source")
                self.record_gate(project, definition, [source, other], [output])
                status, reason = validation.check_marker(project, validation.ROOT, definition)
                self.assertEqual(status, "stale")
                self.assertIn("Missing", reason)

    def test_first_cut_accepts_supported_render_formats_case_insensitively(self):
        for suffix in ("MOV", "mp4", "m4v", "mkv", "avi", "webm", "mxf"):
            with self.subTest(suffix=suffix), TemporaryDirectory() as directory:
                project = Path(directory).resolve()
                definition, source, output, _ = self.gate_fixture(project, "3.6")
                movie = output.with_name(f"a.{suffix}")
                output.rename(movie)
                self.record_gate(project, definition, [source], [movie])
                self.assertEqual(validation.check_marker(project, validation.ROOT, definition)[0], "current")

    def test_first_cut_xml_or_empty_movies_cannot_prove_rendered_completion(self):
        for invalid in ("xml", "empty"):
            with self.subTest(invalid=invalid), TemporaryDirectory() as directory:
                project = Path(directory).resolve()
                definition, source, output, _ = self.gate_fixture(project, "3.6")
                if invalid == "xml":
                    movie = output.with_suffix(".FCPXML")
                    output.rename(movie)
                else:
                    movie = output
                    movie.write_bytes(b"")
                self.record_gate(project, definition, [source], [movie])
                self.assertEqual(validation.check_marker(project, validation.ROOT, definition)[0], "stale")

    def test_generated_extract_projects_require_their_own_render(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            definition, source, output, marker = self.gate_fixture(project, "3.6")
            xml = marker.parent / "Extracts/01-Example.FCPXML"
            xml.parent.mkdir()
            xml.write_text("<fcpxml />")
            self.record_gate(project, definition, [source, xml], [output])
            status, reason = validation.check_marker(project, validation.ROOT, definition)
            self.assertEqual(status, "stale")
            self.assertIn("01-Example", reason)
            rendered = output.with_name("01-Example.mp4")
            rendered.write_bytes(b"rendered extract")
            self.record_gate(project, definition, [source, xml], [output, rendered])
            self.assertEqual(validation.check_marker(project, validation.ROOT, definition)[0], "current")
            xml.write_text("<fcpxml version='changed' />")
            self.assertEqual(validation.check_marker(project, validation.ROOT, definition)[0], "stale")

    def test_first_cut_records_actual_generation_sources_not_only_scores(self):
        for relative in ("3-Post Production/1-Sync/A__C01.json",
                         "1-Pre Production/3-Extracts/APPROVED/Short-Form Excerpt Shortlist.md",
                         "3-Post Production/5-Face Tracking/Face Paths.json"):
            with self.subTest(source=relative), TemporaryDirectory() as directory:
                project = Path(directory).resolve()
                definition, _, _, _ = self.gate_fixture(project, "3.6")
                self.assertEqual(validation.check_marker(project, validation.ROOT, definition)[0], "current")
                (project / relative).write_bytes(b"changed generation source")
                self.assertEqual(validation.check_marker(project, validation.ROOT, definition)[0], "stale")

    def test_fresh_first_cut_evidence_cannot_reapprove_old_movies_after_input_changes(self):
        for archived in (False, True):
            with self.subTest(archived=archived), TemporaryDirectory() as directory:
                project = Path(directory).resolve()
                definition, _, movie, marker = self.gate_fixture(project, "3.6")
                previous = json.loads(marker.read_text())
                if archived:
                    stage_completion.archive_done(marker.parent)
                changed = project / "3-Post Production/1-Sync/A__C01.json"
                changed.write_bytes(b"new synchronization offsets")
                candidate = dict(previous, inputs=[stage_completion.fingerprint(project / entry["path"], project, "project")
                                                   for entry in previous["inputs"]])
                before = {path.relative_to(project): (path.read_bytes(), path.stat().st_mtime_ns)
                          for path in project.rglob("*") if path.is_file()}
                status, reason = validation.validate_evidence(project, validation.ROOT, definition, candidate)
                self.assertEqual(status, "stale")
                self.assertIn("new approved rendered movie", reason)
                self.assertEqual({path.relative_to(project): (path.read_bytes(), path.stat().st_mtime_ns)
                                  for path in project.rglob("*") if path.is_file()}, before)
                movie.write_bytes(b"newly edited and rendered movie with different identity")
                candidate["outputs"] = [stage_completion.fingerprint(movie, project, "project")]
                self.assertEqual(validation.validate_evidence(project, validation.ROOT, definition, candidate)[0], "current")

    def test_script_review_checks_approval_inventory_without_writing(self):
        definition = validation.load_stages()["1.1"]
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            folder = project / definition["folder"]
            approved = folder / "Approved"
            approved.mkdir(parents=True)
            script = approved / "Script.docx"
            script.write_bytes(b"approved script")
            marker = folder / "DONE"
            marker.write_text(json.dumps({"step": "1.1", "inputs": [], "dependencies": [],
                                          "outputs": [stage_completion.fingerprint(script, project, "project")]}))
            before = marker.read_bytes()
            self.assertEqual(validation.check_marker(project, validation.ROOT, definition)[0], "current")
            (folder / "Candidate.docx").write_bytes(b"not approved")
            self.assertEqual(validation.check_marker(project, validation.ROOT, definition)[0], "stale")
            self.assertEqual(marker.read_bytes(), before)
            self.assertFalse((folder / "STALE").exists())

    def test_declared_validator_must_extend_common_class(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            stage = root / ".agents/skills/studio/stage/step"
            stage.mkdir(parents=True)
            (stage / "validation.py").write_text("class Validation: pass\n")
            (root / "stage/step").mkdir(parents=True)
            (root / "stage/step/DONE").write_text("{}")
            definition = {"folder": "stage/step", "step": "1.1", "validation": {"script": "validation.py"}}
            result = validation.check_marker(root, root, definition)
            self.assertEqual(result[0], "unverifiable")
            self.assertIn("extend StageValidation", result[1])

    def test_override_cannot_bypass_common_fingerprint_checks(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "data.json"
            source.write_text("{}")
            (root / "step").mkdir()
            (root / "step/DONE").write_text(json.dumps({"outputs": [stage_completion.fingerprint(source, root, "project")]}))
            definition = {"step": "1.1", "folder": "step", "inputs": [], "outputs": []}

            class CustomValidation(validation.StageValidation):
                def validate_stage(self):
                    raise AssertionError("Should not run with stale fingerprints")

            source.write_text('{"changed": true}')
            self.assertEqual(CustomValidation(root, root, definition).validate()[0], "stale")

    def test_legacy_media_stats_remain_readable_without_precision_conversion(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            media = root / "source.wav"
            media.write_bytes(b"audio")
            stat = media.stat()
            entry = {"scope": "project", "path": "source.wav", "bytes": stat.st_size,
                     "mtime_ns": stat.st_mtime_ns}
            self.assertEqual(validation.recorded_fingerprints({"inputs": {"media": entry}}), [entry])
            self.assertTrue(stage_completion.matches_entry(entry, root, root))
            self.assertFalse(stage_completion.matches_entry(dict(entry, mtime_ns=stat.st_mtime_ns + 1), root, root))

    def test_stage_override_executes_after_common_checks(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            code = root / ".agents/skills/studio/group/step"
            code.mkdir(parents=True)
            (code / "validation.py").write_text(
                "from scripts.validation import StageValidation\n"
                "class Validation(StageValidation):\n"
                "    def validate_stage(self):\n"
                "        raise ValueError('stage rule failed')\n")
            stage = root / "group/step"
            stage.mkdir(parents=True)
            data = stage / "data.json"
            data.write_text("{}")
            (stage / "DONE").write_text(json.dumps({"outputs": [stage_completion.fingerprint(data, root, "project")]}))
            definition = {"step": "1.1", "folder": "group/step", "inputs": [], "outputs": [],
                          "validation": {"script": "validation.py"}}
            status, reason = validation.check_marker(root, root, definition)
            self.assertEqual(status, "stale")
            self.assertIn("stage rule failed", reason)
            self.assertFalse((stage / "STALE").exists())

    def test_inventory_minimum_requires_distinct_fingerprint_evidence(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            stage = root / "step"
            stage.mkdir()
            one = stage / "one.json"
            one.write_text("{}")
            (stage / "two.json").write_text("{}")
            (stage / "DONE").write_text(json.dumps({"outputs": [stage_completion.fingerprint(one, root, "project")]}))
            definition = {"step": "1.1", "folder": "step", "inputs": [], "outputs": [
                {"folder": "step", "filename_regex": "^.+\\.json$", "minimum": 2}]}
            self.assertEqual(validation.check_marker(root, root, definition)[0], "unverifiable")

    def test_stat_fingerprints_cannot_substitute_for_document_hashes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "data.json"
            source.write_text("{}")
            entry = {"scope": "project", "path": "data.json", "method": "media-stat",
                     "size_bytes": source.stat().st_size, "mtime_s": source.stat().st_mtime_ns // 1_000_000_000}
            with self.assertRaisesRegex(ValueError, "non-media"):
                stage_completion.matches_entry(entry, root, root)

    def test_plugin_import_failure_isolated_as_unverifiable(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            code = root / ".agents/skills/studio/group/step"
            code.mkdir(parents=True)
            (code / "validation.py").write_text("raise RuntimeError('broken plugin')\n")
            stage = root / "group/step"
            stage.mkdir(parents=True)
            (stage / "DONE").write_text("{}")
            definition = {"step": "1.1", "folder": "group/step", "validation": {"script": "validation.py"}}
            status, reason = validation.check_marker(root, root, definition)
            self.assertEqual(status, "unverifiable")
            self.assertIn("broken plugin", reason)

    def test_skips_remain_explicit_and_do_not_approve_dependents(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            definitions = {
                "1.1": {"step": "1.1", "folder": "review", "prerequisites": [],
                        "validation": {"skip_reason": "Approval workflow unresolved"}},
                "1.2": {"step": "1.2", "folder": "cards", "prerequisites": [{"step": "1.1"}],
                        "validation": {"skip_reason": "Approval workflow unresolved"}},
                "2.1": {"step": "2.1", "folder": "intake", "prerequisites": [{"step": "1.2"}]},
            }
            results = validation.audit(root, root, stages=definitions)
            self.assertEqual(results["1.1"]["status"], "skipped")
            self.assertEqual(results["1.2"]["status"], "skipped")
            self.assertEqual(results["2.1"]["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
