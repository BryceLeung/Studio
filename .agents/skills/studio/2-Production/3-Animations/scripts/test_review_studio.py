"""Review startup checks without starting Remotion or touching a project."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import call, Mock, patch

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

    def test_background_retries_after_ten_seconds_without_reading_input(self):
        with (patch.object(review.sys, 'stdin', StringIO('t\n')) as stdin,
              patch.object(review.time, 'sleep') as sleep):
            self.assertEqual(review.wait_for_choice('Busy.', allow_takeover=True), 'r')
            self.assertEqual(stdin.tell(), 0)
        sleep.assert_called_once_with(10)

    def test_foreground_timeout_defaults_to_retry(self):
        with (patch.object(review.sys.stdin, 'isatty', return_value=True),
              patch.object(review.time, 'monotonic', side_effect=[0, 10])):
            self.assertEqual(review.wait_for_choice('Busy.', allow_takeover=True), 'r')

    def test_launcher_defaults_to_chat_even_when_stdin_looks_interactive(self):
        for flags, chat in [([], True), (['--chat'], True), (['--terminal'], False)]:
            with (self.subTest(flags=flags),
                  patch.object(review.sys, 'argv', ['review_studio.py', '--project-root', 'project',
                                                   '--asset-id', 'A13', *flags]),
                  patch.object(review.sys.stdin, 'isatty', return_value=True),
                  patch.object(review, 'target', return_value=(Path('target'), Path('asset'))),
                  patch.object(review, 'review') as launch):
                review.main()
                launch.assert_called_once_with(Path.home() / '.cache/studio-remotion/review',
                                               Path('target'), Path('asset'), chat=chat)

    def test_chat_replies_use_loopback_not_stdin(self):
        for choice in 'art':
            with (self.subTest(choice=choice), socket.socket() as listener,
                  ThreadPoolExecutor(max_workers=1) as worker, redirect_stdout(StringIO()) as output):
                listener.bind((review.LOOPBACK, 0))
                listener.listen()
                token = 'ab' * 32
                port = listener.getsockname()[1]
                with patch.object(review, 'RETRY_SECONDS', 2):
                    result = worker.submit(review.wait_for_chat, listener, token, 'Already in use.', True)
                    review.reply_to_chat(f'{port}:{token}', choice)
                    self.assertEqual(result.result(timeout=3), choice)
                self.assertIn(f'Studio review waiting: Already in use. Reply handle: {port}:{token}.',
                              output.getvalue())
            with self.assertRaisesRegex(ValueError, 'did not accept the reply'):
                review.reply_to_chat(f'{port}:{token}', choice)

    def test_chat_timeout_retries_without_taking_over(self):
        with (socket.socket() as listener, redirect_stdout(StringIO()),
              patch.object(review, 'RETRY_SECONDS', 0.02)):
            listener.bind((review.LOOPBACK, 0))
            listener.listen()
            self.assertEqual(review.wait_for_chat(listener, 'ab' * 32, 'Busy.', True), 'r')

    def test_retry_does_not_stop_another_review_and_abort_leaves_it_running(self):
        state = {'directory': 'another-target'}
        with (tempfile.TemporaryDirectory() as temp,
              patch.object(review, 'controller', return_value=True) as controller,
              patch.object(review, 'wait_for_choice', side_effect=['r', 'a']) as choose,
              patch.object(review, 'start_review') as start):
            cache = Path(temp)
            (cache / 'owner.json').write_text(json.dumps(state), encoding='utf-8')
            review.review(cache, Path('requested'), Path('asset'))
        self.assertEqual(choose.call_count, 2)
        self.assertEqual(controller.call_args_list, [call(state, 'status')] * 2)
        start.assert_not_called()

    def test_retry_rechecks_owner_and_reuses_requested_review(self):
        directory = Path('requested')
        state = {'directory': str(directory)}
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp)

            def retry(*args, **kwargs):
                (cache / 'owner.json').write_text(json.dumps(state), encoding='utf-8')
                return 'r'

            @contextmanager
            def busy_then_available(*args, **kwargs):
                if not (cache / 'owner.json').exists():
                    raise review.stage.completion.MutationBusyError(cache)
                yield

            with (patch.object(review.stage.completion, 'mutation_guard', busy_then_available),
                  patch.object(review, 'wait_for_choice', side_effect=retry) as choose,
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
            attempts = 0
            events = []

            @contextmanager
            def guard(*args, **kwargs):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
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
                  patch.object(review, 'wait_for_choice', side_effect=['t', 'r']) as choose,
                  patch.object(review, 'controller', side_effect=controller),
                  patch.object(review.shutil, 'which', return_value='node'),
                  patch.object(Path, 'is_file', return_value=True),
                  patch.object(review, 'start_review', side_effect=start)):
                with self.assertRaisesRegex(ValueError, 'test reached startup'):
                    review.review(cache, Path('requested'), Path('asset'))
        self.assertEqual(events, ['status', 'stop', 'start'])
        self.assertEqual([entry.kwargs['allow_takeover'] for entry in choose.call_args_list], [True, False])

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
