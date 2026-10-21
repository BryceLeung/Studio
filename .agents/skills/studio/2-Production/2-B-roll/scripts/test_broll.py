"""Temporary-project B-roll request, footage and completion regression tests."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


broll = load_module('broll_tests_helper', Path(__file__).with_name('broll.py'))
fixture_module = load_module('broll_registry_fixture', broll.ROOT / '.agents/skills/studio/1-Pre Production/4-Asset Breakdown/scripts/test_assets.py')
assets = fixture_module.assets
from scripts import validation, stage_completion as completion


class BrollTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.AssetTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project = self.fixture.project
        fixture_module.docx(self.fixture.script, comment=False)
        brief = 'A child washing hands.\n\nThen a family lunch.'
        self.fixture.reconcile([self.fixture.suggestion(type='B-roll', brief=brief)])
        self.fixture.edit('Review', 'Keep')
        self.rows = broll.eligible(self.project)
        self.asset = next(iter(self.rows))
        self.directory = broll.folder(self.project, self.asset)
        self.plan = {'sourcing': 'either', 'shots': ['hands', 'lunch']}
        self.mock_probe = patch.object(broll, 'probe', return_value={'duration': 10.0, 'width': 1920, 'height': 1080, 'codec': 'h264'})
        self.mock_probe.start()
        self.addCleanup(self.mock_probe.stop)
        self.initialize()

    def initialize(self):
        work = broll.initialize(self.project, {self.asset: self.plan}, self.rows)
        for asset, row, entry in work:
            broll.write_request(self.project, asset, {'schema': 'studio.broll.candidates', 'schema_version': 2,
                                'asset': asset, 'requests': [], 'candidates': []}, entry, row['Brief'])
        return work

    def save(self, ledger):
        broll.atomic_json(self.directory / 'Candidates.json', ledger)

    def candidate(self, number, *, shots=None, suitable=True, in_place=False):
        source = (self.directory if in_place else self.project) / f'provided{number}.mp4'
        source.write_bytes(f'distinct footage {number}'.encode())
        ledger = broll.load(self.project, self.asset, allow_unregistered=in_place)
        destination, candidate = broll.register(self.project, self.asset, source,
            {'origin': 'User', 'source_id': f'original-{number}', 'description': 'Personally supplied footage'}, ledger)
        if source != destination:
            shutil.copyfile(source, destination)
        candidate['identity'] = completion.fingerprint(destination, self.project, 'project')
        candidate['assessments'].append(broll.assess(ledger, candidate,
            {'suitable': suitable, 'reviewed_by': 'test reviewer', 'notes': 'Content inspected',
             'shots': shots if shots is not None else ['hands', 'lunch'], 'ranges': [[0, 5]]}))
        ledger['candidates'].append(candidate)
        self.save(ledger)
        return candidate

    def revise(self):
        ledger = broll.load(self.project, self.asset)
        text = 'Revised full request\n\n## Changes\nCloser framing.\n'
        version = len(ledger['requests']) + 1
        entry = broll.request_entry(self.project, self.asset, self.rows[self.asset], version,
                                    'either', self.plan['shots'], 'Closer framing', text)
        broll.write_request(self.project, self.asset, ledger, entry, text)

    def test_current_markdown_human_fields_and_exact_idempotent_v1(self):
        self.assertEqual((self.directory / 'request.md').read_text(encoding='utf-8'), self.rows[self.asset]['Brief'])
        before = fixture_module.readonly_state(self.project)
        self.assertEqual(self.initialize(), [])
        self.assertEqual(before, fixture_module.readonly_state(self.project))
        sidecar = self.fixture.path.with_suffix('.json').read_bytes()
        original = self.fixture.path.read_text(encoding='utf-8')
        self.fixture.path.write_text(original.replace('**Review:** Keep', '**Review:** Reject'), encoding='utf-8')
        self.assertEqual(broll.eligible(self.project), {})
        self.assertEqual(sidecar, self.fixture.path.with_suffix('.json').read_bytes())
        self.fixture.path.write_text(original.replace('**Type:** B-roll', '**Type:** Graphic'), encoding='utf-8')
        self.assertEqual(broll.eligible(self.project), {})

    def test_revisions_append_one_journal_and_preserve_previous_bytes(self):
        original = (self.directory / 'request.md').read_bytes()
        for _ in range(9):
            self.revise()
        ledger = broll.load(self.project, self.asset)
        self.assertEqual(ledger['requests'][-1]['version'], 10)
        self.assertEqual(ledger['requests'][-1]['file'], 'request.md')
        self.assertTrue((self.directory / 'request.md').read_bytes().startswith(original))
        self.assertEqual(list(self.directory.glob('request*')), [self.directory / 'request.md'])
        (self.directory / 'request.md').write_text('edited behind history', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'append-only request journal'):
            self.revise()
        self.assertFalse((self.directory / 'request.v11.md').exists())

    def test_untracked_request_refused(self):
        (self.directory / 'request.v02.md').write_text('ambiguous', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Unexpected request files'):
            broll.load(self.project, self.asset)

    def test_search_terms_outcomes_and_reviews_append_without_changing_brief_identity(self):
        for number in range(3):
            self.candidate(number)
        ledger = broll.load(self.project, self.asset)
        request = dict(ledger['requests'][-1])
        original = (self.directory / 'request.md').read_bytes()
        search_id = broll.record_search(self.project, self.asset, ledger, 'pixabay', 'hang up phone', 'Typing clips missed the requested action')
        broll.search_outcome(self.project, self.asset, ledger, search_id, 'succeeded', count=4, result_file='scratch/search-1.json')
        broll.search_note(self.project, self.asset, ledger, search_id, 'Rejected four typing shots; next try ending a call.')
        ledger = broll.load(self.project, self.asset)
        self.assertEqual(ledger['requests'], [request])
        self.assertTrue((self.directory / 'request.md').read_bytes().startswith(original))
        content = (self.directory / 'request.md').read_text(encoding='utf-8')
        self.assertIn('hang up phone', content)
        self.assertIn('Results returned: 4', content)
        self.assertIn('Rejected four typing shots', content)
        self.assertEqual(broll.check(self.project), {self.asset: 3})
        with self.assertRaisesRegex(ValueError, 'already recorded'):
            broll.search_outcome(self.project, self.asset, ledger, search_id, 'failed')

    def test_untracked_journal_append_and_interrupted_publication_fail_closed(self):
        path = self.directory / 'request.md'
        with path.open('ab') as stream:
            stream.write(b'\nUnrecorded text')
        with self.assertRaisesRegex(ValueError, 'append-only request journal'):
            broll.load(self.project, self.asset)

    def test_interrupted_ledger_publication_preserves_append_and_is_detected(self):
        ledger = broll.load(self.project, self.asset)
        before = (self.directory / 'request.md').read_bytes()
        with patch.object(broll, 'atomic_json', side_effect=OSError('Interrupted save')):
            with self.assertRaises(OSError):
                broll.record_search(self.project, self.asset, ledger, 'pixabay', 'hang up phone', 'Refine action terms')
        self.assertTrue((self.directory / 'request.md').read_bytes().startswith(before))
        self.assertIn(b'hang up phone', (self.directory / 'request.md').read_bytes())
        with self.assertRaisesRegex(ValueError, 'interrupted append-only'):
            broll.load(self.project, self.asset)

    def test_five_refinements_limit_persists_across_runs_and_providers(self):
        for number in range(6):
            ledger = broll.load(self.project, self.asset)
            broll.record_search(self.project, self.asset, ledger, 'pixabay' if number % 2 else 'pexels',
                                f'phone action {number}', 'Try a more precise action')
        ledger = broll.load(self.project, self.asset)
        before = (self.directory / 'request.md').read_bytes()
        with self.assertRaisesRegex(ValueError, 'threshold may be too high'):
            broll.record_search(self.project, self.asset, ledger, 'pixabay', 'another direction', 'Keep looking')
        self.assertEqual(before, (self.directory / 'request.md').read_bytes())
        self.assertEqual(len([e for e in ledger['events'] if e['kind'] == 'search']), 6)

    def test_refinement_budget_normalizes_terms_and_preflights_batches(self):
        ledger = broll.load(self.project, self.asset)
        broll.record_search(self.project, self.asset, ledger, 'pixabay', 'Phone Down', 'Initial term')
        self.assertEqual(broll.refinement_budget(ledger, ['  phone   down  '])['refinements'], 0)
        with self.assertRaisesRegex(ValueError, 'refinement limit'):
            broll.refinement_budget(ledger, [f'different {i}' for i in range(6)])
        self.revise()
        ledger = broll.load(self.project, self.asset)
        self.assertEqual(broll.refinement_budget(ledger, ['new brief search'])['refinements'], 0)

    def test_exhausted_budget_stops_runner_before_network_or_output_mutation(self):
        for number in range(6):
            ledger = broll.load(self.project, self.asset)
            broll.record_search(self.project, self.asset, ledger, 'pixabay', f'term {number}', 'Refine direction')
        stock = broll.module(Path(broll.__file__).with_name('stock.py'), 'exhausted_stock_test')
        real_module = broll.module
        def modules(path, name):
            return stock if Path(path).name == 'stock.py' else real_module(path, name)
        destination = self.project.parent / (self.project.name + '-budget-output')
        before = (self.directory / 'request.md').read_bytes()
        with patch.object(broll, 'module', side_effect=modules), patch.object(broll, 'require_prerequisites'), \
                patch.object(stock, 'search_to_file') as api:
            with self.assertRaisesRegex(ValueError, 'refinement limit'):
                broll.run_search(self.project, self.asset, 'pixabay', ['one more term'], 'Try again',
                                 self.project.parent / 'unused-cache', destination)
        api.assert_not_called()
        self.assertFalse(destination.exists())
        self.assertEqual(before, (self.directory / 'request.md').read_bytes())

    def test_scoped_initialization_does_not_create_other_asset_folders(self):
        self.fixture.reconcile([self.fixture.suggestion(type='B-roll', brief='Another brief', key='other')])
        self.fixture.edit('Review', 'Keep')
        rows = broll.eligible(self.project)
        other = next(key for key in rows if key != self.asset)
        plan_file = self.project / 'plans.json'
        plan_file.write_text(json.dumps({other: self.plan}))
        argv = ['broll.py', 'init', '--project-root', str(self.project), '--asset', other, '--spec', str(plan_file)]
        before = (self.directory / 'request.md').read_bytes()
        with patch.object(sys, 'argv', argv), patch.object(broll, 'require_prerequisites'):
            broll.main()
        self.assertEqual((self.directory / 'request.md').read_bytes(), before)
        self.assertTrue((broll.folder(self.project, other) / 'request.md').is_file())

    def test_search_runner_records_attempt_before_api_and_stops_after_failure(self):
        cache, output = self.project.parent / (self.project.name + '-cache'), self.project.parent / (self.project.name + '-search')
        self.addCleanup(lambda: shutil.rmtree(cache, ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(output, ignore_errors=True))
        stock = broll.module(Path(broll.__file__).with_name('stock.py'), 'journal_stock_test')
        real_module = broll.module
        def modules(path, name):
            return stock if Path(path).name == 'stock.py' else real_module(path, name)
        observed = []
        def search(provider, query, cache_dir, destination):
            ledger = broll.load(self.project, self.asset)
            observed.append(ledger['events'][-1])
            self.assertEqual(observed[-1]['kind'], 'search')
            self.assertEqual(observed[-1]['query'], query)
            if len(observed) == 2:
                raise stock.StockError('Access denied')
            destination.write_text('{}')
            return {'count': 2, 'result_file': str(destination), 'provider': provider}
        with patch.object(broll, 'module', side_effect=modules), patch.object(broll, 'require_prerequisites'), \
                patch.object(stock, 'search_to_file', side_effect=search):
            with self.assertRaisesRegex(ValueError, 'No further queries'):
                broll.run_search(self.project, self.asset, 'pixabay', ['phone down', 'end call', 'telephone'],
                                 'Try action-focused alternatives', cache, output)
        self.assertEqual(len(observed), 2)
        ledger = broll.load(self.project, self.asset)
        self.assertEqual([e['status'] for e in ledger['events'] if e['kind'] == 'search-result'], ['succeeded', 'failed'])
        self.assertEqual(len(ledger['requests']), 1)

    def test_deduplicate_bytes_and_source_and_manual_origin(self):
        self.candidate(1)
        source = self.project / 'alternate.mp4'
        ledger = broll.load(self.project, self.asset)
        source.write_bytes((self.directory / ledger['candidates'][0]['file']).read_bytes())
        spec = {'origin': 'User', 'source_id': 'new', 'description': 'Same download'}
        with self.assertRaisesRegex(ValueError, 'Duplicate candidate bytes'):
            broll.register(self.project, self.asset, source, spec, ledger)
        source.write_bytes(b'different encoding')
        spec['source_id'] = 'original-1'
        with self.assertRaisesRegex(ValueError, 'Source already registered'):
            broll.register(self.project, self.asset, source, spec, ledger)
        ledger['requests'][-1]['sourcing'] = 'manual'
        stock = {'origin': 'Pexels', 'source_id': '123', 'description': 'Stock people',
                 'source_url': 'https://www.pexels.com/video/123/',
                 'license': {'url': 'https://www.pexels.com/license/', 'text': 'Retained terms', 'checked_at': '2026-10-20'}}
        with self.assertRaisesRegex(ValueError, 'Manual family-footage'):
            broll.register(self.project, self.asset, source, stock, ledger)

    def test_three_per_asset_coverage_and_reassessment(self):
        for number in range(3):
            self.candidate(number, shots=['hands'])
        with self.assertRaisesRegex(ValueError, 'required shots not covered'):
            broll.check(self.project)
        ledger = broll.load(self.project, self.asset)
        candidate = ledger['candidates'][0]
        candidate['assessments'].append(broll.assess(ledger, candidate,
            {'suitable': True, 'reviewed_by': 'reviewer', 'notes': 'Lunch too', 'shots': ['lunch'], 'ranges': [[5, 8]]}))
        self.save(ledger)
        self.assertEqual(broll.check(self.project), {self.asset: 3})
        self.revise()
        with self.assertRaisesRegex(ValueError, '0/3'):
            broll.check(self.project)
        ledger = broll.load(self.project, self.asset)
        for candidate in ledger['candidates']:
            candidate['assessments'].append(broll.assess(ledger, candidate,
                {'suitable': True, 'reviewed_by': 'reviewer', 'notes': 'Still suitable',
                 'shots': ['hands', 'lunch'], 'ranges': [[0, 6]]}))
        self.save(ledger)
        self.assertEqual(broll.check(self.project), {self.asset: 3})
        self.assertEqual(len(broll.load(self.project, self.asset)['candidates'][0]['assessments']), 3)

    def test_in_place_manual_file_and_unregistered_guard(self):
        self.candidate(1, in_place=True)
        self.assertEqual(broll.load(self.project, self.asset)['candidates'][0]['file'], 'provided1.mp4')
        (self.directory / 'new.mp4').write_bytes(b'unregistered')
        with self.assertRaisesRegex(ValueError, 'Unregistered video'):
            broll.inventory(self.project)

    def test_evidence_nested_inventory_retired_history_and_readonly(self):
        for number in range(3):
            self.candidate(number)
        manifest = broll.evidence(self.project)
        definition = validation.load_stages()['2.2']
        # The shared validator dynamically reloads broll.py, so stub ffprobe at
        # its subprocess boundary rather than relying on this module's mock.
        probe_result = SimpleNamespace(stdout=json.dumps({'format': {'duration': '10'},
            'streams': [{'codec_type': 'video', 'width': 1920, 'height': 1080, 'codec_name': 'h264'}]}))
        with patch.object(broll.subprocess, 'run', return_value=probe_result):
            self.assertEqual(validation.validate_evidence(self.project, broll.ROOT, definition, manifest)[0], 'current')
        validator = validation.StageValidation(self.project, broll.ROOT, definition)
        validator.manifest = manifest
        validator.require_inventory('outputs', broll.inventory(self.project), exact=True)
        original_outputs = set(item['path'] for item in manifest['outputs'])
        self.revise()
        self.assertEqual(validation.validate_evidence(self.project, broll.ROOT, definition, manifest)[0], 'stale')
        # An append changes file fingerprints, while the canonical file inventory stays the same.
        validator.require_inventory('outputs', broll.inventory(self.project), exact=True)
        self.assertEqual(original_outputs, {p.relative_to(self.project).as_posix() for p in broll.inventory(self.project)})
        original = self.fixture.path.read_text(encoding='utf-8')
        self.fixture.path.write_text(original.replace('**Review:** Keep', '**Review:** Reject'), encoding='utf-8')
        self.assertEqual(broll.eligible(self.project), {})
        self.assertTrue(original_outputs <= {p.relative_to(self.project).as_posix() for p in broll.inventory(self.project)})
        before = fixture_module.readonly_state(self.project)
        broll.inventory(self.project)
        self.assertEqual(before, fixture_module.readonly_state(self.project))
        with self.assertRaisesRegex(ValueError, 'stage-owned'):
            completion.build_manifest(self.project, broll.ROOT, '2.2')

    def test_path_and_provenance_guards(self):
        for path in ('../escape.mp4', '/absolute', 'C:/elsewhere/file'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                broll.confined(self.project, path)
        with self.assertRaisesRegex(ValueError, 'Invalid ASSET ID'):
            broll.folder(self.project, '../ASSET-000001')
        with self.assertRaisesRegex(ValueError, 'Storyblocks'):
            broll.provenance({'origin': 'Storyblocks'})
        stock = {'origin': 'Pexels', 'source_id': 'x', 'description': 'x', 'source_url': 'https://pexels.com.evil.example/video'}
        with self.assertRaisesRegex(ValueError, 'Provider page URL'):
            broll.provenance(stock)
        self.fixture.path.with_suffix('.json').unlink()
        with self.assertRaisesRegex(ValueError, 'Both 1.4 registry'):
            broll.eligible(self.project)

    def test_shared_record_reuse_and_reopen_preserves_candidates(self):
        for number in range(3):
            self.candidate(number)
        result = SimpleNamespace(stdout=json.dumps({'format': {'duration': '10'},
            'streams': [{'codec_type': 'video', 'width': 1920, 'height': 1080, 'codec_name': 'h264'}]}))
        # This test isolates own-stage publication; prerequisite behavior is
        # separately checked below and in the shared validator suite.
        with patch.object(validation, 'require_prerequisites'), patch.object(broll.subprocess, 'run', return_value=result):
            completion.record_done(self.project, broll.ROOT, '2.2', broll.evidence(self.project))
            marker = self.directory.parent / 'DONE'
            before = marker.stat().st_mtime_ns, marker.read_bytes()
            completion.record_done(self.project, broll.ROOT, '2.2', broll.evidence(self.project))
            self.assertEqual(before, (marker.stat().st_mtime_ns, marker.read_bytes()))
        from scripts.stage_invalidation import invalidate
        preserved = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        invalidate(self.project, broll.ROOT, '2.2', 'User requests more options')
        self.assertFalse(marker.exists())
        self.assertTrue(list((self.directory.parent / 'STALE').glob('*/DONE')))
        self.assertEqual(preserved, {p.name: p.read_bytes() for p in self.directory.iterdir()})

    def test_failed_prerequisites_do_not_change_project(self):
        before = fixture_module.readonly_state(self.project)
        argv = ['broll.py', 'init', '--project-root', str(self.project)]
        with patch.object(sys, 'argv', argv), self.assertRaises(validation.PrerequisiteError):
            broll.main()
        self.assertEqual(before, fixture_module.readonly_state(self.project))

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg smoke test requires existing tools')
    def test_actual_video_probe_and_non_video_rejection(self):
        self.mock_probe.stop()
        video = self.project / 'real.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=64x64:r=5',
                        '-t', '1', '-c:v', 'mpeg4', str(video)], check=True, capture_output=True, timeout=60)
        metadata = broll.probe(video)
        self.assertEqual((metadata['width'], metadata['height']), (64, 64))
        self.assertGreater(metadata['duration'], 0)
        invalid = self.project / 'not-video.mp4'
        invalid.write_bytes(b'<html>download error</html>')
        with self.assertRaisesRegex(ValueError, 'ffprobe could not verify'):
            broll.probe(invalid)


if __name__ == '__main__':
    unittest.main()
