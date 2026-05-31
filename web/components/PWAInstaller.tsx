"use client";

/**
 * Registers the service worker and surfaces an install prompt.
 *
 * - Registers /sw.js on mount in production-like builds (and dev) so the
 *   offline shell + asset cache work as soon as the user lands.
 * - Listens for `beforeinstallprompt` and shows a dismissible banner.
 * - Detects standalone mode (already installed) and stays quiet.
 * - Remembers dismissal in localStorage for 7 days.
 */
import { useCallback, useEffect, useState } from "react";
import { DeviceMobile, X, DownloadSimple } from "@phosphor-icons/react/dist/ssr";

type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

const DISMISS_KEY = "clawhum.pwa.dismiss.v1";
const DISMISS_TTL_MS = 7 * 24 * 60 * 60 * 1000;

function isStandalone(): boolean {
  if (typeof window === "undefined") return false;
  const mql = window.matchMedia?.("(display-mode: standalone)").matches;
  // iOS Safari uses navigator.standalone
  const ios = (window.navigator as unknown as { standalone?: boolean }).standalone === true;
  return Boolean(mql || ios);
}

function dismissedRecently(): boolean {
  try {
    const raw = localStorage.getItem(DISMISS_KEY);
    if (!raw) return false;
    const ts = Number(raw);
    return Number.isFinite(ts) && Date.now() - ts < DISMISS_TTL_MS;
  } catch {
    return false;
  }
}

export default function PWAInstaller() {
  const [deferred, setDeferred] = useState<BeforeInstallPromptEvent | null>(null);
  const [hidden, setHidden] = useState<boolean>(true);

  // Register the service worker once on mount.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (!("serviceWorker" in navigator)) return;
    const onLoad = () => {
      navigator.serviceWorker
        .register("/sw.js", { scope: "/" })
        .catch(() => {
          /* sw register errors are non-fatal */
        });
    };
    if (document.readyState === "complete") onLoad();
    else window.addEventListener("load", onLoad, { once: true });
    return () => window.removeEventListener("load", onLoad);
  }, []);

  // Listen for the install prompt (Chromium / Edge / Android).
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (isStandalone() || dismissedRecently()) return;
    const onPrompt = (e: Event) => {
      e.preventDefault();
      setDeferred(e as BeforeInstallPromptEvent);
      setHidden(false);
    };
    const onInstalled = () => {
      setHidden(true);
      setDeferred(null);
    };
    window.addEventListener("beforeinstallprompt", onPrompt as EventListener);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt as EventListener);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  const dismiss = useCallback(() => {
    try { localStorage.setItem(DISMISS_KEY, String(Date.now())); } catch {}
    setHidden(true);
  }, []);

  const install = useCallback(async () => {
    if (!deferred) return;
    try {
      await deferred.prompt();
      const choice = await deferred.userChoice;
      if (choice.outcome === "dismissed") dismiss();
      else setHidden(true);
    } finally {
      setDeferred(null);
    }
  }, [deferred, dismiss]);

  if (hidden || !deferred) return null;

  return (
    <div
      role="dialog"
      aria-label="Install clawhum"
      className="fixed z-40 left-3 right-3 bottom-3 sm:left-auto sm:right-4 sm:bottom-4 sm:max-w-sm border border-[var(--color-line)] bg-[var(--color-panel)] shadow-lg"
    >
      <div className="flex items-start gap-3 p-3">
        <div className="shrink-0 mt-0.5">
          <DeviceMobile size={20} weight="duotone" className="text-[var(--color-phosphor)]" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-dim)]">
            install / clawhum
          </div>
          <div className="text-sm text-[var(--color-text)] mt-0.5">
            Add clawhum to your home screen for one tap capture.
          </div>
          <div className="mt-2 flex items-center gap-2">
            <button
              type="button"
              onClick={install}
              className="inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-widest border border-[var(--color-phosphor)] text-[var(--color-phosphor)] px-2.5 py-1.5 hover:bg-[var(--color-bg)]"
            >
              <DownloadSimple size={14} weight="duotone" />
              install
            </button>
            <button
              type="button"
              onClick={dismiss}
              className="font-mono text-[11px] uppercase tracking-widest border border-[var(--color-line)] px-2.5 py-1.5 text-[var(--color-muted)] hover:text-[var(--color-text)]"
            >
              not now
            </button>
          </div>
        </div>
        <button
          type="button"
          onClick={dismiss}
          aria-label="Dismiss install prompt"
          className="shrink-0 -m-1 p-1 text-[var(--color-dim)] hover:text-[var(--color-text)]"
        >
          <X size={14} weight="bold" />
        </button>
      </div>
    </div>
  );
}
