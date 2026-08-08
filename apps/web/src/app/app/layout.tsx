/**
 * Layout for the authenticated area.
 *
 * Exists for one reason: to give the `signOut` server action a way to be
 * invoked. The action was written in Phase 1A and nothing rendered it, so
 * ending a session was not possible through the interface — dead code on one
 * side and a missing control on the other.
 *
 * A plain form posting to a server action, not a client component. There is no
 * state to hold, and sign-out should work with JavaScript disabled: a control
 * that ends a session is the last one that should depend on a bundle loading.
 */

import { signOut } from '../sign-in/actions';

export default function AuthenticatedLayout({ children }: { children: React.ReactNode }) {
  return (
    <>
      <div className="app-session-bar">
        <form action={signOut}>
          <button type="submit" className="link-button">
            Sign out
          </button>
        </form>
      </div>
      {children}
    </>
  );
}
