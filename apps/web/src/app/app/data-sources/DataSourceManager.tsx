'use client';

import { useRef, useState, useTransition } from 'react';

import type { ConnectionTest, DataSource } from '@eip/contracts';

import { addSource, beginTest, pollTest } from './actions';
import { resultSummary } from './presentation';

const POLL_INTERVAL_MS = 1000;
const MAX_POLLS = 30;
const sleep = (milliseconds: number) => new Promise((resolve) => setTimeout(resolve, milliseconds));

export function DataSourceManager({ initialSources }: { initialSources: DataSource[] }) {
  const [sources, setSources] = useState(initialSources);
  const [tests, setTests] = useState<Record<string, ConnectionTest>>({});
  const [message, setMessage] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();
  const formRef = useRef<HTMLFormElement>(null);

  function submit(formData: FormData) {
    setMessage(null);
    // FormData holds the one request copy. Clear the rendered control before
    // awaiting the network so page inspection cannot capture the password.
    formRef.current?.reset();
    startTransition(async () => {
      const result = await addSource(formData);
      if (!result.ok) {
        setMessage(result.message);
        return;
      }
      setSources((current) => [...current, result.source]);
      setMessage('PostgreSQL source added.');
    });
  }

  function testConnection(sourceId: string) {
    setMessage(null);
    startTransition(async () => {
      const started = await beginTest(sourceId);
      if (!started.ok) {
        setMessage(started.message);
        return;
      }
      let current = started.test;
      setTests((all) => ({ ...all, [sourceId]: current }));
      for (
        let count = 0;
        count < MAX_POLLS && ['queued', 'running'].includes(current.status);
        count += 1
      ) {
        await sleep(POLL_INTERVAL_MS);
        const polled = await pollTest(current.poll_url);
        if (!polled.ok) {
          setMessage(polled.message);
          return;
        }
        current = polled.test;
        setTests((all) => ({ ...all, [sourceId]: current }));
      }
      if (['queued', 'running'].includes(current.status))
        setMessage('The connection test is taking longer than expected. Try again.');
    });
  }

  return (
    <>
      <section className="card">
        <h1>Data sources</h1>
        <p className="card-hint">
          Add a PostgreSQL source and verify its connection. Passwords are write-only and are never
          shown again.
        </p>
        {message && (
          <p className="notice" role="status">
            {message}
          </p>
        )}
        {sources.length === 0 ? (
          <p className="notice">No data sources have been added yet.</p>
        ) : (
          <ul className="source-list">
            {sources.map((source) => {
              const test = tests[source.id];
              return (
                <li className="source-card" key={source.id}>
                  <div>
                    <strong>{source.name}</strong>
                    <div className="mono">PostgreSQL · {source.endpoint}</div>
                  </div>
                  <button
                    type="button"
                    disabled={isPending}
                    onClick={() => testConnection(source.id)}
                  >
                    Test connection
                  </button>
                  {test && (
                    <div className="diagnostics" aria-live="polite">
                      <p>
                        <strong>{resultSummary(test)}</strong>
                      </p>
                      <ol>
                        {test.checks.map((check) => (
                          <li key={check.type} data-status={check.status}>
                            <span className="mono">{check.type}</span>: {check.status} —{' '}
                            {check.message}
                            {check.remediation_hint && <div>{check.remediation_hint}</div>}
                          </li>
                        ))}
                      </ol>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="card">
        <h2>Add PostgreSQL source</h2>
        <form ref={formRef} action={submit} className="form" autoComplete="off">
          <div>
            <label htmlFor="source-name">Name</label>
            <input id="source-name" name="name" required maxLength={200} />
          </div>
          <div className="form-row">
            <div>
              <label htmlFor="source-host">Host</label>
              <input id="source-host" name="host" required />
            </div>
            <div>
              <label htmlFor="source-port">Port</label>
              <input
                id="source-port"
                name="port"
                type="number"
                min="1"
                max="65535"
                defaultValue="5432"
                required
              />
            </div>
          </div>
          <div>
            <label htmlFor="source-username">Username</label>
            <input id="source-username" name="username" required autoComplete="off" />
          </div>
          <div>
            <label htmlFor="source-database">Database</label>
            <input id="source-database" name="database" required />
          </div>
          <div>
            <label htmlFor="source-tls">TLS mode</label>
            <select id="source-tls" name="tlsMode" defaultValue="require">
              <option value="require">Require TLS</option>
              <option value="disable">Disable TLS</option>
            </select>
          </div>
          <div>
            <label htmlFor="source-password">Password</label>
            <input
              id="source-password"
              name="password"
              type="password"
              required
              autoComplete="new-password"
            />
          </div>
          <button type="submit" disabled={isPending}>
            {isPending ? 'Working…' : 'Add source'}
          </button>
        </form>
      </section>
    </>
  );
}
