"""Check extract source matching, omissions, and independent timeline camera behavior."""
from pathlib import Path
from tempfile import TemporaryDirectory
from fractions import Fraction
import json
import sys
import unittest
from unittest.mock import patch

import extracts
import first_cut as cut
import preview_subtitles
from test_first_cut import fixture


class ExtractTests(unittest.TestCase):
    def test_preview_accepts_seconds_and_legacy_sync_but_rejects_changed_seconds(self):
        for timestamp_key in ("mtime_s", "mtime_ns"):
            with self.subTest(timestamp_key=timestamp_key), TemporaryDirectory() as directory:
                project = Path(directory).resolve()
                document, scores, syncs, probes = fixture(project)
                scores["source_mapping"] = {"path": "A__Takes.v1.json"}
                sync = syncs[1]
                sync.update(reference_role="camA",
                            offset_convention="positive_means_file_starts_later_than_camA")
                for clip in sync["clips"].values():
                    stat = (project / clip["path"]).stat()
                    clip["size_bytes"] = stat.st_size
                    seconds = stat.st_mtime_ns // 1_000_000_000
                    clip[timestamp_key] = seconds if timestamp_key == "mtime_s" else seconds * 1_000_000_000 + 900_000_000
                score = project / "A__Scores.v2.json"
                score.write_text(json.dumps(scores))
                sync_path = project / cut.SYNC / "A__C01.json"
                sync_path.parent.mkdir(parents=True)
                sync_path.write_text(json.dumps(sync))
                output = project / "3-Post Production/0-Tmp/Preview.FCPXML"
                output.parent.mkdir(parents=True)
                mapped = {"number": 1, "source": "A.docx", "title": "Example", "issues": [],
                          "groups": [{"script": {"word_start": 0, "word_end": 0}, "discarded": False}]}
                with patch.object(extracts, "map_all", return_value={"extracts": [mapped]}), \
                        patch.object(cut.scoring, "load_mapping", return_value=document), \
                        patch.object(cut, "probe", return_value=probes[1]["camA"]), \
                        patch.object(cut, "build_xml", return_value=(b"preview", {"takes": 1, "subtitle_titles": 1})), \
                        patch.object(cut.find, "append_log"):
                    preview_subtitles.preview(project, project / "shortlist.md", [score], 1, output)
                    self.assertEqual(output.read_bytes(), b"preview")
                    sync["clips"]["camA"][timestamp_key] += 1 if timestamp_key == "mtime_s" else 1_000_000_000
                    sync_path.write_text(json.dumps(sync))
                    with self.assertRaisesRegex(ValueError, "Media changed since sync"):
                        preview_subtitles.preview(project, project / "shortlist.md", [score], 1,
                                                  output.with_name("Changed.FCPXML"))

    def test_map_only_cli_does_not_require_stage_prerequisites(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            shortlist = project / "preview.md"
            shortlist.write_text("# Isolated preview\n")
            with patch.object(sys, "argv", ["extracts.py", "--project-root", str(project),
                                           "--shortlist", "preview.md", "--scores", "fixture.json"]), \
                    patch.object(extracts, "map_all", return_value={"extracts": []}) as mapping, \
                    patch.object(cut, "require_prerequisites") as prerequisites:
                extracts.main()
            mapping.assert_called_once_with(project, shortlist, [Path("fixture.json")])
            prerequisites.assert_not_called()
            self.assertFalse((project / cut.STAGE).exists())

    def test_generation_uses_shared_prerequisites_before_mapping(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            shortlist = project / cut.EXTRACTS / "APPROVED" / "Short-Form Excerpt Shortlist.md"
            with patch.object(cut, "require_prerequisites", side_effect=ValueError("stale ancestor")) as prerequisites, \
                    patch.object(extracts, "map_all") as mapping, \
                    patch.object(cut.find, "append_log") as log, \
                    patch.object(cut, "validate_done") as legacy:
                with self.assertRaisesRegex(ValueError, "stale ancestor"):
                    extracts.generate(project, shortlist, [], 50, False)
                prerequisites.assert_called_once_with(project, cut.ROOT, "3.6")
                mapping.assert_not_called()
                log.assert_not_called()
                legacy.assert_not_called()
            self.assertEqual(list(project.iterdir()), [])

    def test_generation_rejects_unapproved_shortlist_before_writing(self):
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            draft = root / cut.EXTRACTS / "Short-Form Excerpt Shortlist.md"
            draft.parent.mkdir(parents=True)
            draft.write_text("# Draft shortlist\n")
            with self.assertRaisesRegex(ValueError, "approved shortlist"):
                extracts.generate(root, draft, [], 50, False)
            self.assertFalse((root / cut.STAGE).exists())

    def test_mixed_formats_publish_only_missing_drafts_and_extend_legacy_mapping(self):
        from scripts.stage_invalidation import prepare_regeneration
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            shortlist = project / cut.EXTRACTS / "APPROVED/Short-Form Excerpt Shortlist.md"
            shortlist.parent.mkdir(parents=True)
            shortlist.write_text("approved source")
            stage = project / cut.STAGE
            preserved = {}
            for relative in ("A.FCPXML", "Extracts/Portrait/01-Example Portrait.FCPXML",
                             "APPROVED/01-Example Portrait.FCPXML"):
                path = stage / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"existing draft or approved artifact")
                preserved[path] = (path.read_bytes(), path.stat().st_mtime_ns)
            old = {"number": 1, "source": "A.docx", "title": "Example", "formats": ["portrait"],
                   "passages": [{"text": "First.", "jump_before": False}], "groups": [], "issues": []}
            new = dict(old, number=5, title="All Animations", formats=["landscape", "portrait"])
            output = stage / "Extracts/Portrait/Extract Mappings.json"
            prior = {"script": "01-Example Portrait", "extract_number": 1, "source_script": "A.docx"}
            batch = {"schema": "studio.extract-mappings", "schema_version": 1, "shortlist": {"sha256": "old"},
                     "projects": [prior], "extracts": [old]}
            output.write_text(json.dumps(batch))
            document, scores, _, _ = fixture(project)
            source = project / "A.docx"
            source.write_bytes(b"script")
            document["script"]["source"] = {"path": source.name}
            document["recordings"] = []
            scores.update(takes=[], source_mapping={"path": "A__Takes.v1.json"})
            score_folder = project / cut.scoring.STAGE
            score_folder.mkdir(parents=True)
            score = score_folder / "A__Scores.v1.json"
            score.write_text(json.dumps(scores))
            mapping = project / "A__Takes.v1.json"
            mapping.write_text("{}")
            with patch.object(cut, "require_prerequisites"), patch.object(cut.find, "append_log"), \
                    patch.object(extracts, "map_all", return_value=dict(batch, extracts=[old, new])), \
                    patch.object(cut.find.mapping, "takes_versions", return_value=[(1, mapping)]), \
                    patch.object(cut.scoring, "load_mapping", return_value=document), \
                    patch.object(cut.find.sources, "load_script"), \
                    patch.object(cut.find, "check_mapping", return_value={"coverage": {"complete": True}}), \
                    patch.object(cut, "build_xml", side_effect=lambda *a, **kw: (a[2].encode(), {"script": a[2]})) as build, \
                    patch.object(cut.portrait, "load_paths", return_value=({}, [])), \
                    patch.object(cut.find, "invalidate") as invalidate:
                results = extracts.generate(project, shortlist, [score], 50, False)
                self.assertEqual([row["extract_number"] for row in results], [5, 5])
                self.assertEqual(build.call_count, 2)
                self.assertIsNone(build.call_args_list[0].kwargs["face_paths"])
                self.assertEqual(build.call_args_list[1].kwargs["face_paths"], {})
                self.assertEqual(json.loads(output.read_text())["projects"][0], prior)
                self.assertEqual(json.loads(output.read_text())["shortlist"], batch["shortlist"])
                self.assertFalse((stage / "Extracts/01-Example.FCPXML").exists())
                self.assertEqual(extracts.generate(project, shortlist, [score], 50, False), [])
                self.assertEqual(build.call_count, 2)
                invalidate.assert_called_once()
                self.assertEqual({p: (p.read_bytes(), p.stat().st_mtime_ns) for p in preserved}, preserved)
                with patch("scripts.stage_invalidation.prepare_regeneration", wraps=prepare_regeneration) as prepare:
                    extracts.generate(project, shortlist, [score], 50, False, regenerate=True)
                    prepare.assert_called_once()
                    self.assertIn(output, prepare.call_args.args[3])
                    self.assertNotIn(stage / "APPROVED/01-Example Portrait.FCPXML", prepare.call_args.args[3])

    def test_cli_uses_shortlist_for_all_formats(self):
        with TemporaryDirectory() as directory:
            project = Path(directory).resolve()
            shortlist = project / "shortlist.md"
            shortlist.write_text("placeholder")
            args = ["extracts.py", "--project-root", str(project), "--shortlist", str(shortlist),
                    "--scores", "A__Scores.v1.json", "--generate"]
            for flags in ([], ["--regenerate"]):
                with patch("sys.argv", args + flags), patch.object(extracts, "map_all", return_value={"extracts": []}):
                    with patch.object(extracts, "generate", return_value=[]) as generate, patch("builtins.print"):
                        extracts.main()
                        self.assertEqual(generate.call_args.kwargs["regenerate"], "--regenerate" in flags)

    def test_format_declarations_and_pending_extracts(self):
        def section(number, status, formats=""):
            return (f"## {number}. Example\n- **Review status:** {status}\n- **Source:** A.docx\n{formats}\n"
                    "### Provisional spoken script\n\n> First.\n\n")
        text = "Extracts 1–4 retain their vertical presentation.\n" + section(1, "Approved")
        text += section(5, "Approved", "- **Versions:** One extract, two timelines: **9:16 portrait** and **16:9 landscape**. Notes.")
        text += section(6, "Pending Review")
        self.assertEqual([(p.name, portrait) for p, _, portrait, _ in extracts.variants(extracts.parse_shortlist(text))],
                         [("01-Example Portrait.FCPXML", True), ("05-Example.FCPXML", False),
                          ("05-Example Portrait.FCPXML", True)])
        with self.assertRaisesRegex(ValueError, "no requested format"):
            list(extracts.variants(extracts.parse_shortlist(section(1, "Approved"))))

    def test_parser_preserves_six_separate_headers_and_jump_directives(self):
        text = "\n".join(f"## {i}. Title {i}\n\n- **Review status:** Approved\n- **Source:** A.docx\n\n"
                         "### Provisional spoken script\n\nFirst.\n\n> [PROPOSED JUMP]\n\nLast.\n"
                         for i in range(1, 7))
        result = extracts.parse_shortlist(text)
        self.assertEqual(len(result), 6)
        self.assertEqual([item["title"] for item in result], [f"Title {i}" for i in range(1, 7)])
        self.assertTrue(all(item["passages"][1]["jump_before"] for item in result))

    def test_parser_accepts_source_version_blockquotes_and_bold_jumps(self):
        text = (
            "## 1. Approved\n\n- **Review status:** Approved. Reviewed in chat.\n"
            "- **Source/version:** `Approved/A.docx`, current approved master.\n\n"
            "### Provisional spoken script\n\n"
            "> First.\n>\n> Second.\n\n"
            "**[PROPOSED JUMP — omit source L02. Nonspoken edit note.]**\n\n"
            "> Last.\n"
            "\n## 2. Pending\n\n- **Review status:** Pending Review\n"
            "- **Source/version:** `Approved/A.docx`\n\n### Provisional spoken script\n\n> Nope.\n")
        result = extracts.parse_shortlist(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["source"], "A.docx")
        self.assertEqual([(p["text"], p["jump_before"]) for p in result[0]["passages"]],
                         [("First.", False), ("Second.", False), ("Last.", True)])

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
