const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const root = path.resolve(__dirname, '../../safar_web');

test('installable PWA manifest uses local app scope and valid local icons', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.webmanifest'), 'utf8'));
  assert.equal(manifest.display, 'standalone');
  assert.equal(manifest.short_name, 'SAFAR');
  const start = new URL(manifest.start_url, 'https://safar.test');
  const scope = new URL(manifest.scope, 'https://safar.test');
  assert.equal(start.origin, 'https://safar.test');
  assert.equal(scope.origin, start.origin);
  assert.ok(start.pathname.startsWith(scope.pathname));
  assert.ok(manifest.icons.some(icon => icon.purpose?.includes('maskable')));
  for (const icon of manifest.icons) {
    const url = new URL(icon.src, start);
    assert.equal(url.origin, start.origin);
    const filename = path.join(root, url.pathname.replace(/^\/safar\/?/, ''));
    assert.ok(fs.statSync(filename).size > 100, `Missing usable icon ${url.pathname}`);
    assert.ok(icon.type.startsWith('image/'));
  }
});

function workerHarness() {
  const filename = ['sw.js', 'service-worker.js'].find(name => fs.existsSync(path.join(root, name)));
  assert.ok(filename, 'SAFAR must provide its privacy-preserving service worker');
  const listeners = new Map();
  const writes = [];
  const reads = [];
  const requests = [];
  const pending = [];
  const shellCache = {
    addAll: async entries => { writes.push(...entries.map(String)); },
    put: async request => { writes.push(typeof request === 'string' ? request : request.url); },
    match: async request => { reads.push(typeof request === 'string' ? request : request.url); return undefined; },
  };
  const cacheStorage = {
    open: async () => shellCache,
    keys: async () => [],
    delete: async () => true,
    match: shellCache.match,
  };
  const self = {
    location: new URL('https://safar.test/safar/' + filename),
    addEventListener: (name, handler) => listeners.set(name, handler),
    skipWaiting: async () => {},
    clients: { claim: async () => {}, matchAll: async () => [] },
    registration: { scope: 'https://safar.test/safar/' },
  };
  const context = vm.createContext({
    self, caches: cacheStorage, URL, Request, Response, Headers,
    fetch: async request => {
      requests.push(typeof request === 'string' ? request : request.url);
      return new Response('network', { status: 200 });
    },
    console, Promise,
  });
  vm.runInContext(fs.readFileSync(path.join(root, filename), 'utf8'), context, { filename });
  const dispatch = async (name, properties = {}) => {
    let result;
    const event = {
      ...properties,
      waitUntil: task => pending.push(Promise.resolve(task)),
      respondWith: task => { result = Promise.resolve(task); },
    };
    const handler = listeners.get(name);
    assert.ok(handler, `Missing service worker ${name} listener`);
    handler(event);
    await Promise.all(pending.splice(0));
    if (result) await result;
  };
  return { dispatch, writes, reads, requests, context };
}

test('service worker installation caches only same-origin public shell assets', async () => {
  const worker = workerHarness();
  await worker.dispatch('install');
  assert.ok(worker.writes.length > 0);
  for (const entry of worker.writes) {
    const url = new URL(entry, 'https://safar.test');
    assert.equal(url.origin, 'https://safar.test');
    assert.ok(url.pathname.startsWith('/safar'), `Non-shell URL cached: ${entry}`);
    assert.ok(!url.search, `User-dependent query cached: ${entry}`);
    assert.ok(!entry.includes('/api/'));
  }
});

test('private API, media, foreign resources and mutations never use CacheStorage', async () => {
  const worker = workerHarness();
  const cases = [
    new Request('https://safar.test/api/safar/orders?limit=20'),
    new Request('https://safar.test/api/safar/orders/fixture-01/photo/0'),
    new Request('https://safar.test/api/safar/session'),
    new Request('https://safar.test/api/safar/analytics'),
    new Request('https://safar.test/api/safar/logout', { method: 'POST' }),
    new Request('https://telegram.org/js/telegram-web-app.js'),
    new Request('https://evil.example/safar/app.js'),
    new Request('https://safar.test/not-public'),
  ];
  for (const request of cases) await worker.dispatch('fetch', { request });
  assert.deepEqual(worker.writes, []);
  assert.deepEqual(worker.reads, []);
  // The worker may use fetch or let the browser handle a request. Both keep
  // protected responses outside its persistent cache.
});

test('an offline private response cannot fall back to a cached customer record', async () => {
  const worker = workerHarness();
  worker.context.fetch = async () => { throw new TypeError('offline'); };
  await worker.dispatch('fetch', { request: new Request('https://safar.test/api/safar/orders') })
    .catch(error => assert.match(error.message, /offline/));
  assert.deepEqual(worker.writes, []);
  assert.deepEqual(worker.reads, []);
});
