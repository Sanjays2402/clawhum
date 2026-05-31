// First-run onboarding state. Pure functions for the persisted shape so
// they can be unit tested; the React hook layers reactivity on top.
//
// Storage shape (localStorage key "clawhum.onboarding.v1"):
//   { dismissed: boolean, hidden: boolean, steps: { tried: boolean, viewed: boolean, saved: boolean } }
//
// Steps:
//   tried  -> user submitted at least one match (sample or live capture)
//   viewed -> user opened a match detail or saw results render
//   saved  -> a match was persisted (saveMatch fired) or share/export happened
//
// The first-run modal opens automatically when no state exists. After
// dismissal it stays closed; the compact checklist banner remains visible
// until all three steps are complete or the user clicks "hide".

import { useCallback, useEffect, useState } from "react";

export const ONBOARDING_KEY = "clawhum.onboarding.v1";
export const ONBOARDING_EVENT = "clawhum:onboarding";

export type StepId = "tried" | "viewed" | "saved";

export interface OnboardingState {
  dismissed: boolean;
  hidden: boolean;
  steps: Record<StepId, boolean>;
}

export const STEP_ORDER: StepId[] = ["tried", "viewed", "saved"];

export const STEP_LABELS: Record<StepId, { title: string; hint: string }> = {
  tried: {
    title: "run your first match",
    hint: "drop a hum, hit record, or play a sample below",
  },
  viewed: {
    title: "open a candidate",
    hint: "click any row in the results to see the pitch contour",
  },
  saved: {
    title: "save or share",
    hint: "every match is auto-saved to history; copy a /r/<id> link to share",
  },
};

export function emptyState(): OnboardingState {
  return { dismissed: false, hidden: false, steps: { tried: false, viewed: false, saved: false } };
}

export function normalise(input: unknown): OnboardingState {
  const base = emptyState();
  if (!input || typeof input !== "object") return base;
  const obj = input as Record<string, unknown>;
  const steps = (obj.steps && typeof obj.steps === "object" ? obj.steps : {}) as Record<string, unknown>;
  return {
    dismissed: Boolean(obj.dismissed),
    hidden: Boolean(obj.hidden),
    steps: {
      tried: Boolean(steps.tried),
      viewed: Boolean(steps.viewed),
      saved: Boolean(steps.saved),
    },
  };
}

export function isComplete(s: OnboardingState): boolean {
  return STEP_ORDER.every((k) => s.steps[k]);
}

export function completedCount(s: OnboardingState): number {
  return STEP_ORDER.reduce((n, k) => n + (s.steps[k] ? 1 : 0), 0);
}

export function withStep(s: OnboardingState, id: StepId, value = true): OnboardingState {
  if (s.steps[id] === value) return s;
  return { ...s, steps: { ...s.steps, [id]: value } };
}

export function withDismissed(s: OnboardingState, dismissed = true): OnboardingState {
  if (s.dismissed === dismissed) return s;
  return { ...s, dismissed };
}

export function withHidden(s: OnboardingState, hidden = true): OnboardingState {
  if (s.hidden === hidden) return s;
  return { ...s, hidden };
}

// --- browser glue (no-ops on SSR) ---

function readRaw(): OnboardingState | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(ONBOARDING_KEY);
    if (!raw) return null;
    return normalise(JSON.parse(raw));
  } catch {
    return null;
  }
}

function writeRaw(s: OnboardingState): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(ONBOARDING_KEY, JSON.stringify(s));
    window.dispatchEvent(new CustomEvent(ONBOARDING_EVENT));
  } catch {
    /* storage may be disabled; ignore */
  }
}

export function loadState(): OnboardingState {
  return readRaw() ?? emptyState();
}

/** True if no persisted state existed prior to this call. Used to decide
 *  whether to auto-open the first-run modal. */
export function isFirstRun(): boolean {
  return readRaw() === null;
}

export function markStep(id: StepId): void {
  const next = withStep(loadState(), id, true);
  writeRaw(next);
}

export function dismiss(): void {
  writeRaw(withDismissed(loadState(), true));
}

export function hide(): void {
  writeRaw(withHidden(loadState(), true));
}

export function reset(): void {
  writeRaw(emptyState());
}

export function useOnboarding(): {
  state: OnboardingState;
  firstRun: boolean;
  markStep: (id: StepId) => void;
  dismiss: () => void;
  hide: () => void;
  reset: () => void;
} {
  const [state, setState] = useState<OnboardingState>(() => emptyState());
  const [firstRun, setFirstRun] = useState<boolean>(false);

  useEffect(() => {
    setFirstRun(isFirstRun());
    setState(loadState());
    const onChange = () => setState(loadState());
    window.addEventListener(ONBOARDING_EVENT, onChange);
    window.addEventListener("storage", onChange);
    return () => {
      window.removeEventListener(ONBOARDING_EVENT, onChange);
      window.removeEventListener("storage", onChange);
    };
  }, []);

  return {
    state,
    firstRun,
    markStep: useCallback((id: StepId) => markStep(id), []),
    dismiss: useCallback(() => dismiss(), []),
    hide: useCallback(() => hide(), []),
    reset: useCallback(() => reset(), []),
  };
}
