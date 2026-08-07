'use client';

/**
 * Development sign-in form.
 *
 * A Client Component only because it needs the pending/error states of
 * `useActionState`. It holds no credential and reads no cookie — the token
 * never crosses into browser-accessible JavaScript (ADR-010).
 */

import { useActionState } from 'react';
import { useFormStatus } from 'react-dom';

import { signIn, type SignInState } from './actions';

const initialState: SignInState = { error: null, correlationId: null };

function SubmitButton() {
  const { pending } = useFormStatus();
  return (
    <button type="submit" disabled={pending}>
      {pending ? 'Signing in…' : 'Sign in'}
    </button>
  );
}

export function SignInForm() {
  const [state, formAction] = useActionState(signIn, initialState);

  return (
    <form action={formAction} className="form">
      <div>
        <label htmlFor="email">Email address</label>
        <input
          id="email"
          name="email"
          type="email"
          autoComplete="off"
          required
          placeholder="ada@acme.invalid"
        />
      </div>

      <div>
        <label htmlFor="tenantId">Organization ID (optional)</label>
        <input id="tenantId" name="tenantId" type="text" autoComplete="off" placeholder="UUID" />
        <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)', margin: '6px 0 0' }}>
          Required only if the account belongs to more than one organization. The server verifies
          membership before honouring it — naming an organization you do not belong to is refused.
        </p>
      </div>

      {state.error && (
        <p className="notice notice--error" role="alert">
          {state.error}
          {state.correlationId && (
            <>
              {' '}
              <code>{state.correlationId}</code>
            </>
          )}
        </p>
      )}

      <SubmitButton />
    </form>
  );
}
