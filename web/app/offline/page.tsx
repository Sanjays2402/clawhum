import Link from "next/link";

export const metadata = {
  title: "offline / clawhum",
  description: "You are offline. Reconnect to fingerprint a hum.",
};

export default function OfflinePage() {
  return (
    <div className="min-h-[60vh] flex items-center justify-center px-4">
      <div className="max-w-md w-full border border-[var(--color-line)] bg-[var(--color-panel)] p-6">
        <div className="flex items-center gap-2 mb-3">
          <span className="led-dot" />
          <span className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-dim)]">
            connection / lost
          </span>
        </div>
        <h1 className="font-mono text-xl text-[var(--color-phosphor)] mb-2">
          you are offline
        </h1>
        <p className="text-sm text-[var(--color-muted)] mb-5 leading-relaxed">
          Matching needs the network because the fingerprint index lives on the
          server. Your saved history is still readable from this device.
        </p>
        <div className="flex flex-col sm:flex-row gap-2">
          <Link
            href="/history"
            className="flex-1 text-center font-mono text-[11px] uppercase tracking-widest border border-[var(--color-line)] px-3 py-2 hover:bg-[var(--color-bg)]"
          >
            open history
          </Link>
          <Link
            href="/"
            className="flex-1 text-center font-mono text-[11px] uppercase tracking-widest border border-[var(--color-phosphor)] text-[var(--color-phosphor)] px-3 py-2 hover:bg-[var(--color-bg)]"
          >
            retry
          </Link>
        </div>
      </div>
    </div>
  );
}
