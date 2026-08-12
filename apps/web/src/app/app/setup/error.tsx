'use client';

export default function SetupError({ reset }: { reset: () => void }) {
  return (
    <section className="notice notice--error" role="alert">
      <h1>Configuration summary could not be loaded</h1>
      <p>The platform recorded the problem without exposing internal details.</p>
      <button type="button" onClick={reset}>
        Try again
      </button>
    </section>
  );
}
