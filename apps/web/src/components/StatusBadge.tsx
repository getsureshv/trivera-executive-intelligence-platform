/**
 * A small status pill.
 *
 * Phase 1A ships exactly one shared presentational component, because a
 * component library built before there are components to share is a library
 * built on guesses. Colour is never the only signal — the label carries the
 * meaning, so the badge is readable without colour vision.
 */

export type StatusTone = 'ok' | 'warn' | 'error' | 'muted';

export function StatusBadge({ tone, children }: { tone: StatusTone; children: React.ReactNode }) {
  return (
    <span className={`badge badge--${tone}`} data-tone={tone}>
      {children}
    </span>
  );
}
