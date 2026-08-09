import assert from 'node:assert/strict';
import test from 'node:test';

import type { ConnectionTest, DiagnosticType } from '@eip/contracts';

import { resultSummary } from './presentation.ts';

function result(type: DiagnosticType): ConnectionTest {
  return {
    id: 'job',
    data_source_id: 'source',
    source_version: 1,
    status: 'failed',
    overall_code: 'SAFE_CODE',
    attempt: 1,
    queued_at: '',
    started_at: '',
    completed_at: '',
    poll_url: '/v1/connection-tests/job',
    checks: [
      {
        type,
        status: 'fail',
        code: 'SAFE_CODE',
        message: 'Safe message',
        remediation_hint: null,
        duration_ms: 0,
      },
    ],
  };
}

test('distinguishes authentication and network failures using diagnostic type', () => {
  assert.match(resultSummary(result('authentication')), /^Authentication failed/);
  assert.match(resultSummary(result('network')), /^Network connection failed/);
});
