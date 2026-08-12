import assert from 'node:assert/strict';
import test from 'node:test';

import type {
  ConnectionTest,
  DataSource,
  GovernedMetricEnvelope,
  LineageEnvelope,
  MeResponse,
} from '@eip/contracts';

import { buildConfigurationSummary } from './presentation.ts';

const me = {
  tenant: {
    id: 'tenant-a',
    name: 'Configured Tenant',
    slug: 'tenant-a',
    status: 'active',
    isolation_mode: 'rls',
  },
  principal: { user_id: 'user-a', email: 'a@example.invalid', actor_type: 'user' },
  role: 'tenant_admin',
  capabilities: [],
} satisfies MeResponse;

const source = {
  id: 'source-a',
  name: 'Approved PostgreSQL',
  connector_type: 'postgresql',
  endpoint: 'postgres',
  configuration: {
    username: 'reader',
    database: 'eip',
    tls_mode: 'disable',
    connect_timeout_seconds: 5,
  },
  connectivity_mode: 'direct',
  status: 'active',
  version: 2,
  credential_configured: true,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  disabled_at: null,
  credential_destroy_after: null,
  credential_destroyed_at: null,
} satisfies DataSource;

const dashboard = {
  metric_id: 'metric',
  metric_version: 3,
  metric_name: 'Configured metric',
  period: {
    kind: 'calendar_ytd',
    timezone: 'America/Chicago',
    start: '2026-01-01',
    end: '2026-08-11',
    as_of_at: '2026-08-11T17:00:00-05:00',
  },
  value: '10',
  prior_value: '9',
  comparison: { absolute: '1', percent: '11.1' },
  target: '12',
  target_variance: { absolute: '-2', percent: '-16.7' },
  unit: 'USD',
  format: 'currency',
  freshness_status: 'fresh',
  freshness_as_of: '2026-08-11T17:00:00-05:00',
  quality_status: 'pass',
  quality_checks: [],
  accountable_owner: 'Configured owner',
  provenance: {
    configuration_version: 4,
    snapshot_id: 'snapshot',
    calculated_at: '2026-08-11T17:00:00-05:00',
    dataset_id: 'dataset',
    origin: 'seeded_demo',
    origin_label: 'Demo dataset / seeded demonstration data',
    observation_basis: 'seeded_demo_observations_not_live_extraction',
    selected_source: {
      data_source_id: source.id,
      connection_test_id: 'test',
      source_version: 2,
      connection_status: 'succeeded',
      relationship: 'selected_source_connection_health_only',
    },
  },
  authorization: { row_scope_applied: true, redactions: [] },
  allowed_drill_down: ['configured_segment'],
  drill_down: [],
  attention: {
    dimension_value_id: 'segment',
    label: 'Configured segment',
    value: '1',
    target: '2',
    target_variance: '-1',
  },
  lineage_handle: 'lineage',
} satisfies GovernedMetricEnvelope;

const lineage = {
  configuration_version: 4,
  origin: 'seeded_demo',
  provenance: dashboard.provenance,
  nodes: [{ id: 'widget', kind: 'widget', label: 'Executive overview' }],
  edges: [],
  authorization: { row_scope_applied: true, redactions: [] },
} satisfies LineageEnvelope;

const latest = {
  id: 'test',
  data_source_id: source.id,
  source_version: 2,
  status: 'succeeded',
  checks: [],
  overall_code: 'ok',
  attempt: 1,
  queued_at: '2026-08-11T17:00:00Z',
  started_at: '2026-08-11T17:00:01Z',
  completed_at: '2026-08-11T17:00:02Z',
  poll_url: '/v1/connection-tests/test',
} satisfies ConnectionTest;

test('builds the summary only from joined tenant-scoped API values', () => {
  const summary = buildConfigurationSummary(me, [source], dashboard, lineage, latest);
  assert.equal(summary?.tenantName, me.tenant.name);
  assert.equal(summary?.sourceName, source.name);
  assert.equal(summary?.dashboardPlacement, lineage.nodes[0]?.label);
  assert.equal(summary?.configurationVersion, dashboard.provenance.configuration_version);
});

test('fails closed for a stale source test or ambiguous widget placement', () => {
  assert.equal(
    buildConfigurationSummary(me, [source], dashboard, lineage, { ...latest, source_version: 1 }),
    null,
  );
  assert.equal(
    buildConfigurationSummary(
      me,
      [source],
      dashboard,
      { ...lineage, nodes: [...lineage.nodes, { id: 'widget-2', kind: 'widget', label: 'Other' }] },
      latest,
    ),
    null,
  );
  assert.equal(
    buildConfigurationSummary(
      me,
      [source],
      dashboard,
      { ...lineage, configuration_version: 99 },
      latest,
    ),
    null,
  );
});

test('fails closed when selected-source provenance does not identify one evidence record', () => {
  assert.equal(
    buildConfigurationSummary(
      me,
      [source],
      {
        ...dashboard,
        provenance: {
          ...dashboard.provenance,
          selected_source: { ...dashboard.provenance.selected_source, source_version: 1 },
        },
      },
      lineage,
      latest,
    ),
    null,
  );
  assert.equal(
    buildConfigurationSummary(
      me,
      [source],
      dashboard,
      {
        ...lineage,
        provenance: {
          ...lineage.provenance,
          selected_source: {
            ...lineage.provenance.selected_source,
            connection_test_id: 'different-test',
          },
        },
      },
      latest,
    ),
    null,
  );
  assert.equal(
    buildConfigurationSummary(me, [source], dashboard, lineage, { ...latest, id: 'newer-test' }),
    null,
  );
  assert.equal(
    buildConfigurationSummary(me, [source], dashboard, lineage, {
      ...latest,
      status: 'failed',
    }),
    null,
  );
});
