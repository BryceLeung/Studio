"""Shared read-only validation, approval gates, and fail-closed evidence checks."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from scripts import stage_completion, validation
from scripts.script_docx_fixture import write_script_docx


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
        for relative in ("Extracts/01-Example.FCPXML", "Extracts/Portrait/01-Example Portrait.FCPXML"):
            with self.subTest(project=relative), TemporaryDirectory() as directory:
                project = Path(directory).resolve()
                definition, source, output, marker = self.gate_fixture(project, "3.6")
                xml = marker.parent / relative
                xml.parent.mkdir(parents=True)
                xml.write_text("<fcpxml />")
                self.record_gate(project, definition, [source, xml], [output])
                status, reason = validation.check_marker(project, validation.ROOT, definition)
                self.assertEqual(status, "stale")
                self.assertIn(xml.stem, reason)
                rendered = output.with_name(xml.stem + ".mp4")
                rendered.write_bytes(b"rendered extract")
                self.record_gate(project, definition, [source, xml], [output, rendered])
                self.assertEqual(validation.check_marker(project, validation.ROOT, definition)[0], "current")
                xml.write_text("<fcpxml version='changed' />")
                self.assertEqual(validation.check_marker(project, validation.ROOT, definition)[0], "stale")

    def test_new_portrait_project_invalidates_complete_input_inventory(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            definition, _, output, marker = self.gate_fixture(project, "3.6")
            xml = marker.parent / "Extracts/Portrait/01-Example Portrait.FCPXML"
            xml.parent.mkdir(parents=True)
            xml.write_text("<fcpxml />")
            rendered = output.with_name(xml.stem + ".mp4")
            rendered.write_bytes(b"rendered portrait extract")
            evidence = json.loads(marker.read_text())
            evidence["outputs"].append(stage_completion.fingerprint(rendered, project, "project"))
            marker.write_text(json.dumps(evidence))
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

    def test_script_review_checks_approval_and_root_inventory_without_writing(self):
        definition = validation.load_stages()["1.1"]
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            folder = project / definition["folder"]
            approved = folder / "Approved"
            approved.mkdir(parents=True)
            script = approved / "Script.docx"
            write_script_docx(script)
            marker = folder / "DONE"
            marker.write_text(json.dumps({"step": "1.1", "inputs": [], "dependencies": [],
                                          "outputs": [stage_completion.fingerprint(script, project, "project")]}))
            (folder / "Candidate.docx").write_bytes(b"not approved")
            before = {path.relative_to(project): (path.read_bytes(), path.stat().st_mtime_ns)
                      for path in project.rglob("*") if path.is_file()}
            status, reason = validation.check_marker(project, validation.ROOT, definition)
            self.assertEqual(status, "stale")
            self.assertIn("found 1", reason)
            self.assertEqual({path.relative_to(project): (path.read_bytes(), path.stat().st_mtime_ns)
                              for path in project.rglob("*") if path.is_file()}, before)
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

    def test_legacy_media_stats_are_compared_in_seconds(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            media = root / "source.wav"
            media.write_bytes(b"audio")
            stat = media.stat()
            entry = {"scope": "project", "path": "source.wav", "bytes": stat.st_size,
                     "mtime_ns": stat.st_mtime_ns}
            self.assertEqual(validation.recorded_fingerprints({"inputs": {"media": entry}}), [entry])
            self.assertTrue(stage_completion.matches_entry(entry, root, root))
            self.assertTrue(stage_completion.matches_entry(
                dict(entry, mtime_ns=stat.st_mtime_ns // 1_000_000_000 * 1_000_000_000), root, root))
            self.assertFalse(stage_completion.matches_entry(
                dict(entry, mtime_ns=stat.st_mtime_ns + 1_000_000_000), root, root))

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


class ScriptReviewValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name).resolve()
        self.definition = validation.load_stages()["1.1"]
        self.folder = self.project / self.definition["folder"]
        self.approved = self.folder / "Approved"
        self.approved.mkdir(parents=True)
        self.script = write_script_docx(self.approved / "A.docx")

    def evidence(self):
        return {"step": "1.1", "inputs": [], "dependencies": [
            stage_completion.fingerprint(validation.ROOT / "scripts/validation.py", validation.ROOT, "workflow")
        ], "outputs": [
            stage_completion.fingerprint(path, self.project, "project")
            for path in sorted(self.approved.iterdir())
            if path.is_file() and path.suffix.lower() == ".docx"]}

    def check(self):
        return validation.validate_evidence(self.project, validation.ROOT, self.definition, self.evidence())

    def test_definition_requires_empty_root_and_shared_format_validation(self):
        self.assertEqual(self.definition["inputs"], [{
            "folder": self.definition["folder"], "filename_regex": r"^.+\.docx$",
            "minimum": 0, "maximum": 0, "complete_set": True,
        }])
        self.assertTrue(self.definition["requires_stage_check"])
        self.assertEqual(self.definition["validation"], {"script": "scripts/validation.py"})
        self.assertIn("studio/script_docx.py", self.definition["workflow_dependencies"])
        self.assertIn(".agents/skills/studio/1-Pre Production/1-Script Review/scripts/write-script.py",
                      self.definition["workflow_dependencies"])
        self.assertIn(".agents/skills/studio/1-Pre Production/1-Script Review/scripts/convert-script.py",
                      self.definition["workflow_dependencies"])

    def test_stored_drafts_cannot_replace_at_least_one_approved_script(self):
        drafts = self.folder / "DRAFTS"
        drafts.mkdir()
        self.script.rename(drafts / "Historical.docx")
        (drafts / "Invalid Draft.docx").write_bytes(b"unconverted historical draft")
        status, reason = self.check()
        self.assertEqual(status, "stale")
        self.assertIn("found 0", reason)
        self.assertFalse((self.folder / "DONE").exists())

    def test_root_docx_blocks_completion_until_moved_to_drafts_or_approved(self):
        for name in ("Pending.docx", "Pending.DOCX"):
            with self.subTest(name=name):
                pending = write_script_docx(self.folder / name, "Pending script.")
                status, reason = self.check()
                self.assertEqual(status, "stale")
                self.assertIn("found 1", reason)
                self.assertFalse((self.folder / "DONE").exists())
                pending.rename(self.approved / name)
                self.assertEqual(self.check()[0], "current")
                (self.approved / name).unlink()
        drafts = self.folder / "DRAFTS"
        drafts.mkdir()
        pending = self.folder / "Old version.docx"
        pending.write_bytes(b"historical draft need not be valid or approved")
        self.assertEqual(self.check()[0], "stale")
        pending.rename(drafts / pending.name)
        self.assertEqual(self.check()[0], "current")

    def test_non_docx_root_files_do_not_block_completion(self):
        (self.folder / "LOG.md").write_text("Review history")
        (self.folder / "Notes.txt").write_text("Unspoken review notes")
        self.assertEqual(self.check()[0], "current")

    def test_every_approved_script_uses_the_shared_validator(self):
        from studio.script_docx import validate_three_column

        other = write_script_docx(self.approved / "B.DOCX", "Another approved script.")
        drafts = self.folder / "DRAFTS"
        drafts.mkdir()
        (drafts / "Historical.docx").write_bytes(b"not an approved script")
        with patch("studio.script_docx.validate_three_column", wraps=validate_three_column) as validator:
            self.assertEqual(self.check()[0], "current")
        self.assertEqual([call.args[0] for call in validator.call_args_list], [self.script, other])
        other.write_bytes(b"broken approved document")
        status, reason = self.check()
        self.assertEqual(status, "stale")
        self.assertIn("B.DOCX", reason)

    def test_added_approved_scripts_require_fingerprint_evidence(self):
        evidence = self.evidence()
        write_script_docx(self.approved / "B.docx", "Newly approved script.")
        status, reason = validation.validate_evidence(self.project, validation.ROOT, self.definition, evidence)
        self.assertEqual(status, "stale")
        self.assertIn("added=", reason)
        self.assertEqual(self.check()[0], "current")

    def test_plain_prose_and_tables_before_heading_cannot_prove_completion(self):
        for options in ({"paragraph_body": True}, {"table_before_heading": True}):
            with self.subTest(options=options):
                write_script_docx(self.script, **options)
                self.assertEqual(self.check()[0], "stale")

    def test_spoken_script_heading_is_required(self):
        for heading in (None, "Spoken script:", "Spoken script notes"):
            with self.subTest(heading=heading):
                write_script_docx(self.script, heading=heading)
                self.assertEqual(self.check()[0], "stale")

    def test_spoken_script_heading_is_case_insensitive(self):
        for heading in ("Spoken Script", "spoken script", "SPOKEN SCRIPT", "  sPoKeN sCrIpT  "):
            with self.subTest(heading=heading):
                write_script_docx(self.script, heading=heading)
                self.assertEqual(self.check()[0], "current")

    def test_canonical_three_column_header_is_required(self):
        for headers, row in (
            (("Spoken Dialog", "Stage Directions"), ("Speech.", "")),
            (("ID", "Spoken Dialogue", "Stage Directions"), ("L01", "Speech.", "")),
            (("ID", "Stage Directions", "Spoken Dialog"), ("L01", "", "Speech.")),
            (("ID", "Spoken Dialog", "Stage Directions", "Extra"), ("L01", "Speech.", "", "")),
        ):
            with self.subTest(headers=headers):
                write_script_docx(self.script, headers=headers, rows=[row])
                self.assertEqual(self.check()[0], "stale")

    def test_fresh_fingerprints_cannot_approve_missing_or_reassigned_line_ids(self):
        for ids in (("",), ("L00",), ("L02",), ("L1",), ("l01",), ("L001",),
                    ("L01", "L01"), ("L01", "L03"), ("L02", "L01")):
            with self.subTest(ids=ids):
                write_script_docx(self.script, rows=[(line_id, "Speech.", "") for line_id in ids])
                status, reason = self.check()
                self.assertEqual(status, "stale")
                self.assertIn("A.docx", reason)

    def test_sequential_line_ids_continue_naturally_past_ninety_nine(self):
        write_script_docx(self.script, rows=[(f"L{index:02d}", f"Line {index}.", "")
                                           for index in range(1, 102)])
        self.assertEqual(self.check()[0], "current")

    def test_direction_only_rows_are_allowed_but_dialogue_is_required_somewhere(self):
        rows = [("L01", "", "Pause before speaking."), ("L02", "Speech.", "")]
        write_script_docx(self.script, rows=rows)
        self.assertEqual(self.check()[0], "current")
        for invalid in ([], [("L01", "", "Pause.")], [("L01", " \t", "Pause.")]):
            with self.subTest(rows=invalid):
                write_script_docx(self.script, rows=invalid)
                self.assertEqual(self.check()[0], "stale")

    def test_malformed_approved_docx_fails_closed(self):
        for content in (b"", b"not a DOCX", b"PK\x03\x04broken ZIP package"):
            with self.subTest(content=content):
                self.script.write_bytes(content)
                status, reason = self.check()
                self.assertEqual(status, "stale")
                self.assertIn("A.docx", reason)
        for name, xml in (("wrong.xml", "<document/>"), ("word/document.xml", "<broken XML")):
            with self.subTest(member=name):
                with ZipFile(self.script, "w") as package:
                    package.writestr(name, xml)
                self.assertEqual(self.check()[0], "stale")

    def test_missing_shared_validator_is_reported_as_an_unavailable_dependency(self):
        with patch.dict("sys.modules", {"studio.script_docx": None}):
            status, reason = self.check()
        self.assertEqual(status, "unverifiable")
        self.assertIn("Stage validator dependency unavailable", reason)

    def test_common_checks_run_before_the_shared_validator_import(self):
        evidence = self.evidence()
        write_script_docx(self.script, "Changed approved dialogue.")
        with patch.dict("sys.modules", {"studio.script_docx": None}):
            status, reason = validation.validate_evidence(self.project, validation.ROOT, self.definition, evidence)
        self.assertEqual(status, "stale")
        self.assertIn("Fingerprint differs", reason)


if __name__ == "__main__":
    unittest.main()
