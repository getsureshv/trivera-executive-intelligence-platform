/**
 * API error type.
 *
 * Deliberately separate from `api.ts`, which is `server-only`. `ApiError` is a
 * plain data type with no server dependency: client components render it, and
 * the test runner can import it without pulling in the fetch layer.
 */

import type { ProblemDocument } from '@eip/contracts';

/** An API failure carrying the server's RFC 9457 problem document. */
export class ApiError extends Error {
  // Assigned explicitly rather than via TypeScript parameter properties: the
  // latter are not erasable syntax and cannot be run by Node's type-stripping
  // test runner.
  readonly status: number;
  readonly problem: ProblemDocument | null;

  constructor(status: number, problem: ProblemDocument | null) {
    super(problem?.detail ?? `Request failed with status ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.problem = problem;
  }

  /**
   * The id to quote when reporting a problem.
   *
   * The server deliberately keeps error detail on its side (ADR-014 §6), so
   * this is genuinely the most useful thing the UI can show — and far better
   * than a guessed explanation.
   */
  get correlationId(): string | null {
    return this.problem?.correlation_id ?? null;
  }

  get isUnauthenticated(): boolean {
    return this.status === 401;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }
}
