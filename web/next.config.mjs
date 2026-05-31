/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      { source: "/api/match", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/match` },
      { source: "/api/stats", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/stats` },
      { source: "/api/reindex", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/reindex` },
      { source: "/api/feedback", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/feedback` },
      { source: "/api/metrics", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/metrics` },
      { source: "/api/health", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/health` },
      { source: "/api/track/:trackId/audio", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/track/:trackId/audio` },
      { source: "/api/track/:trackId/pitch", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/track/:trackId/pitch` },
      { source: "/api/pitch", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/pitch` },
      { source: "/api/share", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/share` },
      { source: "/api/share/:id", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/share/:id` },
      { source: "/api/me", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/me` },
    ];
  },
};
export default nextConfig;
