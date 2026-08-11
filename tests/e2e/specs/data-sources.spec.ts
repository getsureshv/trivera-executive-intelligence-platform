import { randomUUID } from 'node:crypto';

import { expect, signInAs, TENANT_A_USER, test } from '../support/fixtures';

test('adds a PostgreSQL source and completes a safe connection test', async ({ page, tenantA }) => {
  test.setTimeout(90_000);
  const sentinel = process.env.EIP_E2E_SOURCE_PASSWORD;
  expect(
    sentinel,
    'EIP_E2E_SOURCE_PASSWORD must be a unique credential provisioned for the source PostgreSQL login.',
  ).toBeTruthy();
  const responseBodies: string[] = [];
  page.on('response', async (response) => {
    const contentType = response.headers()['content-type'] ?? '';
    if (!contentType.includes('json') && !contentType.includes('text/x-component')) return;
    try {
      responseBodies.push(await response.text());
    } catch {
      // Redirects and already-consumed streaming bodies may not be readable.
    }
  });
  await signInAs(page, TENANT_A_USER, tenantA.id);
  await expect(page).toHaveURL(/\/app$/);
  await page.goto('/app/data-sources');

  const sourceName = `Browser source ${randomUUID()}`;
  await page.getByLabel('Name', { exact: true }).fill(sourceName);
  await page.getByLabel('Host').fill(process.env.EIP_E2E_SOURCE_HOST ?? 'postgres');
  await page.getByLabel('Port').fill('5432');
  await page.getByLabel('Username').fill(process.env.EIP_E2E_SOURCE_USER ?? 'eip_app');
  await page.getByLabel('Database').fill('eip');
  await page.getByLabel('TLS mode').selectOption('disable');
  await page.getByLabel('Password').fill(sentinel!);
  await page.getByRole('button', { name: 'Add source' }).click();

  await expect(page.getByText('PostgreSQL source added.', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Password')).toHaveValue('');
  const sourceCard = page.locator('.source-card').filter({ hasText: sourceName });
  await sourceCard.getByRole('button', { name: 'Test connection' }).click();
  await expect(sourceCard.getByText('Connection succeeded.')).toBeVisible({ timeout: 60_000 });

  page.once('dialog', (dialog) => dialog.accept());
  await sourceCard.getByRole('button', { name: 'Disable source' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Data source disabled.' })).toBeVisible();
  await expect(sourceCard.getByText(/· disabled$/)).toBeVisible();

  expect(page.url()).not.toContain(sentinel);
  expect(await page.content()).not.toContain(sentinel);
  expect(responseBodies.join('\n')).not.toContain(sentinel);
  expect(
    await page.evaluate(() => JSON.stringify({ local: localStorage, session: sessionStorage })),
  ).not.toContain(sentinel);
});
