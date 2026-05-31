"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Sparkle, Microphone, ListChecks, ShareNetwork, Check, X, Play, MusicNote } from "@phosphor-icons/react";
import type { Icon } from "@phosphor-icons/react";
import {
  completedCount,
  isComplete,
  STEP_LABELS,
  STEP_ORDER,
  useOnboarding,
  type StepId,
} from "@/lib/onboarding";

interface Props {
  /** Fired when the user clicks "Run a sample now". Receives the WAV URL. */
  onRunSample?: (file: string) => void | Promise<void>;
}

const STEP_ICONS: Record<StepId, Icon> = {
  tried: Microphone,
  viewed: ListChecks,
  saved: ShareNetwork,
};

const SAMPLE_FILE = "/samples/twinkle.wav";

export default function OnboardingTour({ onRunSample }: Props) {
  const { state, firstRun, markStep, dismiss, hide } = useOnboarding();
  const [modalOpen, setModalOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);

  // Auto-open on first run only.
  useEffect(() => {
    if (firstRun && !state.dismissed) setModalOpen(true);
  }, [firstRun, state.dismissed]);

  // Move focus into the dialog when it opens and restore Esc to close.
  useEffect(() => {
    if (!modalOpen) return;
    const prev = document.activeElement as HTMLElement | null;
    closeBtnRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        handleClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      prev?.focus?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modalOpen]);

  const handleClose = useCallback(() => {
    setModalOpen(false);
    dismiss();
  }, [dismiss]);

  const handleRunSample = useCallback(async () => {
    if (!onRunSample) {
      setModalOpen(false);
      return;
    }
    setRunning(true);
    try {
      await onRunSample(SAMPLE_FILE);
    } finally {
      setRunning(false);
      setModalOpen(false);
    }
  }, [onRunSample]);

  const done = completedCount(state);
  const total = STEP_ORDER.length;
  const complete = isComplete(state);

  return (
    <>
      {/* Compact checklist banner: shown until complete or user dismisses */}
      {!modalOpen && !complete && state.dismissed && !state.hidden && (
        <div
          className="px-4 mt-3"
          aria-label="Getting started checklist"
        >
          <div className="panel rounded-[2px] px-3 py-2 flex items-center gap-3 flex-wrap">
            <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)]">
              <Sparkle size={12} weight="duotone" className="text-[var(--color-phosphor)]" />
              <span>getting started</span>
              <span className="text-[var(--color-phosphor)] tabular-nums">{done}/{total}</span>
            </div>
            <ol className="flex items-center gap-2 flex-wrap text-[11px] font-mono">
              {STEP_ORDER.map((id, i) => {
                const ok = state.steps[id];
                return (
                  <li
                    key={id}
                    className={`flex items-center gap-1.5 px-2 py-1 border border-[var(--color-line)] ${ok ? "text-[var(--color-phosphor)]" : "text-[var(--color-muted)]"}`}
                  >
                    <span className={`inline-flex items-center justify-center w-4 h-4 border ${ok ? "border-[var(--color-phosphor)]" : "border-[var(--color-line)]"}`}>
                      {ok ? <Check size={10} weight="bold" /> : <span className="tabular-nums text-[10px]">{i + 1}</span>}
                    </span>
                    <span>{STEP_LABELS[id].title}</span>
                  </li>
                );
              })}
            </ol>
            <button
              type="button"
              onClick={() => setModalOpen(true)}
              className="ml-auto font-mono text-[10px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-phosphor)] border border-[var(--color-line)] px-2 py-1"
            >
              show guide
            </button>
            <button
              type="button"
              onClick={hide}
              aria-label="Hide checklist"
              className="font-mono text-[10px] text-[var(--color-dim)] hover:text-[var(--color-amber)] border border-[var(--color-line)] px-2 py-1"
            >
              <X size={12} weight="bold" />
            </button>
          </div>
        </div>
      )}

      {/* First-run modal */}
      {modalOpen && (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="onboarding-title"
          className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/70 backdrop-blur-sm px-3 py-4"
          onClick={(e) => {
            if (e.target === e.currentTarget) handleClose();
          }}
        >
          <div className="panel rounded-[2px] w-full max-w-xl border border-[var(--color-line)] shadow-2xl">
            <header className="flex items-center gap-2 px-4 py-3 border-b border-[var(--color-line)]">
              <Sparkle size={16} weight="duotone" className="text-[var(--color-phosphor)]" />
              <h2
                id="onboarding-title"
                className="font-mono text-[12px] uppercase tracking-widest text-[var(--color-phosphor)] flex-1"
              >
                welcome to clawhum
              </h2>
              <button
                ref={closeBtnRef}
                type="button"
                onClick={handleClose}
                aria-label="Dismiss onboarding"
                className="font-mono text-[10px] text-[var(--color-dim)] hover:text-[var(--color-amber)] border border-[var(--color-line)] px-2 py-1"
              >
                <X size={12} weight="bold" />
              </button>
            </header>

            <div className="px-4 py-4 space-y-4">
              <p className="text-[12px] text-[var(--color-muted)] leading-relaxed">
                Hum a melody, get matches from a real audio index in milliseconds. Three quick steps
                to see what the product can do.
              </p>

              <ol className="space-y-2">
                {STEP_ORDER.map((id, i) => {
                  const Icon = STEP_ICONS[id];
                  const ok = state.steps[id];
                  return (
                    <li
                      key={id}
                      className="flex items-start gap-3 px-3 py-2 border border-[var(--color-line)] rounded-[2px]"
                    >
                      <span
                        className={`inline-flex items-center justify-center w-6 h-6 border ${ok ? "border-[var(--color-phosphor)] text-[var(--color-phosphor)]" : "border-[var(--color-line)] text-[var(--color-muted)]"} font-mono text-[10px] tabular-nums`}
                      >
                        {ok ? <Check size={12} weight="bold" /> : i + 1}
                      </span>
                      <div className="flex-1 min-w-0">
                        <div className={`font-mono text-[12px] uppercase tracking-wider ${ok ? "text-[var(--color-phosphor)]" : "text-[var(--color-text)]"}`}>
                          {STEP_LABELS[id].title}
                        </div>
                        <div className="text-[11px] text-[var(--color-muted)] mt-0.5 leading-relaxed">
                          {STEP_LABELS[id].hint}
                        </div>
                      </div>
                      <Icon size={18} weight="duotone" className="text-[var(--color-muted)] mt-0.5" />
                    </li>
                  );
                })}
              </ol>

              <div className="flex flex-col-reverse sm:flex-row items-stretch sm:items-center gap-2 pt-1">
                <button
                  type="button"
                  onClick={handleClose}
                  className="font-mono text-[11px] uppercase tracking-widest text-[var(--color-muted)] hover:text-[var(--color-text)] border border-[var(--color-line)] px-3 py-2"
                >
                  I'll explore on my own
                </button>
                <button
                  type="button"
                  disabled={running}
                  onClick={handleRunSample}
                  className="ml-auto inline-flex items-center justify-center gap-2 font-mono text-[11px] uppercase tracking-widest text-black bg-[var(--color-phosphor)] hover:opacity-90 disabled:opacity-50 px-3 py-2"
                >
                  {running ? (
                    <>
                      <MusicNote size={14} weight="duotone" className="animate-pulse" />
                      running sample...
                    </>
                  ) : (
                    <>
                      <Play size={14} weight="fill" />
                      run a sample now
                    </>
                  )}
                </button>
              </div>

              <p className="text-[10px] font-mono text-[var(--color-dim)] leading-relaxed">
                we'll feed twinkle.wav into the same /match endpoint your hum would hit.
                progress is saved locally; you can reset it from settings.
              </p>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

/** Exposed helper so other pages can mark steps without importing the hook. */
export { markStep as markOnboardingStep, reset as resetOnboarding } from "@/lib/onboarding";
