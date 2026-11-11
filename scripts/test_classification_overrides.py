"""Synthetic regression checks for human quality overrides and downstream consumers."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from studio.tools.line import combined, mapping, quality, sources


ROOT = Path(__file__).resolve().parents[1]
SCORER = ROOT / ".agents/skills/studio/3-Post Production/4-Take Selection/scripts/score_takes.py"
spec = importlib.util.spec_from_file_location("classification_override_scoring", SCORER)
scoring = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scoring)
find = scoring.find


class ClassificationOverrideTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "A.txt").write_text("Amber birch. Cedar dogwood. Elm fir.", encoding="utf-8")

    def state(self, middle="Unrelated speech."):
        text = f"Amber birch. {middle} Elm fir."
        (self.root / "A__C01__lav.json").write_text(json.dumps({"words": [
            {"text": word, "start_ms": index * 100, "end_ms": (index + 1) * 100}
            for index, word in enumerate(text.split())]}), encoding="utf-8")
        state = mapping.new_state()
        state.update(script=sources.load_script(self.root, "A.txt"),
                     transcript=sources.load_transcript(self.root, "A__C01__lav.json"))
        state["groupings"] = {panel: mapping.initial_groupings(state[panel]) for panel in mapping.PANELS}
        state["links"] = [
            {"script": deepcopy(script), "transcript": deepcopy(transcript),
             "classification": quality.classify(mapping.range_text(state["script"], script),
                                                mapping.range_text(state["transcript"], transcript))}
            for script, transcript in zip(state["groupings"]["script"], state["groupings"]["transcript"])
        ]
        return state

    def document(self, state, schema):
        document = mapping._document(state, "A__Takes", 1)
        if schema != "line2":
            document = {"schema": mapping.SCHEMA, "schema_version": 3,
                        "mapping_name": "A__Takes", "version": 1,
                        "script": deepcopy(state["script"]),
                        "script_groupings": deepcopy(state["groupings"]["script"]),
                        "recordings": combined.pack(state)}
        if schema == "findtakes1":
            document.update(schema=find.SCHEMA, schema_version=1)
        return document

    @staticmethod
    def links(document):
        return document["links"] if document["schema_version"] == 2 else document["recordings"][0]["links"]

    def persist(self, document):
        path = self.root / "A__Takes.v1.json"
        path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        return path

    def test_override_round_trip_canonicalizes_stale_labels_for_every_schema(self):
        for schema in ("line2", "line3", "findtakes1"):
            for override in mapping.CLASSIFICATIONS:
                with self.subTest(schema=schema, override=override):
                    state = self.state()
                    document = self.document(state, schema)
                    link = self.links(document)[1]
                    link.update(classification="flub" if override != "flub" else "exact",
                                classification_override=override)
                    original = deepcopy(document)
                    path = self.persist(document)
                    saved_bytes = path.read_bytes()
                    loaded = mapping.read_mapping(self.root, path)
                    self.assertEqual(loaded["links"][1]["classification"], override)
                    self.assertEqual(loaded["links"][1]["classification_override"], override)
                    self.assertEqual(path.read_bytes(), saved_bytes)
                    self.assertEqual(document, original)
                    output = mapping.save_version(self.root, loaded, "A__Takes")
                    reopened = mapping.read_mapping(self.root, output)
                    self.assertEqual(reopened["links"], loaded["links"])
                    self.assertEqual(path.read_bytes(), saved_bytes)

    def test_validation_rejects_malformed_overrides_in_all_consumer_formats(self):
        for schema in ("line2", "line3", "findtakes1"):
            for value in (None, "", "good", "Exact", " partial ", 1, True, [], {}):
                with self.subTest(schema=schema, value=value):
                    document = self.document(self.state(), schema)
                    self.links(document)[1]["classification_override"] = value
                    path = self.persist(document)
                    original = path.read_bytes()
                    with self.assertRaises(ValueError):
                        mapping.read_mapping(self.root, path)
                    with self.assertRaises(ValueError):
                        scoring.load_mapping(self.root, path)
                    if schema != "findtakes1":
                        with self.assertRaises(ValueError):
                            mapping.validate_document(self.root, document)
                    self.assertEqual(path.read_bytes(), original)

    def test_validation_canonicalizes_override_without_mutating_caller(self):
        for schema in ("line2", "line3"):
            with self.subTest(schema=schema):
                document = self.document(self.state(), schema)
                self.links(document)[1].update(classification="flub", classification_override="partial")
                original = deepcopy(document)
                validated = mapping.validate_document(self.root, document)
                self.assertEqual(self.links(validated)[1]["classification"], "partial")
                self.assertEqual(document, original)

    def test_refresh_uses_override_and_leaves_automatic_links_automatic(self):
        state = self.state()
        state["links"][0]["classification"] = "flub"
        state["links"][1]["classification_override"] = "partial"
        original = deepcopy(state)
        self.assertEqual(mapping.refresh_quality(state), 2)
        self.assertEqual([link["classification"] for link in state["links"]], ["exact", "partial", "exact"])
        self.assertTrue(state["dirty"])
        for key in ("script", "transcript", "groupings", "discarded", "script_discarded", "selected"):
            self.assertEqual(state[key], original[key])
        state["dirty"] = False
        self.assertEqual(mapping.refresh_quality(state), 0)
        self.assertFalse(state["dirty"])

    def test_combined_adapters_preserve_overrides_at_recording_offsets(self):
        state = self.state()
        first = deepcopy(state["transcript"])
        second_path = self.root / "A__C02__lav.json"
        second_path.write_bytes((self.root / "A__C01__lav.json").read_bytes())
        second = sources.load_transcript(self.root, second_path)
        state["links"][1].update(classification="partial", classification_override="partial")
        document = self.document(state, "line3")
        document["recordings"].append(deepcopy(document["recordings"][0]))
        document["recordings"][1]["transcript"] = second
        document["recordings"][1]["links"][1].update(classification="flub", classification_override="flub")
        original = deepcopy(document)
        unpacked = combined.unpack(document)
        self.assertEqual(unpacked["links"][4]["transcript"]["word_start"],
                         len(first["words"]) + state["links"][1]["transcript"]["word_start"])
        self.assertEqual(combined.pack(unpacked), document["recordings"])
        self.assertEqual(document, original)
        loaded = mapping.read_mapping(self.root, self.persist(document))
        self.assertEqual([link.get("classification_override") for link in loaded["links"]],
                         [None, "partial", None, None, "flub", None])

    def test_find_takes_coverage_respects_overrides_without_rewriting_saved_bytes(self):
        for schema in ("line2", "line3", "findtakes1"):
            for middle, override, complete in (("Unrelated speech.", "partial", True),
                                                ("Cedar dogwood again.", "flub", False)):
                with self.subTest(schema=schema, override=override):
                    state = self.state(middle)
                    expected_automatic = "flub" if complete else "partial"
                    self.assertEqual(state["links"][1]["classification"], expected_automatic)
                    document = self.document(state, schema)
                    self.links(document)[1]["classification_override"] = override
                    path = self.persist(document)
                    original = path.read_bytes()
                    result = find.check_mapping(self.root, path, state["script"], [state["transcript"]])
                    self.assertEqual(result["coverage"]["complete"], complete)
                    self.assertEqual(result["coverage"]["covered_words"], 6 if complete else 4)
                    self.assertEqual([line["line"] for line in result["blocking_lines"]], [] if complete else [2])
                    if not complete:
                        self.assertEqual(result["blocking_lines"][0]["reason"], "flub_only")
                    self.assertEqual(path.read_bytes(), original)

    def test_take_selection_uses_canonical_override_for_labels_and_continuity(self):
        for schema in ("line2", "line3", "findtakes1"):
            for automatic, override, continuity, scores in (
                ("flub", "partial", [3, 3, 3], [13, 14, 15]),
                ("partial", "flub", [1, 0, 1], [5, 2, 7]),
            ):
                with self.subTest(schema=schema, override=override):
                    state = self.state("Unrelated speech." if automatic == "flub" else "Cedar dogwood again.")
                    self.assertEqual(state["links"][1]["classification"], automatic)
                    document = self.document(state, schema)
                    self.links(document)[1]["classification_override"] = override
                    path = self.persist(document)
                    original = path.read_bytes()
                    loaded = scoring.load_mapping(self.root, path)
                    self.assertEqual(loaded["recordings"][0]["links"][1]["classification"], override)
                    untouched = deepcopy(loaded)
                    result = scoring.score_document(loaded, {"path": path.name, "sha256": find.sha256(path)})
                    self.assertEqual([take["classification"] for take in result["takes"]], ["exact", override, "exact"])
                    self.assertEqual([take["continuity_sentences"] for take in result["takes"]], continuity)
                    self.assertEqual([take["score"] for take in result["takes"]], scores)
                    self.assertEqual(loaded, untouched)
                    self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
