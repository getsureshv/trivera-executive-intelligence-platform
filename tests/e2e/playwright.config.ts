/**
 * Playwright configuration for the browser end-to-end suite.
 *
 * Two decisions here are security decisions rather than ergonomic ones.
 *
 * **Tracing and video are off.** Both would be genuinely useful, and both
 * capture full request headers — including `Authorization` and the session
 * cookie. A CI artefact containing a bearer token is a credential leak with a
 * download link, and it would be one nobody looked at until it mattered.
 * Diagnostics come instead from `diagnostics.ts`, which captures the same
 * information with the credential-bearing parts removed.
 *
 * **`storageState` is never written.** Playwright's usual pattern is to sign in
 * once and persist the cookie jar to disk for reuse. That file would contain a
 * live session token, and it would sit in the working tree. Each test signs in
 * for itself; the suite is small enough that the cost is a second or two.
 */

import { defineConfig, devices } from '@playwright/test';

const WEB_BASE_URL = process.env.EIP_WEB_BASE_URL ?? 'http://localhost:3000';

export default defineConfig({
  testDir: './specs',
  globalSetup: './support/prepare-executive-demo.ts',
  // Isolation is order-dependent in one direction only: a leaked session from
  // one test must not authenticate another. Serial execution plus a fresh
  // context per test makes that structural rather than hoped-for.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  // These are security tests. A retry that turns a real failure green is worse
  // than a flaky run, so there are none.
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],

  use: {
    baseURL: WEB_BASE_URL,
    // See the module docstring. Not a default worth inheriting.
    trace: 'off',
    video: 'off',
    // A screenshot shows the rendered page. The session cookie is `HttpOnly`
    // and never rendered, so this captures the failure without the credential.
    screenshot: 'only-on-failure',
    actionTimeout: 10_000,
  },

  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
