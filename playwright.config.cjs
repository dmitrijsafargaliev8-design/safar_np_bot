const { defineConfig } = require('@playwright/test');

// A remote baseURL is deliberately unsupported: no browser QA may reach the
// deployed bot, a customer database, or Nova Poshta.
const baseURL = 'http://127.0.0.1:8765';
module.exports = defineConfig({
  testDir: './tests/e2e',
  timeout: 30_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  forbidOnly: Boolean(process.env.CI),
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL,
    browserName: 'chromium',
    viewport: { width: 1440, height: 1000 },
    locale: 'uk-UA',
    timezoneId: 'Europe/Kyiv',
    colorScheme: 'dark',
    reducedMotion: 'reduce',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: process.env.SAFAR_CHROMIUM_PATH
      ? { executablePath: process.env.SAFAR_CHROMIUM_PATH }
      : {},
  },
  webServer: {
    command: `${process.env.SAFAR_PYTHON || 'python'} tests/e2e/local_server.py`,
    url: `${baseURL}/__test__/health`,
    reuseExistingServer: false,
    timeout: 30_000,
    env: { SAFAR_DISABLE_BACKGROUND: '1' },
  },
});
