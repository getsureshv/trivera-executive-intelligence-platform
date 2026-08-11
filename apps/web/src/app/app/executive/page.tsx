import { redirect } from 'next/navigation';

import {
  ApiError,
  fetchExecutiveDashboard,
  fetchGovernedMetric,
  fetchMetricLineage,
} from '@/lib/api';

import { ExecutiveCommandCenter } from './ExecutiveCommandCenter';

export const dynamic = 'force-dynamic';

export default async function ExecutivePage() {
  try {
    const dashboard = await fetchExecutiveDashboard();
    const groupBy = dashboard.allowed_drill_down[0];
    if (!groupBy) return <p className="notice">No configured drill-down is available.</p>;
    const [metric, lineage] = await Promise.all([
      fetchGovernedMetric(dashboard, groupBy),
      fetchMetricLineage(dashboard.provenance.configuration_version),
    ]);
    return <ExecutiveCommandCenter metric={metric} lineage={lineage} />;
  } catch (error) {
    if (error instanceof ApiError && error.isUnauthenticated) redirect('/sign-in');
    if (error instanceof ApiError && error.isForbidden) {
      return (
        <p className="notice notice--error">You are not permitted to view executive evidence.</p>
      );
    }
    if (error instanceof ApiError && error.status === 404) {
      return <p className="notice">Executive evidence is not available for this organization.</p>;
    }
    throw error;
  }
}
