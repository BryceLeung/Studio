import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from docx import Document


HELPER = Path(__file__).with_name("write-script.py")
spec = importlib.util.spec_from_file_location("write_script", HELPER)
writer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(writer)


class WriteScriptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.step = self.project / "1-Pre Production" / "1-Script Review"
        (self.step / "Approved").mkdir(parents=True)
        self.draft = {
            "title": "Account comparison",
            "review": {
                "objective": "Trust", "audience": "U.S. parents",
                "ending_cta": "Decision framework; no sales CTA",
                "factual_caveats": "State tax treatment varies",
            },
            "sources": ["IRS: https://www.irs.gov/"],
            "notes": ["Packaging note", "Potential extract idea"],
            "paragraphs": ["First spoken paragraph.", "Second paragraph.\nAnother line."],
        }

    def test_structure_and_word_count(self):
        target, words = writer.write_script(self.project, "Comparison.docx", self.draft)
        paragraphs = [p.text for p in Document(target).paragraphs]
        self.assertEqual(paragraphs[0], self.draft["title"])
        start = paragraphs.index("Spoken script")
        self.assertEqual(paragraphs[start + 1:], self.draft["paragraphs"])
        notes_start = paragraphs.index("Additional notes (not spoken)")
        self.assertEqual(paragraphs[notes_start + 1:notes_start + 3], self.draft["notes"])
        self.assertIn("Objective: Trust", paragraphs[:start])
        self.assertIn("Intended audience: U.S. parents", paragraphs[:start])
        self.assertIn("Ending / CTA: Decision framework; no sales CTA", paragraphs[:start])
        self.assertIn("Factual caveats: State tax treatment varies", paragraphs[:start])
        self.assertIn("Estimated runtime: 0.0-0.1 minutes (7 spoken words at 140-160 wpm)", paragraphs[:start])
        self.assertIn(self.draft["sources"][0], paragraphs[:start])
        self.assertEqual(words, 7)
        self.assertFalse((self.step / "DONE").exists())
        self.assertEqual(list((self.step / "Approved").iterdir()), [])

    def test_notes_are_optional(self):
        draft = {"title": "Title", "paragraphs": ["Speech"]}
        target, _ = writer.write_script(self.project, "Minimal.docx", draft)
        paragraphs = [p.text for p in Document(target).paragraphs]
        self.assertEqual(paragraphs[0], "Title")
        self.assertIn("Objective: Not specified", paragraphs)
        self.assertIn("Not supplied", paragraphs)
        self.assertNotIn("Additional notes (not spoken)", paragraphs)
        self.assertEqual(paragraphs[paragraphs.index("Spoken script") + 1:], ["Speech"])

    def test_template_requires_actual_script(self):
        draft = json.loads(HELPER.with_name("draft-template.json").read_text(encoding="utf-8"))
        self.assertEqual(set(draft["review"]), set(writer.REVIEW_FIELDS))
        with self.assertRaises(ValueError):
            writer.write_script(self.project, "Blank.docx", draft)
        draft["title"] = "Filled template"
        draft["paragraphs"] = ["Actual speech."]
        target, _ = writer.write_script(self.project, "Filled.docx", draft)
        self.assertTrue(target.is_file())

    def test_existing_candidate_is_preserved(self):
        target, _ = writer.write_script(self.project, "Comparison.docx", self.draft)
        original = target.read_bytes()
        with self.assertRaises(FileExistsError):
            writer.write_script(self.project, target.name, self.draft)
        self.assertEqual(target.read_bytes(), original)

    def test_invalid_filenames_and_drafts_do_not_publish(self):
        for filename in ("../escape.docx", "Approved/Script.docx", "C:\\Script.docx", "draft.txt", ".docx"):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                writer.write_script(self.project, filename, self.draft)
        for draft in (
            [], {}, {**self.draft, "title": "Spoken script"},
            {**self.draft, "paragraphs": []}, {**self.draft, "paragraphs": [42]},
            {**self.draft, "notes": "not a list"}, {**self.draft, "notes": ["Spoken script"]},
            {**self.draft, "typo": "unknown field"},
            {**self.draft, "review": []}, {**self.draft, "review": {"audience": 42}},
            {**self.draft, "review": {"objective": "Sales"}},
            {**self.draft, "review": {"unknown": "typo"}},
            {**self.draft, "sources": "not a list"},
            {**self.draft, "sources": ["Spoken script"]},
        ):
            with self.subTest(draft=draft), self.assertRaises(ValueError):
                writer.write_script(self.project, "Invalid.docx", draft)
        self.assertEqual(list(self.step.glob("*.docx")), [])

    def test_cli_stdin_file_and_failure(self):
        command = [sys.executable, str(HELPER), "--project-root", str(self.project), "--filename", "CLI.docx"]
        result = subprocess.run(command, input=json.dumps(self.draft), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Spoken words: 7", result.stdout)
        result = subprocess.run(command, input=json.dumps(self.draft), text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to overwrite", result.stderr)
        draft_path = self.project / "draft.json"
        draft_path.write_text(json.dumps(self.draft), encoding="utf-8-sig")
        command[-1] = "File.docx"
        result = subprocess.run([*command, "--input", str(draft_path)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        command[-1] = "Bad.docx"
        result = subprocess.run(command, input="not JSON", text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.step / "Bad.docx").exists())

    def test_missing_step_is_not_created(self):
        with self.assertRaises(ValueError):
            writer.write_script(self.step / "Approved", "Missing.docx", self.draft)


if __name__ == "__main__":
    unittest.main()
