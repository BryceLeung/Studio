"""Open the single Remotion Studio review on port 3000 or render an MP4 without it."""

import argparse
from contextlib import nullcontext
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import time
from urllib.parse import quote

import animations as stage


PORT = 3000
URL = f'http://localhost:{PORT}'
LOOPBACK = '127.0.0.1'
BACKGROUND_SELECTED = 'Studio review cancelled for Background Render.'


def target(project, asset, candidate):
    project = project.resolve(strict=True)
    stage.require_prerequisites(project, stage.ROOT, stage.STEP)
    stage.require(asset in stage.eligible(project), 'Select a current Graphic or Animation asset')
    relative = stage.STAGE / asset
    if candidate:
        stage.require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]*/v[0-9]{3,}', candidate),
                      'Use --candidate <name>/vNNN')
        relative = relative / 'candidates' / candidate
    stage.require(stage.confined(project, (relative / 'src/Studio.tsx').as_posix()).is_file(),
                  'Missing src/Studio.tsx')
    stage.require(stage.confined(project, (relative / 'references').as_posix()).is_dir(),
                  'Missing references directory')
    return project / relative, project / stage.STAGE / asset


def occupied():
    for host in (LOOPBACK, '::1'):
        try:
            with socket.create_connection((host, PORT), timeout=0.25):
                return True
        except OSError:
            continue
    return False


def live(directory, asset):
    connection = HTTPConnection(LOOPBACK, PORT, timeout=1)
    try:
        connection.request('GET', '/__remotion_config')
        response = connection.getresponse()
        if response.status != 200:
            return False
        config = json.loads(response.read(8192))
        # Older candidates without their own package.json report the enclosing asset.
        if (config.get('isRemotion') is not True or
                Path(config['cwd']).resolve() not in (directory, asset)):
            return False
        connection.request('GET', '/')
        return connection.getresponse().status == 200
    except (OSError, HTTPException, ValueError, KeyError, TypeError, AttributeError, RuntimeError):
        return False
    finally:
        connection.close()


def signature(token, message):
    return hmac.digest(bytes.fromhex(token), message, 'sha256').hex().encode('ascii')


def control_request(conn, token):
    with conn.makefile('rb') as stream:
        request = stream.readline(128).strip()
    message, _, proof = request.rpartition(b' ')
    if not hmac.compare_digest(proof, signature(token, message)):
        return b'', b''
    return message.split(b' ', 1)[0], signature(token, b'ok ' + message) + b'\n'


def controller(state, action):
    """Never use a cached PID to stop a process; ask the live owner instead."""
    try:
        with socket.create_connection((LOOPBACK, state['port']), timeout=2) as conn:
            if action == 'stop':
                conn.settimeout(22)
            message = f'{action} {secrets.token_hex(16)}'.encode('ascii')
            conn.sendall(message + b' ' + signature(state['token'], message) + b'\n')
            with conn.makefile('rb') as stream:
                return hmac.compare_digest(stream.readline(66).strip(),
                                           signature(state['token'], b'ok ' + message))
    except (OSError, KeyError, TypeError, ValueError):
        return False


def stop_child(child):
    if child.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    else:
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name == 'nt':
            child.kill()
        else:
            os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=5)


def wait_for_build(child, directory, asset):
    lines = Queue()
    ready = False
    deadline = time.monotonic() + 30

    def relay():
        for line in child.stdout:
            print(line.rstrip(), flush=True)
            if not ready:
                lines.put(re.sub(r'\x1b\[[0-9;]*m', '', line))

    Thread(target=relay, daemon=True).start()
    listening = built = False
    while time.monotonic() < deadline:
        stage.require(child.poll() is None, 'Remotion exited before the review was ready')
        try:
            line = lines.get(timeout=0.1)
        except Empty:
            line = ''
        if match := re.search(r'Local: http://localhost:(\d+)', line):
            stage.require(int(match[1]) == PORT, f'Remotion selected a different port ({match[1]})')
            listening = True
        built |= bool(re.search(r'\bBuilt in \d+ms', line))
        if listening and built and live(directory, asset):
            ready = True
            return
    raise ValueError(f'Remotion did not build and serve {directory} on port {PORT} within 30 seconds')


def wait_for_choice(reason, allow_takeover, allow_render=True):
    stage.require(sys.stdin.isatty(), 'Use --chat when no interactive terminal is available')

    options = [('t', 'Take Over')] if allow_takeover else []
    if allow_render:
        options.append(('b', 'Background Render'))
    options.append(('a', 'Abort'))
    choices = {key for key, _ in options}
    menu = '  '.join(f'[{key}] {label}' for key, label in options)
    print(f'{reason} {menu}. Waiting for your choice.', flush=True)
    if os.name == 'nt':
        import msvcrt
        while True:
            key = msvcrt.getwch().lower()
            if key in ('\x00', '\xe0'):
                msvcrt.getwch()  # Discard the Windows extended-key scan code.
            elif key in choices:
                return key
    else:
        print('Press Enter after your choice.', flush=True)
        for line in sys.stdin:
            if (key := line.strip().lower()) in choices:
                return key
        return 'a'  # The terminal closed without a choice.


def wait_for_chat(listener, token, reason, allow_takeover, allow_render=True):
    handle = f'{listener.getsockname()[1]}:{token}'
    actions = {b'a'}
    if allow_takeover:
        actions.add(b't')
    if allow_render:
        actions.add(b'b')
    print(f'Studio review waiting: {reason} Reply handle: {handle}. Waiting for your choice.', flush=True)
    while True:
        conn, _ = listener.accept()
        with conn:
            conn.settimeout(0.5)
            try:
                action, reply = control_request(conn, token)
                if action in actions:
                    conn.sendall(reply)
                    return action.decode('ascii')
            except OSError:
                continue


def reply_to_chat(handle, action):
    match = re.fullmatch(r'([0-9]{1,5}):([0-9a-f]{64})', handle)
    if not match or not 1 <= int(match[1]) <= 65535:
        raise ValueError('Invalid review reply handle')
    if not controller({'port': int(match[1]), 'token': match[2]}, action):
        raise ValueError('Waiting review did not accept the reply; check its current output')
    print('Studio review reply accepted.', flush=True)


def serve_video(video):
    route = '/' + quote(video.name)
    size = video.stat().st_size
    stage.require(size > 0, 'Cannot serve an empty video')

    class VideoHandler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_video(body=False)

        def do_GET(self):
            self.send_video(body=True)

        def send_video(self, *, body):
            if self.path != route:
                self.send_error(404)
                return
            try:
                stream = video.open('rb')
            except OSError:
                self.send_error(404)
                return
            with stream:
                start, end = 0, size - 1
                byte_range = self.headers.get('Range') if body else None
                if byte_range:
                    match = re.fullmatch(r'bytes=([0-9]{0,20})-([0-9]{0,20})', byte_range)
                    if match and match[1]:
                        start = int(match[1])
                        end = min(end, int(match[2])) if match[2] else end
                    elif match and match[2]:
                        start = max(0, size - int(match[2]))
                    if not match or not any(match.groups()) or start > end:
                        self.send_response(416)
                        self.send_header('Content-Range', f'bytes */{size}')
                        self.end_headers()
                        return
                self.send_response(206 if byte_range else 200)
                self.send_header('Content-Type', 'video/mp4')
                self.send_header('Content-Length', str(end - start + 1))
                self.send_header('Accept-Ranges', 'bytes')
                if byte_range:
                    self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                self.end_headers()
                if body:
                    stream.seek(start)
                    remaining = end - start + 1
                    try:
                        while remaining and (chunk := stream.read(min(remaining, 64 * 1024))):
                            self.wfile.write(chunk)
                            remaining -= len(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        pass  # Players disconnect when seeking.

    with ThreadingHTTPServer((LOOPBACK, 0), VideoHandler) as server:
        port = server.server_port
        # The listener is already bound; handle the verification request on this server.
        verifier = Thread(target=server.handle_request)
        server.timeout = 3
        verifier.start()
        connection = HTTPConnection(LOOPBACK, port, timeout=3)
        try:
            connection.request('HEAD', route)
            response = connection.getresponse()
            stage.require(response.status == 200 and response.getheader('Content-Type') == 'video/mp4'
                          and response.getheader('Content-Length') == str(size),
                          'Could not verify MP4 playback')
        finally:
            connection.close()
            verifier.join()
        print(f'Studio playback ready: http://{LOOPBACK}:{port}{route}', flush=True)
        server.serve_forever()


def background_render(project, directory, asset):
    print(f'Studio background rendering: {directory}', flush=True)
    with TemporaryDirectory(prefix='studio-background-') as temp:
        snapshot = Path(temp)
        if directory == asset:
            result = stage.render(project, asset.name)
            source = stage.confined(project, result['candidate'])
            video = snapshot / source.name
            shutil.copyfile(source, video)
            stage.require(stage.sha(video) == result['sha256'], 'The rendered MP4 changed before playback')
            serve_video(video)
            return

        # Candidate previews are inspection-only; never replace their existing preview or settings.
        for name in ('src', 'references'):
            (snapshot / name).mkdir()
            for source in stage.files(directory, name):
                destination = snapshot / source.relative_to(directory)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
        settings_file = stage.confined(directory, 'settings.json')
        settings = stage.settings(stage.read_json(settings_file))
        stage.atomic_json(snapshot / 'settings.json', settings)
        settings.update(stage.inspect_settings(snapshot))
        stage.atomic_json(snapshot / 'settings.json', settings)
        video = snapshot / 'preview.mp4'
        stage.run_renderer(snapshot, video)
        stage.valid_video(video, settings)
        serve_video(video)


def start_review(control, state_file, directory, asset, node):
    stage.require(not occupied(), f'Port {PORT} is occupied by a server this helper does not own. '
                  'Stop it at its source before opening a Studio review.')
    control.bind((LOOPBACK, 0))
    control.listen()
    control.settimeout(0.25)
    token = secrets.token_hex(32)
    command = [node, str(stage.RENDERER / 'studio.mjs'), '--source-dir', str(directory / 'src'),
               '--public-dir', str(directory / 'references'), '--port', str(PORT), '--no-open']
    child = subprocess.Popen(command, cwd=stage.RENDERER, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             encoding='utf-8', errors='replace', start_new_session=os.name != 'nt')
    try:
        wait_for_build(child, directory, asset)
        stage.atomic_json(state_file, {'port': control.getsockname()[1], 'token': token,
                                       'directory': str(directory)})
    except BaseException:
        # Clean up under the startup lock before another request checks the port.
        stop_child(child)
        raise
    return child, token


def review(cache, directory=None, asset=None, *, chat=False):
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    state_file = cache / 'owner.json'
    child = None
    takeover = False
    with socket.socket() as control, (socket.socket() if chat else nullcontext()) as pending:
        if pending is not None:
            pending.bind((LOOPBACK, 0))
            pending.listen()
            reply_token = secrets.token_hex(32)
        try:
            while True:
                try:
                    with stage.completion.mutation_guard(cache, blocking=takeover or directory is None):
                        try:
                            state = json.loads(state_file.read_text(encoding='utf-8'))
                        except (FileNotFoundError, ValueError):
                            state = {}
                        owned = isinstance(state, dict) and controller(state, 'status')
                        if directory is None:
                            if owned:
                                stage.require(controller(state, 'stop'), 'The previous review did not stop cleanly')
                            print('Studio review stopped.' if owned else 'No managed Studio review is running.', flush=True)
                            return
                        if owned and state.get('directory') == str(directory) and live(directory, asset):
                            if pending is not None:
                                pending.close()
                            print(f'Studio review ready: {URL} (reused {directory})', flush=True)
                            return
                        if owned and not takeover:
                            reason = f'Remotion Studio is in use ({state.get("directory")}).'
                        else:
                            if pending is not None:
                                pending.close()
                            node = shutil.which('node')
                            stage.require(node, 'Node.js is not installed or is not on PATH')
                            stage.require((stage.RENDERER / 'node_modules/@remotion/cli/remotion-cli.js').is_file(),
                                          'Shared Remotion dependencies are missing; run the renderer setup first')
                            if owned:
                                stage.require(controller(state, 'stop'), 'The previous review did not stop cleanly')
                            child, token = start_review(control, state_file, directory, asset, node)
                            break
                except stage.completion.MutationBusyError:
                    reason = 'Another Remotion Studio operation is in progress.'

                allow_takeover = directory is not None and not takeover
                if pending is not None:
                    choice = wait_for_chat(pending, reply_token, reason, allow_takeover=allow_takeover,
                                           allow_render=directory is not None)
                else:
                    choice = wait_for_choice(reason, allow_takeover=allow_takeover,
                                             allow_render=directory is not None)
                if choice in ('a', 'b'):
                    if pending is not None:
                        pending.close()
                    print(BACKGROUND_SELECTED if choice == 'b' else 'Studio review request aborted.', flush=True)
                    return choice
                takeover = True
                print('Takeover queued; waiting for the current Remotion Studio operation to finish.', flush=True)
            print(f'Studio review ready: {URL} ({directory})', flush=True)
            while child.poll() is None:
                try:
                    conn, _ = control.accept()
                except TimeoutError:
                    continue
                with conn:
                    conn.settimeout(0.5)
                    try:
                        action, reply = control_request(conn, token)
                        if action == b'stop':
                            stop_child(child)
                            conn.sendall(reply)
                            return
                        if action == b'status':
                            conn.sendall(reply)
                    except OSError:
                        continue
            raise ValueError(f'Remotion exited with code {child.returncode}')
        finally:
            if child is not None:
                stop_child(child)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path)
    parser.add_argument('--asset-id')
    parser.add_argument('--candidate', help='Exact candidate, for example 529-girl-power-up/v012')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--stop', action='store_true', help='Stop only the managed review')
    mode.add_argument('--chat', action='store_true', help='Receive waiting choices from chat (default)')
    mode.add_argument('--terminal', action='store_true', help='Read waiting choices from a foreground terminal')
    mode.add_argument('--reply', nargs=2, metavar=('HANDLE', 'CHOICE'), help='Reply to a waiting chat review')
    mode.add_argument('--background-render', action='store_true', help='Render and serve an MP4 without opening Studio')
    args = parser.parse_args()
    cache = Path.home() / '.cache' / 'studio-remotion' / 'review'
    if args.stop or args.reply:
        if args.project_root or args.asset_id or args.candidate:
            parser.error('--stop and --reply do not take a target')
        if args.reply:
            handle, action = args.reply
            if action not in ('t', 'b', 'a'):
                parser.error('--reply choice must be t, b or a')
            reply_to_chat(handle, action)
        else:
            review(cache)
    else:
        if not args.project_root or not args.asset_id:
            parser.error('--project-root and --asset-id are required')
        project = args.project_root.resolve(strict=True)
        directory, asset = target(project, args.asset_id, args.candidate)
        if args.background_render:
            background_render(project, directory, asset)
        else:
            # Chat hands playback off to a separate, session-scoped process.
            choice = review(cache, directory, asset, chat=not args.terminal)
            if args.terminal and choice == 'b':
                background_render(project, directory, asset)


if __name__ == '__main__':
    if os.name != 'nt':
        def terminate(_signum, _frame):
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, terminate)
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        sys.exit(f'Studio review: {error}')
