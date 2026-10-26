import assert from 'node:assert/strict';
import {test} from 'node:test';
import {validateSettings} from './settings.mjs';
import {createServer} from 'node:net';
import {findRenderPort} from './ports.mjs';

const settings = {width: 1920, height: 1080, fps: 30, durationInFrames: 150};

test('accepts recorded settings and optional composition props', () => {
  const value = {...settings, props: {title: 'Savings'}};
  assert.equal(validateSettings(value), value);
  assert.doesNotThrow(() => validateSettings({...settings, fps: 29.97}));
});

test('rejects invalid, unbounded or unsupported H.264 settings', () => {
  for (const value of [null, [], {...settings, width: 1919}, {...settings, height: -2}, {...settings, width: 10000}, {...settings, fps: 0}, {...settings, fps: Infinity}, {...settings, durationInFrames: 1.5}, {...settings, durationInFrames: 108001}, {...settings, props: []}, {...settings, props: null}]) {
    assert.throws(() => validateSettings(value));
  }
});

for (const host of ['127.0.0.1', '::1']) {
  test(`render port avoids an occupied ${host} listener`, async t => {
    const server = createServer();
    try {
      await new Promise((resolve, reject) => {server.once('error', reject); server.listen({host, port: 0}, resolve);});
    } catch (error) {
      if (host === '::1' && ['EAFNOSUPPORT', 'EADDRNOTAVAIL'].includes(error.code)) return t.skip('IPv6 unavailable');
      throw error;
    }
    try {
      const port = server.address().port;
      await assert.rejects(findRenderPort(port, port), /No free local rendering port/);
    } finally {await new Promise(resolve => server.close(resolve));}
  });
}
