"""Check extract range selection, sync conversion, face continuity, and no-op completion."""
from io import BytesIO
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import face_tracking as track


class FaceTrackingTests(unittest.TestCase):
    def test_media_writer_and_legacy_done_use_whole_seconds(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            media = project / "camera.mov"
            media.write_bytes(b"camera")
            track.os.utime(media, ns=(1_700_000_000_100_000_000,) * 2)
            current = track.media_fingerprint(media, project)
            self.assertEqual(current["mtime_s"], 1_700_000_000)
            self.assertNotIn("mtime_ns", current)
            folder = project / "legacy"
            folder.mkdir()
            for key, timestamp in (("mtime_s", 1_700_000_000), ("mtime_ns", 1_700_000_000_900_000_000)):
                entry = {"path": "camera.mov", "bytes": media.stat().st_size, key: timestamp}
                (folder / "DONE").write_text(json.dumps({"outputs": [entry]}))
                track.validate_done(project, Path("legacy"))
                entry[key] += 1 if key == "mtime_s" else 1_000_000_000
                (folder / "DONE").write_text(json.dumps({"outputs": [entry]}))
                with self.assertRaisesRegex(ValueError, "Changed upstream input"):
                    track.validate_done(project, Path("legacy"))

    def test_saved_media_header_normalizes_without_relaxing_dependencies(self):
        media = {"scope": "project", "path": "camera.mov", "bytes": 6, "mtime_s": 1_700_000_000}
        header = {"ranges_ms": [[1000, 2000]], "media": media, "inputs": [media],
                  "dependencies": [{"path": "generator.py", "sha256": "a" * 64}]}
        saved = deepcopy(header)
        for entry in (saved["media"], *saved["inputs"]):
            if "mtime_s" in entry:
                entry["mtime_ns"] = entry.pop("mtime_s") * 1_000_000_000 + 900_000_000
                entry["size_bytes"] = entry.pop("bytes")
        saved.update(width=640, height=360, warnings=[], frames=[
            {"time_ms": timestamp, "status": "centered", "face": None} for timestamp in (1000, 1900)])
        track.validate_path(saved, header)
        saved["dependencies"][0]["sha256"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "inputs, source, or algorithm changed"):
            track.validate_path(saved, header)

    def test_runtime_uses_only_stage_environment(self):
        self.assertEqual(track.ENVIRONMENT, Path(track.__file__).resolve().parents[1] / ".venv-tools")
        with patch.object(track.sys, "prefix", str(track.ENVIRONMENT)), \
                patch.object(track.subprocess, "run") as launch:
            track.use_stage_environment()
            launch.assert_not_called()
        with TemporaryDirectory() as directory:
            environment = Path(directory) / ".venv-tools"
            python = environment / ("Scripts/python.exe" if track.os.name == "nt" else "bin/python")
            with patch.object(track, "ENVIRONMENT", environment), \
                    patch.object(track.subprocess, "run") as launch:
                with self.assertRaisesRegex(SystemExit, "environment missing"):
                    track.use_stage_environment()
                launch.assert_not_called()
                python.parent.mkdir(parents=True)
                python.touch()
                launch.return_value.returncode = 7
                with patch.object(track.sys, "argv", ["face_tracking.py", "--project-root", "project"]):
                    with self.assertRaises(SystemExit) as result:
                        track.use_stage_environment()
                self.assertEqual(result.exception.code, 7)
                launch.assert_called_once_with([str(python), str(Path(track.__file__).resolve()),
                                               "--project-root", "project"])

    def test_model_download_and_reuse_ignore_external_cache_settings(self):
        with TemporaryDirectory() as directory:
            environment = Path(directory) / ".venv-tools"
            content = b"verified model"
            target = environment / "model-cache/face_detection_yunet_2023mar.onnx"
            with patch.object(track, "ENVIRONMENT", environment), \
                    patch.object(track, "MODEL_SHA256", track.find.fingerprints.digest_bytes(content)), \
                    patch.dict(track.os.environ, {key: str(Path(directory) / "external") for key in
                               ("LOCALAPPDATA", "XDG_CACHE_HOME", "HF_HOME", "TMPDIR")}), \
                    patch.object(track, "urlopen", return_value=BytesIO(content)) as download:
                self.assertEqual(track.model_path(), target)
                self.assertEqual(target.read_bytes(), content)
                self.assertEqual(track.model_path(), target)
                download.assert_called_once_with(track.MODEL_URL, timeout=60)
                self.assertEqual(list(target.parent.iterdir()), [target])
                self.assertFalse((Path(directory) / "external").exists())

    def test_invalid_cached_model_is_preserved_without_download(self):
        with TemporaryDirectory() as directory:
            environment = Path(directory) / ".venv-tools"
            target = environment / "model-cache/face_detection_yunet_2023mar.onnx"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"invalid model")
            with patch.object(track, "ENVIRONMENT", environment), patch.object(track, "urlopen") as download:
                with self.assertRaisesRegex(ValueError, "wrong SHA-256"):
                    track.model_path()
                download.assert_not_called()
                self.assertEqual(target.read_bytes(), b"invalid model")

    def test_invalid_download_is_removed_from_local_cache(self):
        with TemporaryDirectory() as directory:
            environment = Path(directory) / ".venv-tools"
            with patch.object(track, "ENVIRONMENT", environment), \
                    patch.object(track, "urlopen", return_value=BytesIO(b"invalid model")):
                with self.assertRaisesRegex(ValueError, "failed SHA-256"):
                    track.model_path()
            self.assertEqual(list((environment / "model-cache").iterdir()), [])

    def test_legacy_windows_manifest_paths_are_verified_locally(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            stage = project / "1-Pre Production/1-Script Review"
            stage.mkdir(parents=True)
            approved = stage / "Approved" / "A.docx"
            approved.parent.mkdir()
            approved.write_bytes(b"approved")
            skill = track.ROOT / ".agents/skills/studio/1-Pre Production/1-Script Review/SKILL.md"
            manifest = {"dependencies": [{"path":
                        r"C:\Users\bryce\VSCode\Studio\.agents\skills\studio\1-Pre Production\1-Script Review\SKILL.md",
                        "sha256": track.find.sha256(skill)}],
                        "outputs": [{"path":
                        rf"C:\Users\bryce\OneDrive\Studio\{project.name}\1-Pre Production\1-Script Review\Approved\A.docx",
                        "sha256": track.find.sha256(approved)}]}
            (stage / "DONE").write_text(json.dumps(manifest))
            track.validate_done(project, Path("1-Pre Production/1-Script Review"))
            manifest["dependencies"][0]["sha256"] = "0" * 64
            (stage / "DONE").write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "Stale upstream fingerprint"):
                track.validate_done(project, Path("1-Pre Production/1-Script Review"))

    def test_approved_extract_passages_match_only_whole_groups(self):
        text = ("## 1. Included\n\n- **Review status:** Approved\n- **Source:** A.docx\n\n"
                "### Provisional spoken script\n\nHello world.\n\n> [PROPOSED JUMP]\n\nGoodbye.\n"
                "## 2. Rejected\n\n- **Review status:** Rejected\n- **Source:** A.docx\n\n"
                "### Provisional spoken script\n\nOther.\n")
        self.assertEqual(track.approved_passages(text), [(1, "A.docx", ["Hello world.", "Goodbye."])])
        script = {"words": [{"text": word} for word in "Hello world. Other. Goodbye.".split()]}
        groups = [{"word_start": 0, "word_end": 1}, {"word_start": 2, "word_end": 2},
                  {"word_start": 3, "word_end": 3}]
        self.assertEqual(track.passage_groups(script, groups, ["Hello world.", "Goodbye."], 1),
                         [groups[0], groups[2]])
        with self.assertRaisesRegex(ValueError, "crops a reviewed script group"):
            track.passage_groups(script, groups, ["Hello", "Goodbye."], 1)

    def test_all_matching_takes_cover_both_cameras_in_source_time(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            intake = project / track.INTAKE
            intake.mkdir(parents=True)
            files = {}
            for role in ("camA", "camB"):
                file = intake / f"A__C01__{role}.mov"
                file.write_bytes(b"camera")
                stat = file.stat()
                files[role] = {"path": file.relative_to(project).as_posix(),
                               "size_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                               "offset_ms": -100 if role == "camB" else 0}
            lav = intake / "A__C01__lav.wav"
            lav.write_bytes(b"audio")
            files["lav"] = {"path": lav.relative_to(project).as_posix(), "offset_ms": 200}
            sync = project / track.SYNC / "A__C01.json"
            sync.parent.mkdir(parents=True)
            sync.write_text(json.dumps({"reference_role": "camA",
                                        "offset_convention": "positive_means_file_starts_later_than_camA",
                                        "clips": files}))
            mapping = project / track.find.OUTPUT / "A__Takes.v1.json"
            mapping.parent.mkdir(parents=True)
            mapping.write_text("{}")
            (mapping.parent / "DONE").write_text(json.dumps({"conditions": {
                "latest_versions": [{"path": mapping.relative_to(project).as_posix()}]}}))
            script = project / "A.docx"
            script.write_bytes(b"script")
            transcript = project / "A__C01__lav.json"
            transcript.write_text("{}")
            group = {"word_start": 0, "word_end": 0}
            doc = {"script": {"words": [{"text": "Hello."}]}, "script_groupings": [group],
                   "recordings": [{"transcript": {"audio": lav.relative_to(project).as_posix(),
                                                  "source": {"path": transcript.relative_to(project).as_posix()},
                                                  "words": [{"start_ms": 1000, "end_ms": 1500},
                                                            {"start_ms": 2000, "end_ms": 2500}]},
                                   "links": [{"script": group, "transcript": {"word_start": i, "word_end": i}}
                                             for i in range(2)]}]}
            with patch.object(track.find.mapping, "takes_versions", return_value=[(1, mapping)]), \
                    patch.object(track, "load_mapping", return_value=(doc, [script, transcript])):
                ranges, inputs = track.required_ranges(project, [(1, "A.docx", ["Hello."])])
            self.assertEqual(len(inputs), 6)
            a = ranges[(intake / "A__C01__camA.mov", "camA")]
            b = ranges[(intake / "A__C01__camB.mov", "camB")]
            self.assertEqual([[float(x), float(y)] for x, y in a], [[1.15, 1.75], [2.15, 2.75]])
            self.assertEqual([[float(x), float(y)] for x, y in b], [[1.25, 1.85], [2.25, 2.85]])
            for timestamp_key in ("mtime_ns", "mtime_s"):
                clip = dict(files["camA"])
                whole_seconds = clip.pop("mtime_ns") // 1_000_000_000
                clip[timestamp_key] = (whole_seconds if timestamp_key == "mtime_s"
                                       else whole_seconds * 1_000_000_000 + 900_000_000)
                sync_document = json.loads(sync.read_text())
                sync_document["clips"]["camA"] = clip
                sync.write_text(json.dumps(sync_document))
                with patch.object(track.find.mapping, "takes_versions", return_value=[(1, mapping)]), \
                        patch.object(track, "load_mapping", return_value=(doc, [script, transcript])):
                    self.assertEqual(track.required_ranges(project, [(1, "A.docx", ["Hello."])])[0], ranges)
                    clip[timestamp_key] += 1 if timestamp_key == "mtime_s" else 1_000_000_000
                    sync.write_text(json.dumps(sync_document))
                    with self.assertRaisesRegex(ValueError, "Camera source changed"):
                        track.required_ranges(project, [(1, "A.docx", ["Hello."])])
            sync_document["clips"]["camA"] = files["camA"]
            sync.write_text(json.dumps(sync_document))
            (mapping.parent / "DONE").write_text(json.dumps({"completion_mode": "manual-override", "outputs": [
                {"scope": "project", "path": mapping.relative_to(project).as_posix()}]}))
            with patch.object(track.find.mapping, "takes_versions", return_value=[(1, mapping)]), \
                    patch.object(track, "load_mapping", return_value=(doc, [script, transcript])):
                overridden_ranges, _ = track.required_ranges(project, [(1, "A.docx", ["Hello."])])
            self.assertEqual(overridden_ranges, ranges)
            (mapping.parent / "DONE").write_text(json.dumps({"conditions": {"latest_versions": []}}))
            with patch.object(track.find.mapping, "takes_versions", return_value=[(1, mapping)]):
                with self.assertRaisesRegex(ValueError, "not recorded in 3.3 DONE"):
                    track.required_ranges(project, [(1, "A.docx", ["Hello."])])
            (mapping.parent / "DONE").write_text(json.dumps({"conditions": {
                "latest_versions": [{"path": mapping.relative_to(project).as_posix()}]}}))
            doc["recordings"][0]["transcript"]["audio"] = "wrong.wav"
            with patch.object(track.find.mapping, "takes_versions", return_value=[(1, mapping)]), \
                    patch.object(track, "load_mapping", return_value=(doc, [script, transcript])):
                with self.assertRaisesRegex(ValueError, "lav audio does not match"):
                    track.required_ranges(project, [(1, "A.docx", ["Hello."])])

    def test_missing_face_holds_previous_or_centers(self):
        self.assertEqual(track.follow(None, []), (None, "centered"))
        face, status = track.follow(None, [[0.4, 0.3, 0.1, 0.1, 0.9]])
        self.assertEqual(status, "tracked")
        self.assertEqual(track.follow(face, [])[1], "held")
        self.assertEqual(track.follow(face, [[0.9, 0.8, 0.1, 0.1, 0.9]]), (face, "held"))

    def test_no_approved_extracts_does_not_fetch_model(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            shortlist = project / track.SHORTLIST
            shortlist.parent.mkdir(parents=True)
            shortlist.write_text("# No viable extracts\n")
            with patch.object(track, "require_prerequisites") as prerequisites, \
                    patch("scripts.validation.require_prerequisites") as recheck, \
                    patch.object(track, "record_done", wraps=track.record_done) as record, \
                    patch.object(track.find, "append_log"):
                result = track.run(project, get_model=lambda: self.fail("model downloaded"))
                prerequisites.assert_called_once_with(project, track.ROOT, "3.5")
                record.assert_called_once_with(project, track.ROOT, "3.5", result)
                recheck.assert_called_once_with(project, track.ROOT, "3.5")
                marker = project / track.STAGE / "DONE"
                original_done, original_mtime = marker.read_bytes(), marker.stat().st_mtime_ns
                self.assertEqual(track.run(project, get_model=lambda: self.fail("model downloaded")), result)
                self.assertEqual(marker.read_bytes(), original_done)
                self.assertEqual(marker.stat().st_mtime_ns, original_mtime)
            self.assertTrue(result["conditions"]["no_extracts"])
            self.assertEqual(result["conditions"]["face_paths"], [])
            self.assertEqual(len(result["outputs"]), 1)
            self.assertTrue((project / track.STAGE / "Face Paths.json").is_file())

    def test_reused_path_rejects_invalid_box(self):
        header = {"ranges_ms": [[1000, 2000]], "role": "camA"}
        output = dict(header, width=3840, height=2160, warnings=[],
                      frames=[{"time_ms": 1100, "face": [0.5, 0.4, 0.1, 0.1], "status": "tracked"},
                              {"time_ms": 1900, "face": [0.5, 0.4, 0.1, 0.1], "status": "tracked"}])
        track.validate_path(output, header)
        output["frames"][0]["face"][0] = float("nan")
        with self.assertRaisesRegex(ValueError, "normalized"):
            track.validate_path(output, header)

    def test_blocked_prerequisites_never_analyze_or_record_completion(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            with patch.object(track, "require_prerequisites", side_effect=ValueError("3.3 blocked")), \
                    patch.object(track, "required_ranges") as ranges, \
                    patch.object(track, "record_done") as record:
                with self.assertRaisesRegex(ValueError, "3.3 blocked"):
                    track.run(project, analyzer=lambda *args: self.fail("analyzer called"),
                              get_model=lambda: self.fail("model requested"))
            ranges.assert_not_called()
            record.assert_not_called()
            self.assertFalse((project / track.STAGE / "DONE").exists())

    def test_run_writes_and_reuses_face_paths_without_rescanning(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            media = project / track.INTAKE / "A__C01__camA.mov"
            media.parent.mkdir(parents=True)
            media.write_bytes(b"camera")
            shortlist = project / track.SHORTLIST
            shortlist.parent.mkdir(parents=True)
            shortlist.write_text("## 1. Example\n\n- **Review status:** Approved\n- **Source:** A.docx\n\n"
                                 "### Provisional spoken script\n\nHello.\n")
            paths = {(media, "camA"): [(track.Fraction(1), track.Fraction(2))]}
            sample = {"width": 640, "height": 360, "frames": [
                {"time_ms": 1000, "face": [0.5, 0.4, 0.1, 0.1], "status": "tracked", "confidence": 0.9},
                {"time_ms": 1900, "face": [0.5, 0.4, 0.1, 0.1], "status": "tracked", "confidence": 0.9}],
                      "warnings": []}
            with patch.object(track, "require_prerequisites"), patch.object(track.find, "append_log"), \
                    patch.object(track, "record_done", side_effect=lambda project, root, step, manifest: manifest) as record, \
                    patch.object(track, "required_ranges", return_value=(paths, [])):
                with patch.object(track, "analyze_video", return_value=sample) as analyzer:
                    first = track.run(project, analyzer=analyzer, get_model=lambda: Path("model.onnx"))
                    second = track.run(project, analyzer=analyzer, get_model=lambda: self.fail("model requested"))
                    self.assertEqual(analyzer.call_count, 1)
                self.assertEqual(record.call_count, 2)
                output = project / track.STAGE / (media.stem + "__FacePath.v1.json")
                original = output.read_bytes()
                edited = json.loads(original)
                edited["model_sha256"] = "changed"
                output.write_text(json.dumps(edited))
                stale_bytes = output.read_bytes()
                marker = output.parent / "DONE"
                marker.write_text("prior completion")
                with self.assertRaisesRegex(ValueError, "inspect before replacing"):
                    track.run(project, analyzer=lambda *args: self.fail("default must preserve edits"),
                              get_model=lambda: self.fail("default must not load model"))
                self.assertEqual(output.read_bytes(), stale_bytes)
                from scripts.stage_invalidation import prepare_regeneration
                with patch("scripts.stage_invalidation.prepare_regeneration") as prepare:
                    def failed_analysis(*args):
                        raise RuntimeError("detector failed")
                    with self.assertRaisesRegex(RuntimeError, "detector failed"):
                        track.run(project, analyzer=failed_analysis, get_model=lambda: Path("model.onnx"), regenerate=True)
                    prepare.assert_not_called()
                self.assertEqual(output.read_bytes(), stale_bytes)
                with patch("scripts.stage_invalidation.prepare_regeneration", wraps=prepare_regeneration) as prepare, \
                        patch.object(track, "analyze_video", return_value=sample) as analyzer:
                    track.run(project, analyzer=analyzer, get_model=lambda: Path("model.onnx"), regenerate=True)
                    prepare.assert_called_once()
                    analyzer.assert_called_once()
                self.assertEqual(output.read_bytes(), original)
                self.assertEqual((output.parent / "STALE/1" / output.name).read_bytes(), stale_bytes)
                self.assertEqual((output.parent / "STALE/1/DONE").read_text(), "prior completion")
                self.assertFalse((output.parent / "STALE/2").exists())
            self.assertEqual(first, second)
            self.assertEqual(len(first["conditions"]["face_paths"]), 1)
            self.assertFalse((project / track.STAGE / "DONE").exists())


if __name__ == "__main__":
    unittest.main()
