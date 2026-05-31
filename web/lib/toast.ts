// Tiny framework-free toast store. Subscribe-based so a React provider
// can render notifications, and any module (server actions, fetch
// wrappers, components) can fire them without prop-drilling.
//
// Design choices:
// - No external dep. We already pull in @phosphor-icons and Tailwind.
// - Pure functions on a module-scoped store; trivially unit testable
//   in node:test without a DOM.
// - IDs are monotonic + random so toasts dedupe cleanly when the
//   same code path fires twice in quick succession (StrictMode in dev).
// - Default auto-dismiss is per-variant; errors stick around longer
//   so they survive a glance away from the screen.

export type ToastVariant = "info" | "success" | "warning" | "error";

export interface ToastAction {
  /** Visible label, kept short. No em dashes. */
  label: string;
  /** Synchronous handler invoked when the action button is clicked. */
  onClick: () => void;
}

export interface Toast {
  id: string;
  variant: ToastVariant;
  title: string;
  description?: string;
  /** Milliseconds to live. 0 means sticky. */
  durationMs: number;
  createdAt: number;
  action?: ToastAction;
}

export interface ToastInput {
  variant?: ToastVariant;
  title: string;
  description?: string;
  /** Override default duration. 0 = sticky. */
  durationMs?: number;
  action?: ToastAction;
}

type Listener = (toasts: readonly Toast[]) => void;

const DEFAULT_DURATIONS: Record<ToastVariant, number> = {
  info: 4000,
  success: 3500,
  warning: 6000,
  error: 8000,
};

const MAX_TOASTS = 5;

let counter = 0;
let toasts: Toast[] = [];
const listeners = new Set<Listener>();

function emit() {
  const snapshot = toasts.slice();
  for (const l of listeners) l(snapshot);
}

export function subscribe(fn: Listener): () => void {
  listeners.add(fn);
  // Push current snapshot so late subscribers see what's on screen.
  fn(toasts.slice());
  return () => {
    listeners.delete(fn);
  };
}

export function getToasts(): readonly Toast[] {
  return toasts.slice();
}

export function showToast(input: ToastInput): Toast {
  const variant: ToastVariant = input.variant ?? "info";
  const id = `t_${Date.now().toString(36)}_${(counter++).toString(36)}`;
  const t: Toast = {
    id,
    variant,
    title: stripEmDash(input.title),
    description: input.description ? stripEmDash(input.description) : undefined,
    durationMs:
      input.durationMs === undefined ? DEFAULT_DURATIONS[variant] : input.durationMs,
    createdAt: Date.now(),
    action: input.action,
  };
  toasts = [t, ...toasts].slice(0, MAX_TOASTS);
  emit();
  return t;
}

export function dismissToast(id: string): boolean {
  const next = toasts.filter((t) => t.id !== id);
  if (next.length === toasts.length) return false;
  toasts = next;
  emit();
  return true;
}

export function clearToasts() {
  if (!toasts.length) return;
  toasts = [];
  emit();
}

// Convenience shortcuts so call sites read naturally.
export const toast = {
  info(title: string, opts: Omit<ToastInput, "title" | "variant"> = {}) {
    return showToast({ ...opts, variant: "info", title });
  },
  success(title: string, opts: Omit<ToastInput, "title" | "variant"> = {}) {
    return showToast({ ...opts, variant: "success", title });
  },
  warning(title: string, opts: Omit<ToastInput, "title" | "variant"> = {}) {
    return showToast({ ...opts, variant: "warning", title });
  },
  error(title: string, opts: Omit<ToastInput, "title" | "variant"> = {}) {
    return showToast({ ...opts, variant: "error", title });
  },
};

// Stripping em / en dashes keeps the UI free of one of the most common
// AI writing tells without forcing callers to babysit punctuation.
export function stripEmDash(s: string): string {
  return s.replace(/\u2014/g, ", ").replace(/\u2013/g, "-");
}

// Exposed for tests.
export function _reset() {
  toasts = [];
  listeners.clear();
  counter = 0;
}
