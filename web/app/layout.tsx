import "../styles/globals.css";
import type { Metadata } from "next";
import { Inter, IBM_Plex_Mono } from "next/font/google";
import TransportBar from "@/components/TransportBar";
import SiteNav from "@/components/SiteNav";

const inter = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-inter",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "clawhum / fingerprint matcher",
  description: "Acoustic fingerprint matching. Hum, drop, capture. Inspect chroma bins and match scores.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${plexMono.variable}`}>
      <body>
        <div className="min-h-screen flex flex-col">
          <TransportBar />
          <SiteNav />
          <main className="flex-1">{children}</main>
          <footer className="border-t border-[var(--color-line)] px-4 py-2 flex items-center justify-between text-[10px] font-mono text-[var(--color-dim)] uppercase tracking-widest">
            <span>clawhum.fingerprint.matcher / build 0.2.0</span>
            <span>sr 44.1k / win 2048 / hop 512</span>
          </footer>
        </div>
      </body>
    </html>
  );
}
