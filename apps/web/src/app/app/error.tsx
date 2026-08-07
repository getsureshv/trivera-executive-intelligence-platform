'use client';

/**
 * Route-level error boundary.
 *
 * Shows the correlation id and nothing else. The server deliberately keeps
 * error detail on its side (ADR-014 §6), so inventing a friendly explanation
 * here would be guessing — and the id is the one thing that actually helps
 * whoever investigates.
 */

import { useEffect } from 'react';

export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Reaches the browser console only; the server already logged the cause
    // with full context.
    console.error('Overview failed to render', error.digest ?? error.message);
  }, [error]);

  return (
    <section className="notice notice--error" role="alert">
      <h2 style={{ marginTop: 0 }}>Something went wrong</h2>
      <p>
        The overview could not be loaded. The platform recorded the details; quote the reference
        below if you report this.
      </p>
      {error.digest && (
        <p>
          Reference: <code>{error.digest}</code>
        </p>
      )}
      <button type="button" onClick={reset}>
        Try again
      </button>
    </section>
  );
}
