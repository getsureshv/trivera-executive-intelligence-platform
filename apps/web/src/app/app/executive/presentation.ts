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

export function formatSignedPercent(value: string | null): string {
  if (value === null) return 'not comparable';
  if (!/^-?\d+(?:\.\d+)?$/.test(value)) return `${value}%`;
  const negative = value.startsWith('-');
  const [whole = '0', fraction = ''] = value.replace('-', '').split('.');
  let tenths = BigInt(whole) * 10n + BigInt(fraction[0] ?? '0');
  if (Number(fraction[1] ?? '0') >= 5) tenths += 1n;
  const rounded = `${tenths / 10n}.${tenths % 10n}`;
  return `${negative ? '-' : '+'}${rounded}%`;
}

export function formatSignedMetricValue(value: string, metric: GovernedMetricEnvelope): string {
  return `${value.startsWith('-') ? '' : '+'}${formatMetricValue(value, metric)}`;
}

export function formatComparison(
  comparison: DecimalComparison,
  metric?: GovernedMetricEnvelope,
): string {
  const absolute = metric
    ? formatSignedMetricValue(comparison.absolute, metric)
    : `${comparison.absolute.startsWith('-') ? '' : '+'}${comparison.absolute}`;
  return `${absolute} (${formatSignedPercent(comparison.percent)})`;
}

export function formatDate(value: string, timezone: string): string {
  const dateOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  const date = dateOnly
    ? new Date(Date.UTC(Number(dateOnly[1]), Number(dateOnly[2]) - 1, Number(dateOnly[3])))
    : new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: dateOnly ? 'UTC' : timezone,
  }).format(date);
}

export function formatDateTime(value: string, timezone: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    timeZone: timezone,
    timeZoneName: 'short',
  }).format(date);
}

function scaledPair(value: string, target: string): [bigint, bigint] | null {
  if (![value, target].every((item) => /^-?\d+(?:\.\d+)?$/.test(item))) return null;
  const scale = Math.max(value.split('.')[1]?.length ?? 0, target.split('.')[1]?.length ?? 0);
  const units = (item: string) => {
    const negative = item.startsWith('-');
    const [whole = '0', fraction = ''] = item.replace('-', '').split('.');
    const magnitude = BigInt(`${whole}${fraction.padEnd(scale, '0')}`);
    return negative ? -magnitude : magnitude;
  };
  return [units(value), units(target)];
}

export function targetProgress(value: string, target: string): number | null {
  const pair = scaledPair(value, target);
  if (!pair || pair[1] <= 0n) return null;
  const tenths = (pair[0] * 1000n + pair[1] / 2n) / pair[1];
  return Number(tenths < 0n ? 0n : tenths > 1000n ? 1000n : tenths) / 10;
}

export function attentionReason(
  label: string,
  variance: string,
  metric: GovernedMetricEnvelope,
): string {
  const magnitude = variance.startsWith('-') ? variance.slice(1) : variance;
  const direction = variance.startsWith('-') ? 'below' : 'above';
  return `${label} is ${formatMetricValue(magnitude, metric)} ${direction} its configured target.`;
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
