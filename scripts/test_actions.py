"""Regression checks for compact tool descriptions and action audit behavior."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from studio import actions
from studio.project import Project


class ActionInputTests(unittest.TestCase):
    def setUp(self):
        self.project = object.__new__(Project)
        object.__setattr__(self.project, "root", Path("/example/project"))

    def test_snapshot_names_defaults_and_values_before_mutation(self):
        def edit(project, state, path, classification="exact"):
            state["selected"]["transcript"] = 8
            return state

        state = {"selected": {"transcript": 3}}
        captured = {}

        def describe(inputs, result):
            captured.update(inputs=inputs, output=result)
            return "Edited selection"

        registered = actions._Action(edit, "", True, describe)
        with patch.dict(actions._registry, {"test.edit": registered}), patch.object(actions, "_log") as log:
            actions.invoke("test.edit", self.project, state, Path("script.docx"))
        inputs = captured["inputs"]
        self.assertEqual(inputs["state"]["selected"]["transcript"], 3)
        self.assertEqual(state["selected"]["transcript"], 8)
        self.assertEqual(inputs["path"], "script.docx")
        self.assertEqual(inputs["classification"], "exact")
        self.assertEqual(inputs["project"]["root"], "/example/project")
        output = captured["output"]
        self.assertEqual(output["selected"]["transcript"], 8)
        self.assertEqual(log.call_args.args[2], "Edited selection")

    def test_large_sources_keep_selection_and_collection_counts(self):
        def edit(project, state):
            pass

        state = {"transcript": {"rows": ["sentence"] * 10000}, "selected": {"transcript": 999}}
        inputs = json.loads(actions._input_snapshot(edit, self.project, (state,), {}))
        self.assertEqual(inputs["state"]["transcript"]["rows"]["count"], 10000)
        self.assertEqual(inputs["state"]["selected"]["transcript"], 999)

    def test_cycles_strings_and_unknown_objects_are_bounded(self):
        class Internal:
            def __repr__(self):
                raise AssertionError("Internal objects must not be repr'd")

        def edit(project, state):
            pass

        state = {"text": "x" * 10000, "internal": Internal()}
        state["cycle"] = state
        snapshot = actions._input_snapshot(edit, self.project, (state,), {})
        self.assertLess(len(snapshot), 10000)
        self.assertIn("truncated", snapshot)
        self.assertIn("nesting limit", snapshot)

    def test_failed_action_logs_inputs_and_preserves_exception(self):
        original = ValueError("Action failed")

        def fail(project, name):
            raise original

        with patch.dict(actions._registry, {"test.fail": actions._Action(fail, "", True)}), patch.object(actions, "_log") as log:
            with self.assertRaises(ValueError) as caught:
                actions.invoke("test.fail", self.project, name="review")
        self.assertIs(caught.exception, original)
        self.assertIn('name="review"', log.call_args.args[2])
        self.assertIs(log.call_args.kwargs["error"], original)
        self.assertNotIn("output", log.call_args.kwargs)

    def test_save_logs_versioned_return_path(self):
        def save(project, name):
            return "studio/line/review.v003.json"

        with patch.dict(actions._registry, {"test.save": actions._Action(save, "", True)}), patch.object(actions, "_log") as log:
            result = actions.invoke("test.save", self.project, "review")
        self.assertEqual(result, "studio/line/review.v003.json")
        self.assertIn(result, log.call_args.args[2])

    def test_returned_state_uses_same_collection_limits(self):
        output = json.loads(actions._value_snapshot({"rows": ["sentence"] * 10000}))
        self.assertEqual(output["rows"]["count"], 10000)
        self.assertNotIn("sentence", json.dumps(output))

    def test_log_writes_compact_summary_and_failure_type(self):
        with patch.object(actions, "_ensure_log"), patch.object(actions, "_append_helper") as helper:
            actions._log(self.project, "test.close", "Closed project: example")
            items = helper.return_value.call_args.args[2]
            self.assertIn("Summary: Closed project: example", items)
            self.assertEqual(len(items), 2)
            self.assertIn("succeeded", helper.return_value.call_args.args[1])
            actions._log(self.project, "test.fail", "Attempted edit", error=ValueError())
            items = helper.return_value.call_args.args[2]
            self.assertIn("Error: ValueError", items)
            self.assertIn("failed", helper.return_value.call_args.args[1])

    def test_formatter_failure_uses_bounded_generic_summary(self):
        def edit(project, state):
            return state

        def broken(inputs, result):
            raise ValueError("Formatting failed")

        state = {"text": "x" * 10000}
        registered = actions._Action(edit, "", True, broken)
        with patch.dict(actions._registry, {"test.edit": registered}), patch.object(actions, "_log") as log:
            self.assertIs(actions.invoke("test.edit", self.project, state), state)
        summary = log.call_args.args[2]
        self.assertIn("state=<dict", summary)
        self.assertNotIn("x" * 100, summary)
        self.assertLessEqual(len(summary), 600)

    def test_formatter_is_single_line_and_bounded(self):
        registered = actions._Action(lambda project: None, "", True, lambda inputs, result: "Long\n" + "x" * 10000)
        summary = actions._summary(registered, "{}")
        self.assertNotIn("\n", summary)
        self.assertEqual(len(summary), 600)

    def test_decorator_registers_formatter_without_exposing_it_as_metadata(self):
        formatter = lambda inputs, result: "Done"
        with patch.dict(actions._registry):
            @actions.action("test.formatted", describe=formatter)
            def edit(project):
                pass

            self.assertIs(actions._registry["test.formatted"].describe, formatter)
            self.assertNotIn("describe", actions.available_actions()["test.formatted"])

    def test_line_discard_logs_selected_sentence_without_state_dump(self):
        from studio.tools.line import actions as line_actions

        state = {"transcript": {"rows": [{"id": str(index)} for index in range(1000)]},
                 "selected": {"transcript": 53}, "links": [], "discarded": [], "dirty": False}
        with patch.object(actions, "_log") as log:
            result = actions.invoke("line.discard", self.project, state)
        self.assertEqual(result["discarded"], ["53"])
        self.assertEqual(log.call_args.args[2], "Discarded transcript sentence 54")

    def test_line_formatter_descriptions(self):
        from studio.tools.line.actions import _describe_line

        inputs = {"state": {"selected": {"script": 1, "transcript": 5}},
                  "classification": "exact", "path": "/project/review.v003.json"}
        cases = [
            ("load_script", {"script": {"source": {"path": "Approved/Script.docx"}, "rows": [1] * 25}}, "Loaded script: Script.docx (25 sentences)"),
            ("load_transcript", {"transcript": {"source": {"path": "Transcribe/Audio.json"}, "rows": [1] * 31}}, "Loaded transcript: Audio.json (31 sentences)"),
            ("link", {"transcript": {"rows": [{"id": str(i)} for i in range(6)]}, "selected": {"transcript": 5}, "links": [{"transcript_id": "5", "classification": "exact"}]}, "Linked script sentence 2 to transcript sentence 6 as exact"),
            ("unlink", {}, "Unlinked transcript sentence 6"),
            ("restore", {}, "Restored transcript sentence 6"),
            ("save", "studio/line/review.v003.json", "Saved: studio/line/review.v003.json"),
            ("load_mapping", {"links": [1], "discarded": [1, 2]}, "Loaded mapping: review.v003.json (links: 1, discarded: 2)"),
            ("close_script", {}, "Closed script panel"),
            ("close_transcript", {}, "Closed transcript panel"),
            ("reset", {}, "Reset sources and mapping"),
        ]
        for operation, result, expected in cases:
            with self.subTest(operation=operation):
                self.assertEqual(_describe_line(operation)(inputs, result), expected)

    def test_dashboard_descriptions(self):
        from studio.session import _describe_close, _describe_open
        from studio.project import ProjectLock

        inputs = {"project": {"root": str(self.project.root)}}
        self.assertEqual(_describe_open(inputs, ProjectLock(self.project)), "Opened project: project")
        self.assertEqual(_describe_close(inputs, None), "Closed project: project")

    def test_real_log_entry_stays_small_for_large_transcript(self):
        from studio.tools.line import actions as line_actions

        with TemporaryDirectory() as directory:
            project = object.__new__(Project)
            object.__setattr__(project, "root", Path(directory).resolve())
            state = {"transcript": {"rows": [{"id": str(index)} for index in range(10000)]},
                     "selected": {"transcript": 53}, "links": [], "discarded": [], "dirty": False}
            actions.invoke("line.discard", project, state)
            text = project.log_path.read_text(encoding="utf-8")
        self.assertIn("line.discard — succeeded", text)
        self.assertIn("Summary: Discarded transcript sentence 54", text)
        self.assertNotIn("Inputs:", text)
        self.assertNotIn("Return value:", text)
        self.assertLess(len(text), 400)

    def test_log_failure_preserves_completed_result(self):
        result = {"saved": "review.v003.json"}

        def save(project):
            return result

        with patch.dict(actions._registry, {"test.save": actions._Action(save, "", True)}), patch.object(actions, "_log", side_effect=OSError("Disk full")):
            with self.assertRaises(actions.ActionLogError) as caught:
                actions.invoke("test.save", self.project)
        self.assertIs(caught.exception.result, result)

    def test_navigation_still_skips_logging(self):
        def navigate(project, index):
            return index

        with patch.dict(actions._registry, {"test.navigate": actions._Action(navigate, "", False)}), patch.object(actions, "_input_snapshot") as snapshot, patch.object(actions, "_log") as log:
            self.assertEqual(actions.invoke("test.navigate", self.project, 2), 2)
        snapshot.assert_not_called()
        log.assert_not_called()


if __name__ == "__main__":
    unittest.main()
