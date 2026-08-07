/**
 * API contract types (ADR-001, principle 8).
 *
 * These mirror the FastAPI response models exactly. `pnpm --filter @eip/contracts
 * sync` fetches the live OpenAPI document into `openapi.json`, and CI fails if
 * the committed document drifts from the running API — that check is what makes
 * "API-first" mechanical rather than aspirational.
 *
 * Phase 1A keeps the types hand-written and small. Code generation is wired in
 * when the surface grows past the point where hand-maintenance is honest;
 * generating five interfaces would add a toolchain for no benefit.
 *
 * NOTE: there is deliberately no `tenantId` field in any *request* type. The
 * server derives tenant context from the authenticated principal's membership
 * (ADR-003 §3); a client that could send one would imply it mattered.
 */

// --- health ----------------------------------------------------------------

export interface HealthResponse {
  status: 'ok';
  service: string;
  environment: string;
  version: string;
}

export type CheckStatus = 'pass' | 'fail';

export interface CheckResult {
  name: string;
  status: CheckStatus;
  detail: string;
  duration_ms: number;
}

export interface ReadinessResponse {
  status: 'ready' | 'not_ready';
  service: string;
  checks: CheckResult[];
}

// --- identity & tenancy ----------------------------------------------------

export interface Principal {
  user_id: string;
  email: string;
  actor_type: 'user' | 'service' | 'system';
}

export interface Tenant {
  id: string;
  slug: string;
  name: string;
  status: string;
  /** Which ADR-003 isolation mode serves this tenant. */
  isolation_mode: string;
}

export interface MeResponse {
  principal: Principal;
  tenant: Tenant;
  role: string;
  capabilities: string[];
}

export interface Membership {
  id: string;
  user_id: string;
  email: string;
  display_name: string;
  role_code: string;
  status: string;
}

export interface AuditEvent {
  id: string;
  seq: number;
  occurred_at: string;
  actor_type: string;
  actor_user_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  outcome: string;
  trace_id: string;
}

// --- errors ----------------------------------------------------------------

/**
 * RFC 9457 problem document.
 *
 * `correlation_id` is the only thing worth quoting in a support request: the
 * server keeps the detail (ADR-014 §6), so the UI must surface this rather
 * than inventing an explanation.
 */
export interface ProblemDocument {
  type: string;
  title: string;
  status: number;
  detail: string;
  code: string;
  instance: string;
  correlation_id: string;
  context?: Record<string, unknown>;
}

// --- development auth (local/ci only) --------------------------------------

export interface DevTokenRequest {
  email: string;
  /**
   * A *request* for a tenant, not an assertion. The server verifies membership
   * before honouring it and refuses otherwise.
   */
  tenant_id?: string;
}

export interface DevTokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}
