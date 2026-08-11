/**
 * Server-side API client.
 *
 * The web tier is a **client of the versioned API and nothing more** (ADR-001,
 * principle 8). It holds no database credentials in any environment — that is
 * enforced by not issuing them, and it is what keeps the governed query path
 * the only path to a number.
 *
 * Two consequences visible in this file:
 *
 *  * every call goes over HTTP to the API; there is no local data access;
 *  * no request carries a tenant identifier. Tenant context is derived
 *    server-side from the authenticated principal's membership (ADR-003 §3),
 *    so there is nothing for this layer to pass along or get wrong.
 */

import 'server-only';

import type {
  AuditEvent,
  HealthResponse,
  MeResponse,
  Membership,
  ProblemDocument,
  ReadinessResponse,
  DataSource,
  CreateDataSourceRequest,
  ConnectionTest,
  GovernedMetricEnvelope,
  LineageEnvelope,
} from '@eip/contracts';

import { ApiError } from './errors';
import { getAccessToken } from './session';

const API_BASE_URL = process.env.EIP_API_BASE_URL ?? 'http://localhost:8000';

interface RequestOptions {
  /** Send the caller's bearer token. Off for unauthenticated probes. */
  authenticated?: boolean;
  method?: string;
  body?: unknown;
  /** Seconds to cache. Defaults to no caching — this is live operational data. */
  revalidate?: number;
  headers?: Record<string, string>;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { authenticated = true, method = 'GET', body, revalidate } = options;

  const headers: Record<string, string> = { Accept: 'application/json' };
  Object.assign(headers, options.headers);
  if (body !== undefined) headers['Content-Type'] = 'application/json';

  if (authenticated) {
    const token = await getAccessToken();
    if (!token) throw new ApiError(401, null);
    headers.Authorization = `Bearer ${token}`;
  }

  // Built incrementally rather than with `undefined` placeholders:
  // `exactOptionalPropertyTypes` distinguishes "absent" from "present and
  // undefined", and fetch's types only accept the former.
  const init: RequestInit & { next?: { revalidate: number } } = { method, headers };
  if (body !== undefined) init.body = JSON.stringify(body);
  if (revalidate === undefined) {
    init.cache = 'no-store';
  } else {
    init.next = { revalidate };
  }

  const response = await fetch(`${API_BASE_URL}${path}`, init);

  if (!response.ok) {
    let problem: ProblemDocument | null = null;
    try {
      problem = (await response.json()) as ProblemDocument;
    } catch {
      // A non-JSON error body (a proxy or gateway page). The status alone is
      // what we can honestly report.
    }
    throw new ApiError(response.status, problem);
  }

  return (await response.json()) as T;
}

// --- endpoints -------------------------------------------------------------

/** Liveness. Unauthenticated by design — it is an infrastructure probe. */
export function fetchHealth(): Promise<HealthResponse> {
  return request<HealthResponse>('/health', { authenticated: false });
}

/** Readiness, including the tenant-isolation self-check. */
export async function fetchReadiness(): Promise<ReadinessResponse> {
  try {
    return await request<ReadinessResponse>('/ready', { authenticated: false });
  } catch (error) {
    // A 503 from /ready is a *valid, informative* response, not an exception:
    // "not ready" is exactly what the status page needs to display.
    if (error instanceof ApiError && error.status === 503) {
      return { status: 'not_ready', service: 'eip-api', checks: [] };
    }
    throw error;
  }
}

/** The caller's identity and organization. Takes no arguments, deliberately. */
export function fetchMe(): Promise<MeResponse> {
  return request<MeResponse>('/v1/me');
}

export function fetchMemberships(): Promise<Membership[]> {
  return request<Membership[]>('/v1/memberships');
}

export function fetchAuditEvents(limit = 20): Promise<AuditEvent[]> {
  return request<AuditEvent[]>(`/v1/audit-events?limit=${limit}`);
}

export function fetchDataSources(): Promise<DataSource[]> {
  return request<DataSource[]>('/v1/data-sources');
}

export function createDataSource(
  payload: CreateDataSourceRequest,
  idempotencyKey: string,
): Promise<DataSource> {
  return request<DataSource>('/v1/data-sources', {
    method: 'POST',
    body: payload,
    headers: { 'Idempotency-Key': idempotencyKey },
  });
}

export function disableDataSource(sourceId: string): Promise<DataSource> {
  return request<DataSource>(`/v1/data-sources/${encodeURIComponent(sourceId)}`, {
    method: 'DELETE',
  });
}

export function requestConnectionTest(
  sourceId: string,
  idempotencyKey: string,
): Promise<ConnectionTest> {
  return request<ConnectionTest>(`/v1/data-sources/${encodeURIComponent(sourceId)}/test`, {
    method: 'POST',
    headers: { 'Idempotency-Key': idempotencyKey },
  });
}

export function fetchConnectionTest(pollUrl: string): Promise<ConnectionTest> {
  if (!/^\/v1\/connection-tests\/[0-9a-f-]+$/i.test(pollUrl)) {
    throw new ApiError(400, null);
  }
  return request<ConnectionTest>(pollUrl);
}

export function fetchExecutiveDashboard(): Promise<GovernedMetricEnvelope> {
  return request<GovernedMetricEnvelope>('/v1/dashboards/executive');
}

export function fetchGovernedMetric(
  dashboard: GovernedMetricEnvelope,
  groupBy: string,
): Promise<GovernedMetricEnvelope> {
  return request<GovernedMetricEnvelope>('/v1/metrics/revenue_ytd/query', {
    method: 'POST',
    body: {
      period: {
        kind: dashboard.period.kind,
        timezone: dashboard.period.timezone,
        as_of_at: dashboard.period.as_of_at,
      },
      group_by: groupBy,
    },
  });
}

export function fetchMetricLineage(configurationVersion: number): Promise<LineageEnvelope> {
  return request<LineageEnvelope>(
    `/v1/metrics/revenue_ytd/lineage?config_version=${encodeURIComponent(configurationVersion)}`,
  );
}

export { API_BASE_URL, ApiError };
