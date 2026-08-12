export default function SetupLoading() {
  return (
    <section className="card" aria-busy="true" aria-label="Loading configuration summary">
      <p className="eyebrow">Configuration summary</p>
      <h1>Loading tenant configuration</h1>
      <div className="skeleton" style={{ width: '60%' }} />
      <div className="skeleton" style={{ width: '42%' }} />
    </section>
  );
}
