/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      { source: "/api/match", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/match` },
      { source: "/api/stats", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/stats` },
      { source: "/api/reindex", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/reindex` },
      { source: "/api/feedback", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/feedback` },
    ];
  },
};
export default nextConfig;
