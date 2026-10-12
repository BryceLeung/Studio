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
    def test_boundary_trial_scores_both_versions_and_reproduces_c02_intervention(self):
        frozen = {path: path.read_bytes() for path in FIXTURE.rglob("*") if path.is_file()}
        initial = FIXTURE.parent / "reports/initial/What Is Freedom For.json"
        initial_bytes = initial.read_bytes()
        real_references = benchmark.reference_documents
        generated_before_references = []
        with TemporaryDirectory() as directory:
            output = Path(directory)

            def references(project, path):
                for version in ("v001", "v002"):
                    relative = Path("What Is Freedom For") / f"What Is Freedom For__Takes.{version}.json"
                    self.assertTrue((output / "baseline" / relative).is_file())
                    self.assertTrue((output / "preview" / relative).is_file())
                generated_before_references.append(path.name)
                return real_references(project, path)

            with patch.object(benchmark, "reference_documents", side_effect=references):
                report = benchmark.run(FIXTURE, output, all_versions=True, boundary_proposals=True)
            self.assertEqual(report["generation"]["reference_versions"], 2)
            self.assertEqual(report["generation"]["transcripts"], 2)
            self.assertEqual(len(generated_before_references), 2)
            self.assertEqual(len(report["reference_conflicts"]), 1)
            self.assertEqual((report["reference_conflicts"][0]["earlier_only_ranges"],
                              report["reference_conflicts"][0]["later_only_ranges"]), (5, 4))
            first, conflicting, second = report["cases"]
            self.assertEqual(first["exact_take_ranges"], benchmark.metrics(23, 27, 28))
            self.assertEqual(first["boundary_preview"]["exact_take_ranges"], benchmark.metrics(28, 28, 28))
            self.assertEqual(first["boundary_preview"]["word_assignments"]["correct"], 426)
            # v002 retains the older C01 boundaries; report the disagreement.
            self.assertEqual(conflicting["boundary_preview"]["exact_take_ranges"], benchmark.metrics(23, 28, 27))
            self.assertEqual(conflicting["boundary_preview"]["word_assignments"]["correct"], 418)
            self.assertEqual(second["exact_take_ranges"], benchmark.metrics(25, 26, 27))
            # The human-identified 'having...' correction differs from v002.
            self.assertEqual(second["boundary_preview"]["exact_take_ranges"], benchmark.metrics(25, 27, 27))
            self.assertEqual(second["boundary_preview"]["word_assignments"]["correct"], 411)
            self.assertEqual(len(second["boundary_proposals"]), 2)
            proposal = second["boundary_proposals"][0]
            self.assertEqual([link["transcript"] for link in proposal["after"][:2]], [
                {"word_start": 67, "word_end": 78}, {"word_start": 79, "word_end": 98}])
            rewind = second["boundary_proposals"][1]
            self.assertEqual([link["transcript"] for link in rewind["after"]], [
                {"word_start": 145, "word_end": 158}, {"word_start": 159, "word_end": 171}])
            self.assertEqual(second["boundary_preview"]["word_errors"][0]["index"], 158)
            preview = json.loads((output / "preview/What Is Freedom For/What Is Freedom For__Takes.v002.json").read_text())
            baseline = json.loads((output / "baseline/What Is Freedom For/What Is Freedom For__Takes.v002.json").read_text())
            self.assertEqual(preview["script"], baseline["script"])
            self.assertEqual(preview["script_groupings"], baseline["script_groupings"])
            self.assertEqual([r["transcript"] for r in preview["recordings"]], [r["transcript"] for r in baseline["recordings"]])
            self.assertNotIn("boundary_proposals", baseline["algorithm"])
            self.assertIn("Boundary proposal preview", benchmark.summary(report))
        self.assertTrue(all(path.read_bytes() == content for path, content in frozen.items()))
        self.assertEqual(initial.read_bytes(), initial_bytes)

    def test_multirecording_review_preserves_reference_and_scores_each_recording(self):
        reference = FIXTURE / "What Is Freedom For/What Is Freedom For__Takes.v002.json"
        original = reference.read_bytes()
        with TemporaryDirectory() as directory:
            output = Path(directory)
            report = benchmark.run(FIXTURE, output)
            self.assertEqual((report["generation"]["scripts"], report["generation"]["transcripts"]), (1, 2))
            self.assertEqual(len(report["cases"]), 2)
            first, second = report["cases"]
            self.assertEqual(first["exact_take_ranges"], benchmark.metrics(23, 28, 27))
            self.assertEqual(second["exact_take_ranges"], benchmark.metrics(25, 27, 27))
            self.assertEqual(first["word_assignments"]["correct"], 418)
            self.assertEqual(second["word_assignments"]["correct"], 411)
            self.assertEqual(first["classification_counts"], {"exact": 22, "partial": 5, "flub": 1})
            self.assertEqual(second["classification_counts"], {"exact": 16, "partial": 11, "flub": 0})
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
            self.assertEqual(expected["exact_take_ranges"]["correct"], 23)
            self.assertEqual(report["cases"][0]["exact_take_ranges"], benchmark.metrics(28, 28, 28))
            self.assertEqual(report["cases"][0]["word_assignments"]["correct"], 426)
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
