"""Start, reuse or switch the single Remotion Studio review on port 3000."""

import argparse
from http.client import HTTPConnection, HTTPException
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
from threading import Thread
import time

import animations as stage


PORT = 3000
URL = f'http://localhost:{PORT}'
LOOPBACK = '127.0.0.1'


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


def review(cache, directory=None, asset=None):
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    state_file = cache / 'owner.json'
    child = None
    with socket.socket() as control:
        try:
            with stage.completion.mutation_guard(cache):
                try:
                    state = json.loads(state_file.read_text(encoding='utf-8'))
                except (FileNotFoundError, ValueError):
                    state = {}
                owned = isinstance(state, dict) and controller(state, 'status')
                if directory is not None:
                    if owned and state.get('directory') == str(directory) and live(directory, asset):
                        print(f'Studio review ready: {URL} (reused {directory})', flush=True)
                        return
                    node = shutil.which('node')
                    stage.require(node, 'Node.js is not installed or is not on PATH')
                    stage.require((stage.RENDERER / 'node_modules/@remotion/cli/remotion-cli.js').is_file(),
                                  'Shared Remotion dependencies are missing; run the renderer setup first')
                if owned:
                    stage.require(controller(state, 'stop'), 'The previous review did not stop cleanly')
                if directory is None:
                    print('Studio review stopped.' if owned else 'No managed Studio review is running.', flush=True)
                    return
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
                wait_for_build(child, directory, asset)
                stage.atomic_json(state_file, {'port': control.getsockname()[1], 'token': token,
                                               'directory': str(directory)})
            print(f'Studio review ready: {URL} ({directory})', flush=True)
            while child.poll() is None:
                try:
                    conn, _ = control.accept()
                except TimeoutError:
                    continue
                with conn:
                    conn.settimeout(0.5)
                    try:
                        with conn.makefile('rb') as stream:
                            request = stream.readline(128).strip()
                        message, _, proof = request.rpartition(b' ')
                        if not hmac.compare_digest(proof, signature(token, message)):
                            continue
                        action = message.split(b' ', 1)[0]
                        reply = signature(token, b'ok ' + message) + b'\n'
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
    parser.add_argument('--stop', action='store_true', help='Stop only the managed review')
    args = parser.parse_args()
    cache = Path.home() / '.cache' / 'studio-remotion' / 'review'
    if args.stop:
        if args.project_root or args.asset_id or args.candidate:
            parser.error('--stop does not take a target')
        review(cache)
    else:
        if not args.project_root or not args.asset_id:
            parser.error('--project-root and --asset-id are required')
        review(cache, *target(args.project_root, args.asset_id, args.candidate))


if __name__ == '__main__':
    if os.name != 'nt':
        def terminate(_signum, _frame):
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, terminate)
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        sys.exit(f'Studio review: {error}')
