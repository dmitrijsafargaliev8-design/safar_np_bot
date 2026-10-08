const fs = require('node:fs');
const path = require('node:path');
const { test, expect } = require('@playwright/test');
const AxeBuilder = require('@axe-core/playwright').default;

const screenshots = process.env.SAFAR_SCREENSHOT_DIR || path.resolve('test-results/screenshots');

test.beforeEach(async ({ page }) => {
  // Even accidental production URLs in the UI are denied. The Telegram SDK is
  // replaced by the actual, locally signed WebApp payload below.
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin === 'http://127.0.0.1:8765') return route.continue();
    if (url.protocol === 'data:' || url.protocol === 'blob:') return route.continue();
    return route.abort('blockedbyclient');
  });
});

async function signIn(page) {
  const signed = await (await page.request.get('/__test__/init-data')).json();
  await page.addInitScript(initData => {
    window.Telegram = { WebApp: { initData, ready() {}, expand() {}, colorScheme: 'dark' } };
  }, signed.initData);
  await page.goto('/safar');
  await expect(page.locator('.nav-link[data-tab="orders"]')).toBeAttached();
  await expect(page.locator('.order-row').first()).toBeVisible();
}

async function tab(page, id) {
  const desktop = page.locator(`.nav-link[data-tab="${id}"]`);
  await (await desktop.isVisible() ? desktop : page.locator(`.bottom-item[data-tab="${id}"]`)).click();
}

async function screenshot(page, filename) {
  fs.mkdirSync(screenshots, { recursive: true });
  await expect.poll(() => page.evaluate(() => [...document.images].filter(image => {
    const rect = image.getBoundingClientRect();
    return rect.width > 0 && rect.top < innerHeight && rect.bottom > 0;
  }).every(image => image.complete && image.naturalWidth > 0))).toBeTruthy();
  await page.screenshot({ path: path.join(screenshots, filename), fullPage: false });
}

test('SAFAR CONTROL shows a safe intake gate, returns tab and verified carrier radar', async ({ page }) => {
  await signIn(page);
  await page.locator('[data-action="open-intake"]').first().click();
  const dialog = page.locator('#actionDialog');
  await expect(dialog.locator('#intakeText')).toBeDisabled();
  await expect(dialog).toContainText('сховищ');
  await dialog.locator('[data-action="close-dialog"]').first().click();

  await tab(page, 'shipments');
  await expect(page.locator('.radar-panel')).toBeVisible();
  await expect(page.locator('.radar-row').first()).toBeVisible();
  await expect(page.locator('.radar-panel')).toContainText('ТТН створено');

  await tab(page, 'returns');
  await expect(page.locator('main.workspace h1')).toContainText('Повернення');
  await expect(page.locator('.return-case-list')).toHaveCount(0);
  expect((await page.request.get('/api/safar/returns')).status()).toBe(200);
});

test('guest access fails closed, forged Telegram auth never returns orders', async ({ page }) => {
  await page.goto('/safar');
  await expect(page.locator('[data-action="pair-start"]')).toBeVisible();
  expect((await page.request.get('/api/safar/orders')).status()).toBe(401);
  expect((await page.request.get('/api/safar/orders/foreign-secret')).status()).toBe(401);
  expect((await page.request.get('/api/safar/orders/fixture-01/photo/0')).status()).toBe(401);
  expect((await page.request.post('/api/safar/session', {
    headers: { Origin: 'http://127.0.0.1:8765' }, data: { initData: 'user=%7B%22id%22%3A100%7D' },
  })).status()).toBe(401);
  await expect(page.locator('.order-row')).toHaveCount(0);
});

test('signed login restores its HttpOnly session and logout removes private views', async ({ page }) => {
  const signed = await (await page.request.get('/__test__/init-data')).json();
  const auth = await page.request.post('/api/safar/session', {
    headers: { Origin: 'http://127.0.0.1:8765' }, data: { initData: signed.initData },
  });
  expect(auth.ok()).toBeTruthy();
  expect(auth.headers()['set-cookie']).toMatch(/HttpOnly/);
  expect(auth.headers()['set-cookie']).toMatch(/Secure/);
  const session = await auth.json();
  expect(session.csrf_token).toBeTruthy();
  await page.goto('/safar');
  await expect(page.locator('.order-row').first()).toBeVisible();
  expect(await page.evaluate(() => document.cookie)).not.toContain('safar_app_session');
  await page.reload();
  await expect(page.locator('.order-row').first()).toBeVisible();
  await tab(page, 'settings');
  await page.locator('[data-action="logout"]').click();
  await expect(page.locator('[data-action="pair-start"]')).toBeVisible();
  expect((await page.request.get('/api/safar/orders')).status()).toBe(401);
  const persisted = await page.evaluate(() => JSON.stringify({ ...localStorage }));
  expect(persisted).not.toContain('Тестовий Одержувач');
  expect(persisted).not.toContain('csrf_token');
  expect(persisted).not.toContain('safar_app_session');
});

test('device pairing remains private until bot approval and is consumed exactly once', async ({ page }) => {
  await page.goto('/safar');
  const startResponse = page.waitForResponse(response => response.url().endsWith('/api/safar/pairing/start'));
  await page.locator('[data-action="pair-start"]').click();
  const pairing = await (await startResponse).json();
  expect(pairing.device_token).toBeTruthy();
  await expect(page.locator('body')).toContainText(pairing.code);
  expect((await page.request.get('/api/safar/orders')).status()).toBe(401);
  const approved = await page.request.post('/__test__/approve-pairing', { data: { code: pairing.code } });
  expect((await approved.json()).approved).toBeTruthy();
  await expect(page.locator('.order-row').first()).toBeVisible({ timeout: 12_000 });
  const replay = await page.request.post('/api/safar/pairing/complete', {
    headers: { Origin: 'http://127.0.0.1:8765' }, data: { device_token: pairing.device_token },
  });
  expect(replay.status()).toBe(401);
});

test('a tracking response arriving after logout cannot restore private state or block the next session', async ({ page }) => {
  await signIn(page);
  await tab(page, 'orders');
  await page.locator('[data-order="fixture-06"]').click();
  let release, notifyStarted;
  const delayed = new Promise(resolve => { release = resolve; });
  const started = new Promise(resolve => { notifyStarted = resolve; });
  const handler = async route => {
    const response = await route.fetch();
    notifyStarted();
    await delayed;
    await route.fulfill({ response });
  };
  await page.route('**/fixture-06/tracking?**', handler);
  await page.locator('[data-action="track"]').click();
  await started;
  await tab(page, 'settings');
  await page.locator('[data-action="logout"]').click();
  await expect(page.locator('[data-action="pair-start"]')).toBeVisible();
  const late = page.waitForResponse(r => r.url().includes('/fixture-06/tracking'));
  release();
  await late;
  await expect(page.locator('.order-row')).toHaveCount(0);
  await expect(page.locator('.tracking-result')).toHaveCount(0);
  await page.unroute('**/fixture-06/tracking?**', handler);
  await signIn(page);
  await tab(page, 'orders');
  await page.locator('[data-order="fixture-06"]').click();
  await expect(page.locator('[data-action="track"]')).toBeEnabled();
  await page.locator('[data-action="track"]').click();
  await expect(page.locator('.tracking-result strong')).toHaveText('ТТН створено');
});

test('scoped pagination, search and filter are server-backed and preserve search focus', async ({ page }) => {
  await signIn(page);
  await tab(page, 'orders');
  await expect(page.locator('.order-row').first()).toBeVisible();
  const initial = await page.locator('.order-row').count();
  expect(initial).toBeGreaterThan(0);
  expect(initial).toBeLessThan(45);
  await page.locator('[data-action="load-more"]').click();
  await expect.poll(() => page.locator('.order-row').count()).toBeGreaterThan(initial);
  expect(await page.locator('body').innerText()).not.toContain('FOREIGN OWNER');
  const search = page.locator('#orderSearch');
  const searching = page.waitForResponse(response => response.url().includes('/api/safar/orders?') && response.url().includes('search='));
  await search.fill('Одержувач 42');
  await searching;
  await expect(page.locator('.order-row')).toHaveCount(1);
  await expect(page.locator('.order-row')).toContainText('Тестовий Одержувач 42');
  await expect(search).toBeFocused();
  await search.fill('');
  await expect.poll(() => page.locator('.order-row').count()).toBeGreaterThan(1);
  await page.locator('[data-filter="attention"]').click();
  await expect(page.locator('.order-row .state-created')).toHaveCount(0);
  await expect(page.locator('.order-row').first()).toBeVisible();
});

test('authorized gallery opens full photo with keyboard navigation and escape restores focus', async ({ page }) => {
  await signIn(page);
  await tab(page, 'orders');
  await page.locator('[data-order="fixture-01"]').click();
  const photo = page.locator('.gallery-item[data-photo="0"]');
  await expect(photo.locator('img')).toBeVisible();
  expect(await photo.locator('img').evaluate(image => image.complete && image.naturalWidth > 0)).toBeTruthy();
  await screenshot(page, 'detail-1440.png');
  await page.setViewportSize({ width: 390, height: 844 });
  await screenshot(page, 'detail-390.png');
  await photo.click();
  const dialog = page.locator('#photoDialog');
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('img')).toBeVisible();
  await page.keyboard.press('ArrowRight');
  await expect(dialog).toContainText('2');
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();
  await expect(photo).toBeFocused();
});

test('safe correction reviews fields and saves a draft without changing an issued TTN', async ({ page }) => {
  await signIn(page);
  await tab(page, 'orders');
  await page.locator('[data-order="fixture-06"]').click();
  await expect(page.locator('[data-action="edit"]')).toBeVisible();
  const original = await (await page.request.get('/api/safar/orders/fixture-06')).json();
  await page.locator('[data-action="edit"]').click();
  await page.locator('#edit-cost').fill('1950');
  await page.locator('#correctionForm button[type="submit"]').click();
  await expect(page.locator('#actionDialog')).toBeVisible();
  await expect(page.locator('.review-diff')).toContainText('1950');
  const response = page.waitForResponse(r => r.url().includes('/fixture-06/corrections') && r.request().method() === 'POST');
  await page.locator('[data-action="confirm-correction"]').click();
  const saved = await (await response).json();
  expect(saved.ttn_modified).toBe(false);
  expect(saved.enqueued).toBe(false);
  expect(saved.order.ttn).toBe(original.order.ttn);
  expect(saved.order.declared).toBe(original.order.declared);
  expect(saved.order.pending_order.cost).toBe(1950);
  await expect(page.locator('#actionDialog')).not.toBeVisible();
  await expect(page.locator('#toastRegion')).toBeVisible();
});

test('failed private photo can be retried inside the full album viewer', async ({ page }) => {
  let fail = true;
  await page.route('**/api/safar/orders/fixture-01/photo/0?**', route => {
    if (fail) return route.abort('failed');
    return route.continue();
  });
  await signIn(page);
  await tab(page, 'orders');
  await page.locator('[data-order="fixture-01"]').click();
  await expect(page.locator('.gallery-item[data-photo="0"] .photo-fallback')).toBeVisible();
  await page.locator('.gallery-item[data-photo="0"]').click();
  fail = false;
  await page.locator('#photoDialog [data-action="photo-retry"]').last().click();
  const photo = page.locator('#photoDialog img');
  await expect(photo).toBeVisible();
  await expect.poll(() => photo.evaluate(image => image.complete && image.naturalWidth > 0)).toBeTruthy();
});

test('mutation rejects missing and forged CSRF without changing current shipment', async ({ page }) => {
  await signIn(page);
  const before = await (await page.request.get('/api/safar/orders/fixture-01')).json();
  const missing = await page.request.post('/api/safar/orders/fixture-01/corrections', {
    headers: { Origin: 'http://127.0.0.1:8765' },
    data: { expected_revision: before.order.revision, fields: { cost: 900 } },
  });
  expect(missing.status()).toBe(403);
  const forged = await page.request.post('/api/safar/orders/fixture-01/corrections', {
    headers: { Origin: 'http://127.0.0.1:8765', 'X-CSRF-Token': 'forged-token' },
    data: { expected_revision: before.order.revision, fields: { cost: 900 } },
  });
  expect(forged.status()).toBe(403);
  const after = await (await page.request.get('/api/safar/orders/fixture-01')).json();
  expect(after.order.ttn).toBe(before.order.ttn);
  expect(after.order.declared).toBe(before.order.declared);
  expect((await page.request.get('/api/safar/orders/foreign-secret')).status()).toBe(404);
});

test('validated correction is a draft with immutable TTN; stale and uncertain edits stay blocked', async ({ page }) => {
  await signIn(page);
  const session = await (await page.request.get('/api/safar/session')).json();
  const before = await (await page.request.get('/api/safar/orders/fixture-07')).json();
  const headers = { Origin: 'http://127.0.0.1:8765', 'X-CSRF-Token': session.csrf_token };
  const stagedResponse = await page.request.post('/api/safar/orders/fixture-07/corrections', {
    headers, data: { expected_revision: before.order.revision, fields: { cost: 900 } },
  });
  expect(stagedResponse.ok()).toBeTruthy();
  const staged = await stagedResponse.json();
  expect(staged.staged).toBe(true);
  expect(staged.enqueued).toBe(false);
  expect(staged.ttn_modified).toBe(false);
  expect(staged.order.ttn).toBe(before.order.ttn);
  expect(staged.order.declared).toBe(before.order.declared);
  expect(staged.order.cod).toBe(before.order.cod);
  const stale = await page.request.post('/api/safar/orders/fixture-07/corrections', {
    headers, data: { expected_revision: before.order.revision, fields: { cost: 800 } },
  });
  expect(stale.status()).toBe(409);
  const uncertain = await (await page.request.get('/api/safar/orders/fixture-05')).json();
  const locked = await page.request.post('/api/safar/orders/fixture-05/corrections', {
    headers, data: { expected_revision: uncertain.order.revision, fields: { cost: 800 } },
  });
  expect(locked.status()).toBe(409);
});

test('offline state is explicit and protected data is never persisted by the PWA', async ({ page, context }) => {
  await signIn(page);
  await expect.poll(() => page.evaluate(() => navigator.serviceWorker?.controller !== null)).toBeTruthy();
  await context.setOffline(true);
  await expect(page.locator('#offlineBanner')).toBeVisible();
  await tab(page, 'orders');
  await expect(page.locator('[data-action="refresh"]').last()).toBeDisabled();
  const cached = await page.evaluate(async () => {
    const names = await caches.keys();
    const urls = [];
    for (const name of names) {
      const cache = await caches.open(name);
      urls.push(...(await cache.keys()).map(request => request.url));
    }
    return urls;
  });
  expect(cached.some(url => url.includes('/api/'))).toBeFalsy();
  expect(cached.some(url => url.includes('/photo/'))).toBeFalsy();
  // Install before the app's listener to model an OS/browser that misses the
  // online event; manual recovery must work independently of that event.
  await page.addInitScript(() => {
    window.safarTestBlockOnline = true;
    window.addEventListener('online', event => {
      if (window.safarTestBlockOnline) event.stopImmediatePropagation();
    });
  });
  await page.reload();
  await expect(page.locator('[data-action="pair-start"]')).toBeVisible();
  await expect(page.locator('.order-row')).toHaveCount(0);
  await expect(page.locator('.offline-banner')).toBeVisible();
  await context.setOffline(false);
  await page.locator('[data-action="reconnect"]').click();
  await expect(page.locator('.offline-banner')).not.toBeVisible();
  await expect(page.locator('.order-row').first()).toBeVisible();
  await page.evaluate(() => { window.safarTestBlockOnline = false; });
  // Also verify normal event-driven recovery actually refreshes private data.
  await context.setOffline(true);
  await expect(page.locator('#offlineBanner')).toBeVisible();
  const refreshed = page.waitForResponse(response => response.url().includes('/api/safar/orders?') && response.ok());
  await context.setOffline(false);
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
  await refreshed;
  await expect(page.locator('#offlineBanner')).not.toBeVisible();
  await expect(page.locator('.order-row').first()).toBeVisible();
});

for (const width of [360, 390, 412, 1440]) {
  test(`premium UI ${width}px has usable navigation, no horizontal overflow, and screenshot evidence`, async ({ page }) => {
    await page.setViewportSize({ width, height: width < 600 ? 844 : 1000 });
    await signIn(page);
    await screenshot(page, `overview-${width}.png`);
    for (const id of ['orders', 'shipments', 'senders', 'settings']) {
      await tab(page, id);
      await expect(page.locator('h1')).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
      if (id === 'orders') await screenshot(page, `orders-${width}.png`);
    }
    if (width < 600) {
      const nav = page.locator('.bottom-nav');
      await expect(nav).toBeVisible();
      await expect.poll(() => page.evaluate(() => {
        const nav = document.querySelector('.bottom-nav');
        return nav ? nav.getBoundingClientRect().width : Infinity;
      })).toBeLessThanOrEqual(width);
      await expect.poll(() => page.evaluate(() => Math.min(...Array.from(
        document.querySelectorAll('.bottom-item'), button => button.getBoundingClientRect().height)))).toBeGreaterThanOrEqual(44);
    }
  });
}

test('desktop and mobile critical pages pass WCAG AA automated checks', async ({ page }) => {
  await signIn(page);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width < 600 ? 844 : 1000 });
    for (const id of ['home', 'orders', 'settings']) {
      await tab(page, id);
      const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
      expect(result.violations.map(issue => ({ id: issue.id, impact: issue.impact, nodes: issue.nodes.map(node => node.target) }))).toEqual([]);
    }
  }
});
