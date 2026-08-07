'use server';

/**
 * Sign-in server action (development only).
 *
 * ADR-010 delegates authentication to an OIDC provider and forbids storing
 * passwords, so there is no credential form here — the action exchanges a known
 * email for a development token via the API, and stores it in an `HttpOnly`
 * cookie the browser cannot read.
 *
 * When a real IdP is configured this file is replaced by the standard OIDC
 * redirect/callback pair. Nothing downstream changes: the API already verifies
 * a bearer token and resolves tenant context from membership, and the cookie
 * handling here is identical either way.
 */

import { redirect } from 'next/navigation';
import { cookies } from 'next/headers';

import type { DevTokenResponse, ProblemDocument } from '@eip/contracts';

import { SESSION_COOKIE, sessionCookieOptions } from '@/lib/session';

const API_BASE_URL = process.env.EIP_API_BASE_URL ?? 'http://localhost:8000';

export interface SignInState {
  error: string | null;
  correlationId: string | null;
}

export async function signIn(_previous: SignInState, formData: FormData): Promise<SignInState> {
  const email = String(formData.get('email') ?? '').trim();
  const tenantId = String(formData.get('tenantId') ?? '').trim();

  if (!email) {
    return { error: 'Enter the email address of a seeded user.', correlationId: null };
  }

  const response = await fetch(`${API_BASE_URL}/v1/dev/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    // `tenant_id` is a *request*. The API verifies membership before honouring
    // it and refuses if the user does not belong to that organization —
    // which is what the isolation tests exercise.
    body: JSON.stringify(tenantId ? { email, tenant_id: tenantId } : { email }),
    cache: 'no-store',
  });

  if (!response.ok) {
    let problem: ProblemDocument | null = null;
    try {
      problem = (await response.json()) as ProblemDocument;
    } catch {
      // Non-JSON error body; the status is all we can honestly report.
    }
    return {
      error:
        response.status === 404
          ? 'No such user in this environment.'
          : (problem?.detail ?? 'Sign-in failed.'),
      correlationId: problem?.correlation_id ?? null,
    };
  }

  const token = (await response.json()) as DevTokenResponse;
  const store = await cookies();
  store.set(SESSION_COOKIE, token.access_token, sessionCookieOptions(token.expires_in));

  redirect('/app');
}

export async function signOut(): Promise<void> {
  const store = await cookies();
  store.delete(SESSION_COOKIE);
  redirect('/sign-in');
}
