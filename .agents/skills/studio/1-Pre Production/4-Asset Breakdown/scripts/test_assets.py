"""Synthetic DOCX and current Markdown/JSON tests; no connected project writes."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape
from zipfile import ZipFile

from scripts import stage_completion as completion
from scripts import stage_invalidation as invalidation
from scripts import validation

SPEC = importlib.util.spec_from_file_location("asset_breakdown_tests", Path(__file__).with_name("assets.py"))
assets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assets)


def docx(path, text="One two three four five six.", brief="Asset: Show the diagram", comment=True, body=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if body is None:
        body = ('<w:commentRangeStart w:id="7"/>' if comment else '')
        body += '<w:r><w:t>' + escape(text) + '</w:t></w:r>'
        body += '<w:commentRangeEnd w:id="7"/>' if comment else ''
    with ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", '<w:document xmlns:w="' + assets.W[1:-1] +
                         '"><w:body><w:p>' + body + '</w:p></w:body></w:document>')
        if comment:
            archive.writestr("word/comments.xml", '<w:comments xmlns:w="' + assets.W[1:-1] +
                '"><w:comment w:id="7" w:author="Human" w:date="2026-10-20T12:00:00Z">'
                '<w:p><w:r><w:t>' + escape(brief) + '</w:t></w:r></w:p></w:comment></w:comments>')


def readonly_state(project):
    """Inventory all paths/mtimes, plus bytes for registries and workflow control files."""
    state = {}
    for path in project.rglob("*"):
        relative = path.relative_to(project)
        stat = path.stat()
        protected = (path.parent == project / assets.STAGE or path.name in ("DONE", "LOG.md") or
                     "STALE" in relative.parts or path.name.startswith((".studio", ".assets", ".DONE")))
        state[relative.as_posix()] = (stat.st_mtime_ns, stat.st_size if path.is_file() else None,
                                      path.read_bytes() if path.is_file() and protected else None)
    return state


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name).resolve()
        self.script = self.project / assets.SCRIPTS / "A.docx"
        docx(self.script)
        self.shortlist = self.project / assets.SHORTLIST
        self.shortlist.parent.mkdir(parents=True)
        self.shortlist.write_text("# Extracts\n\n## 1. Short\n**Review status:** Approved\n"
            "**Source:** A.docx\n\n### Provisional spoken script\n\nOne two three\n", encoding="utf-8")
        self.path = self.project / assets.STAGE / "Asset Registry.md"

    def envelope(self, suggestions=None):
        ctx = assets.context(self.project)
        return {"inputs": ctx["inputs"], "processed": [e["path"] for e in ctx["inputs"]],
                "suggestions": [] if suggestions is None else suggestions}

    def suggestion(self, **changes):
        item = {"key": "diagram", "script": self.script.relative_to(self.project).as_posix(),
                "start": 0, "end": 1, "passage": "One two", "extracts": ["EXTRACT-1"],
                "type": "Graphic", "brief": "Comparison diagram", "notes": "Clarifies the contrast"}
        item.update(changes)
        return item

    def reconcile(self, suggestions=None):
        return assets.reconcile(self.project, self.envelope(suggestions))

    def edit(self, column, value, origin=None):
        registry = assets.load(self.project)
        _, entries = assets.rows(registry)
        for number, row in entries:
            if origin is None or origin == row["Origin"]:
                row[column] = value
        assets.save(registry, self.project)

    def records(self):
        return [row for _, row in assets.rows(assets.load(self.project))[1]]

    def test_compact_ids_limit_includes_comments_and_rejects_without_writes(self):
        suggestions = [self.suggestion(key=f"idea-{n:03d}") for n in range(98)]
        self.reconcile(suggestions)
        self.assertEqual({row["ID"] for row in self.records()}, {f"A{n:02d}" for n in range(1, 100)})
        before = readonly_state(self.project)
        envelope = self.envelope(suggestions + [self.suggestion(key="overflow")])
        for operation in (assets.dry_run, assets.reconcile):
            with self.subTest(operation=operation.__name__), self.assertRaisesRegex(ValueError, "at most 99"):
                operation(self.project, envelope)
            self.assertEqual(readonly_state(self.project), before)

    def test_oversized_initial_slate_publishes_nothing(self):
        envelope = self.envelope([self.suggestion(key=f"idea-{n}") for n in range(99)])
        before = readonly_state(self.project)
        for operation in (assets.dry_run, assets.reconcile):
            with self.assertRaisesRegex(ValueError, "at most 99"):
                operation(self.project, envelope)
            self.assertEqual(readonly_state(self.project), before)

    def test_cli_rejects_over_limit_before_invalidation(self):
        envelope = self.envelope([self.suggestion(key=f"idea-{n}") for n in range(99)])
        request = self.project / "suggestions.json"
        request.write_text(json.dumps(envelope), encoding="utf-8")
        with patch.object(sys, "argv", [assets.__file__, "reconcile", "--project-root", str(self.project),
                                       "--suggestions", str(request)]), patch.object(assets, "audit", return_value={}), \
                patch.object(invalidation, "invalidate") as invalidate:
            with self.assertRaisesRegex(ValueError, "at most 99"):
                assets.main()
            invalidate.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_retired_ids_are_not_reused(self):
        self.reconcile([self.suggestion()])
        self.reconcile([])
        self.reconcile([self.suggestion(key="replacement")])
        self.assertEqual([row["ID"] for row in self.records()], ["A01", "A02", "A03"])
        self.assertEqual(self.records()[0]["Active"], "No")

    def test_existing_legacy_registry_retains_numbering(self):
        with patch.object(assets, "new_registry", return_value={"assets": [], "inputs": [], "history": [], "control": {}}):
            self.reconcile([self.suggestion()])
        self.assertEqual({row["ID"] for row in self.records()}, {"ASSET-000001", "ASSET-000002"})
        self.reconcile([self.suggestion(), self.suggestion(key="new")])
        self.assertEqual({row["ID"] for row in self.records()},
                         {"ASSET-000001", "ASSET-000002", "ASSET-000003"})

    def test_compact_id_syntax_rejects_zero_overflow_and_extra_digits(self):
        self.reconcile()
        row = self.records()[0]
        for identity in ("A00", "A100", "A001", "A1", "A-01"):
            with self.subTest(identity=identity), self.assertRaisesRegex(ValueError, "Invalid asset ID"):
                assets.validate_registry_row(dict(row, ID=identity), set(), set())

    def cli(self, action, *args):
        return subprocess.run([sys.executable, assets.__file__, action, "--project-root", str(self.project), *args],
                              capture_output=True, text=True, encoding="utf-8")

    def test_indexed_context_exact_tokens_and_editorial_context(self):
        docx(self.script, text="One\t two  three\nfour five six.")
        ctx = assets.context(self.project)
        plain = self.cli("context")
        self.assertEqual(json.loads(plain.stdout), ctx)
        result = self.cli("context", "--indexed")
        self.assertEqual(result.returncode, 0, result.stderr)
        import re
        numbered = re.findall(r"\[(\d+)\] (\S+)", result.stdout)
        words = ctx["scripts"][self.suggestion()["script"]]["words"]
        self.assertEqual(numbered, [(str(i), word) for i, word in enumerate(words)])
        self.assertIn("Comment 7 [0-5]: Show the diagram", result.stdout)
        self.assertIn("EXTRACT-1: Short", result.stdout)
        self.assertIn("[0-2] One two three", result.stdout)

    def test_indexed_context_utf8_and_numbering_restarts_for_each_script(self):
        second = self.script.with_name("B.docx")
        docx(second, text="\u4e00 \u4e8c three", comment=False)
        result = self.cli("context", "--indexed")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("[0]"), 2)
        self.assertIn("[0] \u4e00 [1] \u4e8c [2] three", result.stdout)

    def test_dry_run_exact_snapshot_validates_every_proposal_without_mutation(self):
        self.reconcile([self.suggestion()])
        for name in ("LOG.md", "DONE", "STALE/1/DONE", "STALE/PENDING.json", ".DONE.lock"):
            path = self.path.parent / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"do not touch")
        guard = self.project / ".studio-mutations.lock"
        guard.write_bytes(b"read-only must not acquire or remove")
        scratch = Path(self.tmp.name).parent / (self.project.name + "-suggestions.json")
        self.addCleanup(lambda: scratch.unlink(missing_ok=True))
        envelope = self.envelope([self.suggestion()])
        scratch.write_text(json.dumps(envelope), encoding="utf-8")
        before = readonly_state(self.project)
        for action, args in (("context", ()), ("context", ("--indexed",)), ("inspect", ()),
                             ("reconcile", ("--dry-run", "--suggestions", str(scratch)))):
            result = self.cli(action, *args)
            self.assertEqual(result.returncode, 0, result.stderr)
            if action == "reconcile":
                report = json.loads(result.stdout)
                self.assertTrue(report["valid_snapshot"])
                self.assertEqual(report["counts"], {"total": 2, "User": 1, "AI": 1})
                self.assertFalse(report["published"])
            self.assertEqual(before, readonly_state(self.project))
        for bad in ({**envelope, "inputs": []}, {**envelope, "processed": []},
                    {**envelope, "processed": [None]}, [],
                    {**envelope, "suggestions": [self.suggestion(), self.suggestion(key="bad", passage="wrong")]},
                    {**envelope, "suggestions": [self.suggestion(), self.suggestion()]}):
            scratch.write_text(json.dumps(bad), encoding="utf-8")
            result = self.cli("reconcile", "--dry-run", "--suggestions", str(scratch))
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(before, readonly_state(self.project))
        docx(self.script, brief="Asset: Changed since editorial snapshot")
        before = readonly_state(self.project)
        scratch.write_text(json.dumps(envelope), encoding="utf-8")
        result = self.cli("reconcile", "--dry-run", "--suggestions", str(scratch))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("current input snapshot", result.stderr)
        self.assertEqual(before, readonly_state(self.project))
        publication = self.path.parent / ".assets-publication.json"
        publication.write_text("interrupted", encoding="utf-8")
        before = readonly_state(self.project)
        self.assertNotEqual(self.cli("inspect").returncode, 0)
        scratch.write_text(json.dumps(self.envelope([self.suggestion()])), encoding="utf-8")
        result = self.cli("reconcile", "--dry-run", "--suggestions", str(scratch))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Interrupted registry pair publication", result.stderr)
        self.assertEqual(before, readonly_state(self.project))

    def test_incompatible_cli_flags_fail_before_mutations(self):
        before = readonly_state(self.project)
        for action, args in (("inspect", ("--indexed",)), ("check", ("--dry-run",)),
                             ("context", ("--suggestions", "missing.json")),
                             ("reconcile", ("--dry-run",)), ("reconcile", ("--by", "Human")),
                             ("inspect", ("--reason", "not supported"))):
            result = self.cli(action, *args)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(before, readonly_state(self.project))

    def test_inspect_pending_is_successful_but_check_is_strict(self):
        self.reconcile([self.suggestion()])
        before = readonly_state(self.project)
        result = self.cli("inspect")
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["completion_ready"])
        self.assertEqual(report["errors"], [])
        self.assertEqual(sum(issue["kind"] == "pending" for issue in report["issues"]), 2)
        self.assertEqual(report["counts"]["review"]["Pending Review"], 2)
        self.assertTrue(report["input_status"]["inputs_current"])
        self.assertTrue(report["input_status"]["comments"]["complete"])
        self.assertNotEqual(self.cli("check").returncode, 0)
        self.assertEqual(before, readonly_state(self.project))

    def test_inspect_collects_multiple_errors_across_all_pending_rows(self):
        self.reconcile([self.suggestion(), self.suggestion(key="second")])
        registry = assets.load(self.project)
        registry["assets"][0]["Passage"] = "wrong first"
        registry["assets"][1]["Extracts"] = '["EXTRACT-99"]'
        registry["assets"][2]["Type"] = "Video"
        assets.save(registry, self.project)
        before = readonly_state(self.project)
        result = self.cli("inspect")
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(sum(issue["kind"] == "pending" for issue in report["issues"]), 3)
        self.assertEqual({error["asset"] for error in report["errors"]},
                         {row["ID"] for row in registry["assets"]})
        messages = " ".join(error["message"] for error in report["errors"])
        self.assertIn("Passage no longer matches", messages)
        self.assertIn("Extract does not intersect", messages)
        self.assertIn("Invalid type/brief", messages)
        self.assertEqual(before, readonly_state(self.project))

    def test_inspect_proposed_kept_union_excludes_rejected_and_inactive(self):
        docx(self.script, comment=False)
        self.reconcile([self.suggestion(key="kept", start=0, end=1),
                        self.suggestion(key="pending", start=1, end=2, passage="two three"),
                        self.suggestion(key="rejected", start=3, end=5, passage="four five six.", extracts=[]),
                        self.suggestion(key="inactive", start=3, end=5, passage="four five six.", extracts=[])])
        registry = assets.load(self.project)
        for row in registry["assets"]:
            if row["Source Key"] == "agent:kept":
                row["Review"] = "Keep"
            elif row["Source Key"] == "agent:rejected":
                row["Review"] = "Reject"
            elif row["Source Key"] == "agent:inactive":
                row["Active"], row["Review"] = "No", "Reject"
                row["Script"], row["Start"] = "removed.docx", -1
        assets.save(registry, self.project)
        before = readonly_state(self.project)
        report = assets.inspect(self.project)
        self.assertEqual(report["errors"], [])
        self.assertFalse(report["coverage"]["proposed_is_approved"])
        self.assertEqual(report["coverage"]["proposed"]["scripts"][self.suggestion()["script"]], 0.5)
        self.assertEqual(report["coverage"]["kept"]["scripts"][self.suggestion()["script"]], 1 / 3)
        self.assertEqual(report["coverage"]["proposed"]["extracts"]["EXTRACT-1"], 1)
        self.assertEqual(report["coverage"]["kept"]["extracts"]["EXTRACT-1"], 2 / 3)
        self.assertEqual(before, readonly_state(self.project))

    def test_inspect_missing_malformed_and_stale_inputs_return_summary(self):
        report = assets.inspect(self.project)
        self.assertFalse(report["completion_ready"])
        self.assertEqual(report["counts"]["total"], 0)
        self.assertIn("Missing Asset Registry.md", str(report["errors"]))
        self.reconcile()
        docx(self.script, brief="Asset: New current comment")
        report = assets.inspect(self.project)
        self.assertFalse(report["input_status"]["inputs_current"])
        self.assertFalse(report["input_status"]["comments"]["complete"])
        self.assertIn("stale", str(report["errors"]))
        original = self.path.read_text(encoding="utf-8")
        self.path.write_text(original + "Unknown card text\n", encoding="utf-8")
        before = readonly_state(self.project)
        result = self.cli("inspect")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Malformed", str(json.loads(result.stdout)["errors"]))
        self.assertEqual(before, readonly_state(self.project))
        self.path.write_text(original, encoding="utf-8")
        for content in ("{broken", "null", "[]"):
            self.path.with_suffix(".json").write_text(content, encoding="utf-8")
            before = readonly_state(self.project)
            result = self.cli("inspect")
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertTrue(json.loads(result.stdout)["errors"])
            self.assertEqual(before, readonly_state(self.project))

    def test_inspect_control_errors_do_not_hide_pending_row_errors(self):
        self.reconcile([self.suggestion()])
        registry = assets.load(self.project)
        registry["control"]["processed"] = "{broken"
        registry["control"]["empty_confirmation"] = "null"
        for row in registry["assets"]:
            row["Passage"] = "wrong"
        assets.save(registry, self.project)
        report = assets.inspect(self.project)
        self.assertFalse(report["input_status"]["processed_valid"])
        self.assertEqual(sum(issue["kind"] == "pending" for issue in report["issues"]), 2)
        self.assertEqual(sum("Passage no longer matches" in issue["message"] for issue in report["errors"]), 2)
        self.assertIsNotNone(report["coverage"])
        self.assertFalse(report["completion_ready"])

    def test_inspect_comment_import_completeness_and_empty_completion_gate(self):
        self.reconcile([self.suggestion()])
        registry = assets.load(self.project)
        registry["assets"] = [row for row in registry["assets"] if row["Origin"] != "User"]
        registry["control"]["identities"] = assets.encoded({row["Source Key"]: row["ID"] for row in registry["assets"]})
        registry["control"]["proposal_hashes"] = assets.encoded(assets.proposal_hashes(registry))
        assets.save(registry, self.project)
        report = assets.inspect(self.project)
        comments = report["input_status"]["comments"]
        self.assertEqual(comments["expected"], 1)
        self.assertEqual(comments["active_imported"], 0)
        self.assertEqual(len(comments["missing"]), 1)
        self.assertFalse(comments["complete"])
        self.assertTrue(report["errors"])
        docx(self.script, comment=False)
        self.reconcile([])
        self.edit("Review", "Reject")
        before = readonly_state(self.project)
        report = assets.inspect(self.project)
        self.assertEqual(report["errors"], [])
        self.assertFalse(report["completion_ready"])
        self.assertEqual([issue["kind"] for issue in report["issues"]], ["confirmation"])
        self.assertNotEqual(self.cli("check").returncode, 0)
        self.assertEqual(before, readonly_state(self.project))

    def test_word_comment_provenance_split_runs_and_paragraphs(self):
        body = ('<w:r><w:t>Before </w:t></w:r><w:commentRangeStart w:id="7"/>'
                '<w:r><w:rPr><w:highlight w:val="yellow"/></w:rPr><w:t>One </w:t></w:r>'
                '<w:r><w:t>two</w:t></w:r></w:p><w:p><w:r><w:t>three</w:t></w:r>'
                '<w:commentRangeEnd w:id="7"/><w:r><w:t> after</w:t></w:r>')
        docx(self.script, body=body)
        words, comments = assets.read_docx(self.script)
        self.assertEqual(words, ["Before", "One", "two", "three", "after"])
        self.assertEqual((comments[0]["start"], comments[0]["end"]), (1, 3))
        self.assertEqual(comments[0]["passage"], "One two three")
        self.assertEqual(comments[0]["highlighted_text"], "One two\nthree")
        self.assertEqual(comments[0]["author"], "Human")

    def test_word_overlapping_ranges_and_deleted_text(self):
        body = ('<w:commentRangeStart w:id="7"/><w:r><w:t>One </w:t></w:r>'
                '<w:del><w:r><w:delText>removed</w:delText></w:r></w:del>'
                '<w:r><w:t>two</w:t></w:r><w:commentRangeEnd w:id="7"/>')
        docx(self.script, body=body)
        self.assertEqual(assets.read_docx(self.script)[0], ["One", "two"])

    def test_word_overlapping_comments_have_independent_ranges(self):
        with ZipFile(self.script) as archive:
            comments = archive.read("word/comments.xml").decode()
        body = ('<w:commentRangeStart w:id="7"/><w:r><w:t>One </w:t></w:r>'
                '<w:commentRangeStart w:id="8"/><w:r><w:t>two </w:t></w:r>'
                '<w:commentRangeEnd w:id="7"/><w:r><w:t>three</w:t></w:r>'
                '<w:commentRangeEnd w:id="8"/>')
        docx(self.script, body=body, comment=False)
        second = '<w:comment w:id="8" w:author="Other"><w:p><w:r><w:t>Asset: Another brief</w:t></w:r></w:p></w:comment>'
        with ZipFile(self.script, "a") as archive:
            archive.writestr("word/comments.xml", comments.replace('</w:comments>', second + '</w:comments>'))
        words, imported = assets.read_docx(self.script)
        self.assertEqual(words, ["One", "two", "three"])
        self.assertEqual([(c["start"], c["end"]) for c in imported], [(0, 1), (1, 2)])

    def test_malformed_comment_ranges_fail(self):
        for body in ('<w:commentRangeEnd w:id="7"/>', '<w:commentRangeStart w:id="7"/>',
                     '<w:commentRangeStart w:id="9"/><w:r><w:t>X</w:t></w:r><w:commentRangeEnd w:id="9"/>',
                     '<w:commentRangeStart w:id="7"/><w:commentRangeEnd w:id="7"/>'):
            with self.subTest(body=body):
                docx(self.script, body=body)
                with self.assertRaises(ValueError):
                    assets.read_docx(self.script)

    def test_ordinary_comments_ignored_with_or_without_anchors(self):
        docx(self.script, brief="Please revise this sentence")
        self.assertEqual(assets.read_docx(self.script)[1], [])
        docx(self.script, brief="Please revise this sentence", body='<w:r><w:t>One two three</w:t></w:r>')
        self.assertEqual(assets.read_docx(self.script)[1], [])

    def test_prefixed_comments_import_full_description_case_insensitively(self):
        description = "Show the diagram: include both panels.  Keep labels intact.\nAdd a quiet sound cue."
        docx(self.script, brief="aSsEt: " + description)
        self.reconcile()
        row = self.records()[0]
        self.assertEqual(row["Brief"], description)
        self.assertEqual(row["Origin"], "User")
        self.assertEqual(json.loads(row["Provenance"])["comment_text"], "aSsEt: " + description)

    def test_unanchored_asset_comments_fail_instead_of_losing_intent(self):
        for body in ('<w:r><w:t>One two three</w:t></w:r>',
                     '<w:del><w:commentRangeStart w:id="7"/><w:r><w:delText>Old</w:delText></w:r>'
                     '<w:commentRangeEnd w:id="7"/></w:del><w:r><w:t>One two three</w:t></w:r>'):
            with self.subTest(body=body):
                docx(self.script, body=body)
                with self.assertRaisesRegex(ValueError, r"Asset: comment 7.*no anchored script range"):
                    self.reconcile()
                self.assertFalse(self.path.exists())

    def test_empty_asset_description_rejected(self):
        docx(self.script, brief="Asset:   ")
        with self.assertRaisesRegex(ValueError, "nonempty highlighted passage and brief"):
            assets.read_docx(self.script)

    def test_audio_type_supported_in_suggestions_and_review(self):
        self.reconcile([self.suggestion(type="Audio", brief="Soft transition sound")])
        self.edit("Review", "Keep")
        assets.check(self.project)
        row = next(r for r in self.records() if r["Origin"] == "AI")
        self.assertEqual(row["Type"], "Audio")
        self.reconcile([self.suggestion(type="Audio", brief="Soft transition sound")])
        self.assertTrue(all(row["Review"] == "Keep" for row in self.records()))

    def test_import_and_agent_sections_have_neutral_ids_and_review_gate(self):
        self.reconcile([self.suggestion()])
        rows = self.records()
        self.assertEqual({r["Origin"] for r in rows}, {"AI", "User"})
        self.assertEqual({r["ID"] for r in rows}, {"A01", "A02"})
        self.assertTrue(all(r["Review"] == "Pending Review" for r in rows))
        self.assertIn("## A01 | Comparison diagram\n", self.path.read_text(encoding="utf-8"))
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("**Review:** Pending Review\n", text)
        self.assertNotIn("\\\n", text)
        self.assertFalse(any(line.endswith("  ") for line in text.splitlines()))
        self.assertNotIn("<br", text)
        visible = text
        self.assertNotIn("###", visible)
        self.assertNotIn("```", visible)
        self.assertNotIn("Source Key", visible)
        self.assertEqual(text.count("<details>"), 0)
        self.assertLessEqual(len(visible.splitlines()), 6 + 10 * len(rows))
        self.assertTrue(self.path.with_suffix(".json").is_file())
        with self.assertRaisesRegex(ValueError, "Pending Review"):
            assets.check(self.project)

    def test_unchanged_rerun_preserves_decisions_and_exact_registry(self):
        suggestion = self.suggestion()
        self.reconcile([suggestion])
        self.edit("Review", "Reject", "AI")
        self.edit("Review", "Keep", "User")
        before = self.path.read_bytes()
        self.reconcile([suggestion])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(len(self.records()), 2)
        self.assertTrue(assets.check(self.project)["advisory_only"])

    def test_changed_brief_resets_review_preserves_human_edits_and_history(self):
        self.reconcile([self.suggestion()])
        self.edit("Review", "Reject", "AI")
        self.edit("Brief", "Human preferred brief", "AI")
        self.reconcile([self.suggestion(brief="New machine brief")])
        row = next(r for r in self.records() if r["Origin"] == "AI")
        self.assertEqual(row["Brief"], "Human preferred brief")
        self.assertEqual(row["Review"], "Pending Review")
        self.assertEqual(json.loads(row["Proposal"])["fields"]["brief"], "New machine brief")
        history = assets.load(self.project)["history"]
        self.assertEqual(json.loads(history[-1][3])["Review"], "Reject")

    def test_comment_changed_keeps_identity_and_updates_unedited_fields(self):
        self.reconcile()
        self.edit("Review", "Keep")
        old_id = self.records()[0]["ID"]
        docx(self.script, brief="Asset: Revised request")
        self.reconcile()
        row = self.records()[0]
        self.assertEqual(row["ID"], old_id)
        self.assertEqual(row["Brief"], "Revised request")
        self.assertEqual(row["Review"], "Pending Review")

    def test_input_change_and_applicability_change_require_review(self):
        self.reconcile([self.suggestion()])
        self.edit("Review", "Keep")
        self.shortlist.write_text(self.shortlist.read_text() + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "current inputs"):
            assets.check(self.project)
        self.reconcile([self.suggestion(extracts=[])])
        self.assertTrue(all(r["Review"] == "Pending Review" for r in self.records()))

    def test_removed_and_returned_suggestion_retains_rejection_identity(self):
        self.reconcile([self.suggestion()])
        self.edit("Review", "Keep")
        old = next(r for r in self.records() if r["Origin"] == "AI")
        self.reconcile([])
        row = next(r for r in self.records() if r["Origin"] == "AI")
        self.assertEqual(row["Active"], "No")
        self.edit("Review", "Keep")
        with self.assertRaisesRegex(ValueError, "Inactive"):
            assets.check(self.project)
        self.edit("Review", "Reject", "AI")
        assets.check(self.project)
        self.reconcile([self.suggestion()])
        row = next(r for r in self.records() if r["Origin"] == "AI")
        self.assertEqual(row["ID"], old["ID"])
        self.assertEqual(row["Review"], "Pending Review")

    def test_empty_slate_and_all_rejected_need_snapshot_bound_human_confirmation(self):
        docx(self.script, comment=False)
        self.reconcile()
        with self.assertRaisesRegex(ValueError, "confirmation"):
            assets.check(self.project)
        assets.confirm_empty(self.project, "Human", "Explicitly confirmed no supporting assets")
        assets.check(self.project)
        self.reconcile([self.suggestion()])
        self.edit("Review", "Reject")
        with self.assertRaisesRegex(ValueError, "confirmation"):
            assets.check(self.project)
        assets.confirm_empty(self.project, "Human", "Reviewed and rejected suggestions")
        assets.check(self.project)
        self.edit("Notes", "Changed rationale")
        with self.assertRaisesRegex(ValueError, "confirmation"):
            assets.check(self.project)

    def test_empty_confirmation_cannot_approve_pending_or_kept(self):
        self.reconcile()
        with self.assertRaisesRegex(ValueError, "Pending Review"):
            assets.confirm_empty(self.project, "Human", "yes")
        self.edit("Review", "Keep")
        with self.assertRaisesRegex(ValueError, "not empty"):
            assets.confirm_empty(self.project, "Human", "yes")

    def test_low_coverage_is_advisory_and_unions_do_not_double_count(self):
        docx(self.script, comment=False)
        self.reconcile([self.suggestion(end=0, passage="One")])
        self.edit("Review", "Keep")
        report = assets.check(self.project)
        self.assertLess(report["scripts"][self.suggestion()["script"]], 0.25)
        self.assertAlmostEqual(report["extracts"]["EXTRACT-1"], 1 / 3)

    def test_invalid_suggestions_and_stale_envelope_do_not_write(self):
        for changes in ({"start": -1}, {"start": True}, {"passage": "fake"}, {"extracts": ["EXTRACT-9"]},
                        {"type": "Video"}, {"brief": ""}, {"key": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.reconcile([self.suggestion(**changes)])
        envelope = self.envelope()
        envelope["processed"] = []
        with self.assertRaises(ValueError):
            assets.reconcile(self.project, envelope)
        envelope = self.envelope()
        docx(self.script, brief="Asset: Changed")
        with self.assertRaises(ValueError):
            assets.reconcile(self.project, envelope)
        self.assertFalse(self.path.exists())

    def test_invalid_review_id_reference_and_deleted_rows_rejected(self):
        for column, value in (("ID", "bad"), ("Review", "Approved"), ("Passage", "fake"),
                              ("Extracts", '["EXTRACT-9"]'), ("Origin", "AI")):
            with self.subTest(column=column):
                if self.path.exists():
                    self.path.unlink()
                    self.path.with_suffix(".json").unlink()
                self.reconcile()
                self.edit("Review", "Keep")
                self.edit(column, value)
                with self.assertRaises(ValueError):
                    assets.check(self.project)
        self.path.unlink()
        self.path.with_suffix(".json").unlink()
        self.reconcile()
        registry = assets.load(self.project)
        registry["assets"].pop(0)
        assets.save(registry, self.project)
        with self.assertRaisesRegex(ValueError, "deleted"):
            self.reconcile()

    def test_preserves_multiline_markdown_fences_and_formula_like_briefs(self):
        docx(self.script, brief="Asset: =dangerous formula-like text")
        self.reconcile()
        notes = "Human notes\n\n## ASSET-999999\n```\n### Brief\n\nTrailing newline\n"
        self.edit("Notes", notes)
        self.reconcile([self.suggestion()])
        user = next(r for r in self.records() if r["Origin"] == "User")
        self.assertEqual(user["Notes"], notes)
        self.assertEqual(user["Brief"], "=dangerous formula-like text")

    def test_paragraphs_preserve_empty_strings_blank_lines_and_literal_prefixes(self):
        self.reconcile()
        for notes in ("", "\n", "\n\n", "first\n\nlast\n", "> quoted\n> \n## Heading\n</details>",
                      "  leading and trailing  ", "~~~\n```json\n{}\n```\n~~~"):
            with self.subTest(notes=notes):
                self.edit("Notes", notes)
                self.assertEqual(self.records()[0]["Notes"], notes)
                text = self.path.read_text(encoding="utf-8")
                self.assertIn("**Why:** " + assets.prose(notes) + "\n\n", text)
                self.assertNotIn("```text\n", text)

    def test_final_card_accepts_single_terminal_newline(self):
        self.reconcile()
        registry = assets.load(self.project)
        text = assets.render(registry)
        self.assertTrue(text.endswith("\n\n"))
        self.assertEqual(assets.parse(text[:-1], assets.render_evidence(registry)), registry)

    def test_friendly_edits_roundtrip_and_unchanged_reconciliation(self):
        suggestion = self.suggestion()
        self.reconcile([suggestion])
        text = self.path.read_text(encoding="utf-8")
        text = text.replace("**Review:** Pending Review", "**Review:** Keep")
        text = text.replace("**Type:** Graphic", "**Type:** Animation")
        text = text.replace("**Extracts:** EXTRACT-1", "**Extracts:** Full film only")
        text = text.replace("**Asset:** Comparison diagram", "**Asset:** Human brief\n\nSecond paragraph\n")
        text = text.replace("**Why:** Clarifies the contrast", "**Why:** Human rationale\nAnd another line")
        self.path.write_text(text, encoding="utf-8")
        registry = assets.load(self.project)
        ai = registry["assets"][0]
        self.assertEqual(ai["Brief"], "Human brief\n\nSecond paragraph\n")
        self.assertEqual(ai["Type"], "Animation")
        self.assertEqual(ai["Extracts"], "[]")
        self.assertEqual(assets.parse(assets.render(registry), assets.render_evidence(registry)), registry)
        self.reconcile([suggestion])
        self.assertEqual(self.path.read_text(encoding="utf-8"), text)
        assets.check(self.project)

    def test_passage_edits_and_multiline_blocks_are_not_silently_lost(self):
        self.reconcile()
        text = self.path.read_text(encoding="utf-8")
        text = text.replace("> One two three four five six.", "> A changed\n> \n> passage\n> ")
        registry = assets.parse(text, self.path.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(registry["assets"][0]["Passage"], "A changed\n\npassage\n")
        self.assertEqual(assets.parse(assets.render(registry), assets.render_evidence(registry)), registry)
        self.path.write_text(text, encoding="utf-8")
        self.reconcile()
        self.assertEqual(assets.load(self.project), registry)
        self.edit("Review", "Keep")
        with self.assertRaisesRegex(ValueError, "Passage no longer matches"):
            assets.check(self.project)

    def test_sidecar_metadata_tampering_is_detected(self):
        self.reconcile()
        text = self.path.with_suffix(".json").read_text(encoding="utf-8")
        for old, new in (('"Active": "Yes"', '"Active": "No"'),
                         ('"Start": 0', '"Start": 1'),
                         ('"history": []', '"history": ["changed"]')):
            changed = text.replace(old, new, 1)
            self.assertNotEqual(changed, text)
            with self.assertRaisesRegex(ValueError, "machine evidence changed"):
                assets.parse(self.path.read_text(encoding="utf-8"), changed)

    def test_save_detects_saved_edits_during_atomic_preparation(self):
        self.reconcile()
        registry = assets.load(self.project)
        original = self.path.read_text(encoding="utf-8")
        registry["assets"][0]["Notes"] = "Changed rationale"
        real_render = assets.render

        def concurrent_edit(value):
            self.path.write_text(original + "Human saved edit\n", encoding="utf-8")
            return real_render(value)

        with patch.object(assets, "render", side_effect=concurrent_edit):
            with self.assertRaisesRegex(ValueError, "changed before publication"):
                assets.save(registry, self.project)
        self.assertEqual(self.path.read_text(encoding="utf-8"), original + "Human saved edit\n")
        self.assertEqual(list(self.path.parent.glob(".assets-*")), [])

    def test_readonly_labels_and_ambiguous_syntax_rejected_without_write(self):
        self.reconcile([self.suggestion()])
        text = self.path.read_text(encoding="utf-8")
        for old, new in (("| Comparison diagram", "| Changed title"),
                         ("**Source:** A", "**Source:** B"),
                         ("**Origin:** AI", "**Origin:** User"),
                          ("**Asset:** Comparison diagram", "**Asset:** **Why:**\nAmbiguous"),
                         ("**Extracts:** EXTRACT-1", "**Extracts:** EXTRACT-9, unknown")):
            with self.subTest(old=old):
                changed = text.replace(old, new, 1)
                self.path.write_text(changed, encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.reconcile([self.suggestion()])
                self.assertEqual(self.path.read_text(encoding="utf-8"), changed)

    def test_failed_atomic_save_preserves_original(self):
        self.reconcile()
        before = self.path.read_bytes()
        with patch.object(assets.os, "replace", side_effect=PermissionError("Editor open")):
            with self.assertRaises(PermissionError):
                self.reconcile([self.suggestion()])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(list(self.path.parent.glob(".assets-*")), [])

    def test_extract_jump_ambiguity_and_explicit_no_extract(self):
        text = self.shortlist.read_text()
        self.shortlist.write_text(text.replace("One two three", "One two\n\n> [PROPOSED JUMP]\n\nfive six."))
        ctx = assets.context(self.project)
        self.assertEqual(ctx["extracts"][0]["spans"], [[0, 1], [4, 5]])
        docx(self.script, text="One two One two", comment=False)
        self.shortlist.write_text(text.replace("One two three", "One two"))
        with self.assertRaisesRegex(ValueError, "unique"):
            assets.context(self.project)
        self.shortlist.write_text("# No viable extracts\nHuman-approved empty shortlist.\n")
        self.assertEqual(assets.context(self.project)["extracts"], [])

    def test_portability_and_read_only_validation(self):
        self.reconcile()
        self.edit("Review", "Keep")
        original = assets.context(self.project)["inputs"]
        self.assertTrue(all(e["scope"] == "project" and not Path(e["path"]).is_absolute() for e in original))
        destination = self.project / "moved"
        destination.mkdir()
        for folder in ("1-Pre Production",):
            shutil.copytree(self.project / folder, destination / folder)
        self.assertEqual(assets.context(destination)["inputs"], original)
        before = {p.relative_to(destination): p.read_bytes() for p in destination.rglob("*") if p.is_file()}
        assets.check(destination)
        self.assertEqual(before, {p.relative_to(destination): p.read_bytes() for p in destination.rglob("*") if p.is_file()})

    def test_shared_recording_validation_and_protected_generated_policy(self):
        self.reconcile()
        self.edit("Review", "Keep")
        stages = validation.load_stages()
        self.assertEqual(stages["1.4"]["generated"], [])
        self.assertNotIn("1.4", validation.order_stages(stages, "3.6"))
        manifest = completion.build_manifest(self.project, assets.ROOT, "1.4")
        self.assertEqual(validation.validate_evidence(self.project, assets.ROOT, stages["1.4"], manifest)[0], "current")
        with patch.object(validation, "require_prerequisites"):
            completion.record_done(self.project, assets.ROOT, "1.4", manifest)
        marker = self.project / assets.STAGE / "DONE"
        self.assertTrue(marker.is_file())
        self.edit("Brief", "Human revision")
        self.assertEqual(validation.check_marker(self.project, assets.ROOT, stages["1.4"])[0], "stale")
        with self.assertRaises(ValueError):
            invalidation.prepare_regeneration(self.project, assets.ROOT, "1.4", [self.path], "not allowed")
        self.assertTrue(self.path.exists())

    def test_shared_recorder_rejects_pending_and_uses_real_ancestors(self):
        # These inventory stages use their normal shared recorders, not mock DONEs.
        for step in ("1.1", "1.2", "1.3"):
            if step == "1.2":
                deck = self.project / "1-Pre Production/2-Cue Cards/A.pptx"
                deck.parent.mkdir(parents=True)
                deck.write_bytes(b"inventory fixture")
            manifest = completion.build_manifest(self.project, assets.ROOT, step)
            completion.record_done(self.project, assets.ROOT, step, manifest)
        self.reconcile()
        manifest = completion.build_manifest(self.project, assets.ROOT, "1.4")
        with self.assertRaisesRegex(ValueError, "Pending Review"):
            completion.record_done(self.project, assets.ROOT, "1.4", manifest)
        self.assertFalse((self.path.parent / "DONE").exists())
        self.edit("Review", "Keep")
        manifest = completion.build_manifest(self.project, assets.ROOT, "1.4")
        completion.record_done(self.project, assets.ROOT, "1.4", manifest)
        self.assertEqual(validation.audit(self.project, target="1.4")["1.4"]["status"], "current")
        # A changed source invalidates this branch, not the independent presenter pipeline.
        docx(self.script, brief="Asset: New request")
        self.assertEqual(validation.audit(self.project, target="1.4")["1.4"]["status"], "blocked")

    def test_long_briefs_preserved(self):
        self.reconcile([self.suggestion(brief="x" * 33000)])
        self.assertEqual(next(r for r in self.records() if r["Origin"] == "AI")["Brief"], "x" * 33000)

    def test_noncurrent_layouts_rejected_without_publication(self):
        self.reconcile()
        text = self.path.read_text(encoding="utf-8")
        sidecar = self.path.with_suffix(".json").read_bytes()
        layouts = {
            "spaced": text.replace("\n**Review:**", "\n\n**Review:**"),
            "hardbreak": text.replace("Pending Review\n", "Pending Review\\\n"),
            "space-hardbreak": text.replace("Pending Review\n", "Pending Review  \n"),
            "html-hardbreak": text.replace("Pending Review\n", "Pending Review<br>\n"),
            "headings": text.replace("**Review:** Pending Review\n", "### Review\n\nPending Review\n\n"),
            "fenced": text.replace("**Review:** Pending Review\n", "### Review\n\n```text\nPending Review\n```\n\n"),
            "embedded": text + '<details>\n<summary>Machine-Maintained Evidence (Do Not Edit)</summary>\n\n```json\n{}\n```\n\n</details>\n\n',
        }
        for name, malformed in layouts.items():
            with self.subTest(layout=name):
                self.path.write_text(malformed, encoding="utf-8")
                before = assets.snapshot(self.project)
                for operation in (lambda: assets.load(self.project), self.reconcile,
                                  lambda: assets.check(self.project),
                                  lambda: assets.confirm_empty(self.project, "Human", "Confirmed")):
                    with self.assertRaises(ValueError):
                        operation()
                    self.assertEqual(assets.snapshot(self.project), before)
                self.assertEqual(self.path.with_suffix(".json").read_bytes(), sidecar)

    def test_historical_evidence_is_opaque_preserved_and_checksummed(self):
        self.reconcile()
        registry = assets.load(self.project)
        # Deliberately not a migration schema: no backup is read or interpreted.
        history = {"unknown": [None, {"arbitrary": "preserved"}], "backup": "missing.xlsx"}
        registry["migration"] = history
        assets.save(registry, self.project)
        self.edit("Brief", "Human brief\\\nSecond paragraph")
        self.edit("Review", "Keep")
        before = [(p.read_bytes(), p.stat().st_mtime_ns)
                  for p in (self.path, self.path.with_suffix(".json"))]
        self.reconcile()
        assets.check(self.project)
        self.assertEqual(assets.load(self.project)["migration"], history)
        self.assertEqual(before, [(p.read_bytes(), p.stat().st_mtime_ns)
                         for p in (self.path, self.path.with_suffix(".json"))])
        self.reconcile([self.suggestion()])
        self.assertEqual(assets.load(self.project)["migration"], history)
        self.edit("Review", "Reject")
        assets.confirm_empty(self.project, "Human", "Confirmed empty slate")
        assets.check(self.project)
        self.assertEqual(assets.load(self.project)["migration"], history)
        sidecar = self.path.with_suffix(".json")
        sidecar.write_text(sidecar.read_text(encoding="utf-8").replace('"missing.xlsx"', '"changed.xlsx"'), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "machine evidence changed"):
            assets.load(self.project)

    def test_removed_commands_and_flags_rejected_by_cli(self):
        for arguments in (("migrate-xlsx",), ("reformat",),
                          ("context", "--from-fenced"), ("context", "--from-cards"),
                          ("context", "--from-embedded")):
            with self.subTest(arguments=arguments):
                result = subprocess.run([sys.executable, str(Path(assets.__file__)), *arguments,
                                         "--project-root", str(self.project)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(assets.snapshot(self.project), [None, None])

    def test_malformed_duplicate_ids_fields_and_unrecognized_text_not_discarded(self):
        self.reconcile([self.suggestion()])
        text = self.path.read_text(encoding="utf-8")
        for malformed in (text.replace("## A01", "## A-bad"),
                          text.replace("## A02", "## A01"),
                          text.replace("**Review:**", "**Asset:**", 1),
                          text + "Human addition outside field blocks\n"):
            with self.subTest(malformed=malformed[:80]):
                self.path.write_text(malformed, encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.reconcile([self.suggestion()])
                self.assertEqual(self.path.read_text(encoding="utf-8"), malformed)

    def test_identity_and_internal_proposal_tampering_fail(self):
        self.reconcile()
        original = assets.load(self.project)
        self.edit("ID", "A98")
        with self.assertRaisesRegex(ValueError, "identities changed"):
            assets.check(self.project)
        assets.save(original, self.project)
        row = original["assets"][0]
        proposal = json.loads(row["Proposal"])
        proposal["fields"]["brief"] = "Tampered"
        row["Proposal"] = assets.encoded(proposal)
        assets.save(original, self.project)
        with self.assertRaisesRegex(ValueError, "metadata changed"):
            self.reconcile()

    def test_missing_malformed_duplicate_and_mismatched_sidecar_fail_closed(self):
        self.reconcile()
        sidecar = self.path.with_suffix(".json")
        original = sidecar.read_text(encoding="utf-8")
        sidecar.unlink()
        for operation in (lambda: assets.load(self.project), lambda: self.reconcile(),
                          lambda: assets.check(self.project),
                          lambda: assets.confirm_empty(self.project, "Human", "Confirmed")):
            with self.assertRaisesRegex(ValueError, "Missing Asset Registry.json"):
                operation()
        for text in ("{broken", original.replace('"control": {', '"control": {}, "control": {')):
            sidecar.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                assets.load(self.project)
        sidecar.write_text(original, encoding="utf-8")
        registry = assets.load(self.project)
        registry["assets"][0]["Script"] = "different.docx"
        sidecar.write_text(assets.render_evidence(registry), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Read-only"):
            assets.load(self.project)

    def test_second_replace_failure_rolls_back_both_and_failed_rollback_blocks_readers(self):
        self.reconcile()
        before = assets.snapshot(self.project)
        real_replace = assets.os.replace
        calls = []

        def fail_second(source, target):
            calls.append(str(target))
            if len(calls) == 2:
                raise PermissionError("Sidecar locked")
            return real_replace(source, target)

        with patch.object(assets.os, "replace", side_effect=fail_second):
            with self.assertRaises(PermissionError):
                self.reconcile([self.suggestion()])
        self.assertEqual(assets.snapshot(self.project), before)
        self.assertEqual(list(self.path.parent.glob(".assets-*")), [])
        calls.clear()

        def fail_and_block_rollback(source, target):
            calls.append(str(target))
            if len(calls) >= 2:
                raise PermissionError("Publication and rollback locked")
            return real_replace(source, target)

        with patch.object(assets.os, "replace", side_effect=fail_and_block_rollback):
            with self.assertRaises(PermissionError):
                self.reconcile([self.suggestion()])
        guard = self.path.parent / ".assets-publication.json"
        recovery = json.loads(guard.read_text(encoding="utf-8"))
        self.assertEqual([(guard.parent / p).read_bytes() for p in recovery["previous"]], before)
        with self.assertRaisesRegex(ValueError, "Interrupted registry pair publication"):
            assets.load(self.project)

    def test_sidecar_required_in_shared_inventory_and_fingerprint(self):
        self.reconcile()
        self.edit("Review", "Keep")
        stages = validation.load_stages()
        manifest = completion.build_manifest(self.project, assets.ROOT, "1.4")
        self.assertEqual({Path(e["path"]).name for e in manifest["outputs"]},
                         {"Asset Registry.md", "Asset Registry.json"})
        sidecar = self.path.with_suffix(".json")
        with self.assertRaises(ValueError):
            invalidation.prepare_regeneration(self.project, assets.ROOT, "1.4", [sidecar], "not allowed")
        sidecar.write_text(sidecar.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertNotEqual(validation.validate_evidence(self.project, assets.ROOT, stages["1.4"], manifest)[0], "current")
        sidecar.unlink()
        missing = completion.build_manifest(self.project, assets.ROOT, "1.4")
        self.assertNotEqual(validation.validate_evidence(self.project, assets.ROOT, stages["1.4"], missing)[0], "current")

    def test_first_publication_failure_leaves_neither_file_active(self):
        real_replace = assets.os.replace
        calls = []

        def fail_second(source, target):
            calls.append(True)
            if len(calls) == 2:
                raise PermissionError("Sidecar locked")
            return real_replace(source, target)

        with patch.object(assets.os, "replace", side_effect=fail_second):
            with self.assertRaises(PermissionError):
                self.reconcile()
        self.assertEqual(assets.snapshot(self.project), [None, None])
        self.assertEqual(list(self.path.parent.glob(".assets-*")), [])

    def test_sidecar_saved_edit_before_publication_is_preserved(self):
        self.reconcile()
        original = assets.snapshot(self.project)
        registry = assets.load(self.project)
        sidecar = self.path.with_suffix(".json")
        changed = sidecar.read_bytes() + b"\n"
        sidecar.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, "changed before publication"):
            assets.save(registry, self.project, expected_snapshot=original)
        self.assertEqual(sidecar.read_bytes(), changed)

    def test_cli_prerequisites_reconcile_review_check_and_empty_confirmation(self):
        helper = str(Path(assets.__file__))

        def run(action, *args):
            return subprocess.run([sys.executable, helper, action, "--project-root", str(self.project), *args],
                                  capture_output=True, text=True)

        envelope = self.project / "suggestions.json"
        envelope.write_text(json.dumps(self.envelope()), encoding="utf-8")
        result = run("reconcile", "--suggestions", str(envelope))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Prerequisites", result.stderr)
        self.assertFalse(self.path.exists())
        for step in ("1.1", "1.2", "1.3"):
            if step == "1.2":
                deck = self.project / "1-Pre Production/2-Cue Cards/A.pptx"
                deck.parent.mkdir(parents=True)
                deck.write_bytes(b"inventory fixture")
            completion.record_done(self.project, assets.ROOT, step,
                                   completion.build_manifest(self.project, assets.ROOT, step))
        result = run("reconcile", "--suggestions", str(envelope))
        self.assertEqual(result.returncode, 0, result.stderr)
        ctx = run("context")
        self.assertEqual(ctx.returncode, 0, ctx.stderr)
        self.assertEqual(json.loads(ctx.stdout)["registry"][0]["Origin"], "User")
        self.assertNotEqual(run("check").returncode, 0)
        self.edit("Review", "Reject")
        self.assertNotEqual(run("check").returncode, 0)
        result = run("confirm-empty", "--by", "Human", "--reason", "Explicit human confirmation in test")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["advisory_only"])


@unittest.skipUnless(os.environ.get("STUDIO_ASSET_VERIFY_PROJECT"), "Explicit read-only project verification only")
class CanonicalReadOnlyTests(unittest.TestCase):
    def test_actual_project_inspect_and_indexed_context(self):
        project = Path(os.environ["STUDIO_ASSET_VERIFY_PROJECT"]).resolve(strict=True)
        before = readonly_state(project)
        try:
            result = subprocess.run([sys.executable, assets.__file__, "inspect", "--project-root", str(project)],
                                    capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            report = json.loads(result.stdout)
            print("Canonical inspection: " + json.dumps(report, ensure_ascii=True))
            self.assertEqual(report["counts"]["total"], 18)
            self.assertTrue(report["input_status"]["inputs_current"])
            self.assertTrue(report["input_status"]["comments"]["complete"])
            indexed = subprocess.run([sys.executable, assets.__file__, "context", "--indexed",
                                      "--project-root", str(project)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(indexed.returncode, 0, indexed.stderr)
            self.assertIn("[0]", indexed.stdout)
        finally:
            self.assertEqual(before, readonly_state(project), "Read-only commands changed project state")
            print(f"Canonical read-only inventory verified: {len(before)} paths; protected bytes and all mtimes unchanged")


if __name__ == "__main__":
    unittest.main()
