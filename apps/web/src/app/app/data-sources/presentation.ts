import type { ConnectionTest } from '@eip/contracts';

export function resultSummary(test: ConnectionTest): string {
  if (test.status === 'succeeded') return 'Connection succeeded.';
  const failed = test.checks.find((check) => check.status === 'fail');
  if (failed?.type === 'authentication')
    return 'Authentication failed. Check the username and password.';
  if (failed?.type === 'network')
    return 'Network connection failed. Check the host, port, and network access.';
  if (test.status === 'failed')
    return 'Connection test failed. Review the safe diagnostic checks below.';
  if (test.status === 'stale')
    return 'The source changed while this test was running. Start a new test.';
  return 'Connection test is running.';
}
