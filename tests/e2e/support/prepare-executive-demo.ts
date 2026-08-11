import { execFileSync } from 'node:child_process';

import type { ConnectionTest, DataSource, MeResponse } from '@eip/contracts';

const API_BASE_URL = process.env.EIP_API_BASE_URL ?? 'http://localhost:8000';
const TENANT_A_USER = 'ada@acme.invalid';

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok)
    throw new Error(`Executive demo preparation failed with HTTP ${response.status}.`);
  return (await response.json()) as T;
}

export default async function prepareExecutiveDemo(): Promise<void> {
  const password = process.env.EIP_E2E_SOURCE_PASSWORD;
  const username = process.env.EIP_E2E_SOURCE_USER;
  if (!password || !username) {
    throw new Error('EIP_E2E_SOURCE_USER and EIP_E2E_SOURCE_PASSWORD are required.');
  }

  const token = await json<{ access_token: string }>(`${API_BASE_URL}/v1/dev/token`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ email: TENANT_A_USER }),
  });
  const headers = {
    Authorization: `Bearer ${token.access_token}`,
    'content-type': 'application/json',
  };
  const me = await json<MeResponse>(`${API_BASE_URL}/v1/me`, { headers });
  const source = await json<DataSource>(`${API_BASE_URL}/v1/data-sources`, {
    method: 'POST',
    headers: { ...headers, 'Idempotency-Key': `${username}-executive-browser-source-v1` },
    body: JSON.stringify({
      name: `Executive browser verified source ${username}`,
      connector_type: 'postgresql',
      endpoint: `${process.env.EIP_E2E_SOURCE_HOST ?? 'postgres'}:5432`,
      configuration: { username, database: 'eip', tls_mode: 'disable' },
      credential: password,
    }),
  });
  let connection = await json<ConnectionTest>(`${API_BASE_URL}/v1/data-sources/${source.id}/test`, {
    method: 'POST',
    headers: { ...headers, 'Idempotency-Key': `${username}-executive-browser-test-v1` },
  });
  for (
    let attempt = 0;
    attempt < 45 && !['succeeded', 'failed', 'stale'].includes(connection.status);
    attempt += 1
  ) {
    await new Promise((resolve) => setTimeout(resolve, 1_000));
    connection = await json<ConnectionTest>(`${API_BASE_URL}${connection.poll_url}`, { headers });
  }
  if (connection.status !== 'succeeded') {
    throw new Error(`Executive demo source verification ended in ${connection.status}.`);
  }

  execFileSync(
    'docker',
    [
      'compose',
      '-f',
      'infra/docker-compose.yml',
      'exec',
      '-T',
      'api',
      'sh',
      '-c',
      'cd /app/apps/api && python -m eip.scripts.seed_executive_demo --tenant-id "$1" --source-id "$2" --author-id "$3"',
      'seed-executive-demo',
      me.tenant.id,
      source.id,
      me.principal.user_id,
    ],
    { cwd: process.cwd().replace(/[\\/]tests[\\/]e2e$/, ''), stdio: 'ignore' },
  );
}
