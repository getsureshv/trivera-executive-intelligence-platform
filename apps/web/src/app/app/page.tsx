/**
 * The authenticated experience — Phase 1A placeholder.
 *
 * Shows exactly what this phase is meant to prove: that the browser reaches the
 * API, that a tenant context was resolved server-side, and that dependencies
 * are healthy. Nothing else.
 *
 * Explicitly NOT here: dashboards, charts, KPI cards, source configuration.
 * Those require the semantic and metric layers, which do not exist yet.
 */

import { redirect } from 'next/navigation';

import { ApiError, fetchAuditEvents, fetchMe, fetchReadiness } from '@/lib/api';
import { StatusBadge, type StatusTone } from '@/components/StatusBadge';

export const dynamic = 'force-dynamic';

function toneFor(status: string): StatusTone {
  if (status === 'pass' || status === 'ready' || status === 'ok' || status === 'active')
    return 'ok';
  if (status === 'not_ready' || status === 'fail') return 'error';
  return 'muted';
}

export default async function OverviewPage() {
  // Readiness is fetched unauthenticated and first, so an infrastructure
  // problem is reported as such rather than surfacing as a confusing
  // authentication failure.
  const readiness = await fetchReadiness();

  let me;
  try {
    me = await fetchMe();
  } catch (error) {
    if (error instanceof ApiError && (error.isUnauthenticated || error.isForbidden)) {
      redirect('/sign-in');
    }
    throw error;
  }

  // Audit access is capability-gated (ADR-010). A viewer legitimately cannot
  // read it, so a 403 is an expected outcome to render — not an error.
  let auditEvents = null;
  let auditForbidden = false;
  try {
    auditEvents = await fetchAuditEvents(10);
  } catch (error) {
    if (error instanceof ApiError && error.isForbidden) {
      auditForbidden = true;
    } else {
      throw error;
    }
  }

  return (
    <>
      <section className="card">
        <h2>Organization context</h2>
        <p className="card-hint">
          Resolved server-side from your verified membership. The browser cannot influence which
          organization is served.
        </p>
        <dl className="field-grid">
          <dt>Organization</dt>
          <dd>
            {me.tenant.name} <span className="mono">({me.tenant.slug})</span>
          </dd>

          <dt>Organization ID</dt>
          <dd className="mono">{me.tenant.id}</dd>

          <dt>Status</dt>
          <dd>
            <StatusBadge tone={toneFor(me.tenant.status)}>{me.tenant.status}</StatusBadge>
          </dd>

          <dt>Isolation mode</dt>
          <dd className="mono">{me.tenant.isolation_mode}</dd>

          <dt>Signed in as</dt>
          <dd>{me.principal.email}</dd>

          <dt>Role</dt>
          <dd className="mono">{me.role}</dd>

          <dt>Capabilities</dt>
          <dd>
            <ul className="chip-list">
              {me.capabilities.map((capability) => (
                <li key={capability} className="chip mono">
                  {capability}
                </li>
              ))}
            </ul>
          </dd>
        </dl>
      </section>

      <section className="card">
        <h2>
          Platform status{' '}
          <StatusBadge tone={toneFor(readiness.status)}>{readiness.status}</StatusBadge>
        </h2>
        <p className="card-hint">
          Liveness says the process is running; readiness says it can serve correct traffic —
          including a check that tenant isolation is actually enforced.
        </p>
        {readiness.checks.length === 0 ? (
          <p className="notice">
            The API reported that it is not ready and returned no check detail. It is running but
            should not be serving traffic.
          </p>
        ) : (
          <table>
            <thead>
              <tr>
                <th scope="col">Check</th>
                <th scope="col">Result</th>
                <th scope="col">Detail</th>
                <th scope="col">Duration</th>
              </tr>
            </thead>
            <tbody>
              {readiness.checks.map((check) => (
                <tr key={check.name}>
                  <th scope="row" className="mono">
                    {check.name}
                  </th>
                  <td>
                    <StatusBadge tone={toneFor(check.status)}>{check.status}</StatusBadge>
                  </td>
                  <td>{check.detail || '—'}</td>
                  <td className="mono">{check.duration_ms} ms</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="card">
        <h2>Recent governance activity</h2>
        <p className="card-hint">
          The audit trail is a product feature, not only an operational log. It is scoped to your
          organization and is append-only.
        </p>
        {auditForbidden ? (
          <p className="notice">
            Your role does not include <code>audit.read</code>, so the trail is not shown.
          </p>
        ) : auditEvents && auditEvents.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">When</th>
                <th scope="col">Action</th>
                <th scope="col">Resource</th>
                <th scope="col">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {auditEvents.map((event) => (
                <tr key={event.id}>
                  <td className="mono">{event.seq}</td>
                  <td className="mono">{new Date(event.occurred_at).toISOString()}</td>
                  <td className="mono">{event.action}</td>
                  <td className="mono">{event.resource_type}</td>
                  <td>
                    <StatusBadge tone={event.outcome === 'success' ? 'ok' : 'warn'}>
                      {event.outcome}
                    </StatusBadge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="notice">
            No governance events have been recorded for this organization yet.
          </p>
        )}
      </section>
    </>
  );
}
