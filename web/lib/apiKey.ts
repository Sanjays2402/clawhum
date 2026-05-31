"use client";

// Lightweight client-side API key store. The secret stays in the browser
// (localStorage), is sent on every same-origin /api/* request via a fetch
// patch, and never leaves the user's machine unless they explicitly fire
// a request that goes to the backend. This lets the existing FastAPI
// `require_api_key` dependency work end-to-end without us shipping a
// server-side session yet.

import { useEffect, useState } from "react";

const STORAGE_KEY = "clawhum.apiKey.v1";
const EVENT = "clawhum:apiKey";

export function getApiKey(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(STORAGE_KEY) || "";
  } catch {
    return "";
  }
}

export function setApiKey(key: string): void {
  if (typeof window === "undefined") return;
  try {
    if (key) window.localStorage.setItem(STORAGE_KEY, key);
    else window.localStorage.removeItem(STORAGE_KEY);
    window.dispatchEvent(new CustomEvent(EVENT, { detail: key }));
  } catch {
    /* storage may be disabled; ignore */
  }
}

export function useApiKey(): [string, (k: string) => void] {
  const [key, setKey] = useState<string>("");
  useEffect(() => {
    setKey(getApiKey());
    const onChange = () => setKey(getApiKey());
    window.addEventListener(EVENT, onChange);
    window.addEventListener("storage", onChange);
    return () => {
      window.removeEventListener(EVENT, onChange);
      window.removeEventListener("storage", onChange);
    };
  }, []);
  return [key, setApiKey];
}

let installed = false;

// Patch global fetch so all existing /api/* calls automatically carry
// X-API-Key when the user has saved one. No-op for cross-origin calls.
export function installApiKeyFetch(): void {
  if (installed || typeof window === "undefined") return;
  installed = true;
  const orig = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    try {
      const url =
        typeof input === "string"
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      const isLocal =
        url.startsWith("/api/") ||
        url.startsWith(`${window.location.origin}/api/`);
      if (isLocal) {
        const key = getApiKey();
        if (key) {
          const headers = new Headers(init?.headers || (input instanceof Request ? input.headers : undefined));
          if (!headers.has("X-API-Key")) headers.set("X-API-Key", key);
          init = { ...(init || {}), headers };
        }
      }
    } catch {
      /* fall through to original fetch */
    }
    return orig(input as any, init);
  };
}

export function maskKey(key: string): string {
  if (!key) return "";
  if (key.length <= 4) return "*".repeat(key.length);
  return `${"*".repeat(Math.max(4, key.length - 4))}${key.slice(-4)}`;
}
