import {constants, realpathSync} from 'node:fs';
import {copyFile, mkdir, mkdtemp, readFile, realpath, rm, stat, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {parseArgs} from 'node:util';
import {bundle} from '@remotion/bundler';
import {openBrowser, renderMedia, selectComposition} from '@remotion/renderer';
import {validateSettings} from './settings.mjs';

const renderer = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);

export const render = async ({sourceDir, publicDir, settingsFile, output}) => {
  const source = await realpath(sourceDir);
  const compositionFile = await realpath(path.join(source, 'Composition.tsx'));
  if (path.dirname(compositionFile) !== source || !(await stat(compositionFile)).isFile()) {
    throw new Error('Composition.tsx must be a file directly inside the source directory.');
  }
  const settings = validateSettings(JSON.parse(await readFile(settingsFile, 'utf8')));
  const destination = path.resolve(output);
  if (path.extname(destination).toLowerCase() !== '.mp4') {
    throw new Error('Output must be an MP4 file.');
  }
  // Public assets and code are supplied by the lifecycle helper's immutable snapshot.
  const references = await realpath(publicDir);
  if (!(await stat(references)).isDirectory()) {
    throw new Error('Public assets must be a directory.');
  }
  await mkdir(path.join(renderer, 'node_modules/.cache/studio-animations'), {recursive: true});
  const temporary = await mkdtemp(path.join(renderer, 'node_modules/.cache/studio-animations/render-'));
  let browser;
  try {
    const moduleRoots = [source, references, realpathSync(path.join(renderer, 'node_modules')), temporary];
    const provenancePlugin = {
      apply(compiler) {
        compiler.hooks.normalModuleFactory.tap('StudioSnapshotImports', (factory) => {
          factory.hooks.afterResolve.tap('StudioSnapshotImports', (result) => {
            const resource = result?.createData?.resource?.split('?')[0];
            if (!resource || !path.isAbsolute(resource)) return;
            const resolved = realpathSync(resource);
            const included = moduleRoots.some((root) => {
              const relative = path.relative(root, resolved);
              return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative));
            });
            if (!included) throw new Error(`Import is outside the recorded source/resources and installed dependencies: ${resource}`);
          });
        });
      },
    };
    const entry = path.join(temporary, 'entry.js');
    await writeFile(entry, [
      "import React from 'react';",
      "import {Composition, registerRoot} from 'remotion';",
      `import Asset from ${JSON.stringify(compositionFile.replaceAll('\\', '/'))};`,
      `registerRoot(() => React.createElement(Composition, {id: 'Animation', component: Asset, width: ${settings.width}, height: ${settings.height}, fps: ${settings.fps}, durationInFrames: ${settings.durationInFrames}}));`,
    ].join('\n'));
    const serveUrl = await bundle({
      entryPoint: entry,
      rootDir: renderer,
      outDir: path.join(temporary, 'bundle'),
      publicDir: references,
      enableCaching: false,
      webpackOverride: (config) => ({
        ...config,
        plugins: [...(config.plugins ?? []), provenancePlugin],
        resolve: {
          ...config.resolve,
          modules: [path.join(renderer, 'node_modules'), ...(config.resolve?.modules ?? ['node_modules'])],
          alias: {
            ...config.resolve?.alias,
            react: path.dirname(require.resolve('react/package.json')),
            'react-dom': path.dirname(require.resolve('react-dom/package.json')),
            remotion: path.dirname(require.resolve('remotion/package.json')),
          },
        },
      }),
    });
    browser = await openBrowser('chrome', {
      logLevel: 'error',
      onBrowserDownload: () => {
        throw new Error('Rendering browser missing. Run npm run remotion -- browser ensure in the renderer directory during explicit setup.');
      },
    });
    const inputProps = settings.props ?? {};
    const composition = await selectComposition({serveUrl, id: 'Animation', inputProps, puppeteerInstance: browser, logLevel: 'error'});
    const rendered = path.join(temporary, 'render.mp4');
    await renderMedia({
      serveUrl,
      composition,
      inputProps,
      codec: 'h264',
      pixelFormat: 'yuv420p',
      outputLocation: rendered,
      puppeteerInstance: browser,
      concurrency: 1,
      logLevel: 'error',
    });
    await copyFile(rendered, destination, constants.COPYFILE_EXCL);
    return {output: destination, width: settings.width, height: settings.height, fps: settings.fps, durationInFrames: settings.durationInFrames};
  } finally {
    try {
      if (browser) await browser.close({silent: true});
    } finally {
      await rm(temporary, {recursive: true, force: true});
    }
  }
};

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const {values} = parseArgs({options: {
      'source-dir': {type: 'string'},
      'public-dir': {type: 'string'},
      settings: {type: 'string'},
      output: {type: 'string'},
    }});
    if (['source-dir', 'public-dir', 'settings', 'output'].some((key) => !values[key])) {
      throw new Error('Required: --source-dir DIR --public-dir DIR --settings FILE --output FILE.mp4');
    }
    // Remotion locates its installed browser relative to the renderer package.
    const options = {sourceDir: path.resolve(values['source-dir']), publicDir: path.resolve(values['public-dir']), settingsFile: path.resolve(values.settings), output: path.resolve(values.output)};
    process.chdir(renderer);
    const result = await render(options);
    console.log(JSON.stringify(result));
  } catch (error) {
    console.error(error.stack ?? String(error));
    process.exitCode = 1;
  }
}
