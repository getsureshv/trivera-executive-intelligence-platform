import type { GovernedMetricEnvelope, LineageEnvelope } from '@eip/contracts';
import type { APIRequestContext } from '@playwright/test';

import {
  API_BASE_URL,
  expect,
  signInAs,
  TENANT_A_USER,
  TENANT_B_USER,
  test,
} from '../support/fixtures';

async function governedEvidence(request: APIRequestContext) {
  const tokenResponse = await request.post(`${API_BASE_URL}/v1/dev/token`, {
    data: { email: TENANT_A_USER },
  });
  expect(tokenResponse.ok()).toBeTruthy();
  const { access_token: token } = (await tokenResponse.json()) as { access_token: string };
  const headers = { Authorization: `Bearer ${token}` };
  const dashboardResponse = await request.get(`${API_BASE_URL}/v1/dashboards/executive`, {
    headers,
  });
  expect(dashboardResponse.ok()).toBeTruthy();
  const dashboard = (await dashboardResponse.json()) as GovernedMetricEnvelope;
  const queryResponse = await request.post(`${API_BASE_URL}/v1/metrics/revenue_ytd/query`, {
    headers,
    data: {
      period: {
        kind: dashboard.period.kind,
        timezone: dashboard.period.timezone,
        as_of_at: dashboard.period.as_of_at,
      },
      group_by: dashboard.allowed_drill_down[0],
    },
  });
  expect(queryResponse.ok()).toBeTruthy();
  const metric = (await queryResponse.json()) as GovernedMetricEnvelope;
  const lineageResponse = await request.get(
    `${API_BASE_URL}/v1/metrics/revenue_ytd/lineage?config_version=${dashboard.provenance.configuration_version}`,
    { headers },
  );
  expect(lineageResponse.ok()).toBeTruthy();
  return {
    metric,
    governedJson: JSON.stringify({ dashboard, metric }),
    lineage: (await lineageResponse.json()) as LineageEnvelope,
  };
}

function decimalUnits(values: string[]): bigint[] {
  const scale = Math.max(...values.map((value) => value.split('.')[1]?.length ?? 0));
  return values.map((value) => {
    const negative = value.startsWith('-');
    const [whole = '0', fraction = ''] = value.replace('-', '').split('.');
    const magnitude = BigInt(`${whole}${fraction.padEnd(scale, '0')}`);
    return negative ? -magnitude : magnitude;
  });
}

test('dashboard opens attention and one-click trust using governed API evidence', async ({
  page,
  request,
  tenantA,
}) => {
  const evidence = await governedEvidence(request);
  const browserBodies: string[] = [];
  page.on('response', async (response) => {
    const type = response.headers()['content-type'] ?? '';
    if (!type.includes('text') && !type.includes('json')) return;
    try {
      browserBodies.push(await response.text());
    } catch {
      // Streaming framework responses can already be consumed.
    }
  });
  await signInAs(page, TENANT_A_USER, tenantA.id);
  await page.waitForURL('**/app');
  await page.goto('/app/executive');

  await expect(
    page.getByRole('heading', { name: evidence.metric.metric_name }).first(),
  ).toBeVisible();
  await expect(page.locator('.metric-value')).toContainText(
    evidence.metric.value.replace(/\B(?=(\d{3})+(?!\d))/g, ','),
  );
  await expect(
    page.getByText(evidence.metric.provenance.origin_label, { exact: true }),
  ).toHaveCount(1);
  await expect(page.getByText('Progress to target', { exact: true })).toBeVisible();
  await expect(page.getByText('Prior year', { exact: true })).toBeVisible();
  const drillDown = page.locator('.executive-drilldown');
  for (const slice of evidence.metric.drill_down) {
    await expect(drillDown.getByRole('heading', { name: slice.label })).toBeVisible();
  }
  const units = decimalUnits([
    ...evidence.metric.drill_down.map((slice) => slice.value),
    evidence.metric.value,
  ]);
  expect(units.slice(0, -1).reduce((sum, value) => sum + value, 0n)).toBe(units.at(-1));
  await expect(page.getByText('Reconciled', { exact: true })).toBeVisible();

  const attention = page.locator('.attention-card');
  await expect(
    attention.getByRole('heading', { name: evidence.metric.attention.label }),
  ).toBeVisible();
  await expect(attention.getByText(/configured target\.$/)).toBeVisible();
  await page.getByRole('button', { name: `Go to ${evidence.metric.attention.label}` }).click();
  await expect(
    page.locator(`#segment-${evidence.metric.attention.dimension_value_id}`),
  ).toBeFocused();
  await page.getByRole('button', { name: 'View attention details' }).click();
  const formattedAttentionVariance = new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: evidence.metric.unit,
    maximumFractionDigits: 0,
  }).format(BigInt(evidence.metric.attention.target_variance));
  await expect(page.getByText(formattedAttentionVariance, { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Open trust view' }).click();
  const lineagePath = page.getByRole('list', { name: 'Derived lineage path' });
  await expect(lineagePath).toBeVisible();
  for (const node of evidence.lineage.nodes) {
    await expect(lineagePath.getByText(node.label, { exact: true })).toBeVisible();
  }

  const sentinel = process.env.EIP_E2E_SOURCE_PASSWORD;
  expect(
    sentinel,
    'EIP_E2E_SOURCE_PASSWORD is required for executive leakage testing.',
  ).toBeTruthy();
  expect(page.url()).not.toContain(sentinel!);
  expect(await page.content()).not.toContain(sentinel!);
  expect(evidence.governedJson).not.toContain(sentinel!);
  expect(JSON.stringify(evidence.lineage)).not.toContain(sentinel!);
  expect(browserBodies.join('\n')).not.toContain(sentinel!);
  expect((await page.screenshot()).toString('latin1')).not.toContain(sentinel!);
  expect(
    await page.evaluate(() => JSON.stringify({ local: localStorage, session: sessionStorage })),
  ).not.toContain(sentinel!);
});

test('executive evidence remains usable at a narrow viewport', async ({ page, tenantA }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signInAs(page, TENANT_A_USER, tenantA.id);
  await page.waitForURL('**/app');
  await page.goto('/app/executive');
  await expect(page.getByRole('button', { name: 'Open trust view' })).toBeVisible();
  await page.getByRole('button', { name: 'Open trust view' }).click();
  await expect(page.getByRole('list', { name: 'Derived lineage path' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});

test('a forged organization selection cannot render another tenant', async ({
  page,
  tenantA,
  tenantB,
}) => {
  await signInAs(page, TENANT_A_USER, tenantB.id);
  await expect(page).toHaveURL(/\/sign-in/);
  await expect(page.getByText(tenantB.name, { exact: true })).toHaveCount(0);
  await expect(page.getByText(tenantB.slug, { exact: true })).toHaveCount(0);
  await signInAs(page, TENANT_B_USER, tenantA.id);
  await expect(page).toHaveURL(/\/sign-in/);
  await expect(page.getByText(tenantA.name, { exact: true })).toHaveCount(0);
});
