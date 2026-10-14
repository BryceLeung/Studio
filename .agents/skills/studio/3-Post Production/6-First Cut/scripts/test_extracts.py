"""Check extract source matching, omissions, and independent timeline camera behavior."""
from pathlib import Path
from tempfile import TemporaryDirectory
from fractions import Fraction
import unittest

import extracts
import first_cut as cut
from test_first_cut import fixture


class ExtractTests(unittest.TestCase):
    def test_parser_preserves_six_separate_headers_and_jump_directives(self):
        text = "\n".join(f"## {i}. Title {i}\n\n- **Review status:** Approved\n- **Source:** A.docx\n\n"
                         "### Provisional spoken script\n\nFirst.\n\n> [PROPOSED JUMP]\n\nLast.\n"
                         for i in range(1, 7))
        result = extracts.parse_shortlist(text)
        self.assertEqual(len(result), 6)
        self.assertEqual([item["title"] for item in result], [f"Title {i}" for i in range(1, 7)])
        self.assertTrue(all(item["passages"][1]["jump_before"] for item in result))

    def test_matching_is_verbatim_and_resolves_repeats_by_order(self):
        script = {"words": [{"text": t} for t in "A. Then another. Then another. End.".split()]}
        extract = {"number": 1, "passages": [{"text": "A. Then another. Then another.", "jump_before": False},
                                             {"text": "End.", "jump_before": False}]}
        self.assertEqual(extracts.match_passages(extract, script), [(0, 4), (5, 5)])
        extract["passages"] = [{"text": "Then another.", "jump_before": False}]
        with self.assertRaisesRegex(ValueError, "unique ordered"):
            extracts.match_passages(extract, script)
        extract["passages"] = [{"text": "Something else.", "jump_before": False}]
        with self.assertRaisesRegex(ValueError, "approved wording"):
            extracts.match_passages(extract, script)

    def test_mapping_lists_all_takes_and_explicit_discard_without_silent_removal(self):
        with TemporaryDirectory() as directory:
            doc, scores, _, _ = fixture(Path(directory))
            scores["source_mapping"] = {"path": "A__Takes.v2.json"}
            doc["script_discarded"] = [{"word_start": 1, "word_end": 1}]
            item = {"number": 1, "title": "Example", "source": "A.docx",
                    "passages": [{"text": "0 1 2", "jump_before": False}]}
            result = extracts.map_extract(item, doc, scores)
            self.assertEqual([len(g["takes"]) for g in result["groups"]], [2, 1, 1])
            self.assertIsNone(result["groups"][1]["selected_take_key"])
            self.assertEqual(result["issues"][0]["reason"], "script_explicitly_discarded")

    def test_subset_restarts_on_a_and_recomputes_jump_camera_and_edge_padding(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            doc, scores, syncs, probes = fixture(root)
            included = {(0, 0), (2, 2)}
            takes, auditions = cut.plan_takes(doc, scores, syncs, probes, Fraction(1, 30), 50, included)
            self.assertEqual(len(takes), 3)
            self.assertEqual([a["sentence"] for a in auditions], [1, 3])
            self.assertEqual([a["candidates"][0]["angle"] for a in auditions], ["camA", "camB"])
            self.assertFalse(takes[0]["shared_out"])
            self.assertLess(takes[0]["cut_out"], Fraction(8, 5))
            output = root / cut.STAGE / "Extracts/Example.FCPXML"
            _, report = cut.build_xml(root, output, "Example", doc, scores, syncs, probes, 50, included)
            self.assertEqual(report["auditions"], 2)
            self.assertEqual(report["camera_flips"], 1)


if __name__ == "__main__":
    unittest.main()
