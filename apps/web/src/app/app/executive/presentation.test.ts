import assert from 'node:assert/strict';
import test from 'node:test';

import type { GovernedMetricEnvelope } from '@eip/contracts';

import {
  formatComparison,
  formatMetricValue,
  readableMachineLabel,
  reconciles,
} from './presentation.ts';

function metric(values: string[], headline: string): GovernedMetricEnvelope {
  return {
    metric_id: 'metric-id',
    metric_version: 1,
    metric_name: 'Configured metric',
    period: {
      kind: 'calendar_ytd',
      timezone: 'America/Chicago',
      start: '2026-01-01',
      end: '2026-08-11',
      as_of_at: '2026-08-11T17:00:00-05:00',
    },
    value: headline,
    prior_value: '0',
    comparison: { absolute: '0', percent: null },
    target: '0',
    target_variance: { absolute: '0', percent: null },
    unit: 'USD',
    format: 'currency',
    freshness_status: 'fresh',
    freshness_as_of: '2026-08-11T17:00:00-05:00',
    quality_status: 'pass',
    quality_checks: [],
    accountable_owner: 'Configured owner',
    provenance: {
      configuration_version: 1,
      snapshot_id: 'snapshot-id',
      calculated_at: '2026-08-11T17:00:00-05:00',
      dataset_id: 'dataset-id',
      origin: 'seeded_demo',
      origin_label: 'Demo dataset / seeded demonstration data',
      observation_basis: 'seeded_demo_observations_not_live_extraction',
      selected_source: {
        data_source_id: 'source-id',
        connection_test_id: 'test-id',
        source_version: 1,
        connection_status: 'succeeded',
        relationship: 'selected_source_connection_health_only',
      },
    },
    authorization: { row_scope_applied: true, redactions: [] },
    allowed_drill_down: ['configured-dimension'],
    drill_down: values.map((value, index) => ({
      dimension_value_id: `${index}`,
      label: `Configured ${index}`,
      value,
      target: '0',
      target_variance: '0',
    })),
    attention: {
      dimension_value_id: '0',
      label: 'Configured 0',
      value: values[0] ?? '0',
      target: '0',
      target_variance: '0',
    },
    lineage_handle: 'lineage',
  };
}

test('reconciles exact decimal strings without binary floating point', () => {
  assert.equal(reconciles(metric(['0.1', '0.2'], '0.3')), true);
  assert.equal(reconciles(metric(['0.1', '0.2'], '0.4')), false);
});

test('renders API comparison and machine disclosures deterministically', () => {
  assert.equal(formatComparison({ absolute: '-2', percent: '-4.5' }), '-2 (-4.5%)');
  assert.equal(
    readableMachineLabel('selected_source_connection_health_only'),
    'selected source connection health only',
  );
});

test('formats values beyond JavaScript safe integers without precision loss', () => {
  const rendered = formatMetricValue('9007199254740993.25', metric([], '0'));
  assert.match(rendered, /9,007,199,254,740,993\.25/);
});
