/**
 * Server-side session handling.
 *
 * The access token lives in an `HttpOnly` cookie and is read **only** on the
 * server (ADR-010 §1). Browser JavaScript never sees it, so an XSS bug cannot
 * exfiltrate a credential.
 *
 * This module must never be imported by a Client Component. `import 'server-only'`
 * turns that mistake into a build error rather than a review comment.
 */

import 'server-only';

import { cookies } from 'next/headers';

export const SESSION_COOKIE = 'eip_session';

/** Read the access token from the request cookies, if present. */
export async function getAccessToken(): Promise<string | null> {
  const store = await cookies();
  return store.get(SESSION_COOKIE)?.value ?? null;
}

/**
 * Cookie attributes for the session.
 *
 * `sameSite: 'lax'` blocks the cookie on cross-site POSTs, which is the CSRF
 * vector that matters for a cookie-authenticated API. `secure` is off only in
 * local development, where there is no TLS terminator.
 */
export function sessionCookieOptions(maxAgeSeconds: number) {
  return {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax' as const,
    path: '/',
    maxAge: maxAgeSeconds,
  };
}
