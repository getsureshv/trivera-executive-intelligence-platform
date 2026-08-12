/**
 * Root layout — the application shell.
 *
 * Shared application shell for authenticated governed experiences.
 */

import type { Metadata, Viewport } from 'next';

import './globals.css';

export const metadata: Metadata = {
  title: 'TriVera Executive Intelligence Platform',
  description: 'Governed executive intelligence and source management',
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const environment = process.env.EIP_ENV ?? 'local';
  const isDevelopmentAuth = environment === 'local' || environment === 'ci';

  return (
    <html lang="en">
      <body>
        <div className="app-shell">
          {/*
            A standing, unmissable reminder that the development token issuer
            is mounted. It would be an authentication bypass in production, so
            its presence should never be quiet (ADR-010).
          */}
          {isDevelopmentAuth && (
            <div className="dev-banner" role="status">
              DEVELOPMENT AUTHENTICATION ACTIVE — identities are not verified in this environment
            </div>
          )}

          <header className="app-header">
            <div className="app-brand">
              <strong>TriVera Executive Intelligence Platform</strong>
              <span>Governed executive evidence</span>
            </div>
            <nav aria-label="Primary">
              <a href="/app">Overview</a>
              {' · '}
              <a href="/app/executive">Executive</a>
              {' · '}
              <a href="/app/setup">Configuration</a>
              {' · '}
              <a href="/app/data-sources">Data sources</a>
            </nav>
          </header>

          <main className="app-main">{children}</main>

          <footer className="app-footer">
            Environment: <code>{environment}</code> · Governed evidence, tenant scoped.
          </footer>
        </div>
      </body>
    </html>
  );
}
