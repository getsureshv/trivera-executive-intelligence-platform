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

// --- data sources ----------------------------------------------------------

export interface PostgreSQLSourceConfiguration {
  username: string;
  database: string;
  tls_mode: 'disable' | 'require';
  connect_timeout_seconds: number;
}

export interface DataSource {
  id: string;
  name: string;
  connector_type: 'postgresql';
  endpoint: string;
  configuration: PostgreSQLSourceConfiguration;
  connectivity_mode: 'direct';
  status: 'active' | 'disabled';
  version: number;
  credential_configured: true;
  created_at: string;
  updated_at: string;
  disabled_at: string | null;
  credential_destroy_after: string | null;
  credential_destroyed_at: string | null;
}

export interface CreateDataSourceRequest {
  name: string;
  connector_type: 'postgresql';
  endpoint: string;
  configuration: Partial<PostgreSQLSourceConfiguration> &
    Pick<PostgreSQLSourceConfiguration, 'username' | 'database'>;
  /** Write-only. It is never present in a response. */
  credential: string;
}

export interface UpdateDataSourceRequest {
  name?: string;
  endpoint?: string;
  configuration?: Partial<PostgreSQLSourceConfiguration> &
    Pick<PostgreSQLSourceConfiguration, 'username' | 'database'>;
  /** Write-only replacement. It is never present in a response. */
  credential?: string;
}

export type DiagnosticType =
  'network' | 'tls' | 'authentication' | 'authorization' | 'metadata_access' | 'latency';

export interface ConnectionDiagnostic {
  type: DiagnosticType;
  status: 'pass' | 'fail' | 'skipped';
  code: string;
  message: string;
  remediation_hint: string | null;
  duration_ms: number;
}

export interface ConnectionTest {
  id: string;
  data_source_id: string;
  source_version: number;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'stale';
  checks: ConnectionDiagnostic[];
  overall_code: string | null;
  attempt: number;
  queued_at: string;
  started_at: string | null;
  completed_at: string | null;
  poll_url: string;
}

// --- governed executive intelligence -------------------------------------

export type SeededDemoOrigin = 'seeded_demo';
export type DecimalString = string;

export interface MetricPeriod {
  kind: 'calendar_ytd';
  timezone: 'America/Chicago';
  start: string;
  end: string;
  as_of_at: string;
}

export interface MetricProvenance {
  configuration_version: number;
  snapshot_id: string;
  calculated_at: string;
  dataset_id: string;
  origin: SeededDemoOrigin;
  origin_label: 'Demo dataset / seeded demonstration data';
  observation_basis: 'seeded_demo_observations_not_live_extraction';
  selected_source: SelectedSourceHealth;
}

export interface SelectedSourceHealth {
  data_source_id: string;
  connection_test_id: string;
  source_version: number;
  connection_status: 'succeeded';
  /** Connection health is real; it does not claim the seeded revenue was extracted live. */
  relationship: 'selected_source_connection_health_only';
}

export interface MetricAuthorization {
  row_scope_applied: true;
  redactions: string[];
}

export interface GovernedMetricEnvelope {
  metric_id: string;
  metric_version: number;
  metric_name: string;
  period: MetricPeriod;
  value: DecimalString;
  prior_value: DecimalString;
  target: DecimalString;
  unit: string;
  format: string;
  freshness_status: 'fresh' | 'stale';
  freshness_as_of: string;
  quality_status: 'pass' | 'warn' | 'fail';
  accountable_owner: string;
  provenance: MetricProvenance;
  authorization: MetricAuthorization;
  allowed_drill_down: string[];
  lineage_handle: string;
}

export interface LineageNode {
  id: string;
  kind:
    | 'widget'
    | 'metric_version'
    | 'semantic_field'
    | 'field_binding'
    | 'source_field'
    | 'source_object'
    | 'data_source';
  label: string;
}

export interface LineageEdge {
  from: string;
  to: string;
  relation: string;
}

export interface LineageEnvelope {
  configuration_version: number;
  origin: SeededDemoOrigin;
  nodes: LineageNode[];
  edges: LineageEdge[];
  authorization: MetricAuthorization;
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
