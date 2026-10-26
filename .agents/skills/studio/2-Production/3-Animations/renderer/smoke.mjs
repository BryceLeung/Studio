import assert from 'node:assert/strict';
import {copyFile, mkdir, mkdtemp, readFile, rm, writeFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const renderer = path.dirname(fileURLToPath(import.meta.url));
const temporary = await mkdtemp(path.join(os.tmpdir(), 'studio-animation-smoke-'));
const run = (script, args) => {
  const result = spawnSync(process.execPath, [path.join(renderer, script), ...args], {cwd: renderer, encoding: 'utf8', timeout: 120000});
  assert.equal(result.status, 0, result.stderr || result.error?.message || result.stdout);
  return JSON.parse(result.stdout.trim());
};

try {
  await mkdir(path.join(temporary, 'src'));
  await mkdir(path.join(temporary, 'references'));
  await copyFile(path.join(renderer, 'template/Composition.tsx'), path.join(temporary, 'src/Composition.tsx'));
  const entry = (await readFile(path.join(renderer, 'template/Studio.tsx'), 'utf8'))
    .replace('width={1920}', 'width={320}').replace('height={1080}', 'height={180}')
    .replace('durationInFrames={150}', 'durationInFrames={15}');
  await writeFile(path.join(temporary, 'src/Studio.tsx'), entry);
  const args = ['--source-dir', path.join(temporary, 'src'), '--public-dir', path.join(temporary, 'references'), '--settings', path.join(temporary, 'settings.json')];
  await writeFile(path.join(temporary, 'settings.json'), JSON.stringify({width: 320, height: 180, fps: 30, durationInFrames: 150}));
  const inspected = run('render.mjs', [...args, '--inspect']);
  assert.deepEqual(inspected, {width: 320, height: 180, fps: 30, durationInFrames: 15}, 'Read metadata from the actual Studio root.');
  const mismatch = spawnSync(process.execPath, [path.join(renderer, 'render.mjs'), ...args, '--output', path.join(temporary, 'mismatched.mp4')], {cwd: renderer, encoding: 'utf8', timeout: 120000});
  assert.notEqual(mismatch.status, 0, 'Refuse stale settings instead of producing a different video than Studio.');
  assert.match(mismatch.stderr, /Studio metadata differs/);
  await writeFile(path.join(temporary, 'settings.json'), JSON.stringify({width: 320, height: 180, fps: 30, durationInFrames: 15, props: {title: 'Installation smoke test'}}));
  const output = path.join(temporary, 'smoke.mp4');
  run('render.mjs', ['--source-dir', path.join(temporary, 'src'), '--public-dir', path.join(temporary, 'references'), '--settings', path.join(temporary, 'settings.json'), '--output', output]);
  const metadata = run('probe.mjs', [output]);
  assert.equal(metadata.width, 320);
  assert.equal(metadata.height, 180);
  assert.equal(metadata.fps, 30);
  assert.ok(Math.abs(metadata.durationInSeconds - 0.5) < 0.05);
  assert.ok((await readFile(output)).length > 0);
  const repeated = spawnSync(process.execPath, [path.join(renderer, 'render.mjs'), '--source-dir', path.join(temporary, 'src'), '--public-dir', path.join(temporary, 'references'), '--settings', path.join(temporary, 'settings.json'), '--output', output], {cwd: renderer, encoding: 'utf8', timeout: 120000});
  assert.notEqual(repeated.status, 0, 'Rendering must not overwrite an existing candidate.');
  await writeFile(path.join(temporary, 'outside.ts'), 'export default "untracked";');
  await writeFile(path.join(temporary, 'src/Composition.tsx'), 'import text from "../outside"; export default () => text;');
  const escaped = spawnSync(process.execPath, [path.join(renderer, 'render.mjs'), '--source-dir', path.join(temporary, 'src'), '--public-dir', path.join(temporary, 'references'), '--settings', path.join(temporary, 'settings.json'), '--output', path.join(temporary, 'escaped.mp4')], {cwd: renderer, encoding: 'utf8', timeout: 120000});
  assert.notEqual(escaped.status, 0, 'Rendering must reject imports outside the recorded source.');
  assert.match(escaped.stderr, /outside the recorded source/);
  console.log('Studio metadata, mismatch rejection, render and probe passed; existing output was preserved and untracked imports rejected.');
} finally {
  await rm(temporary, {recursive: true, force: true});
}
