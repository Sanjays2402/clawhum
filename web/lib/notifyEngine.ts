"use client";

/**
 * Notification engine.
 *
 * Polls /api/activity on a 30s cadence, diffs items against a cursor
 * stored in localStorage, and fires a browser Notification for every
 * new item whose kind the user opted into. Also records each fired
 * notification into the in-app log so the settings page can show it.
 *
 * Designed to be mounted once at the root layout. Safe to import in
 * a server bundle: all browser APIs are gated behind typeof checks.
 */

import {
  appendFired,
  loadPrefs,
  NOTIFY_PREFS_EVENT,
  type FiredEntry,
  type NotifyKind,
  type NotifyPrefs,
} from "./notifyPrefs";

const CURSOR_KEY = "clawhum.notify.cursor.v1";
const POLL_MS = 30_000;

interface ActivityItem {
  id: string;
  kind: NotifyKind;
  title: string;
  subtitle: string;
  ok: boolean;
  created_at: number; // seconds
  href: string;
}

interface ActivityResp {
  items: ActivityItem[];
  total: number;
  latest_at: number;
}

function getCursor(): number {
  try {
    return Number(window.localStorage.getItem(CURSOR_KEY) || "0");
  } catch {
    return 0;
  }
}

function setCursor(ts: number): void {
  try {
    window.localStorage.setItem(CURSOR_KEY, String(ts));
  } catch {
    /* ignore */
  }
}

function shouldFire(prefs: NotifyPrefs, item: ActivityItem): boolean {
  if (!prefs.enabled) return false;
  if (!prefs.kinds[item.kind]) return false;
  if (prefs.onlyWhenHidden && typeof document !== "undefined" && document.visibilityState === "visible") {
    return false;
  }
  return true;
}

// Tiny "ping" tone via WebAudio. Avoids shipping audio files.
function playPing(): void {
  try {
    const Ctx = (window.AudioContext || (window as any).webkitAudioContext) as typeof AudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.setValueAtTime(880, ctx.currentTime);
    osc.frequency.exponentialRampToValueAtTime(660, ctx.currentTime + 0.18);
    gain.gain.setValueAtTime(0.0001, ctx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.18, ctx.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.25);
    osc.connect(gain).connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.3);
    osc.onended = () => ctx.close().catch(() => {});
  } catch {
    /* sound is best-effort */
  }
}

function fireOne(item: ActivityItem, prefs: NotifyPrefs): void {
  const title = item.kind === "match" ? `clawhum: ${item.title}` : `webhook: ${item.title}`;
  const body = item.subtitle || (item.ok ? "ok" : "failed");
  const entry: FiredEntry = {
    id: item.id,
    kind: item.kind,
    title,
    body,
    at: Date.now(),
    href: item.href,
  };
  appendFired(entry);
  try {
    if (typeof Notification !== "undefined" && Notification.permission === "granted") {
      const n = new Notification(title, {
        body,
        tag: `clawhum-${item.id}`,
        icon: "/icons/pwa-192.png",
        badge: "/favicon.svg",
      });
      n.onclick = () => {
        try {
          window.focus();
          if (item.href) window.location.assign(item.href);
        } catch {
          /* ignore */
        }
      };
    }
  } catch {
    /* ignore */
  }
  if (prefs.sound) playPing();
}

let started = false;
let timer: number | null = null;
let inflight = false;

async function tick(): Promise<void> {
  if (inflight) return;
  inflight = true;
  try {
    const prefs = loadPrefs();
    if (!prefs.enabled) return;
    const r = await fetch("/api/activity?limit=25", { cache: "no-store" });
    if (!r.ok) return;
    const data = (await r.json()) as ActivityResp;
    const cursor = getCursor();
    const fresh = (data.items || [])
      .filter((it) => Number(it.created_at) > cursor)
      .sort((a, b) => a.created_at - b.created_at);
    if (fresh.length === 0) {
      if (cursor === 0 && data.latest_at) setCursor(data.latest_at);
      return;
    }
    // First-ever run: don't blast the user. Just seed the cursor.
    if (cursor === 0) {
      setCursor(data.latest_at || fresh[fresh.length - 1].created_at);
      return;
    }
    for (const it of fresh) {
      if (shouldFire(prefs, it)) fireOne(it, prefs);
    }
    setCursor(data.latest_at || fresh[fresh.length - 1].created_at);
  } catch {
    /* network blips are fine */
  } finally {
    inflight = false;
  }
}

export function startNotifyEngine(): () => void {
  if (typeof window === "undefined") return () => {};
  if (started) return () => {};
  started = true;
  // Kick once on mount, then on interval.
  void tick();
  timer = window.setInterval(tick, POLL_MS);
  const onPrefs = () => {
    // When prefs change, run immediately so toggling on feels live.
    void tick();
  };
  const onVis = () => {
    if (document.visibilityState === "visible") void tick();
  };
  window.addEventListener(NOTIFY_PREFS_EVENT, onPrefs);
  document.addEventListener("visibilitychange", onVis);
  return () => {
    if (timer != null) window.clearInterval(timer);
    window.removeEventListener(NOTIFY_PREFS_EVENT, onPrefs);
    document.removeEventListener("visibilitychange", onVis);
    started = false;
    timer = null;
  };
}
