/**
 * Tests for the API client's error handling.
 *
 * Phase 1A shipped with `pnpm test` passing zero tests, which the review
 * correctly called out: a vacuous pass is worse than a declared gap, because it
 * reads as coverage. This is the initial harness.
 *
 * The subject is deliberately `ApiError` rather than a component: the client's
 * error mapping is where the frontend's security-relevant behaviour lives —
 * which failures redirect to sign-in, and what the user is shown when the
 * server withholds detail (ADR-014 §6).
 */

import assert from 'node:assert/strict';
import { test } from 'node:test';

import { ApiError } from './errors.ts';
import type { ProblemDocument } from '@eip/contracts';

const problem = (overrides: Partial<ProblemDocument> = {}): ProblemDocument => ({
  type: 'https://docs.trivera.invalid/problem/not-found',
  title: 'Resource not found or not permitted',
  status: 404,
  detail: 'Resource not found or not permitted.',
  code: 'NOT_FOUND',
  instance: '/v1/tenants/x',
  correlation_id: 'trace-abc123',
  ...overrides,
});

test('surfaces the correlation id, which is the only actionable detail', () => {
  const error = new ApiError(404, problem());
  assert.equal(error.correlationId, 'trace-abc123');
});

test('tolerates a non-JSON error body from a proxy or gateway', () => {
  const error = new ApiError(502, null);
  assert.equal(error.correlationId, null);
  assert.match(error.message, /502/);
});

test('classifies 401 as unauthenticated', () => {
  const error = new ApiError(401, problem({ status: 401, code: 'UNAUTHENTICATED' }));
  assert.equal(error.isUnauthenticated, true);
  assert.equal(error.isForbidden, false);
});

test('classifies 403 as forbidden', () => {
  const error = new ApiError(403, problem({ status: 403, code: 'FORBIDDEN' }));
  assert.equal(error.isForbidden, true);
  assert.equal(error.isUnauthenticated, false);
});

test('a 404 is neither, so it renders rather than redirecting to sign-in', () => {
  const error = new ApiError(404, problem());
  assert.equal(error.isUnauthenticated, false);
  assert.equal(error.isForbidden, false);
});

test('uses the server detail as the message when present', () => {
  const error = new ApiError(422, problem({ status: 422, detail: 'Grain assertion violated' }));
  assert.equal(error.message, 'Grain assertion violated');
});

test('is a real Error, so it survives throw/catch and stack capture', () => {
  const error = new ApiError(500, null);
  assert.ok(error instanceof Error);
  assert.equal(error.name, 'ApiError');
});
