"""Check that the flat benchmark cases remain runnable and honestly scored."""
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import benchmark


FIXTURE = Path(__file__).resolve().parents[1] / "benchmarks"


class BenchmarkTests(unittest.TestCase):
    def test_multirecording_review_preserves_reference_and_scores_each_recording(self):
        reference = FIXTURE / "What Is Freedom For/What Is Freedom For__Takes.v002.json"
        original = reference.read_bytes()
        with TemporaryDirectory() as directory:
            output = Path(directory)
            report = benchmark.run(FIXTURE, output)
            self.assertEqual((report["generation"]["scripts"], report["generation"]["transcripts"]), (1, 2))
            self.assertEqual(len(report["cases"]), 2)
            first, second = report["cases"]
            self.assertEqual(first["exact_take_ranges"], benchmark.metrics(27, 27, 27))
            self.assertEqual(second["exact_take_ranges"], benchmark.metrics(25, 26, 27))
            self.assertEqual(first["word_assignments"]["correct"], 426)
            self.assertEqual(second["word_assignments"]["correct"], 400)
            self.assertEqual(first["classification_counts"], {"exact": 19, "partial": 8, "flub": 0})
            self.assertEqual(second["classification_counts"], {"exact": 16, "partial": 10, "flub": 0})
            self.assertTrue(all(0 <= item["quality_score"] <= 100
                                for case in report["cases"] for item in case["classification_changes"]))
            prediction = json.loads((output / "What Is Freedom For/What Is Freedom For__Takes.v002.json").read_text())
            self.assertEqual(len(prediction["recordings"]), 2)
            self.assertEqual(prediction["algorithm"]["quality"]["exact_rule"], "normalized_text_equality")
            self.assertNotIn("exact_threshold", prediction["algorithm"]["quality"])
            self.assertEqual(prediction["algorithm"]["quality"]["partial_threshold"], 75)
        self.assertEqual(reference.read_bytes(), original)

    def test_historical_single_recording_ranges_and_initial_report_are_preserved(self):
        reference = FIXTURE / "What Is Freedom For/What Is Freedom For__Takes.v001.json"
        initial = FIXTURE.parent / "reports/initial/What Is Freedom For.json"
        original, initial_bytes = reference.read_bytes(), initial.read_bytes()
        manifest, cases = benchmark.load_cases(FIXTURE)
        case = cases[0]
        case["takes"] = reference.name
        case["transcripts"] = case["reviewed_transcripts"] = ["What Is Freedom For__C01__lav.json"]
        with TemporaryDirectory() as directory, patch.object(benchmark, "load_cases", return_value=(manifest, cases)):
            report = benchmark.run(FIXTURE, Path(directory))
            expected = json.loads(initial_bytes)
            for key in ("exact_take_ranges", "word_assignments", "discarded_words"):
                self.assertEqual(report["cases"][0][key], expected[key], key)
        self.assertEqual(reference.read_bytes(), original)
        self.assertEqual(initial.read_bytes(), initial_bytes)

    def test_rebasing_still_checks_exact_source_bytes_and_word_snapshots(self):
        project = FIXTURE / "What Is Freedom For"
        reference_path = project / "What Is Freedom For__Takes.v002.json"
        original = reference_path.read_bytes()
        for mode in ("fingerprint", "words"):
            with self.subTest(mode=mode):
                reference = benchmark.reference_documents(project, reference_path)[0]
                if mode == "fingerprint":
                    reference["sources"]["transcript"]["source"]["sha256"] = "0" * 64
                    message = "bytes differ"
                else:
                    reference["sources"]["transcript"]["words"][0]["text"] = "tampered"
                    message = "word indexes, text, or timings differ"
                with self.assertRaisesRegex(ValueError, message):
                    benchmark.compare_recording(project, reference, reference_path, {})
        self.assertEqual(reference_path.read_bytes(), original)

    def test_single_case_run_and_frozen_directory_protection(self):
        with TemporaryDirectory() as directory:
            report = benchmark.run(FIXTURE, Path(directory), "What Is Freedom For")
            self.assertEqual(len(report["cases"]), 2)
            self.assertEqual(report["generation"]["transcripts"], 2)
        with self.assertRaisesRegex(ValueError, "outside the frozen"):
            benchmark.run(FIXTURE, FIXTURE / "predictions")

    def test_unreviewed_takes_and_transcripts_are_rejected_before_generation(self):
        for mode in ("machine-takes", "unreviewed-recording"):
            with self.subTest(mode=mode), TemporaryDirectory() as directory:
                root = Path(directory)
                fixture = root / "benchmarks"
                shutil.copytree(FIXTURE, fixture)
                manifest_path = fixture / "manifest.json"
                manifest = json.loads(manifest_path.read_text())
                case = manifest["cases"][0]
                if mode == "machine-takes":
                    case["takes_role"] = "unreviewed-initial-prediction"
                else:
                    case["transcripts"].append("What Is Freedom For__C03__lav.json")
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "human-reviewed"):
                    benchmark.run(fixture, root / "predictions")
                self.assertFalse((root / "predictions").exists())


if __name__ == "__main__":
    unittest.main()
