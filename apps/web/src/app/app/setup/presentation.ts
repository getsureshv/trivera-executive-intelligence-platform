import type {
  ConnectionTest,
  DataSource,
  GovernedMetricEnvelope,
  LineageEnvelope,
  MeResponse,
} from '@eip/contracts';

export interface ConfigurationSummary {
  tenantName: string;
  calendar: string;
  timezone: string;
  sourceName: string;
  sourceHealth: ConnectionTest['status'];
  metricName: string;
  metricVersion: number;
  target: string;
  priorComparison: GovernedMetricEnvelope['comparison'];
  segmentDimension: string;
  accountableOwner: string;
  dashboardPlacement: string;
  configurationVersion: number;
  originLabel: string;
}

export function buildConfigurationSummary(
  me: MeResponse,
  sources: DataSource[],
  dashboard: GovernedMetricEnvelope,
  lineage: LineageEnvelope,
  latest: ConnectionTest,
): ConfigurationSummary | null {
  const sourceId = dashboard.provenance.selected_source.data_source_id;
  const dashboardSource = dashboard.provenance.selected_source;
  const lineageSource = lineage.provenance.selected_source;
  const selected = sources.filter((source) => source.id === sourceId);
  const widgets = lineage.nodes.filter((node) => node.kind === 'widget');
  const dimension = dashboard.allowed_drill_down[0];
  if (
    selected.length !== 1 ||
    widgets.length !== 1 ||
    !dimension ||
    lineage.configuration_version !== dashboard.provenance.configuration_version ||
    dashboardSource.source_version !== selected[0]?.version ||
    dashboardSource.connection_status !== 'succeeded' ||
    lineageSource.data_source_id !== sourceId ||
    lineageSource.source_version !== dashboardSource.source_version ||
    lineageSource.connection_test_id !== dashboardSource.connection_test_id ||
    lineageSource.connection_status !== dashboardSource.connection_status ||
    latest.data_source_id !== sourceId ||
    latest.source_version !== dashboardSource.source_version ||
    latest.id !== dashboardSource.connection_test_id ||
    latest.status !== dashboardSource.connection_status
  ) {
    return null;
  }
  return {
    tenantName: me.tenant.name,
    calendar:
      dashboard.period.kind === 'calendar_ytd' ? 'Calendar year to date' : dashboard.period.kind,
    timezone: dashboard.period.timezone,
    sourceName: selected[0].name,
    sourceHealth: latest.status,
    metricName: dashboard.metric_name,
    metricVersion: dashboard.metric_version,
    target: dashboard.target,
    priorComparison: dashboard.comparison,
    segmentDimension: dimension.replaceAll('_', ' '),
    accountableOwner: dashboard.accountable_owner,
    dashboardPlacement: widgets[0]?.label ?? '',
    configurationVersion: dashboard.provenance.configuration_version,
    originLabel: dashboard.provenance.origin_label,
  };
}
