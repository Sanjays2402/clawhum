import "../styles/globals.css";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "ClawHum",
  description: "Hum it. Find it.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <div className="min-h-screen">
          <nav className="border-b border-[var(--line)] px-6 py-4 flex items-center justify-between">
            <a href="/" className="font-semibold tracking-tight">
              <span className="text-[var(--accent)]">claw</span>hum
            </a>
            <div className="flex gap-6 text-sm text-[var(--muted)]">
              <a href="/" className="hover:text-white">Hum</a>
              <a href="/library" className="hover:text-white">Library</a>
              <a href="/feedback" className="hover:text-white">Feedback</a>
            </div>
          </nav>
          <main>{children}</main>
        </div>
      </body>
    </html>
  );
}
