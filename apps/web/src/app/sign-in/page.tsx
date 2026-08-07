/**
 * Sign-in — development only.
 *
 * There is no password field, and there never will be one: the platform is not
 * an identity provider (ADR-010 §1). This page exchanges a known identity for a
 * development token so the Phase 1A shell can be exercised end to end without
 * standing up an IdP.
 */

import { SignInForm } from './SignInForm';

export const dynamic = 'force-dynamic';

export default function SignInPage() {
  const environment = process.env.EIP_ENV ?? 'local';
  const isDevelopmentAuth = environment === 'local' || environment === 'ci';

  if (!isDevelopmentAuth) {
    return (
      <section className="notice">
        <h2 style={{ marginTop: 0 }}>Sign in with your organization account</h2>
        <p>
          This environment delegates authentication to your organization&rsquo;s identity provider.
          The development sign-in form is not available here.
        </p>
      </section>
    );
  }

  return (
    <section className="card">
      <h2>Development sign-in</h2>
      <p className="card-hint">
        No password is requested because the platform never stores one. This form mints a
        short-lived development token for a seeded identity; in every other environment
        authentication is delegated to the organization&rsquo;s OIDC provider.
      </p>
      <SignInForm />
    </section>
  );
}
