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
} from '@eip/contracts';

import { getAccessToken } from './session';

const API_BASE_URL = process.env.EIP_API_BASE_URL ?? 'http://localhost:8000';

/** An API failure carrying the server's problem document. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly problem: ProblemDocument | null,
  ) {
    super(problem?.detail ?? `Request failed with status ${status}`);
    this.name = 'ApiError';
  }

  /**
   * The id to quote when reporting a problem.
   *
   * The server deliberately keeps error detail on its side (ADR-014 §6), so
   * this is genuinely the most useful thing the UI can show — and much better
   * than a guessed explanation.
   */
  get correlationId(): string | null {
    return this.problem?.correlation_id ?? null;
  }

  get isUnauthenticated(): boolean {
    return this.status === 401;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }
}

interface RequestOptions {
  /** Send the caller's bearer token. Off for unauthenticated probes. */
  authenticated?: boolean;
  method?: string;
  body?: unknown;
  /** Seconds to cache. Defaults to no caching — this is live operational data. */
  revalidate?: number;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { authenticated = true, method = 'GET', body, revalidate } = options;

  const headers: Record<string, string> = { Accept: 'application/json' };
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

export { API_BASE_URL };
