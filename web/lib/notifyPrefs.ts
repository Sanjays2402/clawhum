"use client";

/**
 * Notification preferences.
 *
 * Persists which event kinds should trigger a browser Notification
 * (and optional sound) when the engine polls /api/activity. State
 * lives in localStorage so it survives reloads and is per-device,
 * matching how the rest of the app stores per-user prefs.
 */

import { useCallback, useEffect, useState } from "react";

export const NOTIFY_PREFS_KEY = "clawhum.notify.prefs.v1";
export const NOTIFY_PREFS_EVENT = "clawhum:notify:prefs";
export const NOTIFY_FIRED_KEY = "clawhum.notify.fired.v1";
export const NOTIFY_FIRED_EVENT = "clawhum:notify:fired";

export type NotifyKind = "match" | "delivery";

export interface NotifyPrefs {
  enabled: boolean;
  kinds: Record<NotifyKind, boolean>;
  sound: boolean;
  /** Don't fire if same-tab is already focused (so we don't double up on toasts). */
  onlyWhenHidden: boolean;
}

export const DEFAULT_PREFS: NotifyPrefs = {
  enabled: false,
  kinds: { match: true, delivery: true },
  sound: false,
  onlyWhenHidden: true,
};

export interface FiredEntry {
  id: string;
  kind: NotifyKind;
  title: string;
  body: string;
  at: number; // ms
  href: string;
}

const FIRED_MAX = 50;

function safeParse<T>(raw: string | null, fallback: T): T {
  if (!raw) return fallback;
  try {
    const v = JSON.parse(raw);
    if (v == null || typeof v !== "object") return fallback;
    return v as T;
  } catch {
    return fallback;
  }
}

export function mergePrefs(partial: Partial<NotifyPrefs> | null | undefined): NotifyPrefs {
  const p = partial ?? {};
  return {
    enabled: typeof p.enabled === "boolean" ? p.enabled : DEFAULT_PREFS.enabled,
    sound: typeof p.sound === "boolean" ? p.sound : DEFAULT_PREFS.sound,
    onlyWhenHidden:
      typeof p.onlyWhenHidden === "boolean" ? p.onlyWhenHidden : DEFAULT_PREFS.onlyWhenHidden,
    kinds: {
      match: p.kinds && typeof p.kinds.match === "boolean" ? p.kinds.match : DEFAULT_PREFS.kinds.match,
      delivery:
        p.kinds && typeof p.kinds.delivery === "boolean"
          ? p.kinds.delivery
          : DEFAULT_PREFS.kinds.delivery,
    },
  };
}

export function loadPrefs(): NotifyPrefs {
  if (typeof window === "undefined") return DEFAULT_PREFS;
  return mergePrefs(safeParse<Partial<NotifyPrefs>>(window.localStorage.getItem(NOTIFY_PREFS_KEY), {}));
}

export function savePrefs(p: NotifyPrefs): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(NOTIFY_PREFS_KEY, JSON.stringify(p));
  window.dispatchEvent(new Event(NOTIFY_PREFS_EVENT));
}

export function loadFired(): FiredEntry[] {
  if (typeof window === "undefined") return [];
  const arr = safeParse<unknown>(window.localStorage.getItem(NOTIFY_FIRED_KEY), []);
  if (!Array.isArray(arr)) return [];
  return arr
    .filter(
      (e): e is FiredEntry =>
        !!e &&
        typeof e === "object" &&
        typeof (e as FiredEntry).id === "string" &&
        typeof (e as FiredEntry).title === "string",
    )
    .slice(0, FIRED_MAX);
}

export function appendFired(entry: FiredEntry): void {
  if (typeof window === "undefined") return;
  const existing = loadFired();
  // Dedupe by id
  if (existing.some((e) => e.id === entry.id)) return;
  const next = [entry, ...existing].slice(0, FIRED_MAX);
  window.localStorage.setItem(NOTIFY_FIRED_KEY, JSON.stringify(next));
  window.dispatchEvent(new Event(NOTIFY_FIRED_EVENT));
}

export function clearFired(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(NOTIFY_FIRED_KEY);
  window.dispatchEvent(new Event(NOTIFY_FIRED_EVENT));
}

export function usePrefs(): [NotifyPrefs, (next: NotifyPrefs) => void] {
  const [prefs, setPrefs] = useState<NotifyPrefs>(DEFAULT_PREFS);
  useEffect(() => {
    setPrefs(loadPrefs());
    const onChange = () => setPrefs(loadPrefs());
    window.addEventListener(NOTIFY_PREFS_EVENT, onChange);
    window.addEventListener("storage", onChange);
    return () => {
      window.removeEventListener(NOTIFY_PREFS_EVENT, onChange);
      window.removeEventListener("storage", onChange);
    };
  }, []);
  const update = useCallback((next: NotifyPrefs) => {
    savePrefs(next);
    setPrefs(next);
  }, []);
  return [prefs, update];
}

export function useFired(): FiredEntry[] {
  const [items, setItems] = useState<FiredEntry[]>([]);
  useEffect(() => {
    setItems(loadFired());
    const onChange = () => setItems(loadFired());
    window.addEventListener(NOTIFY_FIRED_EVENT, onChange);
    window.addEventListener("storage", onChange);
    return () => {
      window.removeEventListener(NOTIFY_FIRED_EVENT, onChange);
      window.removeEventListener("storage", onChange);
    };
  }, []);
  return items;
}

export type PermissionState = "default" | "granted" | "denied" | "unsupported";

export function permissionState(): PermissionState {
  if (typeof window === "undefined" || typeof Notification === "undefined") return "unsupported";
  return (Notification.permission as PermissionState) ?? "default";
}

export async function requestPermission(): Promise<PermissionState> {
  if (typeof window === "undefined" || typeof Notification === "undefined") return "unsupported";
  try {
    const p = await Notification.requestPermission();
    return p as PermissionState;
  } catch {
    return permissionState();
  }
}
