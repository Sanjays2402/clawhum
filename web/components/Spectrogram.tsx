"use client";

import { useEffect, useMemo, useRef } from "react";

interface Props {
  /** 2D bins[time][freq] in [0..1]. If absent, generates deterministic noise placeholder. */
  bins?: number[][] | null;
  height?: number;
  width?: number; // fixed render width; if absent fills container
  label?: string;
  showAxis?: boolean;
  /** seed string for deterministic placeholder so rows don't shuffle on rerender */
  seed?: string;
}

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function mulberry32(a: number) {
  return function () {
    let t = (a += 0x6D2B79F5);
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// magma-ish colormap: phosphor green for low, magenta for high
function colorAt(v: number): [number, number, number] {
  v = Math.max(0, Math.min(1, v));
  // 4-stop ramp: dark -> green -> amber -> magenta
  const stops = [
    [0.04, [4, 12, 8]],
    [0.35, [12, 80, 50]],
    [0.6, [61, 252, 142]],
    [0.82, [245, 158, 11]],
    [1.0, [255, 61, 190]],
  ] as const;
  for (let i = 1; i < stops.length; i++) {
    if (v <= stops[i][0]) {
      const a = stops[i - 1];
      const b = stops[i];
      const t = (v - a[0]) / (b[0] - a[0]);
      return [
        a[1][0] + (b[1][0] - a[1][0]) * t,
        a[1][1] + (b[1][1] - a[1][1]) * t,
        a[1][2] + (b[1][2] - a[1][2]) * t,
      ];
    }
  }
  return [255, 61, 190];
}

export default function Spectrogram({ bins, height = 64, width, label, showAxis, seed = "x" }: Props) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const cvsRef = useRef<HTMLCanvasElement | null>(null);

  // Generate placeholder if bins missing
  const data = useMemo<number[][]>(() => {
    if (bins && bins.length) return bins;
    const rng = mulberry32(hash(seed));
    const T = 96;
    const F = 32;
    const out: number[][] = [];
    for (let t = 0; t < T; t++) {
      const row: number[] = [];
      for (let f = 0; f < F; f++) {
        // formant-ish: stronger low/mid, decay high, plus moving harmonic
        const base = Math.max(0, 1 - f / F * 0.9);
        const harm = 0.6 * Math.exp(-Math.pow((f - (6 + 8 * Math.sin(t * 0.15))) / 3, 2));
        const noise = rng() * 0.35;
        row.push(Math.min(1, base * 0.3 + harm + noise * 0.5));
      }
      out.push(row);
    }
    return out;
  }, [bins, seed]);

  useEffect(() => {
    const wrap = wrapRef.current;
    const cvs = cvsRef.current;
    if (!wrap || !cvs) return;
    const dpr = window.devicePixelRatio || 1;

    function draw() {
      if (!cvs || !wrap) return;
      const W = width ?? wrap.clientWidth;
      const H = height;
      cvs.width = Math.floor(W * dpr);
      cvs.height = Math.floor(H * dpr);
      cvs.style.width = `${W}px`;
      cvs.style.height = `${H}px`;
      const ctx = cvs.getContext("2d");
      if (!ctx) return;

      ctx.fillStyle = "#04080A";
      ctx.fillRect(0, 0, cvs.width, cvs.height);

      const T = data.length;
      const F = data[0]?.length ?? 1;
      const cellW = cvs.width / T;
      const cellH = cvs.height / F;
      const img = ctx.createImageData(cvs.width, cvs.height);
      for (let t = 0; t < T; t++) {
        for (let f = 0; f < F; f++) {
          const v = data[t][f];
          const [r, g, b] = colorAt(v);
          const x0 = Math.floor(t * cellW);
          const x1 = Math.floor((t + 1) * cellW);
          // y-axis flipped: low freq at bottom
          const y0 = Math.floor((F - 1 - f) * cellH);
          const y1 = Math.floor((F - f) * cellH);
          for (let yy = y0; yy < y1; yy++) {
            for (let xx = x0; xx < x1; xx++) {
              const idx = (yy * cvs.width + xx) * 4;
              img.data[idx] = r;
              img.data[idx + 1] = g;
              img.data[idx + 2] = b;
              img.data[idx + 3] = 255;
            }
          }
        }
      }
      ctx.putImageData(img, 0, 0);

      if (showAxis) {
        ctx.fillStyle = "rgba(107,118,130,0.85)";
        ctx.font = `${9 * dpr}px var(--font-plex-mono), monospace`;
        const labels = ["20", "200", "1k", "4k", "12k"];
        labels.forEach((lbl, i) => {
          const y = ((labels.length - i) / labels.length) * cvs.height - 2 * dpr;
          ctx.fillText(`${lbl}Hz`, 2 * dpr, y);
        });
      }
    }

    draw();
    const ro = new ResizeObserver(draw);
    if (!width) ro.observe(wrap);
    return () => ro.disconnect();
  }, [data, height, width, showAxis]);

  return (
    <div ref={wrapRef} className="relative inline-block w-full">
      <canvas ref={cvsRef} className="block" />
      {label && (
        <div className="absolute top-0.5 right-1 label-xs text-[var(--color-muted)] bg-[#04080A]/80 px-1 rounded-[1px]">
          {label}
        </div>
      )}
    </div>
  );
}
