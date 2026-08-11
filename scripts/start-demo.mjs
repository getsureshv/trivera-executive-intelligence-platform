import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const demoUrl = 'http://localhost:3100/sign-in';
const webDirectory = fileURLToPath(new URL('../apps/web/', import.meta.url));
const nextCli = fileURLToPath(
  new URL('../apps/web/node_modules/next/dist/bin/next', import.meta.url),
);
const server = spawn(process.execPath, [nextCli, 'start', '--port', '3100'], {
  cwd: webDirectory,
  stdio: 'inherit',
  env: { ...process.env, EIP_ENV: process.env.EIP_ENV ?? 'local' },
});

let ready = false;
for (let attempt = 0; attempt < 45 && !ready; attempt += 1) {
  if (server.exitCode !== null) break;
  await new Promise((resolve) => setTimeout(resolve, 1_000));
  try {
    const response = await fetch(demoUrl);
    const body = await response.text();
    await new Promise((resolve) => setTimeout(resolve, 100));
    ready =
      server.exitCode === null &&
      response.ok &&
      body.includes('TriVera Executive Intelligence Platform');
  } catch {
    // The dedicated server may still be starting.
  }
}

if (!ready) {
  if (server.exitCode === null) server.kill('SIGTERM');
  throw new Error(`TriVera demo readiness check failed at ${demoUrl}.`);
}

console.log(`TriVera CEO demo ready: ${demoUrl}`);
if (process.argv.includes('--check')) {
  server.kill('SIGTERM');
  await new Promise((resolve) => server.once('exit', resolve));
  process.exit(0);
}
const stop = () => {
  if (server.exitCode === null) server.kill('SIGTERM');
};
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
await new Promise((resolve) => server.once('exit', resolve));
