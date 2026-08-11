export default function ExecutiveLoading() {
  return (
    <section className="card" aria-busy="true" aria-label="Loading executive evidence">
      <p className="eyebrow">Executive Command Center</p>
      <h1>Loading governed evidence</h1>
      <div className="skeleton" style={{ width: '60%' }} />
      <div className="skeleton" style={{ width: '40%' }} />
    </section>
  );
}
