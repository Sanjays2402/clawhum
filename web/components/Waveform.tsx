"use client";

import { useEffect, useRef } from "react";

interface Props {
  /** Float samples in [-1,1] or null/undefined for placeholder sine. */
  samples?: Float32Array | null;
  /** Height px */
  height?: number;
  /** Color override */
  color?: string;
  /** Show frequency-axis-like tick marks along x */
  ticks?: boolean;
  /** Optional time markers in seconds */
  duration?: number;
  /** Mark a highlighted segment [startSec, endSec] in magenta */
  highlight?: [number, number] | null;
  /** Animate noise/sine if no samples */
  animate?: boolean;
  /** Label text in top-left */
  label?: string;
  /** Compact mode (no padding) */
  compact?: boolean;
}

export default function Waveform({
  samples,
  height = 120,
  color = "#3DFC8E",
  ticks = true,
  duration,
  highlight,
  animate = true,
  label,
  compact,
}: Props) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const cvsRef = useRef<HTMLCanvasElement | null>(null);
  const rafRef = useRef<number | null>(null);
  const tRef = useRef(0);

  useEffect(() => {
    const wrap = wrapRef.current;
    const cvs = cvsRef.current;
    if (!wrap || !cvs) return;

    const dpr = window.devicePixelRatio || 1;

    function resize() {
      if (!wrap || !cvs) return;
      const w = wrap.clientWidth;
      cvs.width = Math.max(1, Math.floor(w * dpr));
      cvs.height = Math.max(1, Math.floor(height * dpr));
      cvs.style.width = `${w}px`;
      cvs.style.height = `${height}px`;
      draw();
    }

    function draw() {
      if (!cvs) return;
      const ctx = cvs.getContext("2d");
      if (!ctx) return;
      const W = cvs.width;
      const H = cvs.height;
      ctx.clearRect(0, 0, W, H);

      // background grid
      ctx.fillStyle = "#06090C";
      ctx.fillRect(0, 0, W, H);

      ctx.strokeStyle = "rgba(61,252,142,0.04)";
      ctx.lineWidth = 1;
      const gridX = 12;
      const gridY = 4;
      for (let i = 0; i <= gridX; i++) {
        const x = (i / gridX) * W;
        ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
      }
      for (let j = 0; j <= gridY; j++) {
        const y = (j / gridY) * H;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
      }

      // center line
      ctx.strokeStyle = "rgba(61,252,142,0.15)";
      ctx.beginPath(); ctx.moveTo(0, H / 2); ctx.lineTo(W, H / 2); ctx.stroke();

      // highlight band (matched segment)
      if (highlight && duration && duration > 0) {
        const [s, e] = highlight;
        const x0 = (Math.max(0, s) / duration) * W;
        const x1 = (Math.min(duration, e) / duration) * W;
        ctx.fillStyle = "rgba(255,61,190,0.10)";
        ctx.fillRect(x0, 0, x1 - x0, H);
        ctx.strokeStyle = "rgba(255,61,190,0.6)";
        ctx.lineWidth = 1 * dpr;
        ctx.beginPath();
        ctx.moveTo(x0, 0); ctx.lineTo(x0, H);
        ctx.moveTo(x1, 0); ctx.lineTo(x1, H);
        ctx.stroke();
      }

      // waveform
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.25 * dpr;
      ctx.beginPath();

      const src = samples;
      if (src && src.length > 0) {
        // peak-rendering: bucket samples per pixel column
        const cols = Math.floor(W / dpr);
        const step = src.length / cols;
        for (let c = 0; c < cols; c++) {
          let min = 1, max = -1;
          const a = Math.floor(c * step);
          const b = Math.floor((c + 1) * step);
          for (let i = a; i < b; i++) {
            const v = src[i];
            if (v < min) min = v;
            if (v > max) max = v;
          }
          const x = c * dpr;
          const y1 = ((1 - max) / 2) * H;
          const y2 = ((1 - min) / 2) * H;
          if (c === 0) ctx.moveTo(x, y1);
          ctx.lineTo(x, y1);
          ctx.lineTo(x, y2);
        }
      } else {
        // animated sine + low noise placeholder
        const t = tRef.current;
        for (let x = 0; x < W; x += dpr) {
          const u = x / W;
          const v =
            0.35 * Math.sin(u * 18 + t * 1.4) +
            0.22 * Math.sin(u * 47 + t * 0.7) +
            0.08 * Math.sin(u * 113 - t * 2.1) +
            (Math.random() - 0.5) * 0.06;
          const y = H / 2 - v * (H / 2) * 0.7;
          if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
      }
      ctx.stroke();

      // ticks
      if (ticks) {
        ctx.fillStyle = "rgba(107,118,130,0.7)";
        ctx.font = `${10 * dpr}px var(--font-plex-mono), monospace`;
        for (let i = 0; i <= gridX; i += 2) {
          const x = (i / gridX) * W;
          const sec = duration ? (i / gridX) * duration : null;
          const txt = sec !== null ? `${sec.toFixed(2)}s` : `${i}`;
          ctx.fillText(txt, x + 3 * dpr, H - 4 * dpr);
        }
      }
    }

    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);

    if (!samples && animate) {
      const loop = () => {
        tRef.current += 0.016;
        draw();
        rafRef.current = requestAnimationFrame(loop);
      };
      rafRef.current = requestAnimationFrame(loop);
    }

    return () => {
      ro.disconnect();
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
    };
  }, [samples, height, color, ticks, duration, animate, highlight?.[0], highlight?.[1]]);

  return (
    <div ref={wrapRef} className={`relative w-full ${compact ? "" : ""}`}>
      <canvas ref={cvsRef} />
      {label && (
        <div className="absolute top-1.5 left-2 label-xs text-[var(--color-muted)] bg-[#06090C]/70 px-1.5 py-0.5 rounded-[1px]">
          {label}
        </div>
      )}
    </div>
  );
}
