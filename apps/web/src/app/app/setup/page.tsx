import { redirect } from 'next/navigation';

import {
  ApiError,
  fetchDataSources,
  fetchExecutiveDashboard,
  fetchLatestConnectionTest,
  fetchMe,
  fetchMetricLineage,
} from '@/lib/api';
import { formatComparison, formatMetricValue } from '../executive/presentation';

import { buildConfigurationSummary } from './presentation';

export const dynamic = 'force-dynamic';

export default async function SetupPage() {
  try {
    const [me, sources, dashboard] = await Promise.all([
      fetchMe(),
      fetchDataSources(),
      fetchExecutiveDashboard(),
    ]);
    const sourceId = dashboard.provenance.selected_source.data_source_id;
    const [lineage, latest] = await Promise.all([
      fetchMetricLineage(dashboard.provenance.configuration_version),
      fetchLatestConnectionTest(sourceId),
    ]);
    const summary = buildConfigurationSummary(me, sources, dashboard, lineage, latest);
    if (!summary) {
      return <p className="notice">A complete executive configuration is not available.</p>;
    }
    return (
      <div className="setup-page">
        <header className="setup-heading">
          <div>
            <p className="eyebrow">Read-only configuration summary</p>
            <h1>Configured for {summary.tenantName}</h1>
            <p>
              This configuration summary is available now. A full self-service configuration builder
              is planned for a future phase.
            </p>
          </div>
          <span className="demo-disclosure">{summary.originLabel}</span>
        </header>

        <section className="setup-grid" aria-label="Configured executive experience">
          <article className="card setup-card">
            <p className="eyebrow">Organization &amp; calendar</p>
            <h2>{summary.tenantName}</h2>
            <dl className="field-grid">
              <dt>Calendar</dt>
              <dd>{summary.calendar}</dd>
              <dt>Timezone</dt>
              <dd>{summary.timezone.replace('_', ' ')}</dd>
            </dl>
          </article>

          <article className="card setup-card">
            <p className="eyebrow">Selected PostgreSQL source</p>
            <h2>{summary.sourceName}</h2>
            <dl className="field-grid">
              <dt>Latest health</dt>
              <dd>
                <span
                  className={`badge badge--${summary.sourceHealth === 'succeeded' ? 'ok' : 'warn'}`}
                >
                  {summary.sourceHealth}
                </span>
              </dd>
              <dt>Evidence</dt>
              <dd>Real current-version connection test</dd>
            </dl>
          </article>

          <article className="card setup-card setup-card--wide">
            <p className="eyebrow">Governed metric</p>
            <div className="section-heading">
              <h2>{summary.metricName}</h2>
              <span className="badge badge--muted">Version {summary.metricVersion}</span>
            </div>
            <dl className="field-grid">
              <dt>Target</dt>
              <dd>{formatMetricValue(summary.target, dashboard)}</dd>
              <dt>Prior comparison</dt>
              <dd>{formatComparison(summary.priorComparison, dashboard)}</dd>
              <dt>Configured segment</dt>
              <dd>{summary.segmentDimension}</dd>
              <dt>Accountable owner</dt>
              <dd>{summary.accountableOwner}</dd>
            </dl>
          </article>

          <article className="card setup-card setup-card--wide">
            <p className="eyebrow">Executive delivery</p>
            <h2>{summary.dashboardPlacement}</h2>
            <dl className="field-grid">
              <dt>Configuration</dt>
              <dd>Version {summary.configurationVersion}</dd>
              <dt>Publication</dt>
              <dd>
                <strong>Published for executive use</strong>
                <small className="inference-note">
                  Inferred from successful delivery by the published-only governed dashboard API;
                  this is not a raw status field.
                </small>
              </dd>
            </dl>
          </article>
        </section>

        <a className="setup-preview-link" href="/app/executive">
          Preview Executive Dashboard →
        </a>
      </div>
    );
  } catch (error) {
    if (error instanceof ApiError && error.isUnauthenticated) redirect('/sign-in');
    if (error instanceof ApiError && error.isForbidden) {
      return <p className="notice notice--error">You are not permitted to view configuration.</p>;
    }
    if (error instanceof ApiError && error.status === 404) {
      return <p className="notice">A complete executive configuration is not available.</p>;
    }
    throw error;
  }
}
