import {realpath, stat} from 'node:fs/promises';
import {getVideoMetadata} from '@remotion/renderer';

try {
  if (process.argv.length !== 3) throw new Error('Usage: node probe.mjs FILE');
  const file = await realpath(process.argv[2]);
  if (!(await stat(file)).isFile()) throw new Error('Video must be a local file.');
  const metadata = await getVideoMetadata(file, {logLevel: 'error'});
  if (!metadata.width || !metadata.height || !metadata.fps || !(metadata.durationInSeconds > 0)) {
    throw new Error('File does not contain a usable video stream.');
  }
  console.log(JSON.stringify(metadata));
} catch (error) {
  console.error(error.stack ?? String(error));
  process.exitCode = 1;
}
