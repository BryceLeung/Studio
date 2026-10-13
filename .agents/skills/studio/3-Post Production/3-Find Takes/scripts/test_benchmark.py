"""Check that benchmark cases remain runnable and honestly scored."""
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import benchmark


FIXTURE = Path(__file__).resolve().parents[1] / "benchmarks"
TAKES = Path("What Is Freedom For/What Is Freedom For__Takes.json")
REFERENCE = FIXTURE / "What Is Freedom For" / TAKES


class BenchmarkTests(unittest.TestCase):
    def test_all_versions_matches_approved_reference_and_preserves_c02_rewind(self):
        frozen = {path: path.read_bytes() for path in FIXTURE.rglob("*") if path.is_file()}
        initial = FIXTURE.parent / "reports/initial/What Is Freedom For.json"
        initial_bytes = initial.read_bytes()
        real_references = benchmark.reference_documents
        generated_before_references = []
        with TemporaryDirectory() as directory:
            output = Path(directory)

            def references(project, path):
                self.assertTrue((output / TAKES).is_file())
                generated_before_references.append(path.name)
                return real_references(project, path)

            with patch.object(benchmark, "reference_documents", side_effect=references):
                report = benchmark.run(FIXTURE, output, "What Is Freedom For", all_versions=True)
            self.assertEqual(report["generation"]["reference_versions"], 1)
            self.assertEqual(report["generation"]["transcripts"], 2)
            self.assertEqual(generated_before_references, [TAKES.name])
            self.assertEqual(report["reference_conflicts"], [])
            first, second = report["cases"]
            self.assertEqual(first["exact_take_ranges"], benchmark.metrics(28, 28, 28))
            self.assertEqual(first["word_assignments"]["correct"], 426)
            self.assertEqual(second["exact_take_ranges"], benchmark.metrics(27, 27, 27))
            self.assertEqual(second["word_assignments"]["correct"], 412)
            self.assertEqual(second["word_errors"], [])
            prediction = json.loads((output / TAKES).read_text(encoding="utf-8"))
            recording = prediction["recordings"][1]
            rewind = [link for link in recording["links"]
                      if link["transcript"]["word_start"] in (145, 159)]
            self.assertEqual([link["transcript"] for link in rewind], [
                {"word_start": 145, "word_end": 158}, {"word_start": 159, "word_end": 171}])
            self.assertEqual([link["script"] for link in rewind], [
                {"word_start": 143, "word_end": 158}, {"word_start": 130, "word_end": 142}])
            self.assertTrue(benchmark.find_takes.mapping.range_text(recording["transcript"], rewind[0]["transcript"]).endswith("having..."))
            self.assertTrue(benchmark.find_takes.mapping.range_text(recording["transcript"], rewind[1]["transcript"]).startswith("He wanted"))
        self.assertTrue(all(path.read_bytes() == content for path, content in frozen.items()))
        self.assertEqual(initial.read_bytes(), initial_bytes)
        self.assertEqual(json.loads(initial_bytes)["exact_take_ranges"]["correct"], 23)

    def test_multirecording_review_preserves_reference_and_scores_each_recording(self):
        reference = REFERENCE
        original = reference.read_bytes()
        with TemporaryDirectory() as directory:
            output = Path(directory)
            report = benchmark.run(FIXTURE, output, "What Is Freedom For")
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
        project = REFERENCE.parent
        reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
        expected = benchmark.reference_documents(project, REFERENCE)
        multi = {**reference, "schema": benchmark.find_takes.mapping.SCHEMA,
                 "schema_version": 3, "mapping_name": TAKES.stem, "version": 1}
        with TemporaryDirectory() as directory:
            path = Path(directory) / TAKES.name
            for document, documents in ((expected[0], expected[:1]), (multi, expected)):
                with self.subTest(version=document["schema_version"]):
                    path.write_text(json.dumps(document), encoding="utf-8")
                    self.assertEqual(benchmark.reference_documents(project, path), documents)

    def test_unsupported_reference_formats_are_rejected(self):
        reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
        with TemporaryDirectory() as directory:
            path = Path(directory) / TAKES.name
            for changes in ({"schema": "unknown"}, {"schema_version": 2}, {"schema_version": True}):
                with self.subTest(changes=changes):
                    path.write_text(json.dumps({**reference, **changes}), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        benchmark.reference_documents(REFERENCE.parent, path)

    def test_rebasing_still_checks_exact_source_bytes_and_word_snapshots(self):
        project = REFERENCE.parent
        reference_path = REFERENCE
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

    def test_nested_cases_keep_the_reviewed_imperfect_partial_match_visible(self):
        frozen = {path: path.read_bytes() for path in FIXTURE.rglob("*") if path.is_file()}
        real_references = benchmark.reference_documents
        with TemporaryDirectory() as directory:
            output = Path(directory)

            def references(project, path):
                # Both cases must be generated before either expected mapping is read.
                for name in ("What Is Freedom For", "Love Compounds Too"):
                    self.assertTrue((output / name / f"{name}__Takes.json").is_file())
                return real_references(project, path)

            with patch.object(benchmark, "reference_documents", side_effect=references):
                report = benchmark.run(FIXTURE, output)
            self.assertEqual((report["generation"]["scripts"], report["generation"]["transcripts"]), (2, 3))
            self.assertEqual(len(report["cases"]), 3)
            love = next(case for case in report["cases"] if case["case"] == "Love Compounds Too")
            self.assertEqual(love["reference"], "What Is Freedom For/Love Compounds Too/Love Compounds Too__Takes.json")
            self.assertEqual(love["exact_take_ranges"], benchmark.metrics(136, 137, 138))
            self.assertEqual(love["word_assignments"]["correct"], 1110)
            self.assertEqual(love["word_assignments"]["total"], 1113)
            self.assertEqual([link["transcript"] for link in love["missing_or_different"]], [
                {"word_start": 491, "word_end": 492}, {"word_start": 493, "word_end": 494}])
            self.assertEqual([link["script"] for link in love["missing_or_different"]], [
                {"word_start": 428, "word_end": 430}, {"word_start": 431, "word_end": 433}])
            self.assertTrue(all(link["classification"] == "partial" for link in love["missing_or_different"]))
            self.assertEqual([error["index"] for error in love["word_errors"]], [491, 492, 494])
            self.assertEqual(love["classification_changes"], [])
        self.assertTrue(all(path.read_bytes() == content for path, content in frozen.items()))

    def test_case_directory_cannot_escape_the_fixture(self):
        for directory in ("../What Is Freedom For", str(FIXTURE.resolve()), "What Is Freedom For/../What Is Freedom For"):
            with self.subTest(directory=directory), TemporaryDirectory() as temporary:
                root = Path(temporary)
                fixture = root / "fixtures"
                fixture.mkdir()
                manifest = json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))
                manifest["cases"][0]["directory"] = directory
                (fixture / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "case directories"):
                    benchmark.run(fixture, root / "predictions")
                self.assertFalse((root / "predictions").exists())

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
