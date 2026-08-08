/**
 * ============================================================================
 *  RELEASE-GATING BROWSER TESTS — TENANT ISOLATION THROUGH A REAL BROWSER
 * ============================================================================
 *
 *  If any test in this file fails, THE BUILD MUST NOT SHIP.
 *
 *  Phase 1A proved tenant isolation at the API and at the database. It never
 *  proved it through a browser, and `tests/e2e` from ADR-001 did not exist —
 *  gap G3. Every isolation assertion the project had was made by a test client
 *  that could not send a stray header, follow a redirect it was not expecting,
 *  or render a page containing the wrong organization's name.
 *
 *  The manipulations below are the ones an actual attacker reaches for first,
 *  and each is checked against tenant B's *real* identifiers, resolved from a
 *  real session, not against a placeholder.
 *
 *  What "no tenant B data appears" means here is deliberately strict: tenant
 *  B's slug and display name must be absent from the entire response, script
 *  payloads included, and its id must never be rendered. A page that leaked
 *  only the name would pass a narrower check while telling an attacker that
 *  Borealis Capital is a customer. See `expectNoTraceOfTenantB` for why the id
 *  is treated differently from the other two.
 * ============================================================================
 */

import {
  API_BASE_URL,
  TENANT_A_USER,
  TENANT_B_USER,
  expect,
  signInAs,
  test,
} from '../support/fixtures';
import { redact } from '../support/diagnostics';

const SESSION_COOKIE = 'eip_session';

/**
 * A settled card, not the loading skeleton.
 *
 * `loading.tsx` renders sections with the same class and the same headings, so
 * a naive `section.card` locator matches two elements and Playwright's strict
 * mode — correctly — refuses to guess.
 */
function card(page: import('@playwright/test').Page, heading: string) {
  return page.locator('section.card:not([aria-busy="true"])').filter({ hasText: heading });
}

/**
 * Assert that nothing identifying tenant B reached tenant A's session.
 *
 * Two strengths of assertion, and the distinction is the whole point.
 *
 * **Values the attacker did not supply** — checked against the *entire*
 * response, script payloads included. Their appearance anywhere could only mean
 * the server looked tenant B up, which is the disclosure being hunted.
 *
 * **Values the attack itself put in the URL** — checked against the *rendered
 * text* only. Next.js embeds the request URL in its RSC flight payload, so a
 * tenant identifier placed in the query string comes back in the response as an
 * echo of the attacker's own input. That is not a disclosure — the attacker
 * already had it — and asserting its total absence would fail on a page that
 * leaked nothing whatsoever. It must still never be *displayed*, which is what
 * the rendered-text check enforces.
 *
 * `supplied` is passed explicitly per attempt rather than inferred, so widening
 * it is a visible change in the diff rather than a quiet relaxation.
 */
async function expectNoTraceOfTenantB(
  page: import('@playwright/test').Page,
  tenantB: { id: string; slug: string; name: string },
  supplied: readonly string[] = [],
): Promise<void> {
  const dom = await page.content();
  const rendered = await page.locator('body').innerText();

  // Explicitly the three identifying fields. Iterating the whole object would
  // sweep in `status: "active"`, which every tenant shares — an assertion that
  // fails on a page leaking nothing is worse than no assertion, because the
  // fix for it is to weaken the test.
  const identifying = { id: tenantB.id, slug: tenantB.slug, name: tenantB.name };

  for (const [label, needle] of Object.entries(identifying)) {
    if (supplied.includes(needle)) {
      expect(rendered, `Tenant B's ${label} was displayed to tenant A.`).not.toContain(needle);
    } else {
      expect(dom, `Tenant B's ${label} appeared in tenant A's page.`).not.toContain(needle);
    }
  }
}

test.describe('signed in as tenant A', () => {
  test('the organization context shown is tenant A, resolved server-side', async ({
    page,
    tenantA,
  }) => {
    await signInAs(page, TENANT_A_USER);
    await page.waitForURL('**/app');

    const context = card(page, 'Organization context');
    await expect(context).toContainText(tenantA.name);
    await expect(context).toContainText(tenantA.slug);
    await expect(context).toContainText(tenantA.id);
    await expect(context).toContainText(TENANT_A_USER);
  });

  test('readiness information is displayed', async ({ page }) => {
    await signInAs(page, TENANT_A_USER);
    await page.waitForURL('**/app');

    const status = card(page, 'Platform status');
    await expect(status).toBeVisible();
    // The readiness check that matters is the isolation self-check. Its
    // presence on the page is the point — an operator can see that the API
    // verified isolation, not merely that it is running.
    await expect(status).toContainText('ready');
    await expect(status.locator('tbody tr')).not.toHaveCount(0);
  });

  test('the session token is unreachable from browser JavaScript', async ({ page }) => {
    /**
     * The premise every other assertion rests on. If a page script could read
     * the token, tenant isolation in the browser would be a rendering
     * convention rather than a security property.
     */
    await signInAs(page, TENANT_A_USER);
    await page.waitForURL('**/app');

    const exposed = await page.evaluate(() => ({
      cookie: document.cookie,
      local: JSON.stringify(window.localStorage),
      session: JSON.stringify(window.sessionStorage),
    }));

    expect(exposed.cookie).not.toContain(SESSION_COOKIE);
    expect(exposed.local).not.toContain('eyJ');
    expect(exposed.session).not.toContain('eyJ');

    // The negative control: the cookie IS there, the browser simply will not
    // hand it to script. Without this, the assertions above would pass for an
    // unauthenticated page.
    const cookies = await page.context().cookies();
    const session = cookies.find((cookie) => cookie.name === SESSION_COOKIE);
    expect(session, 'No session cookie was set — the test proved nothing.').toBeDefined();
    expect(session?.httpOnly).toBe(true);
  });
});

test.describe('manipulation toward tenant B', () => {
  test('a forged X-Tenant-Id header changes nothing', async ({ page, tenantA, tenantB }) => {
    await signInAs(page, TENANT_A_USER);
    await page.waitForURL('**/app');

    // The header an attacker tries first, on every request from here on.
    await page.setExtraHTTPHeaders({
      'X-Tenant-Id': tenantB.id,
      'X-Tenant-Slug': tenantB.slug,
    });
    await page.reload();

    await expect(card(page, 'Organization context')).toContainText(tenantA.name);
    await expectNoTraceOfTenantB(page, tenantB);
  });

  test('tenant identifiers in the URL change nothing', async ({ page, tenantA, tenantB }) => {
    await signInAs(page, TENANT_A_USER);
    await page.waitForURL('**/app');

    const attempts: ReadonlyArray<{ url: string; supplied: readonly string[] }> = [
      { url: `/app?tenant_id=${tenantB.id}`, supplied: [tenantB.id] },
      { url: `/app?tenantId=${tenantB.id}`, supplied: [tenantB.id] },
      { url: `/app?tenant=${tenantB.slug}`, supplied: [tenantB.slug] },
      { url: `/app#tenant=${tenantB.id}`, supplied: [tenantB.id] },
      // The name is never supplied in any attempt, so it is checked against
      // the whole response every time — the strictest assertion available.
    ];

    for (const { url, supplied } of attempts) {
      await page.goto(url);
      await expect(
        card(page, 'Organization context'),
        `${url} did not render tenant A`,
      ).toContainText(tenantA.name);
      await expectNoTraceOfTenantB(page, tenantB, supplied);
    }
  });

  test('a token naming tenant B grants nothing', async ({ page, tenantB }) => {
    /**
     * The strongest of these, because it attacks the mechanism rather than the
     * rendering. The token request *names* tenant B — the one place a tenant
     * identifier is legitimately accepted from the client — for a user who
     * belongs only to tenant A.
     *
     * The issuer honours the request and mints the token, deliberately: the
     * `tid` claim is a *request*, and refusing it at minting time would move
     * the decision to the wrong place. The membership lookup is where it is
     * answered, and it answers no — so the browser gets a cryptographically
     * valid token that opens nothing, and is returned to sign-in.
     *
     * A session cookie may be present afterwards. It is inert, and the loop
     * below proves it: navigating to the application with that cookie held
     * lands on sign-in every time.
     */
    await signInAs(page, TENANT_A_USER, tenantB.id);

    // The id was typed into the form, so it is legitimately still in the field.
    await expect(page).toHaveURL(/\/sign-in/);
    await expectNoTraceOfTenantB(page, tenantB, [tenantB.id]);

    await page.goto('/app');
    await expect(page).toHaveURL(/\/sign-in/);
    await expectNoTraceOfTenantB(page, tenantB, [tenantB.id]);
  });

  test("tenant B's own session sees tenant B and not tenant A", async ({
    page,
    tenantA,
    tenantB,
  }) => {
    /**
     * The negative control for the whole file. Every assertion above is that
     * tenant B's data is absent; this one proves that data exists and is
     * reachable by the right person — otherwise "absent" would be trivially
     * true and the suite would be testing nothing.
     */
    await signInAs(page, TENANT_B_USER);
    await page.waitForURL('**/app');

    const context = card(page, 'Organization context');
    await expect(context).toContainText(tenantB.name);
    await expect(context).toContainText(tenantB.id);

    const dom = await page.content();
    expect(dom).not.toContain(tenantA.id);
    expect(dom).not.toContain(tenantA.slug);
  });
});

test.describe('unauthenticated access', () => {
  test('reaching the application without a session returns to sign-in', async ({ page }) => {
    await page.context().clearCookies();
    await page.goto('/app');

    await expect(page).toHaveURL(/\/sign-in/);
    await expect(page.getByLabel('Email address')).toBeVisible();
  });

  test('signing out ends the session and the application is no longer reachable', async ({
    page,
  }) => {
    await signInAs(page, TENANT_A_USER);
    await page.waitForURL('**/app');

    await page.getByRole('button', { name: /sign out/i }).click();
    await expect(page).toHaveURL(/\/sign-in/);

    // Not merely redirected — the cookie is gone, so going back is not a way in.
    const cookies = await page.context().cookies();
    expect(cookies.find((cookie) => cookie.name === SESSION_COOKIE)).toBeUndefined();

    await page.goto('/app');
    await expect(page).toHaveURL(/\/sign-in/);
  });

  test('the API refuses an unauthenticated request for tenant data', async ({ request }) => {
    /**
     * The browser is one client. The guarantee has to hold for any of them, so
     * this bypasses the UI entirely.
     */
    const response = await request.get(`${API_BASE_URL}/v1/me`, { failOnStatusCode: false });
    expect(response.status()).toBe(401);
  });
});

test.describe('failure diagnostics', () => {
  test('redaction removes credentials from captured diagnostics', () => {
    /**
     * The diagnostics helper is the thing standing between a failing CI run and
     * a published bearer token, so it is tested rather than trusted. Playwright
     * traces and video are off for the same reason — both record complete
     * request headers.
     */
    const jwt = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhZGEifQ.c2lnbmF0dXJlLXZhbHVl';

    expect(redact(`Authorization: Bearer ${jwt}`)).not.toContain(jwt);
    expect(redact(`cookie: ${SESSION_COOKIE}=${jwt}; path=/`)).not.toContain(jwt);
    expect(redact(`{"access_token":"${jwt}"}`)).not.toContain(jwt);
    expect(redact(`?token=${jwt}&x=1`)).not.toContain(jwt);

    // Still useful afterwards: the shape of the message survives.
    expect(redact(`Authorization: Bearer ${jwt}`)).toContain('Authorization');
    expect(redact('GET /app 500 Internal Server Error')).toBe('GET /app 500 Internal Server Error');
  });
});
