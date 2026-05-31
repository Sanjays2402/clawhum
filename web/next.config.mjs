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
      { source: "/api/history", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/history` },
      { source: "/api/history/export", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/history/export` },
      { source: "/api/history/:id", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/history/:id` },
      { source: "/api/me", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/me` },
      { source: "/api/usage", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/usage` },
      { source: "/api/webhooks", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/webhooks` },
      { source: "/api/webhooks/:id", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/webhooks/:id` },
      { source: "/api/webhooks/:id/deliveries", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/webhooks/:id/deliveries` },
      { source: "/api/webhooks/:id/test", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/webhooks/:id/test` },
      { source: "/api/webhooks/:id/deliveries/:dId/redeliver", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/webhooks/:id/deliveries/:dId/redeliver` },
      { source: "/api/activity", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/activity` },
      { source: "/api/v1/privacy/export", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/v1/privacy/export` },
      { source: "/api/v1/privacy/me", destination: `${process.env.CLAWHUM_API_URL || "http://127.0.0.1:7451"}/v1/privacy/me` },
    ];
  },
};
export default nextConfig;
