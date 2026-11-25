"""Review startup checks without starting Remotion or touching a project."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stdout
from http.client import HTTPConnection
from io import StringIO
import json
from pathlib import Path
import socket
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

    def test_terminal_requires_interactive_input(self):
        with patch.object(review.sys, 'stdin', StringIO('t\n')) as stdin:
            with self.assertRaisesRegex(ValueError, 'Use --chat'):
                review.wait_for_choice('Busy.', allow_takeover=True)
            self.assertEqual(stdin.tell(), 0)

    def test_terminal_ignores_invalid_input_and_waits_for_a_choice(self):
        with (patch.object(review.sys, 'stdin', StringIO('r\nb\n')) as stdin,
              patch.object(stdin, 'isatty', return_value=True),
              patch.object(review.os, 'name', 'posix'),
              redirect_stdout(StringIO()) as output):
            self.assertEqual(review.wait_for_choice('Busy.', allow_takeover=True), 'b')
        self.assertIn('[t] Take Over  [b] Background Render  [a] Abort', output.getvalue())

    def test_launcher_defaults_to_chat_even_when_stdin_looks_interactive(self):
        for flags, chat in [([], True), (['--chat'], True), (['--terminal'], False)]:
            with (self.subTest(flags=flags),
                  patch.object(review.sys, 'argv', ['review_studio.py', '--project-root', 'project',
                                                   '--asset-id', 'A13', *flags]),
                  patch.object(review.sys.stdin, 'isatty', return_value=True),
                  patch.object(Path, 'resolve', return_value=Path('resolved')),
                  patch.object(review, 'target', return_value=(Path('target'), Path('asset'))),
                  patch.object(review, 'review') as launch):
                review.main()
                launch.assert_called_once_with(Path.home() / '.cache/studio-remotion/review',
                                               Path('target'), Path('asset'), chat=chat)

    def test_chat_replies_use_loopback_not_stdin(self):
        for choice in 'tba':
            with (self.subTest(choice=choice), socket.socket() as listener,
                  ThreadPoolExecutor(max_workers=1) as worker, redirect_stdout(StringIO()) as output):
                listener.bind((review.LOOPBACK, 0))
                listener.listen()
                token = 'ab' * 32
                port = listener.getsockname()[1]
                result = worker.submit(review.wait_for_chat, listener, token, 'Already in use.', True)
                try:
                    self.assertFalse(review.controller({'port': port, 'token': token}, 'r'))
                finally:
                    review.reply_to_chat(f'{port}:{token}', choice)
                self.assertEqual(result.result(timeout=3), choice)
                self.assertIsNone(listener.gettimeout())
                self.assertIn(f'Studio review waiting: Already in use. Reply handle: {port}:{token}.',
                              output.getvalue())
            with self.assertRaisesRegex(ValueError, 'did not accept the reply'):
                review.reply_to_chat(f'{port}:{token}', choice)

    def test_abort_does_not_recheck_or_stop_another_review(self):
        state = {'directory': 'another-target'}
        with (tempfile.TemporaryDirectory() as temp,
              patch.object(review, 'controller', return_value=True) as controller,
              patch.object(review, 'wait_for_choice', return_value='a') as choose,
              patch.object(review, 'start_review') as start):
            cache = Path(temp)
            (cache / 'owner.json').write_text(json.dumps(state), encoding='utf-8')
            review.review(cache, Path('requested'), Path('asset'))
        choose.assert_called_once()
        controller.assert_called_once_with(state, 'status')
        start.assert_not_called()

    def test_background_render_choice_releases_review_request_without_touching_owner(self):
        state = {'directory': 'active-target'}
        with (tempfile.TemporaryDirectory() as temp, redirect_stdout(StringIO()) as output,
              patch.object(review, 'controller', return_value=True) as controller,
              patch.object(review, 'wait_for_chat', return_value='b'),
              patch.object(review, 'start_review') as start):
            cache = Path(temp)
            owner = cache / 'owner.json'
            owner.write_text(json.dumps(state), encoding='utf-8')
            self.assertEqual(review.review(cache, Path('requested'), Path('asset'), chat=True), 'b')
            self.assertEqual(json.loads(owner.read_text(encoding='utf-8')), state)
        controller.assert_called_once_with(state, 'status')
        start.assert_not_called()
        self.assertIn(review.BACKGROUND_SELECTED, output.getvalue())

    def test_background_command_bypasses_review_mutex(self):
        with (tempfile.TemporaryDirectory() as temp,
              patch.object(review.sys, 'argv', ['review_studio.py', '--project-root', temp,
                                               '--asset-id', 'A13', '--background-render']),
              patch.object(review, 'target', return_value=(Path('candidate'), Path('asset'))),
              patch.object(review, 'review') as launch,
              patch.object(review, 'background_render') as render):
            review.main()
            render.assert_called_once_with(Path(temp).resolve(), Path('candidate'), Path('asset'))
        launch.assert_not_called()

    def test_candidate_background_render_keeps_existing_preview_and_props(self):
        value = dict(review.stage.DEFAULT_SETTINGS, props={'label': 'keep'})
        metadata = dict(review.stage.DEFAULT_SETTINGS, durationInFrames=48)
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            asset = project / 'asset'
            candidate = asset / 'candidates/power-up/v012'
            (candidate / 'src').mkdir(parents=True)
            (candidate / 'references').mkdir()
            (candidate / 'src/Studio.tsx').write_text('test', encoding='utf-8')
            (candidate / 'settings.json').write_text(json.dumps(value), encoding='utf-8')
            (candidate / 'preview.mp4').write_bytes(b'previous render')

            def render(snapshot, video):
                self.assertNotEqual(snapshot, candidate)
                self.assertEqual((snapshot / 'src/Studio.tsx').read_text(encoding='utf-8'), 'test')
                self.assertEqual(review.stage.read_json(snapshot / 'settings.json'), dict(value, **metadata))
                video.write_bytes(b'new render')

            with (patch.object(review.stage, 'inspect_settings', return_value=metadata),
                  patch.object(review.stage, 'run_renderer', side_effect=render),
                  patch.object(review.stage, 'valid_video') as probe,
                  patch.object(review, 'serve_video', side_effect=lambda p: self.assertEqual(p.read_bytes(), b'new render')) as serve):
                review.background_render(project, candidate, asset)
                probe.assert_called_once_with(serve.call_args.args[0], dict(value, **metadata))
            self.assertFalse(serve.call_args.args[0].exists())
            self.assertEqual((candidate / 'preview.mp4').read_bytes(), b'previous render')
            self.assertEqual(review.stage.read_json(candidate / 'settings.json'), value)

    def test_playback_serves_only_selected_mp4_and_supports_seeking(self):
        with tempfile.TemporaryDirectory() as temp:
            video = Path(temp) / 'select me.mp4'
            video.write_bytes(b'0123456789')
            video.with_name('other.mp4').write_bytes(b'not for playback')
            real_serve_forever = review.ThreadingHTTPServer.serve_forever

            def check_playback(server):
                with ThreadPoolExecutor(max_workers=1) as worker:
                    running = worker.submit(real_serve_forever, server)
                    connection = HTTPConnection(review.LOOPBACK, server.server_port, timeout=3)
                    try:
                        connection.request('GET', '/select%20me.mp4', headers={'Range': 'bytes=2-4'})
                        response = connection.getresponse()
                        self.assertEqual(response.status, 206)
                        self.assertEqual(response.getheader('Content-Range'), 'bytes 2-4/10')
                        self.assertEqual(response.read(), b'234')
                        connection.request('GET', '/other.mp4')
                        self.assertEqual(connection.getresponse().status, 404)
                    finally:
                        connection.close()
                        server.shutdown()
                    running.result(timeout=3)

            with (patch.object(review.ThreadingHTTPServer, 'serve_forever', check_playback),
                  redirect_stdout(StringIO()) as output):
                review.serve_video(video)
            self.assertRegex(output.getvalue(), r'Studio playback ready: http://127\.0\.0\.1:\d+/select%20me\.mp4')

    def test_takeover_reuses_review_if_requested_target_started_while_waiting(self):
        directory = Path('requested')
        state = {'directory': str(directory)}
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp)

            def choose_takeover(*args, **kwargs):
                (cache / 'owner.json').write_text(json.dumps(state), encoding='utf-8')
                return 't'

            @contextmanager
            def busy_then_available(*args, **kwargs):
                if not (cache / 'owner.json').exists():
                    raise review.stage.completion.MutationBusyError(cache)
                yield

            with (patch.object(review.stage.completion, 'mutation_guard', busy_then_available),
                  patch.object(review, 'wait_for_choice', side_effect=choose_takeover) as choose,
                  patch.object(review, 'controller', return_value=True) as controller,
                  patch.object(review, 'live', return_value=True),
                  patch.object(review, 'start_review') as start):
                review.review(cache, directory, Path('asset'))
            choose.assert_called_once()
            controller.assert_called_once_with(state, 'status')
            start.assert_not_called()

    def test_takeover_waits_for_mutex_then_stops_managed_owner_before_starting(self):
        state = {'directory': 'another-target'}
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp)
            (cache / 'owner.json').write_text(json.dumps(state), encoding='utf-8')
            attempts = []
            events = []

            @contextmanager
            def guard(*args, blocking):
                attempts.append(blocking)
                if not blocking:
                    raise review.stage.completion.MutationBusyError(cache)
                yield

            def controller(owner, action):
                self.assertEqual(owner, state)
                events.append(action)
                return True

            def start(*args):
                events.append('start')
                raise ValueError('test reached startup')

            with (patch.object(review.stage.completion, 'mutation_guard', guard),
                  patch.object(review, 'wait_for_choice', return_value='t') as choose,
                  patch.object(review, 'controller', side_effect=controller),
                  patch.object(review.shutil, 'which', return_value='node'),
                  patch.object(Path, 'is_file', return_value=True),
                  patch.object(review, 'start_review', side_effect=start)):
                with self.assertRaisesRegex(ValueError, 'test reached startup'):
                    review.review(cache, Path('requested'), Path('asset'))
        self.assertEqual(events, ['status', 'stop', 'start'])
        self.assertEqual(attempts, [False, True])
        choose.assert_called_once_with('Another Remotion Studio operation is in progress.',
                                       allow_takeover=True, allow_render=True)

    def test_unmanaged_port_is_never_stopped(self):
        with (tempfile.TemporaryDirectory() as temp,
              patch.object(review, 'controller', return_value=False) as controller,
              patch.object(review.shutil, 'which', return_value='node'),
              patch.object(Path, 'is_file', return_value=True),
              patch.object(review, 'occupied', return_value=True),
              patch.object(review.subprocess, 'Popen') as launch):
            with self.assertRaisesRegex(ValueError, 'server this helper does not own'):
                review.review(Path(temp), Path('requested'), Path('asset'))
        controller.assert_called_once_with({}, 'status')
        launch.assert_not_called()

    def test_failed_build_cleans_up_before_releasing_mutex(self):
        locked = False

        @contextmanager
        def guard(*args, **kwargs):
            nonlocal locked
            locked = True
            try:
                yield
            finally:
                locked = False

        child = Mock()
        with (tempfile.TemporaryDirectory() as temp,
              patch.object(review.stage.completion, 'mutation_guard', guard),
              patch.object(review.shutil, 'which', return_value='node'),
              patch.object(Path, 'is_file', return_value=True),
              patch.object(review, 'occupied', return_value=False),
              patch.object(review.subprocess, 'Popen', return_value=child),
              patch.object(review, 'wait_for_build', side_effect=ValueError('build failed')),
              patch.object(review, 'stop_child', side_effect=lambda _: self.assertTrue(locked)) as stop):
            with self.assertRaisesRegex(ValueError, 'build failed'):
                review.review(Path(temp), Path('requested'), Path('asset'))
            self.assertFalse((Path(temp) / 'owner.json').exists())
        stop.assert_called_once_with(child)
