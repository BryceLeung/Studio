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
const temporary = await mkdtemp(path.join(renderer, 'node_modules/.cache/studio-animations-studio-'));
const config = path.join(temporary, 'remotion.config.ts');
const modules = path.join(renderer, 'node_modules');
await writeFile(config, [
  `import {Config} from ${JSON.stringify(require.resolve('@remotion/cli/config'))};`,
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
const args = ['studio', entry, '--config', config, '--public-dir', references];
if (values.port !== undefined) args.push('--port', values.port);
if (values['no-open']) args.push('--no-open');
const child = spawn(process.execPath, [path.join(renderer, 'node_modules/@remotion/cli/remotion-cli.js'),
  ...args],
  {cwd: path.dirname(source), env: {...process.env, NODE_PATH: modules}, stdio: 'inherit'});
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
try {
  const code = await new Promise((resolve, reject) => {child.once('error', reject); child.once('exit', resolve);});
  process.exitCode = code ?? 0;
} finally {
  await rm(temporary, {recursive: true, force: true});
}
