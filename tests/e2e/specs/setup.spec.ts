import type {
  ConnectionTest,
  DataSource,
  GovernedMetricEnvelope,
  LineageEnvelope,
  MeResponse,
} from '@eip/contracts';
import type { APIRequestContext } from '@playwright/test';

import {
  API_BASE_URL,
  expect,
  signInAs,
  TENANT_A_USER,
  TENANT_B_USER,
  test,
} from '../support/fixtures';

async function configurationEvidence(request: APIRequestContext) {
  const tokenResponse = await request.post(`${API_BASE_URL}/v1/dev/token`, {
    data: { email: TENANT_A_USER },
  });
  expect(tokenResponse.ok()).toBeTruthy();
  const { access_token: token } = (await tokenResponse.json()) as { access_token: string };
  const headers = { Authorization: `Bearer ${token}` };
  const [meResponse, sourcesResponse, dashboardResponse] = await Promise.all([
    request.get(`${API_BASE_URL}/v1/me`, { headers }),
    request.get(`${API_BASE_URL}/v1/data-sources`, { headers }),
    request.get(`${API_BASE_URL}/v1/dashboards/executive`, { headers }),
  ]);
  expect(meResponse.ok()).toBeTruthy();
  expect(sourcesResponse.ok()).toBeTruthy();
  expect(dashboardResponse.ok()).toBeTruthy();
  const me = (await meResponse.json()) as MeResponse;
  const sources = (await sourcesResponse.json()) as DataSource[];
  const dashboard = (await dashboardResponse.json()) as GovernedMetricEnvelope;
  const source = sources.find(
    (candidate) => candidate.id === dashboard.provenance.selected_source.data_source_id,
  );
  expect(source).toBeTruthy();
  const [latestResponse, lineageResponse] = await Promise.all([
    request.get(`${API_BASE_URL}/v1/data-sources/${source!.id}/connection-tests/latest`, {
      headers,
    }),
    request.get(
      `${API_BASE_URL}/v1/metrics/revenue_ytd/lineage?config_version=${dashboard.provenance.configuration_version}`,
      { headers },
    ),
  ]);
  expect(latestResponse.ok()).toBeTruthy();
  expect(lineageResponse.ok()).toBeTruthy();
  return {
    me,
    source: source!,
    dashboard,
    latest: (await latestResponse.json()) as ConnectionTest,
    lineage: (await lineageResponse.json()) as LineageEnvelope,
  };
}

test('configuration summary renders only joined governed API values', async ({
  page,
  request,
  tenantA,
}) => {
  const evidence = await configurationEvidence(request);
  const responseBodies: string[] = [];
  page.on('response', async (response) => {
    if (!(response.headers()['content-type'] ?? '').match(/json|text/)) return;
    try {
      responseBodies.push(await response.text());
    } catch {
      // A streamed response may already be consumed.
    }
  });
  await signInAs(page, TENANT_A_USER, tenantA.id);
  await page.waitForURL('**/app');
  await page.goto('/app/setup');

  await expect(
    page.getByRole('heading', { name: `Configured for ${evidence.me.tenant.name}` }),
  ).toBeVisible();
  const organizationCard = page.getByRole('article').filter({ hasText: 'Organization & calendar' });
  const sourceCard = page.getByRole('article').filter({ hasText: 'Selected PostgreSQL source' });
  const metricCard = page.getByRole('article').filter({ hasText: 'Governed metric' });
  const deliveryCard = page.getByRole('article').filter({ hasText: 'Executive delivery' });
  await expect(sourceCard.getByRole('heading', { name: evidence.source.name })).toBeVisible();
  await expect(
    metricCard.getByRole('heading', { name: evidence.dashboard.metric_name }),
  ).toBeVisible();
  await expect(
    metricCard.getByText(`Version ${evidence.dashboard.metric_version}`, { exact: true }),
  ).toBeVisible();
  await expect(
    organizationCard.getByText(evidence.dashboard.period.timezone, { exact: true }),
  ).toBeVisible();
  await expect(
    metricCard.getByText(evidence.dashboard.allowed_drill_down[0]!.replaceAll('_', ' '), {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    metricCard.getByText(evidence.dashboard.accountable_owner, { exact: true }),
  ).toBeVisible();
  const widget = evidence.lineage.nodes.find((node) => node.kind === 'widget');
  expect(widget).toBeTruthy();
  await expect(deliveryCard.getByRole('heading', { name: widget!.label })).toBeVisible();
  await expect(
    deliveryCard.getByText('Published for executive use', { exact: true }),
  ).toBeVisible();
  await expect(sourceCard.getByText(evidence.latest.status, { exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Preview Executive Dashboard' })).toBeVisible();

  const sentinel = process.env.EIP_E2E_SOURCE_PASSWORD;
  expect(sentinel, 'EIP_E2E_SOURCE_PASSWORD is required for setup leakage testing.').toBeTruthy();
  expect(page.url()).not.toContain(sentinel!);
  expect(await page.content()).not.toContain(sentinel!);
  expect(responseBodies.join('\n')).not.toContain(sentinel!);
  expect(JSON.stringify(evidence)).not.toContain(sentinel!);
  expect((await page.screenshot()).toString('latin1')).not.toContain(sentinel!);
  expect(
    await page.evaluate(() => JSON.stringify({ local: localStorage, session: sessionStorage })),
  ).not.toContain(sentinel!);
});

test('configuration summary is contained at 390 pixels', async ({ page, tenantA }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signInAs(page, TENANT_A_USER, tenantA.id);
  await page.waitForURL('**/app');
  await page.goto('/app/setup');
  await expect(page.getByRole('link', { name: 'Preview Executive Dashboard' })).toBeVisible();
  const containment = await page.evaluate(() => ({
    viewportWidth: window.innerWidth,
    documentWidth: document.documentElement.scrollWidth,
    offenders: Array.from(document.querySelectorAll<HTMLElement>('body *'))
      .map((element) => {
        const bounds = element.getBoundingClientRect();
        return {
          tag: element.tagName,
          className: element.className,
          text: (element.textContent ?? '').trim().slice(0, 120),
          left: bounds.left,
          right: bounds.right,
          width: bounds.width,
        };
      })
      .filter(({ left, right }) => left < -1 || right > window.innerWidth + 1),
  }));
  expect(containment.documentWidth, JSON.stringify(containment, null, 2)).toBeLessThanOrEqual(
    containment.viewportWidth,
  );
});

test('forged tenant selection cannot render another tenant configuration', async ({
  page,
  tenantA,
  tenantB,
}) => {
  await signInAs(page, TENANT_A_USER, tenantB.id);
  await expect(page).toHaveURL(/\/sign-in/);
  await expect(page.getByText(tenantB.name, { exact: true })).toHaveCount(0);
  await signInAs(page, TENANT_B_USER, tenantA.id);
  await expect(page).toHaveURL(/\/sign-in/);
  await expect(page.getByText(tenantA.name, { exact: true })).toHaveCount(0);
});
