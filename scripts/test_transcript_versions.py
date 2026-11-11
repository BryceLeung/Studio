"""Synthetic committed-repair discovery and normal stage evidence contracts."""
import importlib.util
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest

from scripts import stage_completion as completion
from scripts.validation import check_marker
from studio.fingerprints import raw_sha256
from studio.tools.line import sources
from studio.transcript_versions import COMMIT, TRANSCRIBED, resolve_transcripts, transcript_selection

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".agents/skills/studio/3-Post Production"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = load("versions_check", SKILLS / "2-Transcribe/scripts/check_transcripts.py")
find = load("versions_find", SKILLS / "3-Find Takes/scripts/find_takes.py")
DEFINITION_33 = completion.load_definition(SKILLS / "3-Find Takes/dependencies.json")


class TranscriptVersionsTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name).resolve()
        self.original = self.project / TRANSCRIBED / "A__C01__lav.json"
        self.original.parent.mkdir(parents=True)
        self.original.write_text(json.dumps({"words": [
            {"text": "Hello.", "start_ms": 0, "end_ms": 1000}]}))
        self.script = self.project / find.APPROVED / "A.txt"
        self.script.parent.mkdir(parents=True)
        self.script.write_text("Hello.")
        self.audio = self.project / check.INTAKE / "A__C01__lav.wav"
        self.audio.parent.mkdir(parents=True)
        self.audio.write_bytes(b"synthetic audio")
        self.original_bytes = self.original.read_bytes()

    def repair(self, number=1, source=None, *, committed=True):
        candidate = self.original.parent / "REPAIRS" / self.original.stem / f"v{number}" / self.original.name
        candidate.parent.mkdir(parents=True)
        candidate.write_bytes(self.original_bytes + b"\n" * number)
        if committed:
            source = source or self.original
            (candidate.parent / COMMIT).write_text(json.dumps({
                "status": "success", "transcript": candidate.name, "sha256": raw_sha256(candidate),
                "source": {"path": source.relative_to(self.project).as_posix(), "sha256": raw_sha256(source)}}))
        return candidate

    def manifest32(self):
        return check.manifest(self.project, *check.inventory(self.project))

    def status32(self, manifest):
        return check_marker(self.project, ROOT, check.DEFINITION, manifest)[0]

    def test_orphans_reports_and_unrelated_nested_json_are_not_sources(self):
        self.repair(20, committed=False)
        (self.original.parent / "Stale Transcripts.json").write_text("{}")
        orphan = self.original.parent / "reports" / "A__C02__lav.json"
        orphan.parent.mkdir()
        orphan.write_text("{}")
        self.assertEqual(transcript_selection(self.project), [self.original.relative_to(self.project).as_posix()])
        self.assertEqual(find.discover(self.project), [(self.script, [self.original])])
        self.assertEqual(sources.discover_sources(self.project)["transcripts"], transcript_selection(self.project))

    def test_numeric_version_chain_and_exact_historical_load(self):
        first = self.repair(2)
        latest = self.repair(10, first)
        self.repair(99, committed=False)
        records = resolve_transcripts(self.project)
        self.assertEqual(records[0]["effective"], latest)
        self.assertEqual(find.discover(self.project), [(self.script, [latest])])
        self.assertEqual(sources.load_transcript(self.project, self.original)["source"]["path"],
                         self.original.relative_to(self.project).as_posix())
        self.assertEqual(self.original.read_bytes(), self.original_bytes)

    def test_malformed_highest_commit_never_falls_back(self):
        self.repair(1)
        highest = self.repair(2)
        (highest.parent / COMMIT).write_text("{}")
        with self.assertRaisesRegex(ValueError, "Invalid committed"):
            resolve_transcripts(self.project)

    def test_changed_candidate_or_original_provenance_fails(self):
        candidate = self.repair()
        candidate.write_bytes(candidate.read_bytes() + b" ")
        with self.assertRaises(ValueError):
            resolve_transcripts(self.project)
        candidate.write_bytes(self.original_bytes + b"\n")
        self.original.write_bytes(self.original_bytes + b" ")
        with self.assertRaises(ValueError):
            resolve_transcripts(self.project)

    def test_publication_uses_raw_bytes_even_with_crlf(self):
        self.original.write_bytes(self.original_bytes + b"\r\n")
        self.repair()
        self.assertEqual(len(resolve_transcripts(self.project)), 1)

    def test_self_referential_provenance_and_symlink_are_rejected(self):
        candidate = self.repair()
        marker = candidate.parent / COMMIT
        document = json.loads(marker.read_text())
        document["source"]["path"] = candidate.relative_to(self.project).as_posix()
        document["source"]["sha256"] = raw_sha256(candidate)
        marker.write_text(json.dumps(document))
        with self.assertRaises(ValueError):
            resolve_transcripts(self.project)
        marker.unlink()
        marker.symlink_to(self.original)
        with self.assertRaises(ValueError):
            resolve_transcripts(self.project)

    def test_project_relocation_preserves_publication_identity(self):
        first = self.repair()
        last = self.repair(2, first)
        with TemporaryDirectory() as relocated:
            copied = Path(relocated).resolve() / "project"
            shutil.copytree(self.project, copied)
            self.assertEqual(transcript_selection(copied), [last.relative_to(self.project).as_posix()])

    def test_absolute_publication_source_is_rejected(self):
        candidate = self.repair()
        marker = candidate.parent / COMMIT
        document = json.loads(marker.read_text())
        document["source"]["path"] = str(self.original)
        marker.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, "project-relative POSIX"):
            resolve_transcripts(self.project)

    def test_normal_32_adoption_stale_selection_and_historical_pairing(self):
        before = self.manifest32()
        self.assertEqual(self.status32(before), "current")
        candidate = self.repair()
        self.assertEqual(self.status32(before), "stale")
        current = self.manifest32()
        self.assertEqual(self.status32(current), "current")
        recorded = {entry["path"] for entry in current["outputs"]}
        self.assertIn(self.original.relative_to(self.project).as_posix(), recorded)
        self.assertIn(candidate.relative_to(self.project).as_posix(), recorded)
        (self.original.parent / "DONE").write_text(json.dumps(current))
        self.assertEqual(self.status32(current), "current")
        self.repair(2, candidate)
        self.assertEqual(self.status32(current), "stale")
        self.assertEqual(self.status32(self.manifest32()), "current")

    def test_repair_does_not_bypass_audio_stale_guard(self):
        baseline = self.manifest32()
        (self.original.parent / "DONE").write_text(json.dumps(baseline))
        self.repair()
        self.audio.write_bytes(b"different audio with a changed byte size")
        self.assertEqual(self.status32(self.manifest32()), "stale")

    def test_33_accepts_exact_repaired_source_and_detects_new_selection(self):
        first = self.repair()
        result = find.generate(self.project, self.project / find.OUTPUT)
        self.assertTrue(result["complete"], result)
        manifest = find.completion_manifest(self.project, result)
        self.assertEqual(check_marker(self.project, ROOT, DEFINITION_33, manifest)[0], "current")
        document = json.loads(Path(result["outputs"][0]["path"]).read_text())
        self.assertEqual(document["recordings"][0]["transcript"]["source"]["path"],
                         first.relative_to(self.project).as_posix())
        self.repair(2, first)
        self.assertEqual(check_marker(self.project, ROOT, DEFINITION_33, manifest)[0], "stale")
        # The old exact source is still usable, even though it is no longer current.
        self.assertEqual(sources.load_transcript(self.project, first)["words"][0]["text"], "Hello.")
        old_state = find.mapping.read_mapping(self.project, result["outputs"][0]["path"])
        self.assertIsInstance(old_state, dict)


if __name__ == "__main__":
    unittest.main()
