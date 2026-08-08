/**
 * Failure diagnostics that are safe to upload.
 *
 * A failing browser test is nearly useless without context, and the usual ways
 * of capturing that context — Playwright traces, videos, HAR files — all record
 * complete request headers. That means `Authorization: Bearer …` and the
 * session cookie, published as a downloadable CI artefact.
 *
 * So the diagnostics are assembled here instead, and everything
 * credential-shaped is removed before it is attached:
 *
 *  * the final URL and page title;
 *  * console messages, redacted;
 *  * a DOM snapshot, redacted;
 *  * the *names* of cookies present, never their values.
 *
 * `redact` is deliberately blunt. It over-matches — a UUID in a log line is not
 * a secret — and that is the right trade for a debugging aid. Anything it
 * mangles is still identifiable by shape and position, and the alternative
 * failure mode is publishing a live token.
 */

import type { Page, TestInfo } from '@playwright/test';

/** Anything shaped like a JWT, a bearer header, or a long opaque token. */
const CREDENTIAL_PATTERNS: readonly RegExp[] = [
  // JWT: three base64url segments.
  /\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b/g,
  /\b(bearer)\s+[A-Za-z0-9._~+/-]{8,}=*/gi,
  /\b(eip_session)=[^;\s"']+/gi,
  // Any `password`/`secret`/`token` assignment, in JSON or query-string form.
  /("?(?:password|secret|token|access_token)"?\s*[:=]\s*")([^"]+)(")/gi,
  /\b((?:password|secret|token|access_token)=)([^&;\s"']+)/gi,
];

export function redact(value: string): string {
  let output = value;
  for (const pattern of CREDENTIAL_PATTERNS) {
    output = output.replace(pattern, (_match, ...groups) => {
      // Patterns with capture groups keep the label and drop the value; the
      // rest are replaced wholesale.
      const captured = groups.slice(0, -2);
      if (captured.length >= 2) return `${captured[0]}[REDACTED]${captured[2] ?? ''}`;
      if (captured.length === 1) return `${captured[0]} [REDACTED]`;
      return '[REDACTED]';
    });
  }
  return output;
}

/** Console output collected for the lifetime of a page. */
export function collectConsole(page: Page): string[] {
  const messages: string[] = [];
  page.on('console', (message) => {
    messages.push(redact(`[${message.type()}] ${message.text()}`));
  });
  page.on('pageerror', (error) => {
    messages.push(redact(`[pageerror] ${error.message}`));
  });
  return messages;
}

/**
 * Attach diagnostics when — and only when — the test failed.
 *
 * Also asserts nothing: a diagnostic helper that can fail a passing test is a
 * source of noise, and the redaction it performs is verified by its own unit
 * test rather than by running.
 */
export async function attachDiagnosticsOnFailure(
  page: Page,
  testInfo: TestInfo,
  consoleMessages: string[],
): Promise<void> {
  if (testInfo.status === testInfo.expectedStatus) return;

  const attach = async (name: string, body: string) => {
    await testInfo.attach(name, { body: redact(body), contentType: 'text/plain' });
  };

  try {
    await attach('final-url', page.url());
    await attach('page-title', await page.title());
    await attach('console', consoleMessages.join('\n') || '(no console output)');

    // Names only. A cookie value here would be the exact leak this file exists
    // to prevent — the session cookie is HttpOnly, which stops page scripts
    // reading it but not the test framework.
    const cookies = await page.context().cookies();
    await attach('cookie-names', cookies.map((cookie) => cookie.name).join('\n') || '(none)');

    await attach('dom', await page.content());
  } catch (error) {
    await testInfo.attach('diagnostics-error', {
      body: redact(String(error)),
      contentType: 'text/plain',
    });
  }
}
