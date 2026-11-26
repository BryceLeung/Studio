import {createHash} from 'node:crypto';
import {access, mkdir, mkdtemp, realpath, rm, writeFile} from 'node:fs/promises';
import {constants} from 'node:fs';
import {createRequire} from 'node:module';
import {arch, platform} from 'node:os';
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
const modules = path.join(renderer, 'node_modules');
// Use the browser installed by `remotion browser ensure` in the shared renderer.
const browserPlatform = {
  darwin: arch() === 'arm64' ? 'mac-arm64' : 'mac-x64',
  linux: arch() === 'arm64' ? 'linux-arm64' : 'linux64',
  win32: 'win64',
}[platform()];
if (!browserPlatform) throw new Error(`Unsupported Remotion browser platform: ${platform()}`);
const browserDir = path.join(modules, '.remotion', 'chrome-headless-shell', browserPlatform,
  `chrome-headless-shell-${browserPlatform}`);
let browserExecutable;
for (const name of ['headless_shell', platform() === 'win32' ? 'chrome-headless-shell.exe' : 'chrome-headless-shell']) {
  const executable = path.join(browserDir, name);
  try {
    await access(executable, constants.X_OK);
    browserExecutable = executable;
    break;
  } catch (error) {
    if (!['ENOENT', 'EACCES'].includes(error.code)) throw error;
  }
}
if (!browserExecutable) {
  throw new Error('Shared browser missing. Run npm run remotion -- browser ensure in the renderer directory during explicit setup.');
}
const cache = path.join(modules, '.cache');
const cacheKey = createHash('sha256').update(JSON.stringify([entry, references])).digest('hex');
const webpackCache = path.join(cache, 'studio-animations-webpack', cacheKey);
const cli = path.join(modules, '@remotion/cli/remotion-cli.js');
const studioServer = createRequire(cli).resolve('@remotion/studio-server');
const historyModule = path.join(path.dirname(studioServer), 'helpers/recently-used-apps.js');
const historyRoot = path.join(cache, 'studio-animations-history', cacheKey);
await mkdir(cache, {recursive: true});
const temporary = await mkdtemp(path.join(cache, 'studio-animations-studio-'));
const config = path.join(temporary, 'remotion.config.ts');
const preload = path.join(temporary, 'shared-history.cjs');
// Remotion has no history path override. Redirect only that helper, not the editing root.
await writeFile(preload, [
  `const history = require(${JSON.stringify(historyModule)});`,
  `const root = ${JSON.stringify(historyRoot)};`,
  'for (const name of ["getRecentlyUsedApps", "subscribeToRecentlyUsedApps"]) {',
  '  const original = history[name];',
  '  if (typeof original !== "function") throw new Error(`Unsupported Remotion app history: ${name}`);',
  '  history[name] = options => original({...options, remotionRoot: root});',
  '}',
].join('\n'));
await writeFile(config, [
  // Keep this import external to config bundling so it shares the CLI's state.
  'import {Config} from "@remotion/cli/config";',
  'import {join} from "node:path";',
  // Disable the CLI production cache; the live Studio compiler handles its own cache below.
  'Config.setCachingEnabled(false);',
  `Config.setBrowserExecutable(${JSON.stringify(browserExecutable)});`,
  ...(isFullAnimation ? [`Config.setOutputLocation(${JSON.stringify(`out/${assetId}.mp4`)});`] : []),
  'Config.overrideWebpackConfig((config) => ({',
  '  ...config,',
  // Remotion overrides cache config. Set the final pack path after Webpack's defaults,
  // but before it creates the filesystem cache backend.
  '  plugins: [...(config.plugins ?? []), {apply(compiler) {',
  '    compiler.hooks.afterEnvironment.tap("StudioSharedWebpackCache", () => {',
  '      const cache = compiler.options.cache;',
  `      if (cache && cache.type === 'filesystem') cache.cacheLocation = join(${JSON.stringify(webpackCache)}, cache.name);`,
  '    });',
  '  }}],',
  '  resolve: {...config.resolve,',
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
const child = spawn(process.execPath, ['--require', preload, cli,
  ...args],
  {cwd: directory, env: {...process.env, NODE_PATH: modules}, stdio: 'inherit'});
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
try {
  const code = await new Promise((resolve, reject) => {child.once('error', reject); child.once('exit', resolve);});
  process.exitCode = code ?? 0;
} finally {
  await rm(temporary, {recursive: true, force: true});
}
