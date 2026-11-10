"""Dependency setup tests never install packages or load transcription models."""

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).with_name('install_dependencies.py')
SPEC = importlib.util.spec_from_file_location('transcribe_installer', SOURCE)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def test_stage_local_environment(self):
        self.assertEqual(installer.ENVIRONMENT, SOURCE.parents[1] / '.venv-tools')

    def test_current_environment_is_reused_without_installation(self):
        with patch.object(sys, 'argv', [str(SOURCE)]), \
                patch.object(Path, 'is_file', return_value=True), \
                patch.object(installer, 'probe_environment', return_value={'whisperx': '3.8.6'}), \
                patch.object(installer.subprocess, 'run') as run:
            self.assertEqual(installer.main(), 0)
            run.assert_not_called()

    def test_missing_check_does_not_create_environment(self):
        with patch.object(sys, 'argv', [str(SOURCE), '--check']), \
                patch.object(Path, 'is_file', return_value=False), \
                patch.object(installer.venv, 'EnvBuilder') as builder, \
                patch.object(installer.subprocess, 'run') as run:
            self.assertEqual(installer.main(), 1)
            builder.assert_not_called()
            run.assert_not_called()

    def test_failed_check_does_not_install(self):
        with patch.object(sys, 'argv', [str(SOURCE), '--check']), \
                patch.object(Path, 'is_file', return_value=True), \
                patch.object(installer, 'probe_environment', side_effect=RuntimeError('broken')), \
                patch.object(installer.subprocess, 'run') as run:
            self.assertEqual(installer.main(), 1)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
