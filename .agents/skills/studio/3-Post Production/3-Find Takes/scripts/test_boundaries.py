"""Boundary search keeps script groups fixed and preserves the word stream."""
from copy import deepcopy
import unittest

from studio.tools.line import boundaries, mapping, quality
from test_find_takes import script, transcript


def recording(expected, spoken, spans):
    groups = mapping.initial_groupings(expected)
    links = [{"script": dict(groups[index]), "transcript": {"word_start": start, "word_end": end},
              "classification": quality.classify(mapping.range_text(expected, groups[index]),
                                                  mapping.range_text(spoken, {"word_start": start, "word_end": end}))}
             for index, start, end in spans]
    groupings, discarded = boundaries.regroup(spoken, links)
    return {"transcript": spoken, "groupings": groupings, "links": links, "discarded": discarded}


class BoundaryTests(unittest.TestCase):
    def check_preview(self, expected, original, analysis, preview):
        self.assertEqual(preview["transcript"], original["transcript"])
        groups = mapping.initial_groupings(expected)
        self.assertTrue(all(link["script"] in groups for link in preview["links"]))
        covered = [word for group in preview["groupings"]
                   for word in range(group["word_start"], group["word_end"] + 1)]
        self.assertEqual(covered, list(range(len(original["transcript"]["words"]))))
        selected = [item for item in analysis["candidates"] if item["selected"]]
        self.assertTrue(all(item["text_gain"] >= 5 and item["gain"] >= 5 for item in selected))
        self.assertTrue(all(a["window"]["word_end"] < b["window"]["word_start"]
                            for a, b in zip(selected, selected[1:])))

    def test_merge_two_fragments_into_one_reading(self):
        expected = script(["Alpha beta gamma delta epsilon zeta."])
        spoken = transcript(["Alpha beta gamma.", "Delta epsilon zeta."])
        original = recording(expected, spoken, [(0, 0, 2), (0, 3, 5)])
        untouched = deepcopy(original)
        analysis, preview = boundaries.propose(expected, original)
        self.assertEqual([link["transcript"] for link in preview["links"]], [{"word_start": 0, "word_end": 5}])
        self.assertEqual(preview["links"][0]["classification"], "exact")
        self.assertEqual(original, untouched)
        self.check_preview(expected, original, analysis, preview)

    def test_split_preserves_abandoned_prefix_and_complete_restart(self):
        line = "During his final month of life I was nine months pregnant."
        expected = script([line])
        spoken = transcript(["During his final...", line])
        original = recording(expected, spoken, [(0, 0, 13)])
        analysis, preview = boundaries.propose(expected, original)
        self.assertEqual([link["transcript"] for link in preview["links"]], [
            {"word_start": 0, "word_end": 2}, {"word_start": 3, "word_end": 13}])
        self.assertEqual(preview["discarded"], [])
        self.assertEqual(preview["links"][1]["classification"], "exact")
        self.check_preview(expected, original, analysis, preview)

    def test_move_cut_between_two_script_readings(self):
        expected = script(["Alpha beta gamma delta.", "Echo foxtrot golf hotel."])
        spoken = transcript(["Alpha beta gamma.", "Delta echo foxtrot golf hotel."])
        original = recording(expected, spoken, [(0, 0, 2), (1, 3, 7)])
        analysis, preview = boundaries.propose(expected, original)
        self.assertEqual([link["transcript"] for link in preview["links"]], [
            {"word_start": 0, "word_end": 3}, {"word_start": 4, "word_end": 7}])
        self.check_preview(expected, original, analysis, preview)

    def test_chatter_cannot_earn_more_credit_by_being_absorbed(self):
        line = "Alpha beta gamma delta epsilon zeta eta theta."
        expected = script([line])
        spoken = transcript(["Please check the camera.", line])
        original = recording(expected, spoken, [(0, 0, 11)])
        analysis, preview = boundaries.propose(expected, original)
        self.assertEqual(preview["links"][0]["transcript"], {"word_start": 4, "word_end": 11})
        self.assertEqual(preview["discarded"], [{"word_start": 0, "word_end": 3}])
        self.check_preview(expected, original, analysis, preview)

    def test_adjacent_repair_does_not_crop_a_weak_extra_word(self):
        expected = script(["Years later he was diagnosed with brain cancer.",
                           "During his final month of life I was nine months pregnant."])
        spoken = transcript(["But years later he was diagnosed with brain cancer.",
                             "During his final...", "During his final month of life I was nine months pregnant."])
        original = recording(expected, spoken, [(0, 0, 8), (1, 9, 22)])
        analysis, preview = boundaries.propose(expected, original)
        self.assertEqual(preview["links"][0]["transcript"], {"word_start": 0, "word_end": 8})
        self.assertEqual(len(preview["links"]), 3)
        self.check_preview(expected, original, analysis, preview)

    def test_exact_equivalent_forms_are_anchors_and_high_gain_threshold_keeps_baseline(self):
        expected = script(["I have thirty-one father-in-law stories."])
        spoken = transcript(["I have 31 father in law stories!"])
        original = recording(expected, spoken, [(0, 0, 6)])
        analysis, preview = boundaries.propose(expected, original)
        self.assertEqual(analysis["selected_count"], 0)
        self.assertEqual(preview["links"], original["links"])
        expected = script(["Alpha beta gamma delta.", "Echo foxtrot golf hotel."])
        spoken = transcript(["Alpha beta gamma delta echo foxtrot golf hotel."])
        original = recording(expected, spoken, [(0, 0, 2), (1, 3, 7)])
        analysis, preview = boundaries.propose(expected, original, {"min_gain": 1000})
        self.assertEqual(analysis["selected_count"], 0)
        self.assertEqual(preview["links"], original["links"])

    def test_unrelated_words_remain_discarded(self):
        expected = script(["Alpha beta gamma delta."])
        spoken = transcript(["Camera setup lighting microphone testing please."])
        original = recording(expected, spoken, [])
        analysis, preview = boundaries.propose(expected, original)
        self.assertEqual(analysis["selected_count"], 0)
        self.assertEqual(preview["links"], [])
        self.assertEqual(preview["discarded"], original["discarded"])


if __name__ == "__main__":
    unittest.main()
