"""Temporary-file tests for frozen overrides; no stage runtime is required."""
import builtins
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import override_snapshot as snapshot


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.project, self.workflow = self.root / "project", self.root / "workflow"
        self.project.mkdir()
        self.workflow.mkdir()
        for path in snapshot.CRITICAL_FILES:
            self.put(self.workflow, path, "# critical stub\n")
        self.put(self.workflow, snapshot.ROOT_SKILL, "excluded router\n")
        self.definitions = {}
        self.define("1.1", "1-Pre Production/1-Script Review", [],
                    [self.rule("sources", r".+\.docx", 1)],
                    [self.rule("approved", r".+\.docx", 1)])
        self.define("3.3", "3-Post Production/3-Find Takes", ["1.1"],
                    [self.rule("transcripts", r".+\.json", 1),
                     self.rule("media", r".+\.wav", 1)],
                    [self.rule("takes", r".+__Takes\.v[1-9]\d*\.json", 1)])
        self.define("3.4", "3-Post Production/4-Take Selection", ["3.3"],
                    [self.rule("takes", r".+__Takes\.v[1-9]\d*\.json", 1)],
                    [self.rule("scores", r".+\.json", 1, 1)])
        self.define("3.5", "3-Post Production/5-Face Tracking", ["3.3"],
                    [self.rule("takes", r".+__Takes\.v[1-9]\d*\.json", 1)],
                    [self.rule("faces", r".+\.json", 1)])
        for path in ("sources/script.docx", "approved/script.docx", "transcripts/lav.json",
                     "media/lav.wav", "takes/script__Takes.v1.json", "scores/script.json", "faces/index.json"):
            self.put(self.project, path)
        self.extra_rule = self.rule("transcripts", r"Stale Transcripts\.json", 0)
        self.definitions["3.3"]["override_watches"] = [self.extra_rule, self.rule("optional", r".+\.txt", 0)]
        self.save_definition("3.3")

    @staticmethod
    def put(root, path, content="fixture\n"):
        path = root / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    @staticmethod
    def rule(folder, regex, minimum, maximum=None):
        rule = {"folder": folder, "filename_regex": regex, "minimum": minimum}
        if maximum is not None:
            rule["maximum"] = maximum
        return rule

    def define(self, step, folder, parents, inputs, outputs):
        self.definitions[step] = {
            "schema": "studio.stage", "schema_version": 1, "step": step,
            "folder": folder, "prerequisites": [{"step": parent, "folder": self.definitions[parent]["folder"]}
                                                  for parent in parents],
            "inputs": inputs, "outputs": outputs,
            "validation": {"script": "scripts/validation.py"},
            "workflow_dependencies": ["shared/settings.json"],
        }
        stage = ".agents/skills/studio/" + folder
        self.put(self.workflow, stage + "/SKILL.md", "stage policy\n")
        self.put(self.workflow, stage + "/scripts/validation.py", "raise RuntimeError('must not import')\n")
        self.put(self.workflow, "shared/settings.json", "{}\n")
        self.save_definition(step)

    def save_definition(self, step):
        path = ".agents/skills/studio/" + self.definitions[step]["folder"] + "/dependencies.json"
        self.put(self.workflow, path, json.dumps(self.definitions[step], sort_keys=True))

    def collect(self, step="3.4"):
        return snapshot.collect_snapshot(self.project, self.workflow, step)

    def status(self, evidence, step="3.4"):
        return snapshot.validate_snapshot(self.project, self.workflow, self.definitions[step], evidence)[0]

    def test_current_and_deterministic(self):
        evidence = self.collect()
        self.assertEqual(evidence, self.collect())
        self.assertEqual("current", self.status(evidence))
        self.assertEqual({"scope", "path", "method", "size_bytes", "mtime_s"},
                         set(next(item for item in evidence["inputs"] if item["path"].endswith(".wav"))))
        self.assertTrue(all(item["method"] == "sha256" for item in evidence["dependencies"]))

    def test_34_and_35_include_all_ancestor_categories(self):
        for step in ("3.4", "3.5"):
            with self.subTest(step=step):
                evidence = self.collect(step)
                inputs = {item["path"] for item in evidence["inputs"]}
                self.assertEqual({"sources/script.docx", "approved/script.docx", "transcripts/lav.json",
                                  "media/lav.wav", "takes/script__Takes.v1.json"}, inputs)
                self.assertEqual(1, len(evidence["outputs"]))
                self.assertTrue(all(item["path"] not in inputs for item in evidence["outputs"]))

    def test_additions_removal_and_empty_membership(self):
        for path in ("sources/new.docx", "takes/script__Takes.v2.json",
                     "optional/new.txt", "transcripts/Stale Transcripts.json"):
            with self.subTest(path=path):
                evidence = self.collect()
                added = self.put(self.project, path)
                self.assertEqual("stale", self.status(evidence))
                added.unlink()
                self.assertEqual("current", self.status(evidence))
        evidence = self.collect()
        (self.project / "approved/script.docx").unlink()
        self.assertEqual("stale", self.status(evidence))

    def test_case_only_rename_changes_lexical_membership(self):
        evidence = self.collect()
        original = self.project / "takes/script__Takes.v1.json"
        original.rename(original.with_name("SCRIPT__TAKES.V1.JSON"))
        self.assertEqual("stale", self.status(evidence))

    def test_case_duplicate_changes_membership_on_case_sensitive_filesystems(self):
        evidence = self.collect()
        duplicate = self.project / "takes/SCRIPT__TAKES.V1.JSON"
        if duplicate.exists():
            self.skipTest("Fixture filesystem is case-insensitive")
        self.put(self.project, "takes/SCRIPT__TAKES.V1.JSON")
        self.assertEqual("stale", self.status(evidence))

    def test_empty_watch_is_retained_without_complete_set(self):
        watches = self.collect()["snapshot"]["watches"]
        self.assertTrue(any(item["folder"] == "optional" and item["paths"] == [] for item in watches))
        self.assertTrue(any(item["scope"] == "workflow" and item["paths"] == []
                            and item["filename_regex"] == snapshot.REQUIREMENTS for item in watches))

    def test_output_membership_and_declared_bounds(self):
        evidence = self.collect()
        self.put(self.project, "scores/extra.json")
        with self.assertRaises(ValueError):
            self.collect()
        self.assertEqual("stale", self.status(evidence))
        (self.project / "scores/extra.json").unlink()
        (self.project / "scores/script.json").unlink()
        with self.assertRaises(ValueError):
            self.collect()

    def test_changed_source_output_and_workflow_dependencies(self):
        paths = [(self.project, "sources/script.docx"), (self.project, "scores/script.json"),
                 (self.workflow, "shared/settings.json"), (self.workflow, "scripts/stage_override.py"),
                 (self.workflow, ".agents/skills/studio/1-Pre Production/1-Script Review/SKILL.md")]
        for root, path in paths:
            with self.subTest(path=path):
                evidence = self.collect()
                self.put(root, path, "changed\n")
                self.assertEqual("stale", self.status(evidence))

    def test_helper_and_requirements_membership(self):
        for path in ("scripts/new_helper.py", "scripts/requirements-extra.txt", "requirements.txt", "pyproject.toml",
                     ".agents/skills/studio/3-Post Production/3-Find Takes/scripts/new_helper.py"):
            with self.subTest(path=path):
                evidence = self.collect()
                added = self.put(self.workflow, path)
                self.assertEqual("stale", self.status(evidence))
                added.unlink()

    def test_no_router_lifecycle_logs_stale_or_tests(self):
        evidence = self.collect()
        for root, path in ((self.workflow, snapshot.ROOT_SKILL), (self.workflow, "scripts/test_new.py"),
                           (self.workflow, "scripts/new_test.py"), (self.project, "optional/LOG.md"),
                           (self.project, "takes/STALE/script__Takes.v2.json"),
                           (self.project, "optional/DONE")):
            self.put(root, path, "ignored\n")
        self.assertEqual("current", self.status(evidence))

    def test_declared_log_or_debug_named_inputs_are_not_silently_excluded(self):
        for name in ("Log.docx", "Debug.docx"):
            self.put(self.project, "sources/" + name, "approved script\n")
        evidence = self.collect()
        paths = {entry["path"] for entry in evidence["inputs"]}
        self.assertIn("sources/Log.docx", paths)
        self.assertIn("sources/Debug.docx", paths)
        self.put(self.project, "sources/Log.docx", "changed approved script\n")
        self.assertEqual("stale", self.status(evidence))

    def test_file_directory_and_broken_symlinks(self):
        for value in ("sources/script.docx", "scores/script.json", "transcripts", "optional/missing.txt"):
            with self.subTest(value=value):
                evidence = self.collect()
                path = self.project / value
                original = path.with_name(path.name + ".saved")
                existed = path.exists()
                if existed:
                    path.rename(original)
                else:
                    path.parent.mkdir(exist_ok=True)
                path.symlink_to(original)
                try:
                    with self.assertRaises(ValueError):
                        self.collect()
                    self.assertEqual("stale", self.status(evidence))
                finally:
                    path.unlink()
                    if existed:
                        original.rename(path)

    def test_workflow_symlink_and_missing_critical_file(self):
        evidence = self.collect()
        path = self.workflow / "studio/fingerprints.py"
        path.unlink()
        self.assertEqual("stale", self.status(evidence))
        path.symlink_to(self.workflow / "scripts/validation.py")
        with self.assertRaises(ValueError):
            self.collect()

    def test_declared_paths_cannot_escape_project_or_workflow(self):
        for field, value in (("inputs", [self.rule("../outside", r".+", 0)]),
                             ("workflow_dependencies", ["../outside.py"])):
            with self.subTest(field=field):
                previous = self.definitions["3.4"][field]
                self.definitions["3.4"][field] = value
                self.save_definition("3.4")
                try:
                    with self.assertRaises(ValueError):
                        self.collect()
                finally:
                    self.definitions["3.4"][field] = previous
                    self.save_definition("3.4")

    def test_ancestor_helper_removal_and_policy_changes_are_stale(self):
        helper = self.put(self.workflow,
                          ".agents/skills/studio/1-Pre Production/1-Script Review/scripts/helper.py")
        evidence = self.collect()
        helper.unlink()
        self.assertEqual("stale", self.status(evidence))
        evidence = self.collect()
        self.definitions["3.4"]["outputs"][0]["maximum"] = 2
        self.save_definition("3.4")
        self.assertEqual("stale", self.status(evidence))

    def test_workflow_directory_symlink_is_rejected(self):
        evidence = self.collect()
        directory = self.workflow / "shared"
        original = self.workflow / "shared-original"
        directory.rename(original)
        directory.symlink_to(original, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.collect()
        self.assertEqual("stale", self.status(evidence))

    def test_omitted_or_weakened_watches_and_fingerprints(self):
        evidence = self.collect()
        for change in (lambda value: value["snapshot"]["watches"].pop(),
                       lambda value: value["snapshot"]["watches"][0].update(minimum=0),
                       lambda value: value["inputs"].pop(),
                       lambda value: value["dependencies"].pop()):
            saved = copy.deepcopy(evidence)
            change(saved)
            if saved != evidence:
                self.assertEqual("stale", self.status(saved))

    def test_invalid_schema_paths_and_fingerprints(self):
        for change in (lambda value: value.pop("snapshot"),
                       lambda value: value["snapshot"].update(schema_version=True),
                       lambda value: value["inputs"][0].update(path="../escape"),
                       lambda value: value["inputs"][0].update(path="/absolute"),
                       lambda value: value["inputs"][0].update(method="anything"),
                       lambda value: value["snapshot"]["watches"][0].update(filename_regex="[")):
            saved = self.collect()
            change(saved)
            self.assertEqual("unverifiable", self.status(saved))

    def test_overlapping_own_output_is_not_an_input(self):
        self.definitions["3.4"]["inputs"].append(self.rule("scores", r".+\.json", 1))
        self.save_definition("3.4")
        evidence = self.collect()
        self.assertNotIn("scores/script.json", {item["path"] for item in evidence["inputs"]})

    def test_read_only_no_stage_imports_or_runtime_dependencies(self):
        def tree():
            return {path.relative_to(self.root).as_posix():
                    (path.stat().st_mtime_ns, path.read_bytes() if path.is_file() else None)
                    for path in self.root.rglob("*")}

        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.startswith(("numpy", "torch", "cv2", "rapidfuzz", "importlib")) or "studio_validation_" in name:
                raise AssertionError("Forbidden import: " + name)
            return original_import(name, *args, **kwargs)

        before, old_path, old_bytecode = tree(), sys.path[:], sys.dont_write_bytecode
        with patch("builtins.__import__", side_effect=guarded_import), \
                patch("scripts.validation.check_marker", side_effect=AssertionError("stage validator")), \
                patch("scripts.validation.StageValidation.validate", side_effect=AssertionError("stage validator")):
            evidence = self.collect()
            self.assertEqual("current", self.status(evidence))
            evidence["inputs"].pop()
            self.assertEqual("stale", self.status(evidence))
        self.assertEqual(before, tree())
        self.assertEqual(old_path, sys.path)
        self.assertEqual(old_bytecode, sys.dont_write_bytecode)


if __name__ == "__main__":
    unittest.main()
