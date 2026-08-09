'use server';

import { randomUUID } from 'node:crypto';

import type { ConnectionTest, DataSource } from '@eip/contracts';

import { ApiError, createDataSource, fetchConnectionTest, requestConnectionTest } from '@/lib/api';

export interface ActionFailure {
  ok: false;
  kind: 'denied' | 'authentication' | 'generic';
  message: string;
  correlationId: string | null;
}
export interface CreateSuccess {
  ok: true;
  source: DataSource;
}
export interface TestSuccess {
  ok: true;
  test: ConnectionTest;
}

function failure(error: unknown): ActionFailure {
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 403)
      return {
        ok: false,
        kind: 'denied',
        message: 'You are not permitted to perform this action.',
        correlationId: error.correlationId,
      };
    if (error.status === 404)
      return {
        ok: false,
        kind: 'denied',
        message: 'The requested source is unavailable.',
        correlationId: error.correlationId,
      };
    return {
      ok: false,
      kind: 'generic',
      message: 'The request could not be completed.',
      correlationId: error.correlationId,
    };
  }
  return {
    ok: false,
    kind: 'generic',
    message: 'The request could not be completed.',
    correlationId: null,
  };
}

export async function addSource(formData: FormData): Promise<CreateSuccess | ActionFailure> {
  try {
    return {
      ok: true,
      source: await createDataSource(
        {
          name: String(formData.get('name') ?? ''),
          connector_type: 'postgresql',
          endpoint: `${String(formData.get('host') ?? '')}:${String(formData.get('port') ?? '')}`,
          configuration: {
            username: String(formData.get('username') ?? ''),
            database: String(formData.get('database') ?? ''),
            tls_mode: formData.get('tlsMode') === 'require' ? 'require' : 'disable',
          },
          credential: String(formData.get('password') ?? ''),
        },
        randomUUID(),
      ),
    };
  } catch (error) {
    return failure(error);
  }
}

export async function beginTest(sourceId: string): Promise<TestSuccess | ActionFailure> {
  try {
    return { ok: true, test: await requestConnectionTest(sourceId, randomUUID()) };
  } catch (error) {
    return failure(error);
  }
}

export async function pollTest(pollUrl: string): Promise<TestSuccess | ActionFailure> {
  try {
    return { ok: true, test: await fetchConnectionTest(pollUrl) };
  } catch (error) {
    return failure(error);
  }
}
