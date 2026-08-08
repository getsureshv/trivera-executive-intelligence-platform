/**
 * Minimal flat config. The browser suite has no framework rules to inherit —
 * it is Node code driving a browser, not application code.
 */

const config = [
  {
    ignores: ['node_modules/**', 'playwright-report/**', 'test-results/**'],
  },
];

export default config;
