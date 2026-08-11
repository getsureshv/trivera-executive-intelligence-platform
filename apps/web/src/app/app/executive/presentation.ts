import type { DecimalComparison, GovernedMetricEnvelope } from '@eip/contracts';

export function formatMetricValue(value: string, metric: GovernedMetricEnvelope): string {
  if (!/^-?\d+(?:\.\d+)?$/.test(value)) return value;
  if (metric.format === 'currency') {
    try {
      const [whole = '0', fraction = ''] = value.split('.');
      const formatter = new Intl.NumberFormat(undefined, {
        style: 'currency',
        currency: metric.unit,
        maximumFractionDigits: 0,
      });
      const parts = formatter.formatToParts(BigInt(whole));
      if (!fraction) return parts.map((part) => part.value).join('');
      let insertion = parts.length - 1;
      while (
        insertion >= 0 &&
        parts[insertion]?.type !== 'integer' &&
        parts[insertion]?.type !== 'group'
      ) {
        insertion -= 1;
      }
      parts.splice(
        insertion + 1,
        0,
        { type: 'decimal', value: '.' },
        { type: 'fraction', value: fraction },
      );
      return parts.map((part) => part.value).join('');
    } catch {
      return `${value} ${metric.unit}`;
    }
  }
  return `${value} ${metric.unit}`.trim();
}

export function formatComparison(comparison: DecimalComparison): string {
  const prefix = comparison.absolute.startsWith('-') ? '' : '+';
  const percent = comparison.percent === null ? 'not comparable' : `${comparison.percent}%`;
  return `${prefix}${comparison.absolute} (${percent})`;
}

export function reconciles(metric: GovernedMetricEnvelope): boolean {
  const values = [...metric.drill_down.map((item) => item.value), metric.value];
  if (!values.every((value) => /^-?\d+(?:\.\d+)?$/.test(value))) return false;
  const scale = Math.max(...values.map((value) => value.split('.')[1]?.length ?? 0));
  const units = (value: string) => {
    const negative = value.startsWith('-');
    const [whole = '0', fraction = ''] = value.replace('-', '').split('.');
    const magnitude = BigInt(`${whole}${fraction.padEnd(scale, '0')}`);
    return negative ? -magnitude : magnitude;
  };
  return (
    metric.drill_down.reduce((sum, item) => sum + units(item.value), 0n) === units(metric.value)
  );
}

export function readableMachineLabel(value: string): string {
  return value.replaceAll('_', ' ');
}
