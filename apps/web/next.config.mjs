/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  // Type and lint errors fail the build. Suppressing them here would let a
  // broken contract ship, which is precisely what the generated-types
  // discipline exists to prevent (ADR-001).
  typescript: { ignoreBuildErrors: false },
  eslint: { ignoreDuringBuilds: false },

  // Transpile the workspace contracts package (it ships TypeScript source).
  transpilePackages: ['@eip/contracts'],

  // Do not advertise the framework version.
  poweredByHeader: false,

  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
        ],
      },
    ];
  },
};

export default nextConfig;
