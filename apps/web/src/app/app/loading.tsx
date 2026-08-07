/**
 * Route-level loading state.
 *
 * The overview does real network work (identity resolution, a readiness probe),
 * so a placeholder that mirrors the final layout is shown rather than leaving
 * the shell blank.
 */

export default function Loading() {
  return (
    <>
      <section className="card" aria-busy="true" aria-label="Loading organization context">
        <h2>Organization context</h2>
        <div className="skeleton" style={{ width: '55%' }} />
        <div className="skeleton" style={{ width: '35%' }} />
        <div className="skeleton" style={{ width: '45%' }} />
      </section>
      <section className="card" aria-busy="true" aria-label="Loading platform status">
        <h2>Platform status</h2>
        <div className="skeleton" style={{ width: '40%' }} />
        <div className="skeleton" style={{ width: '60%' }} />
      </section>
    </>
  );
}
