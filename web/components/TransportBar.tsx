"use client";

import { useEffect, useRef } from "react";
import { setMeters, setTransport, useTransport } from "@/lib/transport";

function db(v: number): number {
  if (v <= 0) return -Infinity;
  return 20 * Math.log10(v);
}

function MeterBar({ value, label, unit }: { value: number; label: string; unit: string }) {
  // value in dBFS, e.g. -60..0
  const clamped = Math.max(-60, Math.min(0, isFinite(value) ? value : -60));
  const pct = ((clamped + 60) / 60) * 100;
  return (
    <div className="flex items-center gap-2 min-w-[110px]">
      <span className="label-xs w-8">{label}</span>
      <div className="meter-track h-1.5 flex-1 rounded-[1px]">
        <div className="meter-fill" style={{ width: `${pct}%` }} />
      </div>
      <span className="font-mono text-[10px] text-[var(--color-muted)] w-12 text-right tabular-nums">
        {isFinite(value) ? value.toFixed(1) : "-inf"}{unit}
      </span>
    </div>
  );
}

export default function TransportBar() {
  const { state, meters } = useTransport();
  const tickRef = useRef<number | null>(null);

  // Decay meters back toward floor when nothing pushing them.
  useEffect(() => {
    let last = performance.now();
    const tick = (t: number) => {
      const dt = (t - last) / 1000;
      last = t;
      // Slow decay so the bars feel alive but not jittery.
      if (state !== "rec" && state !== "play") {
        const decayed = {
          peak: Math.max(0, meters.peak - dt * 0.4),
          rms: Math.max(0, meters.rms - dt * 0.2),
          lufs: meters.lufs - dt * 4,
        };
        if (Math.abs(decayed.peak - meters.peak) > 0.001 || Math.abs(decayed.rms - meters.rms) > 0.001) {
          setMeters(decayed);
        }
      }
      tickRef.current = requestAnimationFrame(tick);
    };
    tickRef.current = requestAnimationFrame(tick);
    return () => { if (tickRef.current) cancelAnimationFrame(tickRef.current); };
  }, [state, meters.peak, meters.rms, meters.lufs]);

  const peakDb = db(meters.peak);
  const rmsDb = db(meters.rms);
  const lufs = isFinite(meters.lufs) ? meters.lufs : -60;

  const isRec = state === "rec";
  const isPlay = state === "play";

  return (
    <div className="border-b border-[var(--color-line)] bg-[var(--color-panel)] px-3 py-1.5 flex items-center gap-4 text-xs">
      {/* Transport buttons */}
      <div className="flex items-center gap-1">
        <button
          onClick={() => setTransport("play")}
          aria-label="play"
          className={`w-7 h-7 flex items-center justify-center border rounded-[2px] transition
            ${isPlay ? "border-[var(--color-phosphor)] text-[var(--color-phosphor)] bg-[rgba(61,252,142,0.08)]" : "border-[var(--color-line-2)] text-[var(--color-muted)] hover:text-[var(--color-phosphor)] hover:border-[var(--color-phosphor)]"}`}
        >
          <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor"><path d="M2 1 L9 5 L2 9 Z" /></svg>
        </button>
        <button
          onClick={() => setTransport("pause")}
          aria-label="pause"
          className={`w-7 h-7 flex items-center justify-center border rounded-[2px] transition
            ${state === "pause" ? "border-[var(--color-amber)] text-[var(--color-amber)]" : "border-[var(--color-line-2)] text-[var(--color-muted)] hover:text-[var(--color-text)]"}`}
        >
          <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor"><rect x="2" y="1" width="2" height="8" /><rect x="6" y="1" width="2" height="8" /></svg>
        </button>
        <button
          onClick={() => setTransport("stop")}
          aria-label="stop"
          className={`w-7 h-7 flex items-center justify-center border rounded-[2px] transition
            ${state === "stop" ? "border-[var(--color-line-2)] text-[var(--color-text)]" : "border-[var(--color-line-2)] text-[var(--color-muted)] hover:text-[var(--color-text)]"}`}
        >
          <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor"><rect x="1.5" y="1.5" width="7" height="7" /></svg>
        </button>
      </div>

      {/* REC indicator */}
      <div className="flex items-center gap-2 pl-3 border-l border-[var(--color-line)]">
        <span className={isRec ? "rec-dot" : "led-dot off"} />
        <span className={`label-xs ${isRec ? "text-[var(--color-red)]" : ""}`}>rec</span>
      </div>

      {/* Meters */}
      <div className="flex items-center gap-4 pl-3 border-l border-[var(--color-line)] flex-1">
        <MeterBar value={peakDb} label="PEAK" unit="dB" />
        <MeterBar value={rmsDb} label="RMS" unit="dB" />
        <MeterBar value={lufs} label="LUFS" unit="" />
      </div>

      {/* Right-side status */}
      <div className="hidden md:flex items-center gap-3 pl-3 border-l border-[var(--color-line)] font-mono text-[10px] text-[var(--color-muted)] uppercase tracking-widest">
        <span>44.1<span className="text-[var(--color-dim)]">k</span></span>
        <span>24<span className="text-[var(--color-dim)]">bit</span></span>
        <span className="text-[var(--color-phosphor)]">●</span>
      </div>
    </div>
  );
}
