"""Synthetic-project lifecycle tests; Node rendering and probing are mocked."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


a = load_module('studio_animation_tests', Path(__file__).with_name('animations.py'))
fixtures = load_module('studio_animation_registry_fixture', a.ROOT /
                       '.agents/skills/studio/1-Pre Production/4-Asset Breakdown/scripts/test_assets.py')
from scripts import validation, stage_completion as completion


class AnimationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AssetTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.project = self.fixture.project
        fixtures.docx(self.fixture.script, comment=False)
        self.fixture.reconcile([self.fixture.suggestion(type='Graphic')])
        self.fixture.edit('Review', 'Keep')
        self.asset = next(iter(a.eligible(self.project)))
        self.directory = a.folder(self.project, self.asset)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workflow = Path(self.temporary.name).resolve()
        original = a.ROOT
        required = [a.LOCAL / 'SKILL.md', a.LOCAL / 'dependencies.json',
                    a.LOCAL / 'scripts/animations.py', a.LOCAL / 'scripts/validation.py',
                    Path('scripts/validation.py'), Path('scripts/stage_completion.py'),
                    Path('scripts/append-log.py'),
                    Path('scripts/stage_invalidation.py'), Path('studio/fingerprints.py'),
                    Path('.agents/skills/studio/1-Pre Production/4-Asset Breakdown/scripts/assets.py')]
        required.extend(Path(name) for name in json.loads((original / a.LOCAL / 'dependencies.json').read_text())['workflow_dependencies'])
        for relative in required:
            target = self.workflow / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            source = original / relative
            if source.is_file():
                shutil.copyfile(source, target)
            else:
                target.write_text('Synthetic test workflow\n', encoding='utf-8')
        prerequisite = self.workflow / '.agents/skills/studio/1-Pre Production/4-Asset Breakdown/dependencies.json'
        prerequisite.write_text(json.dumps({'schema': 'studio.stage', 'schema_version': 1,
            'step': '1.4', 'folder': a.REGISTRY.as_posix(), 'prerequisites': [],
            'inputs': [], 'outputs': []}), encoding='utf-8')
        renderer = self.workflow / a.LOCAL / 'renderer'
        (renderer / 'template').mkdir(parents=True, exist_ok=True)
        for name in ('render.mjs', 'studio.mjs', 'probe.mjs', 'settings.mjs', 'package.json', 'package-lock.json'):
            (renderer / name).write_text('{}', encoding='utf-8')
        (renderer / 'template/Composition.tsx').write_text('export default () => null;\n')
        for name in ('Studio.tsx', 'tsconfig.json'):
            shutil.copyfile(original / a.LOCAL / 'renderer/template' / name, renderer / 'template' / name)
        self.start_patch(a, 'ROOT', self.workflow)
        self.start_patch(a, 'RENDERER', renderer)
        self.prerequisites = self.start_patch(a, 'require_prerequisites')
        self.start_patch(validation, 'require_prerequisites')
        self.real_transition = a.transition
        self.real_renderer = a.run_renderer
        self.real_inspector = a.inspect_settings
        self.inspector = self.start_patch(a, 'inspect_settings', return_value=dict(a.DEFAULT_SETTINGS))
        self.start_patch(a, 'log')
        self.transition = self.start_patch(a, 'transition')
        self.start_patch(subprocess, 'run', side_effect=self.external)
        self.render_count = 0
        self.start_patch(a, 'run_renderer', side_effect=self.fake_render)
        a.init(self.project, self.asset)

    def start_patch(self, target, name, *args, **kwargs):
        patcher = patch.object(target, name, *args, **kwargs)
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def external(self, command, **kwargs):
        self.assertEqual(Path(command[1]).name, 'probe.mjs')
        return subprocess.CompletedProcess(command, 0, json.dumps(
            {'width': 1920, 'height': 1080, 'fps': 30, 'durationInSeconds': 5}), '')

    def fake_render(self, snapshot, output):
        self.render_count += 1
        output.write_bytes(f'fake mp4 candidate {self.render_count}'.encode())
        return {'output': str(output), 'success': True}

    def approve(self, name='Human renamed approved video.mp4', *, move=True):
        rendered = a.render(self.project, self.asset)
        source = self.project / rendered['candidate']
        target = self.directory / 'APPROVED' / name
        if move:
            source.rename(target)
        else:
            shutil.copyfile(source, target)
        return target

    def save(self, ledger):
        a.atomic_json(self.directory / 'Animation.json', ledger)

    def validate(self, manifest):
        definition = completion.load_definition(self.workflow / a.LOCAL / 'dependencies.json')
        return validation.validate_evidence(self.project, self.workflow, definition, manifest)

    def state(self):
        return {p.relative_to(self.project).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.project.rglob('*') if p.is_file()}

    def test_selection_uses_canonical_pair_and_exact_controls(self):
        self.assertEqual(len(a.eligible(self.project)), 1)
        for field, value in (('Type', 'Animation'), ('Type', 'B-roll'),
                             ('Review', 'Reject'), ('Active', 'No')):
            registry = fixtures.assets.load(self.project)
            saved = self.fixture.path.read_bytes(), self.fixture.path.with_suffix('.json').read_bytes()
            registry['assets'][0][field] = value
            fixtures.assets.save(registry, self.project)
            self.assertEqual(bool(a.eligible(self.project)), value == 'Animation')
            self.fixture.path.write_bytes(saved[0])
            self.fixture.path.with_suffix('.json').write_bytes(saved[1])
        self.fixture.path.with_suffix('.json').unlink()
        with self.assertRaisesRegex(ValueError, 'Both canonical'):
            a.eligible(self.project)

    def test_init_preserves_approved_context_and_never_approves_template(self):
        ledger = a.load(self.project, self.asset)
        request = (self.directory / 'request.01.md').read_text()
        self.assertIn('Comparison diagram', request)
        self.assertIn('One two', request)
        self.assertIn('Clarifies the contrast', request)
        self.assertEqual(ledger['requests'][0]['registry'], a.eligible(self.project)[self.asset])
        self.assertEqual(list((self.directory / 'APPROVED').iterdir()), [])
        self.assertTrue((self.directory / 'src/Composition.tsx').is_file())
        self.assertTrue((self.directory / 'references').is_dir())
        self.assertTrue(ledger['studio_ready'])
        self.assertTrue((self.directory / 'src/Studio.tsx').is_file())
        self.assertTrue(a.read_json(self.directory / 'package.json')['private'])
        self.assertEqual(a.read_json(self.directory / 'tsconfig.json')['include'], ['src/**/*'])
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            a.check(self.project)
        before = self.state()
        with patch.object(a, 'audit', return_value={a.STEP: {'status': 'missing'}}):
            self.assertEqual(a.init(self.project, self.asset)['status'], 'reused')
        self.assertEqual(before, self.state())

    def test_init_recovers_only_pristine_scaffold_before_request_publication(self):
        (self.directory / 'Animation.json').unlink()
        (self.directory / 'request.01.md').unlink()
        source = self.directory / 'src/Composition.tsx'
        before = source.read_bytes(), source.stat().st_mtime_ns
        self.assertEqual(a.init(self.project, self.asset)['status'], 'initialized')
        self.assertEqual(before, (source.read_bytes(), source.stat().st_mtime_ns))
        self.assertEqual(a.load(self.project, self.asset)['renders'], [])

    def test_init_recovery_refuses_edited_or_unregistered_work(self):
        (self.directory / 'Animation.json').unlink()
        (self.directory / 'request.01.md').unlink()
        source = self.directory / 'src/Composition.tsx'
        original = source.read_bytes()
        source.write_text('Human authored composition')
        with self.assertRaisesRegex(ValueError, 'edited or unexpected'):
            a.init(self.project, self.asset)
        self.assertEqual(source.read_text(), 'Human authored composition')
        source.write_bytes(original)
        (self.directory / 'references/human.txt').write_text('Human reference')
        with self.assertRaisesRegex(ValueError, 'edited or unexpected'):
            a.init(self.project, self.asset)

    def test_first_log_entry_creates_log_and_appends_without_rewriting(self):
        logger = load_module('studio_animation_real_logger', Path(__file__).with_name('animations.py'))
        log_path = self.project / a.STAGE / 'LOG.md'
        with patch.object(logger, 'ROOT', self.workflow):
            logger.log(self.project, 'First', 'First entry')
            first = log_path.read_bytes()
            logger.log(self.project, 'Second', 'Second entry')
        self.assertTrue(log_path.read_bytes().startswith(first))
        self.assertIn(b'Second entry', log_path.read_bytes())

    def test_studio_launch_preserves_existing_work_and_releases_mutation_lock(self):
        (self.directory / 'src/Composition.tsx').write_text('Human authored artwork')
        before = self.state()
        def launch(command, **kwargs):
            self.assertEqual(command, ['node', str(a.RENDERER / 'studio.mjs'), '--source-dir',
                str(self.directory / 'src'), '--public-dir', str(self.directory / 'references'),
                '--port', '3033', '--no-open'])
            with completion.mutation_guard(self.project):
                pass
            return subprocess.CompletedProcess(command, 0)
        with patch.object(subprocess, 'run', side_effect=launch):
            a.studio(self.project, self.asset, 3033, True)
        self.assertEqual(before, self.state())

    def test_current_studio_contract_requires_its_entry_and_configuration(self):
        (self.directory / 'src/Studio.tsx').unlink()
        before = self.state()
        for action in (lambda: a.render(self.project, self.asset), lambda: a.studio(self.project, self.asset)):
            with self.assertRaisesRegex(ValueError, 'missing required Studio'):
                action()
        self.assertEqual(before, self.state())

    def test_studio_metadata_is_captured_before_candidate_snapshot(self):
        value = dict(width=1280, height=720, fps=24, durationInFrames=240)
        metadata = dict(width=1280, height=720, fps=24, durationInSeconds=10)
        with patch.object(a, 'inspect_settings', return_value=value), patch.object(a, 'probe', return_value=metadata):
            a.render(self.project, self.asset)
        self.assertEqual(a.read_json(self.directory / 'settings.json'), value)
        snapshot = self.directory / 'snapshots/v001'
        self.assertEqual(a.read_json(snapshot / 'settings.json'), value)
        self.assertEqual((snapshot / 'src/Studio.tsx').read_bytes(), (self.directory / 'src/Studio.tsx').read_bytes())
        self.assertEqual((snapshot / 'package.json').read_bytes(), (self.directory / 'package.json').read_bytes())

    def test_metadata_race_never_registers_a_render_or_rewrites_settings(self):
        original = (self.directory / 'settings.json').read_bytes()
        def editing(directory):
            (directory / 'src/Studio.tsx').write_text('Changed while inspecting')
            return dict(a.DEFAULT_SETTINGS, durationInFrames=300)
        with patch.object(a, 'inspect_settings', side_effect=editing), self.assertRaisesRegex(ValueError, 'changed during Studio metadata'):
            a.render(self.project, self.asset)
        self.assertEqual((self.directory / 'settings.json').read_bytes(), original)
        self.assertEqual(a.load(self.project, self.asset)['renders'], [])

    def test_current_render_reuse_does_not_start_metadata_browser(self):
        a.render(self.project, self.asset)
        self.inspector.reset_mock()
        self.assertEqual(a.render(self.project, self.asset)['status'], 'reused')
        self.inspector.assert_not_called()

    def test_approval_matches_moved_and_renamed_bytes_and_is_readonly(self):
        approved = self.approve()
        before = self.state()
        self.assertEqual(a.check(self.project)[self.asset], approved.relative_to(self.project).as_posix())
        self.assertEqual(before, self.state())
        renamed = approved.with_name('Another name.MP4')
        approved.rename(renamed)
        self.assertEqual(a.check(self.project)[self.asset], renamed.relative_to(self.project).as_posix())

    def test_multiple_or_unregistered_approvals_rejected(self):
        approved = self.approve()
        extra = approved.with_name('extra.mp4')
        shutil.copyfile(approved, extra)
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            a.check(self.project)
        extra.unlink()
        approved.write_bytes(b'arbitrary video')
        with self.assertRaisesRegex(ValueError, 'unregistered or stale'):
            a.check(self.project)

    def test_approved_folder_rejects_nonvideo_and_nested_entries(self):
        self.approve()
        unexpected = self.directory / 'APPROVED/notes.txt'
        unexpected.write_text('Not a video')
        with self.assertRaisesRegex(ValueError, 'only MP4 files directly'):
            a.check(self.project)
        unexpected.unlink()
        nested = self.directory / 'APPROVED/nested'
        nested.mkdir()
        with self.assertRaisesRegex(ValueError, 'only MP4 files directly'):
            a.check(self.project)

    def test_unchanged_render_reuses_candidate_or_renamed_human_approval(self):
        first = a.render(self.project, self.asset)
        before = self.state()
        transitions = self.transition.call_count
        reused = a.render(self.project, self.asset)
        self.assertEqual(reused['status'], 'reused')
        self.assertEqual(reused['candidate'], first['candidate'])
        self.assertEqual(before, self.state())
        self.assertEqual(self.transition.call_count, transitions)
        approved = self.directory / 'APPROVED/Human renamed.mp4'
        (self.project / first['candidate']).rename(approved)
        before = self.state()
        reused = a.render(self.project, self.asset)
        self.assertEqual(reused['candidate'], approved.relative_to(self.project).as_posix())
        self.assertEqual(before, self.state())
        self.assertEqual(self.render_count, 1)

    def test_registry_why_notes_is_a_render_input(self):
        self.approve()
        self.fixture.edit('Notes', 'Different reason for this animation')
        with self.assertRaisesRegex(ValueError, 'registry context changed'):
            a.check(self.project)
        a.revise(self.project, self.asset, 'Update the composition for the revised purpose')
        self.assertEqual(a.load(self.project, self.asset)['requests'][-1]['registry']['Notes'],
                         'Different reason for this animation')

    def test_settings_source_resources_and_renderer_changes_stale_approval(self):
        approved = self.approve()
        changes = [(self.directory / 'src/Composition.tsx', b'export default () => "Changed";'),
                   (self.directory / 'references/image.png', b'image'),
                   (self.directory / 'settings.json', json.dumps(dict(a.DEFAULT_SETTINGS, props={'title': 'changed'})).encode()),
                   (a.RENDERER / 'render.mjs', b'changed renderer'),
                   (a.RENDERER / 'new-helper.mjs', b'new renderer helper')]
        for path, contents in changes:
            with self.subTest(path=path):
                previous = path.read_bytes() if path.exists() else None
                path.write_bytes(contents)
                with self.assertRaisesRegex(ValueError, 'unregistered or stale'):
                    a.check(self.project)
                self.assertTrue(approved.exists())
                if previous is None:
                    path.unlink()
                else:
                    path.write_bytes(previous)
        a.check(self.project)

    def test_chat_revision_preserves_all_history_and_invalidates_approval(self):
        approved = self.approve()
        previous = (self.directory / 'request.01.md').read_bytes()
        text = 'Human request verbatim\r\n\nMake the title blue.\n'
        a.revise(self.project, self.asset, text)
        self.assertEqual((self.directory / 'request.02.md').read_bytes(), text.encode())
        self.assertEqual((self.directory / 'request.01.md').read_bytes(), previous)
        self.assertTrue(approved.exists())
        self.assertTrue((self.directory / 'snapshots/v001/src/Composition.tsx').is_file())
        with self.assertRaisesRegex(ValueError, 'unregistered or stale'):
            a.check(self.project)
        self.approve(name='revised.mp4')
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            a.check(self.project)

    def test_request_history_and_untracked_request_rejected(self):
        request = self.directory / 'request.01.md'
        original = request.read_bytes()
        request.write_text('Changed human context')
        with self.assertRaisesRegex(ValueError, 'history changed'):
            a.load(self.project, self.asset)
        request.write_bytes(original)
        (self.directory / 'request.02.md').write_text('Interrupted revision')
        with self.assertRaisesRegex(ValueError, 'Unregistered request'):
            a.load(self.project, self.asset)

    def test_registry_brief_change_requires_revision(self):
        self.approve()
        self.fixture.edit('Brief', 'New approved brief')
        with self.assertRaisesRegex(ValueError, 'registry context changed'):
            a.check(self.project)
        a.revise(self.project, self.asset, 'Apply the newly approved registry brief')
        self.assertEqual(a.load(self.project, self.asset)['requests'][-1]['registry']['Brief'], 'New approved brief')

    def test_render_snapshots_source_resources_settings_requests_and_renderer(self):
        (self.directory / 'references/photo.png').write_bytes(b'original photo')
        self.approve()
        snapshot = self.directory / 'snapshots/v001'
        self.assertEqual((snapshot / 'references/photo.png').read_bytes(), b'original photo')
        self.assertEqual((snapshot / 'request.01.md').read_bytes(), (self.directory / 'request.01.md').read_bytes())
        self.assertEqual(a.read_json(snapshot / 'renderer.json'), a.renderer_identity())
        self.assertEqual(a.read_json(snapshot / 'settings.json'), a.DEFAULT_SETTINGS)
        (snapshot / 'src/Composition.tsx').write_text('Modified historical snapshot')
        with self.assertRaisesRegex(ValueError, 'changed render snapshot'):
            a.check(self.project)

    def test_numbering_preserves_failed_partial_output_and_never_overwrites(self):
        def fail(snapshot, output):
            output.write_bytes(b'partial output')
            raise ValueError('mock renderer failed')
        with patch.object(a, 'run_renderer', side_effect=fail), self.assertRaisesRegex(ValueError, 'mock renderer failed'):
            a.render(self.project, self.asset)
        self.assertEqual(a.load(self.project, self.asset)['renders'][0]['status'], 'failed')
        rendered = a.render(self.project, self.asset)
        self.assertTrue(rendered['candidate'].endswith('v002.mp4'))
        self.assertEqual((self.directory / 'renders/v001.mp4').read_bytes(), b'partial output')
        self.assertTrue((self.directory / 'snapshots/v001').is_dir())

    def test_changed_input_during_render_cannot_be_registered_current(self):
        original = self.fake_render
        def change(snapshot, output):
            result = original(snapshot, output)
            (self.directory / 'src/Composition.tsx').write_text('New source during render')
            return result
        with patch.object(a, 'run_renderer', side_effect=change), self.assertRaisesRegex(ValueError, 'changed during rendering'):
            a.render(self.project, self.asset)
        self.assertEqual(a.load(self.project, self.asset)['renders'][0]['status'], 'failed')

    def test_in_progress_final_check_rejected_even_with_previous_approval(self):
        self.approve()
        ledger = a.load(self.project, self.asset)
        ledger['renders'].append({'version': 2, 'status': 'rendering'})
        self.save(ledger)
        with self.assertRaisesRegex(ValueError, 'active or interrupted'):
            a.check(self.project)

    def test_video_metadata_must_match_settings(self):
        self.approve()
        for change in ({'width': 100}, {'height': 100}, {'fps': 25}, {'durationInSeconds': 10},
                       {'durationInSeconds': float('nan')}):
            metadata = dict(width=1920, height=1080, fps=30, durationInSeconds=5)
            metadata.update(change)
            with self.subTest(change=change), patch.object(a, 'probe', return_value=metadata), self.assertRaises(ValueError):
                a.check(self.project)

    def test_prerequisite_rejection_precedes_any_execution_mutation(self):
        before = self.state()
        self.prerequisites.side_effect = ValueError('1.4 is stale')
        for action in (lambda: a.init(self.project, self.asset),
                       lambda: a.revise(self.project, self.asset, 'new request'),
                       lambda: a.render(self.project, self.asset), lambda: a.complete(self.project),
                       lambda: a.studio(self.project, self.asset)):
            with self.assertRaisesRegex(ValueError, '1.4 is stale'):
                action()
        self.assertEqual(before, self.state())

    def test_exact_nested_inventory_and_readonly_validation(self):
        self.approve()
        manifest = a.evidence(self.project)
        before = self.state()
        self.assertEqual(self.validate(manifest)[0], 'current')
        self.assertEqual(before, self.state())
        unexpected = self.directory / 'references/new.txt'
        unexpected.write_text('New resource')
        self.assertEqual(self.validate(manifest)[0], 'stale')
        unexpected.unlink()
        manifest['outputs'] = manifest['outputs'][:-1]
        self.assertEqual(self.validate(manifest)[0], 'stale')

    def test_complete_calls_shared_writer_and_reuses_unchanged_done(self):
        self.approve()
        result = a.complete(self.project)
        self.assertEqual(result['status'], 'complete')
        marker = self.project / a.STAGE / 'DONE'
        before = marker.read_bytes(), marker.stat().st_mtime_ns
        a.complete(self.project)
        self.assertEqual(before, (marker.read_bytes(), marker.stat().st_mtime_ns))
        self.assertEqual(self.validate(a.read_json(marker))[0], 'current')
        for section in ('inputs', 'outputs', 'dependencies'):
            for entry in a.read_json(marker)[section]:
                self.assertFalse(Path(entry['path']).is_absolute())
                self.assertNotIn('..', Path(entry['path']).parts)
        with self.assertRaisesRegex(ValueError, 'stage-owned'):
            completion.build_manifest(self.project, self.workflow, a.STEP)

    def test_empty_selection_succeeds_and_new_renderer_inventory_is_watched(self):
        self.fixture.edit('Review', 'Reject')
        self.assertEqual(a.check(self.project), {})
        a.complete(self.project)
        marker = self.project / a.STAGE / 'DONE'
        manifest = a.read_json(marker)
        self.assertEqual(self.validate(manifest)[0], 'current')
        (a.RENDERER / 'new-file.mjs').write_text('New implementation')
        self.assertEqual(self.validate(manifest)[0], 'stale')

    def test_metadata_cannot_redirect_snapshot_or_approval_paths(self):
        self.approve()
        ledger = a.load(self.project, self.asset)
        ledger['renders'][0]['snapshot']['../../outside'] = 'made up'
        ledger['renders'][0]['path'] = '../../outside.mp4'
        self.save(ledger)
        with self.assertRaisesRegex(ValueError, 'changed render snapshot'):
            a.check(self.project)
        for value in ('../outside', '/outside', 'ASSET-000001/../../outside', 'C:/outside'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                a.folder(self.project, value)

    def test_symlinked_source_or_approval_rejected(self):
        outside = self.workflow / 'outside.txt'
        outside.write_text('outside')
        link = self.directory / 'references/linked.txt'
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest('Symlink creation unavailable on this host')
        with self.assertRaisesRegex(ValueError, 'escapes|Redirected'):
            a.render(self.project, self.asset)
        self.assertEqual(outside.read_text(), 'outside')

    def test_settings_reject_invalid_and_arbitrary_fields(self):
        for value in (dict(a.DEFAULT_SETTINGS, width=True), dict(a.DEFAULT_SETTINGS, fps=float('nan')),
                      dict(a.DEFAULT_SETTINGS, durationInFrames=0), dict(a.DEFAULT_SETTINGS, output='../outside'),
                      dict(a.DEFAULT_SETTINGS, props=[])):
            with self.subTest(value=value), self.assertRaises(ValueError):
                a.settings(value)

    def test_shared_invalidation_archives_markers_not_human_work(self):
        approved = self.approve()
        a.complete(self.project)
        marker = self.project / a.STAGE / 'DONE'
        original = marker.read_bytes()
        self.transition.side_effect = self.real_transition
        a.revise(self.project, self.asset, 'Human requested a new color')
        self.assertFalse(marker.exists())
        archives = list((self.project / a.STAGE / 'STALE').glob('*/DONE'))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].read_bytes(), original)
        self.assertTrue(approved.is_file())
        self.assertTrue((self.directory / 'src/Composition.tsx').is_file())
        self.assertTrue((self.directory / 'request.01.md').is_file())

    def test_node_argument_contract_and_failure_diagnostics(self):
        snapshot = self.directory / 'snapshots/v001'
        output = self.directory / 'renders/v001.mp4'
        with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '{}', '')) as run:
            self.real_renderer(snapshot, output)
            command = run.call_args.args[0]
            self.assertEqual(command, ['node', str(a.RENDERER / 'render.mjs'),
                '--source-dir', str(snapshot / 'src'), '--public-dir', str(snapshot / 'references'),
                '--settings', str(snapshot / 'settings.json'), '--output', str(output)])
            self.assertEqual(run.call_args.kwargs['cwd'], a.RENDERER)
            self.assertTrue(run.call_args.kwargs['check'])
            self.assertEqual(run.call_args.kwargs['timeout'], 3600)
        with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(a.DEFAULT_SETTINGS), '')) as run:
            self.assertEqual(self.real_inspector(self.directory), a.DEFAULT_SETTINGS)
            self.assertIn('--inspect', run.call_args.args[0])
            self.assertNotIn('--output', run.call_args.args[0])
        failure = subprocess.CalledProcessError(1, 'node', stderr='Browser missing; explicit setup required')
        with patch.object(subprocess, 'run', side_effect=failure):
            with self.assertRaisesRegex(ValueError, 'Browser missing'):
                self.real_renderer(snapshot, output)
            with self.assertRaisesRegex(ValueError, 'Browser missing'):
                a.probe(output)

    def test_symlink_and_junction_checks_do_not_depend_on_host_link_privileges(self):
        target = self.directory / 'src/Composition.tsx'
        with patch.object(Path, 'is_symlink', autospec=True, side_effect=lambda p: p == target):
            with self.assertRaisesRegex(ValueError, 'Redirected'):
                a.render(self.project, self.asset)
        if hasattr(Path, 'is_junction'):
            with patch.object(Path, 'is_junction', autospec=True, side_effect=lambda p: p == target.parent):
                with self.assertRaisesRegex(ValueError, 'Redirected'):
                    a.render(self.project, self.asset)


if __name__ == '__main__':
    unittest.main()
