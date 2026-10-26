import {createConnection, createServer} from 'node:net';

const reachable = (port, host) => new Promise(resolve => {
  const socket = createConnection({port, host});
  let done = false;
  const finish = value => {
    if (done) return;
    done = true;
    socket.destroy();
    resolve(value);
  };
  socket.once('connect', () => finish(true));
  socket.once('error', error => finish(!['ECONNREFUSED', 'EAFNOSUPPORT', 'EADDRNOTAVAIL'].includes(error.code)));
  socket.setTimeout(500, () => finish(true));
});

const available = async (port, host) => {
  // Forwarded local services can be reachable even when a bind probe succeeds.
  if (await reachable(port, host)) return false;
  return new Promise((resolve, reject) => {
  const server = createServer();
  server.unref();
  server.once('error', error => {
    if (['EADDRINUSE', 'EACCES'].includes(error.code)) resolve(false);
    else if (host === '::1' && ['EAFNOSUPPORT', 'EADDRNOTAVAIL'].includes(error.code)) resolve(true);
    else reject(error);
  });
  server.listen({port, host}, () => server.close(() => resolve(true)));
  });
};

// Remotion's render server binds IPv4 but navigates to "localhost". A Studio
// server on IPv6 at the same port would otherwise receive the browser request.
export async function findRenderPort(from = 3000, to = 3100) {
  for (let port = from; port <= to; port++) {
    if (await available(port, '127.0.0.1') && await available(port, '::1')) return port;
  }
  throw new Error(`No free local rendering port between ${from} and ${to}.`);
}
