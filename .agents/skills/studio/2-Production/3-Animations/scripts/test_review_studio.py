"""Review startup checks without starting Remotion or touching a project."""

from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import review_studio as review


class ReviewStudioTests(unittest.TestCase):
    def test_candidate_requires_exact_version_and_existing_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            asset = project / review.stage.STAGE / 'A13'
            candidate = asset / 'candidates/power-up/v012'
            (candidate / 'src').mkdir(parents=True)
            (candidate / 'src/Studio.tsx').touch()
            with (patch.object(review.stage, 'require_prerequisites'),
                  patch.object(review.stage, 'eligible', return_value={'A13': {}})):
                with self.assertRaisesRegex(ValueError, 'Use --candidate'):
                    review.target(project, 'A13', '../v012')
                with self.assertRaisesRegex(ValueError, 'Missing references'):
                    review.target(project, 'A13', 'power-up/v012')
                (candidate / 'references').mkdir()
                self.assertEqual(review.target(project, 'A13', 'power-up/v012'), (candidate, asset))

    def test_readiness_requires_build_and_verified_http(self):
        child = Mock(stdout=StringIO('Local: http://localhost:3000\nBuilt in 10ms\n'))
        child.poll.return_value = None
        with patch.object(review, 'live', return_value=True) as live:
            review.wait_for_build(child, Path('candidate'), Path('asset'))
        live.assert_called_once_with(Path('candidate'), Path('asset'))

    def test_readiness_refuses_remotion_port_fallback(self):
        child = Mock(stdout=StringIO('Local: http://localhost:3001\nBuilt in 10ms\n'))
        child.poll.return_value = None
        with patch.object(review, 'live') as live:
            with self.assertRaisesRegex(ValueError, 'different port'):
                review.wait_for_build(child, Path('candidate'), Path('asset'))
        live.assert_not_called()
