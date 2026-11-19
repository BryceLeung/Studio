import {mkdtemp, realpath, rm, writeFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {parseArgs} from 'node:util';

// Local Studio launcher. Uses the already installed shared renderer packages.
const renderer = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const {values} = parseArgs({options: {
  'source-dir': {type: 'string'}, 'public-dir': {type: 'string'}, port: {type: 'string'},
  'no-open': {type: 'boolean', default: false},
}});
if (!values['source-dir'] || !values['public-dir'] || (values.port !== undefined && (!/^\d+$/.test(values.port) || Number(values.port) < 1 || Number(values.port) > 65535))) {
  throw new Error('Use --source-dir <src> --public-dir <references> [--port 3000] [--no-open]');
}
const source = await realpath(values['source-dir']);
const entry = await realpath(path.join(source, 'Studio.tsx'));
const references = await realpath(values['public-dir']);
const directory = path.dirname(source);
const assetId = path.basename(directory);
const isFullAnimation = path.basename(path.dirname(directory)) === '3-Animations' &&
  /^(?:A(?:0[1-9]|[1-9][0-9])|ASSET-\d{6,})$/.test(assetId);
const temporary = await mkdtemp(path.join(renderer, 'node_modules/.cache/studio-animations-studio-'));
const config = path.join(temporary, 'remotion.config.ts');
const modules = path.join(renderer, 'node_modules');
await writeFile(config, [
  // Keep this import external to config bundling so it shares the CLI's state.
  'import {Config} from "@remotion/cli/config";',
  ...(isFullAnimation ? [`Config.setOutputLocation(${JSON.stringify(`out/${assetId}.mp4`)});`] : []),
  'Config.overrideWebpackConfig((config) => ({',
  '  ...config, resolve: {...config.resolve,',
  `    modules: [${JSON.stringify(modules)}, ...(config.resolve?.modules ?? ['node_modules'])],`,
  `    alias: {...config.resolve?.alias, react: ${JSON.stringify(path.dirname(require.resolve('react/package.json')))},`,
  `      'react-dom': ${JSON.stringify(path.dirname(require.resolve('react-dom/package.json')))},`,
  `      remotion: ${JSON.stringify(path.dirname(require.resolve('remotion/package.json')))}}`,
  '  }',
  '}));',
].join('\n'));
console.log(`Opening source-backed Studio for ${source}`);
// Match the Python launcher's IPv4 port allocation and keep the browser URL stable.
const args = ['studio', entry, '--config', config, '--public-dir', references, '--ipv4'];
if (values.port !== undefined) args.push('--port', values.port);
if (values['no-open']) args.push('--no-open');
const child = spawn(process.execPath, [path.join(renderer, 'node_modules/@remotion/cli/remotion-cli.js'),
  ...args],
  {cwd: directory, env: {...process.env, NODE_PATH: modules}, stdio: 'inherit'});
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
try {
  const code = await new Promise((resolve, reject) => {child.once('error', reject); child.once('exit', resolve);});
  process.exitCode = code ?? 0;
} finally {
  await rm(temporary, {recursive: true, force: true});
}
