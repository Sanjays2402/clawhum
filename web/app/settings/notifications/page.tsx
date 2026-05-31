"use client";

/**
 * Notification preferences page.
 *
 * Lets the user grant browser notification permission, opt in to
 * specific event kinds (match saved, webhook delivery), enable a
 * soft audio cue, and inspect the most recent notifications the
 * engine actually fired.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  Bell,
  BellSlash,
  CheckCircle,
  WarningCircle,
  WaveSine,
  Broadcast,
  ShieldWarning,
  TestTube,
  Trash,
  ArrowLeft,
  SpeakerHigh,
  EyeSlash,
} from "@phosphor-icons/react/dist/ssr";
import {
  appendFired,
  clearFired,
  permissionState,
  requestPermission,
  useFired,
  usePrefs,
  type FiredEntry,
  type NotifyKind,
  type PermissionState,
} from "@/lib/notifyPrefs";

function fmtAgo(ms: number): string {
  const diff = Date.now() - ms;
  if (diff < 0) return "just now";
  const s = Math.round(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  return `${d}d ago`;
}

const KIND_META: Record<NotifyKind, { label: string; hint: string; icon: typeof WaveSine }> = {
  match: { label: "saved match", hint: "fires when a new query is saved to history", icon: WaveSine },
  delivery: { label: "webhook delivery", hint: "fires on every outbound webhook attempt", icon: Broadcast },
};

function PermissionPill({ state }: { state: PermissionState }) {
  if (state === "granted")
    return (
      <span className="inline-flex items-center gap-1.5 text-[11px] font-mono uppercase tracking-widest text-[var(--color-phosphor)]">
        <CheckCircle weight="duotone" size={14} /> granted
      </span>
    );
  if (state === "denied")
    return (
      <span className="inline-flex items-center gap-1.5 text-[11px] font-mono uppercase tracking-widest text-red-400">
        <WarningCircle weight="duotone" size={14} /> denied
      </span>
    );
  if (state === "unsupported")
    return (
      <span className="inline-flex items-center gap-1.5 text-[11px] font-mono uppercase tracking-widest text-[var(--color-dim)]">
        <ShieldWarning weight="duotone" size={14} /> unsupported
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] font-mono uppercase tracking-widest text-[var(--color-muted)]">
      <Bell weight="duotone" size={14} /> not requested
    </span>
  );
}

export default function NotificationsPage() {
  const [prefs, setPrefs] = usePrefs();
  const fired = useFired();
  const [perm, setPerm] = useState<PermissionState>("default");
  const [busy, setBusy] = useState(false);
  const [testMsg, setTestMsg] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    setPerm(permissionState());
  }, []);

  async function onEnable() {
    setBusy(true);
    try {
      const p = await requestPermission();
      setPerm(p);
      if (p === "granted") {
        setPrefs({ ...prefs, enabled: true });
      } else if (p === "denied") {
        setPrefs({ ...prefs, enabled: false });
      }
    } finally {
      setBusy(false);
    }
  }

  function onToggleEnabled() {
    if (!prefs.enabled && perm !== "granted") {
      void onEnable();
      return;
    }
    setPrefs({ ...prefs, enabled: !prefs.enabled });
  }

  function onToggleKind(k: NotifyKind) {
    setPrefs({ ...prefs, kinds: { ...prefs.kinds, [k]: !prefs.kinds[k] } });
  }

  function onTest() {
    const id = `test-${Date.now()}`;
    const entry: FiredEntry = {
      id,
      kind: "match",
      title: "clawhum: test notification",
      body: "if you can read this, the wiring works",
      at: Date.now(),
      href: "/settings/notifications",
    };
    appendFired(entry);
    try {
      if (typeof Notification !== "undefined" && Notification.permission === "granted") {
        new Notification(entry.title, { body: entry.body, tag: id, icon: "/icons/pwa-192.png" });
        setTestMsg("sent. check your system tray.");
      } else {
        setTestMsg("logged locally. grant permission to also send a system notification.");
      }
    } catch (e) {
      setTestMsg(`could not send: ${(e as Error).message}`);
    }
  }

  const permLabel =
    perm === "granted"
      ? "browser permission is granted"
      : perm === "denied"
      ? "permission was denied. enable it in your browser's site settings to re-arm."
      : perm === "unsupported"
      ? "your browser does not expose the notification api. in-app log will still record events."
      : "click enable to request browser permission.";

  return (
    <div className="max-w-3xl mx-auto px-4 sm:px-6 py-8 sm:py-12 space-y-8">
      <div className="space-y-2">
        <Link
          href="/settings"
          className="inline-flex items-center gap-1.5 text-[11px] font-mono uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-text)]"
        >
          <ArrowLeft size={12} /> back to settings
        </Link>
        <h1 className="font-mono text-[18px] uppercase tracking-widest text-[var(--color-phosphor)] flex items-center gap-2">
          <Bell weight="duotone" size={20} /> notifications
        </h1>
        <p className="text-[12px] text-[var(--color-muted)] max-w-xl">
          push activity from your account into the browser when something happens. matches, deliveries, anything that
          lands in the activity inbox. polling cadence: 30 seconds.
        </p>
      </div>

      <section className="border border-[var(--color-line)] bg-[var(--color-panel)]">
        <header className="px-4 py-3 border-b border-[var(--color-line)] flex items-center justify-between gap-3">
          <div>
            <div className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-text)]">
              browser permission
            </div>
            <div className="text-[11px] text-[var(--color-muted)] mt-0.5">{permLabel}</div>
          </div>
          {mounted ? <PermissionPill state={perm} /> : null}
        </header>
        <div className="px-4 py-4 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={onEnable}
            disabled={busy || perm === "granted" || perm === "denied" || perm === "unsupported"}
            className="px-3 py-1.5 border border-[var(--color-line)] bg-[var(--color-bg)] text-[11px] font-mono uppercase tracking-widest hover:border-[var(--color-phosphor)] hover:text-[var(--color-phosphor)] disabled:opacity-40 disabled:cursor-not-allowed inline-flex items-center gap-1.5"
          >
            <Bell weight="duotone" size={12} /> {busy ? "asking..." : "enable browser notifications"}
          </button>
          <button
            type="button"
            onClick={onTest}
            className="px-3 py-1.5 border border-[var(--color-line)] bg-[var(--color-bg)] text-[11px] font-mono uppercase tracking-widest hover:border-[var(--color-phosphor)] hover:text-[var(--color-phosphor)] inline-flex items-center gap-1.5"
          >
            <TestTube weight="duotone" size={12} /> send test
          </button>
        </div>
        {testMsg ? (
          <div className="px-4 pb-3 text-[11px] font-mono text-[var(--color-dim)]">{testMsg}</div>
        ) : null}
      </section>

      <section className="border border-[var(--color-line)] bg-[var(--color-panel)]">
        <header className="px-4 py-3 border-b border-[var(--color-line)] flex items-center justify-between gap-3">
          <div>
            <div className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-text)]">
              delivery preferences
            </div>
            <div className="text-[11px] text-[var(--color-muted)] mt-0.5">choose what wakes you up.</div>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={prefs.enabled}
            onClick={onToggleEnabled}
            className={`relative inline-flex h-5 w-10 items-center border border-[var(--color-line)] transition-colors ${
              prefs.enabled ? "bg-[var(--color-phosphor)]/30 border-[var(--color-phosphor)]" : "bg-[var(--color-bg)]"
            }`}
          >
            <span
              className={`inline-block h-3 w-3 transform transition-transform ${
                prefs.enabled
                  ? "translate-x-6 bg-[var(--color-phosphor)]"
                  : "translate-x-1 bg-[var(--color-muted)]"
              }`}
              aria-hidden
            />
            <span className="sr-only">{prefs.enabled ? "disable notifications" : "enable notifications"}</span>
          </button>
        </header>
        <ul className="divide-y divide-[var(--color-line)]">
          {(Object.keys(KIND_META) as NotifyKind[]).map((k) => {
            const meta = KIND_META[k];
            const Icon = meta.icon;
            const on = prefs.kinds[k];
            return (
              <li key={k} className="px-4 py-3 flex items-center gap-3">
                <Icon weight="duotone" size={18} className="text-[var(--color-phosphor)]" />
                <div className="flex-1 min-w-0">
                  <div className="font-mono text-[12px] uppercase tracking-widest text-[var(--color-text)]">
                    {meta.label}
                  </div>
                  <div className="text-[11px] text-[var(--color-muted)] truncate">{meta.hint}</div>
                </div>
                <label className="inline-flex items-center gap-2 text-[11px] font-mono uppercase tracking-widest text-[var(--color-muted)] cursor-pointer">
                  <input
                    type="checkbox"
                    checked={on}
                    onChange={() => onToggleKind(k)}
                    aria-label={`notify on ${meta.label}`}
                    className="accent-[var(--color-phosphor)]"
                  />
                  {on ? "on" : "off"}
                </label>
              </li>
            );
          })}
          <li className="px-4 py-3 flex items-center gap-3">
            <SpeakerHigh weight="duotone" size={18} className="text-[var(--color-phosphor)]" />
            <div className="flex-1 min-w-0">
              <div className="font-mono text-[12px] uppercase tracking-widest text-[var(--color-text)]">
                play a sound
              </div>
              <div className="text-[11px] text-[var(--color-muted)] truncate">
                tiny 250ms tone via webaudio, no asset download.
              </div>
            </div>
            <label className="inline-flex items-center gap-2 text-[11px] font-mono uppercase tracking-widest text-[var(--color-muted)] cursor-pointer">
              <input
                type="checkbox"
                checked={prefs.sound}
                onChange={() => setPrefs({ ...prefs, sound: !prefs.sound })}
                aria-label="play sound on notification"
                className="accent-[var(--color-phosphor)]"
              />
              {prefs.sound ? "on" : "off"}
            </label>
          </li>
          <li className="px-4 py-3 flex items-center gap-3">
            <EyeSlash weight="duotone" size={18} className="text-[var(--color-phosphor)]" />
            <div className="flex-1 min-w-0">
              <div className="font-mono text-[12px] uppercase tracking-widest text-[var(--color-text)]">
                only when tab hidden
              </div>
              <div className="text-[11px] text-[var(--color-muted)] truncate">
                skip notifications when this tab is already focused.
              </div>
            </div>
            <label className="inline-flex items-center gap-2 text-[11px] font-mono uppercase tracking-widest text-[var(--color-muted)] cursor-pointer">
              <input
                type="checkbox"
                checked={prefs.onlyWhenHidden}
                onChange={() => setPrefs({ ...prefs, onlyWhenHidden: !prefs.onlyWhenHidden })}
                aria-label="only notify when tab is hidden"
                className="accent-[var(--color-phosphor)]"
              />
              {prefs.onlyWhenHidden ? "on" : "off"}
            </label>
          </li>
        </ul>
      </section>

      <section className="border border-[var(--color-line)] bg-[var(--color-panel)]">
        <header className="px-4 py-3 border-b border-[var(--color-line)] flex items-center justify-between gap-3">
          <div>
            <div className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-text)]">recently fired</div>
            <div className="text-[11px] text-[var(--color-muted)] mt-0.5">
              what the engine actually delivered, newest first. last {fired.length} of 50.
            </div>
          </div>
          {fired.length > 0 ? (
            <button
              type="button"
              onClick={() => clearFired()}
              className="px-2 py-1 border border-[var(--color-line)] text-[10px] font-mono uppercase tracking-widest hover:border-red-400 hover:text-red-400 inline-flex items-center gap-1"
            >
              <Trash size={11} /> clear log
            </button>
          ) : null}
        </header>
        {fired.length === 0 ? (
          <div className="px-4 py-10 text-center text-[12px] text-[var(--color-muted)]">
            <BellSlash weight="duotone" size={28} className="mx-auto mb-2 text-[var(--color-dim)]" />
            nothing fired yet. enable notifications above, then trigger a match or a webhook.
          </div>
        ) : (
          <ul className="divide-y divide-[var(--color-line)]">
            {fired.map((e) => (
              <li key={e.id} className="px-4 py-2.5 flex items-start gap-3">
                <Bell weight="duotone" size={14} className="text-[var(--color-phosphor)] mt-0.5" />
                <div className="flex-1 min-w-0">
                  <Link
                    href={e.href || "/activity"}
                    className="font-mono text-[12px] text-[var(--color-text)] hover:text-[var(--color-phosphor)] block truncate"
                  >
                    {e.title}
                  </Link>
                  <div className="text-[11px] text-[var(--color-muted)] truncate">{e.body}</div>
                </div>
                <span className="text-[10px] font-mono uppercase tracking-widest text-[var(--color-dim)] shrink-0">
                  {fmtAgo(e.at)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
