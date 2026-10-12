"""Check that the flat benchmark cases remain runnable and honestly scored."""
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

import benchmark


FIXTURE = Path(__file__).resolve().parents[1] / "benchmarks"


class BenchmarkTests(unittest.TestCase):
    def test_human_case_preserves_baseline_and_reference(self):
        reference = FIXTURE / "What Is Freedom For/What Is Freedom For__Takes.v001.json"
        original = reference.read_bytes()
        with TemporaryDirectory() as directory:
            output = Path(directory)
            report = benchmark.run(FIXTURE, output)
            self.assertEqual((report["generation"]["scripts"], report["generation"]["transcripts"]), (1, 1))
            cases = {case["case"]: case for case in report["cases"]}
            self.assertEqual(set(cases), {"What Is Freedom For"})
            expected = json.loads((FIXTURE.parent / "reports/initial/What Is Freedom For.json").read_text())
            actual = cases["What Is Freedom For"]
            for key in ("exact_take_ranges", "word_assignments", "discarded_words",
                        "classification_agreement_on_exact_ranges", "missing_or_different", "extra_or_different"):
                self.assertEqual(actual[key], expected[key], key)
            for name in cases:
                path = output / name / f"{name}__Takes.v001.json"
                self.assertTrue(path.is_file())
            prediction = json.loads((output / "What Is Freedom For/What Is Freedom For__Takes.v001.json").read_text())
            self.assertEqual(len(prediction["recordings"]), 1)
        self.assertEqual(reference.read_bytes(), original)
        # Rebased references resolve directly from the flat case directory in Line.
        state = benchmark.find_takes.mapping.read_mapping(reference.parent, reference.name)
        self.assertEqual(len(state["links"]), 28)

    def test_single_case_run_and_frozen_directory_protection(self):
        with TemporaryDirectory() as directory:
            report = benchmark.run(FIXTURE, Path(directory), "What Is Freedom For")
            self.assertEqual(len(report["cases"]), 1)
            self.assertEqual(report["generation"]["transcripts"], 1)
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
                    case["transcripts"].append("What Is Freedom For__C02__lav.json")
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "human-reviewed"):
                    benchmark.run(fixture, root / "predictions")
                self.assertFalse((root / "predictions").exists())


if __name__ == "__main__":
    unittest.main()
