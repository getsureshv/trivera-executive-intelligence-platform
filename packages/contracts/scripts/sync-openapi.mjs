/**
 * Fetch the live OpenAPI document into `openapi.json`.
 *
 * The committed document is the contract of record (ADR-001). CI runs this and
 * fails if the result differs from what is committed, so an endpoint cannot
 * change without the contract change being visible in the diff — which is the
 * mechanism that keeps "API-first" from becoming a slogan.
 *
 *   node ./scripts/sync-openapi.mjs [--check]
 */

import { writeFile, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const target = join(here, '..', 'openapi.json');
const baseUrl = process.env.EIP_API_BASE_URL ?? 'http://localhost:8000';
const checkOnly = process.argv.includes('--check');

const response = await fetch(`${baseUrl}/openapi.json`);
if (!response.ok) {
  console.error(`Failed to fetch ${baseUrl}/openapi.json — is the API running?`);
  process.exit(1);
}

// Stable key ordering so the committed document produces a readable diff
// rather than a reshuffle on every sync.
const sortKeys = (value) => {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, sortKeys(value[key])]),
    );
  }
  return value;
};

const serialized = `${JSON.stringify(sortKeys(await response.json()), null, 2)}\n`;

if (checkOnly) {
  const committed = await readFile(target, 'utf8').catch(() => null);
  if (committed !== serialized) {
    console.error(
      'openapi.json is out of date. Run `pnpm --filter @eip/contracts sync` and commit the result.',
    );
    process.exit(1);
  }
  console.log('openapi.json is up to date.');
} else {
  await writeFile(target, serialized, 'utf8');
  console.log(`Wrote ${target}`);
}
