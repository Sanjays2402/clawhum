"use client";

/**
 * Renders the active toast stack with an ARIA live region so screen
 * readers announce completions and errors. Mounted once in the root
 * layout; anywhere in the app can fire `toast.success(...)` etc.
 *
 * Layout is bottom-right on desktop and full-width bottom on mobile
 * so the toasts never cover the transport bar at 375px.
 */

import { useEffect, useState } from "react";
import {
  CheckCircle,
  Info,
  WarningCircle,
  XCircle,
  X,
} from "@phosphor-icons/react/dist/ssr";
import {
  dismissToast,
  subscribe,
  type Toast,
  type ToastVariant,
} from "@/lib/toast";

const VARIANT_ICON: Record<ToastVariant, typeof Info> = {
  info: Info,
  success: CheckCircle,
  warning: WarningCircle,
  error: XCircle,
};

const VARIANT_ACCENT: Record<ToastVariant, string> = {
  info: "text-[var(--color-phosphor)]",
  success: "text-[var(--color-phosphor)]",
  warning: "text-[var(--color-amber,#f4b400)]",
  error: "text-[var(--color-magenta)]",
};

export default function Toaster() {
  const [items, setItems] = useState<readonly Toast[]>([]);
  useEffect(() => subscribe(setItems), []);

  // Auto-dismiss timers, one per toast id. We re-create on every
  // change so removing one toast does not cancel another's timer.
  useEffect(() => {
    const timers = items
      .filter((t) => t.durationMs > 0)
      .map((t) => {
        const remaining = Math.max(
          0,
          t.durationMs - (Date.now() - t.createdAt),
        );
        return window.setTimeout(() => dismissToast(t.id), remaining);
      });
    return () => {
      for (const id of timers) clearTimeout(id);
    };
  }, [items]);

  if (!items.length) {
    return (
      <div
        aria-live="polite"
        aria-atomic="true"
        role="status"
        className="sr-only"
      />
    );
  }

  return (
    <div
      aria-live="polite"
      aria-atomic="false"
      role="region"
      aria-label="Notifications"
      className="pointer-events-none fixed inset-x-0 bottom-12 z-50 flex flex-col items-stretch gap-2 px-3 sm:bottom-6 sm:left-auto sm:right-4 sm:max-w-sm sm:px-0"
    >
      {items.map((t) => {
        const Icon = VARIANT_ICON[t.variant];
        return (
          <div
            key={t.id}
            role={t.variant === "error" ? "alert" : "status"}
            className="pointer-events-auto flex items-start gap-2 border border-[var(--color-line)] bg-[var(--color-panel-2)] px-3 py-2 shadow-lg backdrop-blur"
          >
            <Icon
              size={16}
              weight="duotone"
              className={`mt-0.5 shrink-0 ${VARIANT_ACCENT[t.variant]}`}
              aria-hidden="true"
            />
            <div className="min-w-0 flex-1">
              <div className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-phosphor)]">
                {t.title}
              </div>
              {t.description ? (
                <div className="mt-0.5 break-words text-[12px] leading-snug text-[var(--color-muted)]">
                  {t.description}
                </div>
              ) : null}
              {t.action ? (
                <button
                  onClick={() => {
                    try {
                      t.action!.onClick();
                    } finally {
                      dismissToast(t.id);
                    }
                  }}
                  className="mt-1 inline-flex items-center font-mono text-[10px] uppercase tracking-widest text-[var(--color-phosphor)] underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-1 focus-visible:outline-[var(--color-phosphor)]"
                >
                  {t.action.label}
                </button>
              ) : null}
            </div>
            <button
              onClick={() => dismissToast(t.id)}
              aria-label="Dismiss notification"
              className="shrink-0 text-[var(--color-dim)] hover:text-[var(--color-phosphor)] focus-visible:outline focus-visible:outline-1 focus-visible:outline-[var(--color-phosphor)]"
            >
              <X size={12} weight="bold" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
