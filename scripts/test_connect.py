"""Run with: python -B -m unittest discover -s scripts -p test_connect.py."""

import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import connect as module


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        temp_root = Path(tempfile.gettempdir()).resolve()
        self.temp = tempfile.TemporaryDirectory(prefix="studio-connect-check-", dir=temp_root)
        self.base = Path(self.temp.name).resolve()
        self._check_test_directory()
        self.addCleanup(self._cleanup)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.home = self.base / "home"
        self.home.mkdir()
        self.root = self.home.joinpath("OneDrive", *module.PROJECT_ROOT_PARTS)
        self.root.mkdir(parents=True)
        self.name = "01-Love Compounds Too"
        self.project = self.root / self.name
        self.project.mkdir()
        self.content = self.project / "keep.txt"
        self.content.write_text("preserve this project", encoding="utf-8")
        self.options = dict(workspace=self.workspace, project_root=self.root)

    def _check_test_directory(self):
        self.assertEqual(self.base.parent, Path(tempfile.gettempdir()).resolve())
        self.assertTrue(self.base.name.startswith("studio-connect-check-"))

    def _cleanup(self):
        self._check_test_directory()
        self.temp.cleanup()

    def test_connect_reconnect_disconnect_preserves_target(self):
        link = module.connect(self.name, **self.options)
        self.assertTrue(module._is_link(link))
        self.assertEqual(link.resolve(), self.project)
        self.assertEqual((link / "keep.txt").read_text(), "preserve this project")
        self.assertEqual(module.connect(self.name, **self.options), link)
        self.assertTrue(module.disconnect(self.name, **self.options))
        self.assertFalse(os.path.lexists(link))
        self.assertEqual(self.content.read_text(), "preserve this project")
        self.assertFalse(module.disconnect(self.name, **self.options))

    def test_multiple_connected_projects(self):
        other = "01.1-Love Compounds Too Pickup"
        (self.root / other).mkdir()
        first = module.connect(self.name, **self.options)
        second = module.connect(other, **self.options)
        module.disconnect(self.name, **self.options)
        self.assertTrue(module._is_link(second))
        self.assertFalse(os.path.lexists(first))

    def test_real_directory_collision_is_preserved(self):
        occupied = self.workspace / self.name
        occupied.mkdir()
        marker = occupied / "keep.txt"
        marker.write_text("keep")
        for operation in (module.connect, module.disconnect):
            with self.assertRaises(module.ConnectionError):
                operation(self.name, **self.options)
        self.assertEqual(marker.read_text(), "keep")

    def test_real_file_collision_is_preserved(self):
        occupied = self.workspace / self.name
        occupied.write_text("keep")
        for operation in (module.connect, module.disconnect):
            with self.assertRaises(module.ConnectionError):
                operation(self.name, **self.options)
        self.assertEqual(occupied.read_text(), "keep")

    def test_unrelated_symlink_is_preserved(self):
        other = self.base / "unrelated"
        other.mkdir()
        link = self.workspace / self.name
        if os.name == "nt":
            module._create_junction(other, link)
        else:
            link.symlink_to(other, target_is_directory=True)
        for operation in (module.connect, module.disconnect):
            with self.assertRaises(module.ConnectionError):
                operation(self.name, **self.options)
        self.assertTrue(module._is_link(link))

    def test_broken_link_and_unavailable_onedrive_disconnect(self):
        link = module.connect(self.name, **self.options)
        self.content.unlink()
        self.project.rmdir()
        self.root.rmdir()
        self.assertTrue(module.disconnect(self.name, **self.options))
        self.assertFalse(os.path.lexists(link))

    def test_missing_source_does_not_create_link(self):
        with self.assertRaises(module.ConnectionError):
            module.connect("99-Missing", **self.options)
        self.assertFalse(os.path.lexists(self.workspace / "99-Missing"))

    def test_path_traversal_is_rejected(self):
        for name in ("", ".", "..", "../project", "..\\project", "/project", "C:project", "trailing.", " spaced "):
            for operation in (module.connect, module.disconnect):
                with self.subTest(name=name, operation=operation.__name__):
                    with self.assertRaises(module.ConnectionError):
                        operation(name, **self.options)

    def test_windows_environment_and_macos_cloudstorage_discovery(self):
        with patch.object(module.Path, "home", return_value=self.home), patch.dict(os.environ, {"OneDrive": str(self.home / "OneDrive")}, clear=True):
            self.assertEqual(module._project_root(None), self.root)
        mac_root = self.home.joinpath("Library", "CloudStorage", "OneDrive-Personal", *module.PROJECT_ROOT_PARTS)
        mac_root.mkdir(parents=True)
        with patch.object(module.Path, "home", return_value=self.home), patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(module.ConnectionError):
                module._project_root(None)
            self.content.unlink()
            self.project.rmdir()
            self.root.rmdir()
            self.assertEqual(module._project_root(None), mac_root)

    @unittest.skipUnless(os.name == "nt", "Windows junction fallback")
    def test_windows_privilege_error_uses_junction_fallback(self):
        error = OSError("privilege not held")
        error.winerror = 1314
        with patch.object(module.Path, "symlink_to", side_effect=error):
            link = module.connect(self.name, **self.options)
        self.assertTrue(module._is_junction(link))
        self.assertEqual(link.resolve(), self.project)
        module.disconnect(self.name, **self.options)
        self.assertTrue(self.content.is_file())

    def test_unicode_and_shell_metacharacters_in_project_name(self):
        name = "02-Café & 100% [Love] \U0001f60a"
        target = self.root / name
        target.mkdir()
        marker = target / "keep.txt"
        marker.write_text("keep")
        link = module.connect(name, **self.options)
        self.assertEqual(link.resolve(), target)
        self.assertEqual((link / "keep.txt").read_text(), "keep")
        self.assertTrue(module.disconnect(name, **self.options))
        self.assertEqual(marker.read_text(), "keep")

    def test_cli_unquoted_spaces_from_another_working_directory(self):
        command = [sys.executable, "-B", str(Path(module.__file__).resolve()), "connect", "01-Love", "Compounds", "Too", "--workspace", str(self.workspace), "--project-root", str(self.root)]
        result = subprocess.run(command, cwd=self.base, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Connected:", result.stdout)
        command[3] = "disconnect"
        result = subprocess.run(command, cwd=self.base, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.content.is_file())

    def test_cli_missing_project_has_nonzero_exit_status(self):
        with contextlib.redirect_stderr(io.StringIO()) as output:
            status = module.main(["connect", "99-Missing", "--workspace", str(self.workspace), "--project-root", str(self.root)])
        self.assertEqual(status, 1)
        self.assertIn("Project folder does not exist", output.getvalue())


if __name__ == "__main__":
    unittest.main()
