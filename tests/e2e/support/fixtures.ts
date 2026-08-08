/**
 * Shared fixtures for the browser suite.
 *
 * The suite needs two real tenants — isolation cannot be observed with one —
 * and it needs tenant B's identifiers in order to *attempt* to reach them. Those
 * identifiers are obtained through the API rather than hard-coded, so the tests
 * still work against a freshly seeded environment.
 *
 * Note what tenant B's identity is used for: it is the attack payload. Every
 * assertion that follows is that these strings appear nowhere in tenant A's
 * session.
 */

import { expect, test as base, type APIRequestContext, type Page } from '@playwright/test';

import { attachDiagnosticsOnFailure, collectConsole } from './diagnostics';

export const API_BASE_URL = process.env.EIP_API_BASE_URL ?? 'http://localhost:8000';

/** Seeded by `python -m eip.scripts.seed_demo`. */
export const TENANT_A_USER = 'ada@acme.invalid';
export const TENANT_B_USER = 'ben@borealis.invalid';

export interface TenantIdentity {
  id: string;
  slug: string;
  name: string;
}

/**
 * Resolve a seeded user's tenant through the API.
 *
 * Uses the local development identity path — the same one the browser uses —
 * rather than reading the database, so the fixture cannot drift from what a
 * user would actually get.
 */
export async function identityOf(
  request: APIRequestContext,
  email: string,
): Promise<TenantIdentity> {
  const tokenResponse = await request.post(`${API_BASE_URL}/v1/dev/token`, { data: { email } });
  expect(
    tokenResponse.ok(),
    `Could not obtain a development token for ${email}. Is the environment seeded?`,
  ).toBeTruthy();
  const { access_token: accessToken } = (await tokenResponse.json()) as { access_token: string };

  const meResponse = await request.get(`${API_BASE_URL}/v1/me`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  expect(meResponse.ok(), `/v1/me failed for ${email}`).toBeTruthy();
  const me = (await meResponse.json()) as { tenant: TenantIdentity };
  return me.tenant;
}

/** Sign in through the browser, exactly as a person would. */
export async function signInAs(page: Page, email: string, tenantId?: string): Promise<void> {
  await page.goto('/sign-in');
  await page.getByLabel('Email address').fill(email);
  if (tenantId) {
    await page.getByLabel('Organization ID (optional)').fill(tenantId);
  }
  await page.getByRole('button', { name: 'Sign in' }).click();
}

interface Fixtures {
  tenantA: TenantIdentity;
  tenantB: TenantIdentity;
  consoleMessages: string[];
}

export const test = base.extend<Fixtures>({
  tenantA: async ({ request }, use) => {
    await use(await identityOf(request, TENANT_A_USER));
  },
  tenantB: async ({ request }, use) => {
    await use(await identityOf(request, TENANT_B_USER));
  },
  consoleMessages: async ({ page }, use) => {
    await use(collectConsole(page));
  },
});

test.afterEach(async ({ page, consoleMessages }, testInfo) => {
  await attachDiagnosticsOnFailure(page, testInfo, consoleMessages);
});

export { expect };
