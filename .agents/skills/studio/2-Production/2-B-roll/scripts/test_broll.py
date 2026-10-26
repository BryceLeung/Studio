"""Temporary-project B-roll request, footage and completion regression tests."""
import importlib.util
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier
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


class CreditCorrectionTests(unittest.TestCase):
    def test_credit_correction_preserves_source_and_requires_same_identity(self):
        candidate = {'origin': 'Pixabay', 'source_id': '86866',
                     'source_url': 'https://pixabay.com/videos/id-86866/',
                     'license': {'attribution': 'Wrong author'}, 'sha256': 'unchanged'}
        spec = {key: candidate[key] for key in ('origin', 'source_id', 'source_url')}
        spec['attribution'] = 'Verified author, Pixabay, original page'
        correction = broll.correct_credit(candidate, spec, 'Verified saved provider result')
        self.assertEqual(correction['previous'], 'Wrong author')
        self.assertEqual(candidate['license']['attribution'], 'Wrong author')
        self.assertEqual(candidate['sha256'], 'unchanged')
        for key in ('origin', 'source_id', 'source_url'):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'source identity'):
                broll.correct_credit(candidate, dict(spec, **{key: 'different'}), 'Verified')
        with self.assertRaisesRegex(ValueError, 'requires a reason'):
            broll.correct_credit(candidate, spec, '')


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
            broll.write_request(self.project, asset, {'schema': 'studio.broll.candidates', 'schema_version': 3,
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

    @contextmanager
    def mocked_provider(self, search):
        stock = broll.module(Path(broll.__file__).with_name('stock.py'), 'layout_stock_test')
        real_module = broll.module
        def modules(path, name):
            return stock if Path(path).name == 'stock.py' else real_module(path, name)
        with patch.object(broll, 'module', side_effect=modules), \
                patch.object(broll, 'require_prerequisites'), \
                patch.object(stock, 'search_to_file', side_effect=search):
            yield stock

    def test_current_markdown_human_fields_and_exact_idempotent_v1(self):
        self.assertTrue((self.directory / 'search.01.md').read_text(encoding='utf-8').startswith(self.rows[self.asset]['Brief'] + '\n\n### Worker requirements'))
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

    def test_storyblocks_begin_finish_journals_and_reuses_existing_review_flow(self):
        params = {'keywords': 'elderly hands', 'required_keywords': 'hand', 'content_type': 'footage'}
        with patch.object(broll, 'require_prerequisites'):
            begun = broll.storyblocks_bridge(self.project, self.asset, 'storyblocks-begin',
                         query=params['keywords'], reason='Find supportive hand contact', parameters=params)
            self.assertEqual(begun['arguments'], params)
            with self.assertRaisesRegex(ValueError, 'pending'):
                broll.storyblocks_bridge(self.project, self.asset, 'storyblocks-begin',
                    query=params['keywords'], reason='Retry', parameters=params)
            response = self.project / 'mcp-response.json'
            response.write_text(json.dumps({'tool': begun['tool'], 'arguments': params,
                'response': {'results': [{'id': 123, 'contentClass': 'video', 'duration': 8,
                    'details_url': 'https://www.storyblocks.com/video/stock/hands-123',
                    'thumbnail_url': 'https://dm0qx8t0i9gc9.cloudfront.net/thumbnails/123.jpg'}]}}))
            finished = broll.storyblocks_bridge(self.project, self.asset, 'storyblocks-finish',
                           search_id=begun['search_id'], response_file=response)
            self.assertEqual(finished['count'], 1)
            ledger = broll.load(self.project, self.asset)
            broll.search_note(self.project, self.asset, ledger, begun['search_id'], 'Review pending')
            self.assertEqual(broll.load(self.project, self.asset)['events'][-1]['kind'], 'search-note')
            with self.assertRaisesRegex(ValueError, 'already recorded'):
                broll.storyblocks_bridge(self.project, self.asset, 'storyblocks-finish',
                    search_id=begun['search_id'], response_file=response)

    def test_storyblocks_failure_and_explicit_retry_do_not_reset_budget(self):
        ledger = broll.load(self.project, self.asset)
        for i in range(6):
            sid = broll.record_search(self.project, self.asset, ledger, 'pixabay', f'query {i}', 'Search fixture')
            broll.search_outcome(self.project, self.asset, ledger, sid, 'failed')
        old = (self.directory / 'search.01.md').read_bytes()
        params = {'keywords': 'older hands', 'required_keywords': 'hand'}
        with patch.object(broll, 'require_prerequisites'):
            with self.assertRaisesRegex(ValueError, 'limit reached'):
                broll.storyblocks_bridge(self.project, self.asset, 'storyblocks-begin',
                    query=params['keywords'], reason='Retry', parameters=params)
            begun = broll.storyblocks_bridge(self.project, self.asset, 'storyblocks-begin',
                query=params['keywords'], reason='Try the authorized provider', parameters=params,
                authorization='User explicitly requested one Storyblocks retry')
            broll.storyblocks_bridge(self.project, self.asset, 'storyblocks-finish', search_id=begun['search_id'], failed=True)
        ledger = broll.load(self.project, self.asset)
        self.assertEqual(len(ledger['requests']), 1)
        self.assertTrue((self.directory / 'search.01.md').read_bytes().startswith(old))
        self.assertEqual(ledger['events'][-1]['status'], 'failed')
        with self.assertRaisesRegex(ValueError, 'limit reached'):
            broll.refinement_budget(ledger, ['another term'])

    def test_storyblocks_registration_requires_licensed_bytes(self):
        source = self.project / 'licensed.mp4'
        source.write_bytes(b'licensed footage')
        ledger = broll.load(self.project, self.asset)
        spec = {'origin': 'Storyblocks', 'source_id': '123', 'description': 'Inspected older hands',
                'source_url': 'https://www.storyblocks.com/video/stock/hands-123',
                'license': {'url': 'https://www.storyblocks.com/video/stock/hands-123',
                            'text': 'Captured MCP licensed acquisition', 'checked_at': '2026-10-25T00:00:00Z'}}
        with self.assertRaisesRegex(ValueError, 'licensed acquisition'):
            broll.register(self.project, self.asset, source, spec, ledger)
        spec['license']['acquisition'] = {'tool': 'mcp__storyblocks__get_download_url', 'stock_item_id': '123',
                'authorization': 'User requested this clip', 'sha256': broll.sha(source), 'response_sha256': 'a' * 64}
        destination, candidate = broll.register(self.project, self.asset, source, spec, ledger)
        shutil.copyfile(source, destination)
        candidate['identity'] = completion.fingerprint(destination, self.project, 'project')
        ledger['candidates'].append(candidate)
        self.save(ledger)
        self.assertEqual(broll.load(self.project, self.asset)['candidates'][0]['origin'], 'Storyblocks')
        other = self.project / 'other.mp4'
        other.write_bytes(b'not the licensed bytes')
        with self.assertRaisesRegex(ValueError, 'receipt does not match'):
            broll.register(self.project, self.asset, other, spec, ledger)
        candidate['assessments'].append(broll.assess(ledger, candidate, {
            'suitable': True, 'reviewed_by': 'Fixture reviewer', 'notes': 'Full-resolution inspected',
            'ranges': [[1, 5]], 'shots': ['hands', 'lunch']}))
        self.save(ledger)
        self.candidate(20)
        self.candidate(21)
        self.assertEqual(broll.check(self.project), {self.asset: 3})

    def test_revisions_create_numbered_journals_and_preserve_previous_bytes(self):
        original = (self.directory / 'search.01.md').read_bytes()
        for _ in range(9):
            self.revise()
        ledger = broll.load(self.project, self.asset)
        self.assertEqual(ledger['requests'][-1]['version'], 10)
        self.assertEqual(ledger['requests'][-1]['file'], 'search.10.md')
        self.assertEqual((self.directory / 'search.01.md').read_bytes(), original)
        expected = {f'search.{version:02d}.md' for version in range(1, 11)}
        self.assertEqual(set(ledger['journals']), expected)
        self.assertEqual({path.name for path in self.directory.glob('search.*.md')}, expected)
        self.assertFalse((self.directory / 'Requirements.md').exists())
        for version, request in enumerate(ledger['requests'], 1):
            self.assertEqual(request['file'], f'search.{version:02d}.md')
            events = [event for event in ledger['events'] if event['file'] == request['file']]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['offset'], 0)
        (self.directory / 'search.01.md').write_text('edited behind history', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'append-only request journal'):
            self.revise()
        self.assertFalse((self.directory / 'search.11.md').exists())

    def test_untracked_request_refused(self):
        (self.directory / 'search.02.md').write_text('ambiguous', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            broll.load(self.project, self.asset)

    def test_search_terms_outcomes_and_reviews_append_without_changing_brief_identity(self):
        for number in range(3):
            self.candidate(number)
        ledger = broll.load(self.project, self.asset)
        request = dict(ledger['requests'][-1])
        original = (self.directory / 'search.01.md').read_bytes()
        search_id = broll.record_search(self.project, self.asset, ledger, 'pixabay', 'hang up phone', 'Typing clips missed the requested action')
        broll.search_outcome(self.project, self.asset, ledger, search_id, 'succeeded', count=4, result_file='scratch/search-1.json')
        broll.search_note(self.project, self.asset, ledger, search_id, 'Rejected four typing shots; next try ending a call.')
        ledger = broll.load(self.project, self.asset)
        self.assertEqual(ledger['requests'], [request])
        self.assertTrue((self.directory / 'search.01.md').read_bytes().startswith(original))
        content = (self.directory / 'search.01.md').read_text(encoding='utf-8')
        self.assertIn('hang up phone', content)
        self.assertIn('Results returned: 4', content)
        self.assertIn('Rejected four typing shots', content)
        self.assertEqual(broll.check(self.project), {self.asset: 3})
        with self.assertRaisesRegex(ValueError, 'already recorded'):
            broll.search_outcome(self.project, self.asset, ledger, search_id, 'failed')

    def test_search_journal_indented_scores_preserve_brief_and_review_history(self):
        ledger = broll.load(self.project, self.asset)
        request = dict(ledger['requests'][-1])
        sid = broll.record_search(self.project, self.asset, ledger, 'pexels', 'family lunch', 'Initial action search')
        broll.search_outcome(self.project, self.asset, ledger, sid, 'succeeded', count=2, result_file='scratch/results.json')
        before = (self.directory / 'search.01.md').read_bytes()
        review = {'file': 'Result 1.mp4', 'source_id': '123', 'stage': 'contact-sheet',
                  'ratings': {'subject_action': 4, 'context_tone': 3, 'editing_usefulness': 4, 'technical_quality': 5},
                  'evidence': 'Inspected samples at 0, 2, 4 seconds', 'decision': 'Closer review'}
        broll.search_note(self.project, self.asset, ledger, sid, 'Inspect action continuity next.', [review])
        content = (self.directory / 'search.01.md').read_text()
        self.assertIn('family lunch\n\tResult 1.mp4: 77/100 (contact-sheet; Closer review)', content)
        self.assertTrue((self.directory / 'search.01.md').read_bytes().startswith(before))
        loaded = broll.load(self.project, self.asset)
        self.assertEqual(loaded['requests'][-1], request)
        self.assertEqual(loaded['events'][-1]['reviews'][0]['score'], 77)
        self.assertEqual(loaded['events'][-1]['reviews'][0]['weights'], broll.SCORE_WEIGHTS)
        review['stage'] = 'closer-review'
        review['ratings']['subject_action'] = 2
        review['decision'] = 'Reject: wrong action'
        broll.search_note(self.project, self.asset, loaded, sid, 'Next try packing lunch.', [review])
        content = (self.directory / 'search.01.md').read_text()
        self.assertIn('Result 1.mp4: 61/100 (closer-review;', content)
        self.assertIn('Result 1.mp4: 77/100 (contact-sheet;', content)
        self.assertIn(self.directory / 'search.01.md', broll.inventory(self.project))

    def test_unknown_and_invalid_search_scores(self):
        review = {'file': 'Result 2.mov', 'source_id': '456', 'stage': 'thumbnail',
                  'ratings': {key: None for key in broll.SCORE_WEIGHTS},
                  'evidence': 'Thumbnail only; technical quality unknown', 'decision': 'Inspect preview'}
        ledger = broll.load(self.project, self.asset)
        sid = broll.record_search(self.project, self.asset, ledger, 'pexels', 'family lunch', 'Initial search')
        broll.search_note(self.project, self.asset, ledger, sid, 'Need a contact sheet.', [review])
        self.assertIn('Result 2.mov: Pending (thumbnail;', (self.directory / 'search.01.md').read_text())
        for bad in ({'score': 90}, {'ratings': dict.fromkeys(broll.SCORE_WEIGHTS, True)},
                    {'ratings': dict.fromkeys(broll.SCORE_WEIGHTS, 6)},
                    {'weights': dict.fromkeys(broll.SCORE_WEIGHTS, 10)}, {'file': '../escape.mp4'}):
            before = (self.directory / 'search.01.md').read_bytes()
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                broll.search_note(self.project, self.asset, ledger, sid, 'Invalid review', [dict(review, **bad)])
            self.assertEqual(before, (self.directory / 'search.01.md').read_bytes())

    def test_scored_search_note_cli_and_superseded_brief(self):
        ledger = broll.load(self.project, self.asset)
        sid = broll.record_search(self.project, self.asset, ledger, 'pexels', 'family lunch', 'Initial search')
        spec = self.project / 'review.json'
        spec.write_text(json.dumps({'reviews': [{'file': 'Result.mp4', 'source_id': '789',
            'stage': 'closer-review', 'ratings': dict.fromkeys(broll.SCORE_WEIGHTS, 4),
            'evidence': 'Reviewed denser samples across 1–4 seconds', 'decision': 'Acquire'}]}))
        argv = ['broll.py', 'search-note', '--project-root', str(self.project), '--asset', self.asset,
                '--search-id', str(sid), '--reason', 'Matches the requested action.', '--spec', str(spec)]
        with patch.object(sys, 'argv', argv), patch.object(broll, 'require_prerequisites'):
            broll.main()
        self.assertIn('Result.mp4: 80/100 (closer-review; Acquire)', (self.directory / 'search.01.md').read_text())
        self.revise()
        ledger = broll.load(self.project, self.asset)
        before = (self.directory / 'search.01.md').read_bytes()
        with self.assertRaisesRegex(ValueError, 'superseded'):
            broll.search_note(self.project, self.asset, ledger, sid, 'Wrong brief')
        self.assertEqual(before, (self.directory / 'search.01.md').read_bytes())

    def test_untracked_journal_append_and_interrupted_publication_fail_closed(self):
        path = self.directory / 'search.01.md'
        with path.open('ab') as stream:
            stream.write(b'\nUnrecorded text')
        with self.assertRaisesRegex(ValueError, 'append-only request journal'):
            broll.load(self.project, self.asset)

    def test_interrupted_ledger_publication_preserves_append_and_is_detected(self):
        ledger = broll.load(self.project, self.asset)
        before = (self.directory / 'search.01.md').read_bytes()
        publish = broll.atomic_json
        def interrupted_publish(path, data):
            if path.name == 'Candidates.json':
                raise OSError('Interrupted save')
            return publish(path, data)
        with patch.object(broll, 'atomic_json', side_effect=interrupted_publish):
            with self.assertRaises(OSError):
                broll.record_search(self.project, self.asset, ledger, 'pixabay', 'hang up phone', 'Refine action terms')
        self.assertTrue((self.directory / 'search.01.md').read_bytes().startswith(before))
        self.assertIn(b'hang up phone', (self.directory / 'search.01.md').read_bytes())
        with self.assertRaisesRegex(ValueError, 'interrupted append-only'):
            broll.load(self.project, self.asset)

    def test_five_refinements_limit_persists_across_runs_and_providers(self):
        for number in range(6):
            ledger = broll.load(self.project, self.asset)
            broll.record_search(self.project, self.asset, ledger, 'pixabay' if number % 2 else 'pexels',
                                f'phone action {number}', 'Try a more precise action')
        ledger = broll.load(self.project, self.asset)
        before = (self.directory / 'search.01.md').read_bytes()
        with self.assertRaisesRegex(ValueError, 'threshold may be too high'):
            broll.record_search(self.project, self.asset, ledger, 'pixabay', 'another direction', 'Keep looking')
        self.assertEqual(before, (self.directory / 'search.01.md').read_bytes())
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
        destination = self.directory / 'search.01'
        before_state = fixture_module.readonly_state(self.project)
        before = (self.directory / 'search.01.md').read_bytes()
        with patch.object(broll, 'module', side_effect=modules), patch.object(broll, 'require_prerequisites'), \
                patch.object(stock, 'search_to_file') as api:
            with self.assertRaisesRegex(ValueError, 'refinement limit'):
                broll.run_search(self.project, self.asset, 'pixabay', ['one more term'], 'Try again',
                                 cache_dir=None, output_dir=None)
        api.assert_not_called()
        self.assertEqual(before_state, fixture_module.readonly_state(self.project))
        self.assertFalse((destination / '07').exists())
        self.assertEqual(before, (self.directory / 'search.01.md').read_bytes())

    def test_scoped_initialization_does_not_create_other_asset_folders(self):
        self.fixture.reconcile([self.fixture.suggestion(type='B-roll', brief='Another brief', key='other')])
        self.fixture.edit('Review', 'Keep')
        rows = broll.eligible(self.project)
        other = next(key for key in rows if key != self.asset)
        plan_file = self.project / 'plans.json'
        plan_file.write_text(json.dumps({other: self.plan}))
        argv = ['broll.py', 'init', '--project-root', str(self.project), '--asset', other, '--spec', str(plan_file)]
        before = (self.directory / 'search.01.md').read_bytes()
        with patch.object(sys, 'argv', argv), patch.object(broll, 'require_prerequisites'):
            broll.main()
        self.assertEqual((self.directory / 'search.01.md').read_bytes(), before)
        self.assertTrue((broll.folder(self.project, other) / 'search.01.md').is_file())

    def test_search_runner_records_attempt_before_api_and_stops_after_failure(self):
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
                                 'Try action-focused alternatives')
        self.assertEqual(len(observed), 2)
        ledger = broll.load(self.project, self.asset)
        self.assertEqual([e['status'] for e in ledger['events'] if e['kind'] == 'search-result'], ['succeeded', 'failed'])
        self.assertEqual(len(ledger['requests']), 1)

    def test_default_runner_allocates_attempts_across_calls_and_revisions(self):
        destinations = []
        def search(provider, query, cache_dir, destination):
            self.assertEqual(cache_dir, self.directory / 'cache')
            destinations.append(destination)
            destination.write_text(json.dumps({'provider': provider, 'query': query, 'results': []}))
            return {'count': 0, 'result_file': str(destination), 'provider': provider}
        with self.mocked_provider(search):
            first = broll.run_search(self.project, self.asset, 'pixabay', ['phone down', 'end call'], 'Initial search')
            retry = broll.run_search(self.project, self.asset, 'pexels', ['  Phone   Down  '], 'Try another provider')
            original = (self.directory / 'search.01.md').read_bytes()
            self.revise()
            latest = broll.run_search(self.project, self.asset, 'pixabay', ['phone down'], 'New brief search')
        expected = [self.directory / 'search.01' / f'{number:02d}' / 'results.json' for number in (1, 2, 3)]
        expected.append(self.directory / 'search.02' / '01' / 'results.json')
        self.assertEqual(destinations, expected)
        self.assertEqual([result['output_dir'] for result in first['searches']], [str(path.parent) for path in expected[:2]])
        self.assertEqual(retry['searches'][0]['output_dir'], str(expected[2].parent))
        self.assertEqual(latest['request_journal'], str(self.directory / 'search.02.md'))
        self.assertEqual(first['refinements_used'], 1)
        self.assertEqual(retry['refinements_used'], 1)
        self.assertEqual(latest['refinements_used'], 0)
        self.assertEqual((self.directory / 'search.01.md').read_bytes(), original)
        ledger = broll.load(self.project, self.asset)
        attempts = [event for event in ledger['events'] if event['kind'] == 'search']
        self.assertEqual([event['search_id'] for event in attempts], [1, 2, 3, 4])
        self.assertEqual([event['attempt'] for event in attempts], [1, 2, 3, 1])
        self.assertEqual([event['directory'] for event in attempts], ['search.01/01', 'search.01/02', 'search.01/03', 'search.02/01'])
        self.assertTrue(all(path.is_file() for path in expected))
        self.assertTrue(all((path.parent / 'attempt.json').is_file() for path in expected))
        self.assertTrue(all((path.parent / 'outcome.json').is_file() for path in expected))

    def test_noncanonical_output_and_cache_overrides_fail_before_writing(self):
        canonical = self.directory / 'search.01' / '01'
        overrides = [dict(output_dir=self.project.parent / 'external-search-output'),
                     dict(cache_dir=self.project.parent / 'external-search-cache'),
                     dict(output_dir=self.directory / 'search.01' / '02'),
                     dict(output_dir=canonical, cache_dir=self.directory / 'other-cache')]
        before = fixture_module.readonly_state(self.project)
        with self.mocked_provider(lambda *args: self.fail('Invalid paths must not call provider')):
            for options in overrides:
                with self.subTest(options=options), self.assertRaises(ValueError):
                    broll.run_search(self.project, self.asset, 'pixabay', ['phone down'], 'Initial search', **options)
                self.assertEqual(before, fixture_module.readonly_state(self.project))
            with self.assertRaises(ValueError):
                broll.run_search(self.project, self.asset, 'pixabay', ['phone down', 'end call'],
                                 'Initial search', output_dir=canonical)
            self.assertEqual(before, fixture_module.readonly_state(self.project))

    def test_empty_results_and_failures_preserve_project_attempts(self):
        destinations = []
        def search(provider, query, cache_dir, destination):
            destinations.append(destination)
            if len(destinations) == 2:
                raise stock.StockError('Access denied')
            destination.write_text(json.dumps({'provider': provider, 'query': query, 'results': []}))
            return {'count': 0, 'result_file': str(destination), 'provider': provider}
        with self.mocked_provider(search) as stock:
            empty = broll.run_search(self.project, self.asset, 'pixabay', ['phone down'], 'Initial search')
            with self.assertRaisesRegex(ValueError, 'No further queries'):
                broll.run_search(self.project, self.asset, 'pixabay', ['end call', 'telephone'], 'Refine action')
        self.assertEqual(empty['searches'][0]['count'], 0)
        self.assertEqual(destinations, [self.directory / 'search.01' / f'{number:02d}' / 'results.json' for number in (1, 2)])
        self.assertTrue(destinations[0].is_file())
        self.assertTrue(destinations[1].parent.is_dir())
        self.assertFalse(destinations[1].exists())
        for destination, status in zip(destinations, ('succeeded', 'failed')):
            self.assertTrue((destination.parent / 'attempt.json').is_file())
            self.assertEqual(json.loads((destination.parent / 'outcome.json').read_text())['status'], status)
        self.assertFalse((self.directory / 'search.01' / '03').exists())
        ledger = broll.load(self.project, self.asset)
        outcomes = [event for event in ledger['events'] if event['kind'] == 'search-result']
        self.assertEqual([event['status'] for event in outcomes], ['succeeded', 'failed'])
        self.assertEqual([event['file'] for event in outcomes], ['search.01.md', 'search.01.md'])
        self.assertIn('end call', (self.directory / 'search.01.md').read_text())

    def test_nested_preview_artifacts_are_watched_without_counting_as_candidates(self):
        for number in range(3):
            self.candidate(number)
        ledger = broll.load(self.project, self.asset)
        broll.record_search(self.project, self.asset, ledger, 'pixabay', 'phone down', 'Search for alternatives')
        attempt = self.directory / 'search.01' / '01'
        artifacts = [attempt / 'results.json', attempt / 'preview.mp4', attempt / 'frames' / 'frame-001.jpg', attempt / 'frames' / 'sheet.json']
        for artifact in artifacts:
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(b'inspection evidence')
        cache = self.directory / 'cache' / 'pixabay' / 'query.json'
        cache.parent.mkdir(parents=True)
        cache.write_text('cached response')
        inventory = broll.inventory(self.project)
        self.assertTrue(set(artifacts) <= set(inventory))
        self.assertNotIn(cache, inventory)
        self.assertEqual(broll.check(self.project), {self.asset: 3})
        manifest = broll.evidence(self.project)
        definition = validation.load_stages()['2.2']
        extra = attempt / 'frames' / 'frame-002.jpg'
        extra.write_bytes(b'new inspected sample')
        self.assertIn(extra, broll.inventory(self.project))
        self.assertEqual(validation.validate_evidence(self.project, broll.ROOT, definition, manifest)[0], 'stale')
        extra.unlink()
        probe_result = SimpleNamespace(stdout=json.dumps({'format': {'duration': '10'},
            'streams': [{'codec_type': 'video', 'width': 1920, 'height': 1080, 'codec_name': 'h264'}]}))
        cache.write_text('updated cached response')
        with patch.object(broll.subprocess, 'run', return_value=probe_result):
            self.assertEqual(validation.validate_evidence(self.project, broll.ROOT, definition, manifest)[0], 'current')

    def test_concurrent_assets_get_isolated_project_output_paths(self):
        self.fixture.reconcile([self.fixture.suggestion(type='B-roll', brief=self.rows[self.asset]['Brief']),
                                self.fixture.suggestion(type='B-roll', brief='Another brief', key='other')])
        self.fixture.edit('Review', 'Keep')
        rows = broll.eligible(self.project)
        other = next(key for key in rows if key != self.asset)
        for asset, row, entry in broll.initialize(self.project, {other: self.plan}, {other: rows[other]}):
            broll.write_request(self.project, asset, {'schema': 'studio.broll.candidates', 'schema_version': 3,
                                'asset': asset, 'requests': [], 'candidates': []}, entry, row['Brief'])
        together = Barrier(2)
        destinations = []
        def search(provider, query, cache_dir, destination):
            together.wait(timeout=10)
            destinations.append(destination)
            destination.write_text(json.dumps({'query': query}))
            return {'count': 0, 'result_file': str(destination), 'provider': provider}
        with self.mocked_provider(search), ThreadPoolExecutor(max_workers=2) as pool:
            pending = [pool.submit(broll.run_search, self.project, asset, 'pixabay', ['phone down'], 'Independent asset search')
                       for asset in (self.asset, other)]
            results = [task.result(timeout=30) for task in pending]
        expected = {broll.folder(self.project, asset) / 'search.01' / '01' / 'results.json' for asset in (self.asset, other)}
        self.assertEqual(set(destinations), expected)
        self.assertEqual({result['asset'] for result in results}, {self.asset, other})
        for asset in (self.asset, other):
            ledger = broll.load(self.project, asset)
            self.assertEqual([event['search_id'] for event in ledger['events'] if event['kind'] == 'search'], [1])
            self.assertTrue((broll.folder(self.project, asset) / 'search.01' / '01' / 'results.json').is_file())

    def test_each_run_journal_digest_and_event_file_are_validated(self):
        self.revise()
        ledger = broll.load(self.project, self.asset)
        self.assertEqual(set(ledger['journals']), {'search.01.md', 'search.02.md'})
        original = (self.directory / 'search.01.md').read_bytes()
        (self.directory / 'search.01.md').write_bytes(original + b'\nUntracked historical content')
        with self.assertRaisesRegex(ValueError, 'append-only request journal'):
            broll.load(self.project, self.asset)
        (self.directory / 'search.01.md').write_bytes(original)
        broll.load(self.project, self.asset)
        ledger['events'][-1]['file'] = 'search.01.md'
        self.save(ledger)
        with self.assertRaises(ValueError):
            broll.load(self.project, self.asset)

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
        revised_outputs = {p.relative_to(self.project).as_posix() for p in broll.inventory(self.project)}
        self.assertEqual(revised_outputs - original_outputs, {(self.directory / 'search.02.md').relative_to(self.project).as_posix()})
        with self.assertRaises(ValueError):
            validator.require_inventory('outputs', broll.inventory(self.project), exact=True)
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
        with self.assertRaisesRegex(ValueError, 'Unsupported origin'):
            broll.provenance({'origin': 'Unknown'})
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
        preserved = {p.relative_to(self.directory).as_posix(): p.read_bytes() for p in self.directory.rglob('*') if p.is_file()}
        invalidate(self.project, broll.ROOT, '2.2', 'User requests more options')
        self.assertFalse(marker.exists())
        self.assertTrue(list((self.directory.parent / 'STALE').glob('*/DONE')))
        self.assertEqual(preserved, {p.relative_to(self.directory).as_posix(): p.read_bytes() for p in self.directory.rglob('*') if p.is_file()})

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
