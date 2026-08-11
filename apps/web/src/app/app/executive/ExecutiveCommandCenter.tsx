'use client';

import { useState } from 'react';

import type { GovernedMetricEnvelope, LineageEnvelope } from '@eip/contracts';

import {
  attentionReason,
  formatComparison,
  formatDate,
  formatDateTime,
  formatMetricValue,
  formatSignedMetricValue,
  readableMachineLabel,
  reconciles,
  targetProgress,
} from './presentation';

export function ExecutiveCommandCenter({
  metric,
  lineage,
}: {
  metric: GovernedMetricEnvelope;
  lineage: LineageEnvelope;
}) {
  const [trustOpen, setTrustOpen] = useState(false);
  const [attentionOpen, setAttentionOpen] = useState(false);
  const attention = metric.attention;
  const reconciled = reconciles(metric);
  const progress = targetProgress(metric.value, metric.target);
  const focusAttentionSegment = () => {
    const segment = document.getElementById(`segment-${attention.dimension_value_id}`);
    segment?.focus();
    segment?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  return (
    <div className="executive-page">
      <header className="executive-heading">
        <div>
          <p className="eyebrow">Executive Command Center</p>
          <h1>{metric.metric_name}</h1>
        </div>
        <span className="demo-disclosure">{metric.provenance.origin_label}</span>
      </header>

      <section className="executive-hero" aria-labelledby="headline-metric">
        <div className="headline-block">
          <p id="headline-metric" className="metric-label">
            {metric.metric_name}
          </p>
          <p className="metric-value">{formatMetricValue(metric.value, metric)}</p>
          <p className="metric-period">
            {formatDate(metric.period.start, metric.period.timezone)} –{' '}
            {formatDate(metric.period.end, metric.period.timezone)} · as of{' '}
            {formatDateTime(metric.period.as_of_at, metric.period.timezone)}
          </p>
        </div>
        <div className="comparison-grid">
          <div>
            <span>Prior year</span>
            <strong>{formatMetricValue(metric.prior_value, metric)}</strong>
            <small>{formatComparison(metric.comparison, metric)}</small>
          </div>
          <div>
            <span>Target</span>
            <strong>{formatMetricValue(metric.target, metric)}</strong>
            <small>Gap {formatComparison(metric.target_variance, metric)}</small>
          </div>
        </div>
        {progress !== null && (
          <div className="target-progress">
            <div>
              <span>Progress to target</span>
              <strong>{progress}%</strong>
            </div>
            <div className="progress-track" aria-label={`${progress}% of target`}>
              <span style={{ width: `${progress}%` }} />
            </div>
          </div>
        )}
      </section>

      <section className="trust-strip" aria-label="Evidence status">
        <span className={`badge badge--${metric.freshness_status === 'fresh' ? 'ok' : 'warn'}`}>
          Freshness: {metric.freshness_status}
        </span>
        <span className={`badge badge--${metric.quality_status === 'pass' ? 'ok' : 'warn'}`}>
          Quality: {metric.quality_status}
        </span>
        <span>Accountable owner: {metric.accountable_owner}</span>
        <span>
          Calculated: {formatDateTime(metric.provenance.calculated_at, metric.period.timezone)}
        </span>
      </section>
      <div className="executive-grid">
        <section className="card executive-drilldown">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Segment comparison</p>
              <h2>
                Performance by{' '}
                {readableMachineLabel(metric.allowed_drill_down.join(', ')).replace(
                  /^./,
                  (character) => character.toUpperCase(),
                )}
              </h2>
            </div>
            <span className={`badge badge--${reconciled ? 'ok' : 'error'}`}>
              {reconciled ? 'Reconciled' : 'Does not reconcile'}
            </span>
          </div>
          <div className="slice-list">
            {metric.drill_down.map((slice) => (
              <article
                className="slice-card"
                id={`segment-${slice.dimension_value_id}`}
                key={slice.dimension_value_id}
                tabIndex={-1}
              >
                <h3>{slice.label}</h3>
                <strong>{formatMetricValue(slice.value, metric)}</strong>
                <span>Target {formatMetricValue(slice.target, metric)}</span>
                <small>Variance {formatSignedMetricValue(slice.target_variance, metric)}</small>
              </article>
            ))}
          </div>
        </section>

        <aside className="card attention-card" id={`attention-${attention.dimension_value_id}`}>
          <p className="eyebrow">Requires Attention</p>
          <h2>{attention.label}</h2>
          <p>{formatMetricValue(attention.value, metric)}</p>
          <p className="attention-reason">
            {attentionReason(attention.label, attention.target_variance, metric)}
          </p>
          <button type="button" className="secondary" onClick={focusAttentionSegment}>
            Go to {attention.label}
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => setAttentionOpen((open) => !open)}
          >
            {attentionOpen ? 'Hide details' : 'View attention details'}
          </button>
          {attentionOpen && (
            <dl className="field-grid attention-detail">
              <dt>Target</dt>
              <dd>{formatMetricValue(attention.target, metric)}</dd>
              <dt>Variance</dt>
              <dd>{formatMetricValue(attention.target_variance, metric)}</dd>
            </dl>
          )}
        </aside>
      </div>

      <section className="card trust-card">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Trust this number</p>
            <h2>See where it came from</h2>
          </div>
          <button
            type="button"
            onClick={() => setTrustOpen((open) => !open)}
            aria-expanded={trustOpen}
          >
            {trustOpen ? 'Close trust view' : 'Open trust view'}
          </button>
        </div>
        <p>Inspect the governed configuration, calculation, source health, and lineage.</p>
        {trustOpen && (
          <div className="trust-detail">
            <p>Seeded observations for demonstration; not a live source extraction.</p>
            <span hidden>{metric.provenance.observation_basis}</span>
            <dl className="field-grid">
              <dt>Configuration</dt>
              <dd>{lineage.provenance.configuration_version}</dd>
              <dt>Snapshot</dt>
              <dd className="mono">{lineage.provenance.snapshot_id}</dd>
              <dt>Calculated</dt>
              <dd>{formatDateTime(lineage.provenance.calculated_at, metric.period.timezone)}</dd>
              <dt>Selected-source health</dt>
              <dd>
                {lineage.provenance.selected_source.connection_status} · connection health only
                <span hidden>{lineage.provenance.selected_source.relationship}</span>
              </dd>
            </dl>
            <ol className="lineage-path" aria-label="Derived lineage path">
              {lineage.nodes.map((node) => (
                <li key={node.id}>
                  <span>{readableMachineLabel(node.kind)}</span>
                  <strong>{node.label}</strong>
                </li>
              ))}
            </ol>
          </div>
        )}
      </section>
    </div>
  );
}
