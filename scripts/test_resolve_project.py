"""Run with: python -B -m unittest discover -s scripts -p test_resolve_project.py."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import connect
from resolve_project import resolve_project


class ResolveProjectTests(unittest.TestCase):
    def test_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            self.assertEqual(resolve_project(path), path.resolve())

    def test_connected_link_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            workspace = base / "workspace"
            workspace.mkdir()
            root = base / "OneDrive"
            project = root / "01-Test Project"
            project.mkdir(parents=True)
            link = connect.connect(project.name, workspace=workspace, project_root=root)
            try:
                result = subprocess.run(
                    [sys.executable, str(Path(__file__).with_name("resolve_project.py")), str(link)],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout.strip(), str(project.resolve()))
            finally:
                connect.disconnect(project.name, workspace=workspace, project_root=root)

    def test_missing_path(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                resolve_project(Path(directory) / "missing")

    def test_file_cli(self):
        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("resolve_project.py")), __file__],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("not a directory", result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
