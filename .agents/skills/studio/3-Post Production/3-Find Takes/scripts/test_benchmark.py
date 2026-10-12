"""Check that the flat benchmark cases remain runnable and honestly scored."""
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import benchmark


FIXTURE = Path(__file__).resolve().parents[1] / "benchmarks"
TAKES = Path("What Is Freedom For/What Is Freedom For__Takes.json")


class BenchmarkTests(unittest.TestCase):
    def test_boundary_trial_matches_approved_reference_and_preserves_c02_rewind(self):
        frozen = {path: path.read_bytes() for path in FIXTURE.rglob("*") if path.is_file()}
        initial = FIXTURE.parent / "reports/initial/What Is Freedom For.json"
        initial_bytes = initial.read_bytes()
        real_references = benchmark.reference_documents
        generated_before_references = []
        with TemporaryDirectory() as directory:
            output = Path(directory)

            def references(project, path):
                self.assertTrue((output / "baseline" / TAKES).is_file())
                self.assertTrue((output / "preview" / TAKES).is_file())
                generated_before_references.append(path.name)
                return real_references(project, path)

            with patch.object(benchmark, "reference_documents", side_effect=references):
                report = benchmark.run(FIXTURE, output, all_versions=True, boundary_proposals=True)
            self.assertEqual(report["generation"]["reference_versions"], 1)
            self.assertEqual(report["generation"]["transcripts"], 2)
            self.assertEqual(generated_before_references, [TAKES.name])
            self.assertEqual(report["reference_conflicts"], [])
            first, second = report["cases"]
            self.assertEqual(first["exact_take_ranges"], benchmark.metrics(23, 27, 28))
            self.assertEqual(first["boundary_preview"]["exact_take_ranges"], benchmark.metrics(28, 28, 28))
            self.assertEqual(first["boundary_preview"]["word_assignments"]["correct"], 426)
            self.assertEqual(second["exact_take_ranges"], benchmark.metrics(23, 26, 27))
            self.assertEqual(second["word_assignments"]["correct"], 399)
            self.assertEqual(second["boundary_preview"]["exact_take_ranges"], benchmark.metrics(27, 27, 27))
            self.assertEqual(second["boundary_preview"]["word_assignments"]["correct"], 412)
            self.assertEqual(len(second["boundary_proposals"]), 2)
            proposal = second["boundary_proposals"][0]
            self.assertEqual([link["transcript"] for link in proposal["after"][:2]], [
                {"word_start": 67, "word_end": 78}, {"word_start": 79, "word_end": 98}])
            rewind = second["boundary_proposals"][1]
            self.assertEqual([link["transcript"] for link in rewind["after"]], [
                {"word_start": 145, "word_end": 158}, {"word_start": 159, "word_end": 171}])
            self.assertEqual([link["script"] for link in rewind["after"]], [
                {"word_start": 143, "word_end": 158}, {"word_start": 130, "word_end": 142}])
            self.assertTrue(rewind["after"][0]["transcript_text"].endswith("having..."))
            self.assertTrue(rewind["after"][1]["transcript_text"].startswith("He wanted"))
            self.assertEqual(second["boundary_preview"]["word_errors"], [])
            preview = json.loads((output / "preview" / TAKES).read_text(encoding="utf-8"))
            baseline = json.loads((output / "baseline" / TAKES).read_text(encoding="utf-8"))
            self.assertEqual(preview["script"], baseline["script"])
            self.assertEqual(preview["script_groupings"], baseline["script_groupings"])
            self.assertEqual([r["transcript"] for r in preview["recordings"]], [r["transcript"] for r in baseline["recordings"]])
            self.assertNotIn("boundary_proposals", baseline["algorithm"])
            self.assertIn("Boundary proposal preview", benchmark.summary(report))
        self.assertTrue(all(path.read_bytes() == content for path, content in frozen.items()))
        self.assertEqual(initial.read_bytes(), initial_bytes)
        self.assertEqual(json.loads(initial_bytes)["exact_take_ranges"]["correct"], 23)

    def test_multirecording_review_preserves_reference_and_scores_each_recording(self):
        reference = FIXTURE / TAKES
        original = reference.read_bytes()
        with TemporaryDirectory() as directory:
            output = Path(directory)
            report = benchmark.run(FIXTURE, output)
            self.assertEqual((report["generation"]["scripts"], report["generation"]["transcripts"]), (1, 2))
            self.assertEqual(len(report["cases"]), 2)
            first, second = report["cases"]
            self.assertEqual(first["exact_take_ranges"], benchmark.metrics(28, 28, 28))
            self.assertEqual(second["exact_take_ranges"], benchmark.metrics(27, 27, 27))
            self.assertEqual(first["word_assignments"]["correct"], 426)
            self.assertEqual(second["word_assignments"]["correct"], 412)
            self.assertEqual(first["classification_counts"], {"exact": 22, "partial": 5, "flub": 1})
            self.assertEqual(second["classification_counts"], {"exact": 16, "partial": 11, "flub": 0})
            for case, count in zip(report["cases"], (28, 27)):
                self.assertEqual(case["classification_agreement_on_exact_ranges"], {"correct": count, "compared": count})
                self.assertEqual(case["classification_changes"], [])
                self.assertEqual(case["word_errors"], [])
            prediction = json.loads((output / TAKES).read_text(encoding="utf-8"))
            self.assertEqual(len(prediction["recordings"]), 2)
            self.assertEqual(prediction["algorithm"]["quality"]["exact_rule"], "normalized_text_equality")
            self.assertNotIn("exact_threshold", prediction["algorithm"]["quality"])
            self.assertEqual(prediction["algorithm"]["quality"]["partial_threshold"], 75)
        self.assertEqual(reference.read_bytes(), original)

    def test_legacy_line_reviews_remain_supported(self):
        project = FIXTURE / TAKES.parent
        reference = json.loads((FIXTURE / TAKES).read_text(encoding="utf-8"))
        expected = benchmark.reference_documents(project, FIXTURE / TAKES)
        multi = {**reference, "schema": benchmark.find_takes.mapping.SCHEMA,
                 "schema_version": 3, "mapping_name": TAKES.stem, "version": 1}
        with TemporaryDirectory() as directory:
            path = Path(directory) / TAKES.name
            for document, documents in ((expected[0], expected[:1]), (multi, expected)):
                with self.subTest(version=document["schema_version"]):
                    path.write_text(json.dumps(document), encoding="utf-8")
                    self.assertEqual(benchmark.reference_documents(project, path), documents)

    def test_unsupported_reference_formats_are_rejected(self):
        reference = json.loads((FIXTURE / TAKES).read_text(encoding="utf-8"))
        with TemporaryDirectory() as directory:
            path = Path(directory) / TAKES.name
            for changes in ({"schema": "unknown"}, {"schema_version": 2}, {"schema_version": True}):
                with self.subTest(changes=changes):
                    path.write_text(json.dumps({**reference, **changes}), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        benchmark.reference_documents(FIXTURE / TAKES.parent, path)

    def test_rebasing_still_checks_exact_source_bytes_and_word_snapshots(self):
        project = FIXTURE / "What Is Freedom For"
        reference_path = FIXTURE / TAKES
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
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
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
