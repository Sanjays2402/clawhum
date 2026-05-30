"use client";

import { useEffect, useRef, useState } from "react";
import { setMeters, setTransport } from "@/lib/transport";
import Waveform from "./Waveform";

interface Props {
  onAudio: (b: Blob, capturedSamples: Float32Array, durationSec: number) => void;
  loading?: boolean;
  topK: number;
  threshold: number;
}

export default function CaptureSurface({ onAudio, loading, topK, threshold }: Props) {
  const [recording, setRecording] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [liveSamples, setLiveSamples] = useState<Float32Array | null>(null);
  const dragRef = useRef<HTMLDivElement | null>(null);
  const [drag, setDrag] = useState(false);

  const mediaRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const ctxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const rafRef = useRef<number | null>(null);
  const startTsRef = useRef(0);
  const capturedRef = useRef<number[]>([]);

  useEffect(() => () => cleanup(), []);

  function cleanup() {
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
    if (streamRef.current) {
      streamRef.current.getTracks().forEach(t => t.stop());
      streamRef.current = null;
    }
    if (ctxRef.current && ctxRef.current.state !== "closed") {
      ctxRef.current.close();
      ctxRef.current = null;
    }
    analyserRef.current = null;
    mediaRef.current = null;
  }

  function pumpAnalyser() {
    const an = analyserRef.current;
    if (!an) return;
    const buf = new Float32Array(an.fftSize);
    let lufsAvg = -60;
    const tick = () => {
      if (!analyserRef.current) return;
      analyserRef.current.getFloatTimeDomainData(buf);
      let peak = 0;
      let sumSq = 0;
      for (let i = 0; i < buf.length; i++) {
        const v = buf[i];
        const a = Math.abs(v);
        if (a > peak) peak = a;
        sumSq += v * v;
      }
      const rms = Math.sqrt(sumSq / buf.length);
      // crude LUFS approximation: K-weighting omitted, but visually tracks loudness
      const inst = rms > 1e-6 ? 20 * Math.log10(rms) - 0.691 : -60;
      lufsAvg = lufsAvg * 0.9 + inst * 0.1;
      setMeters({ peak, rms, lufs: lufsAvg });

      // capture a downsampled rolling sample window for visualization (max ~16k samples)
      const stride = Math.max(1, Math.floor(buf.length / 256));
      for (let i = 0; i < buf.length; i += stride) {
        capturedRef.current.push(buf[i]);
        if (capturedRef.current.length > 16000) capturedRef.current.shift();
      }
      setLiveSamples(new Float32Array(capturedRef.current));
      setElapsed((performance.now() - startTsRef.current) / 1000);
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
  }

  async function start() {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
      streamRef.current = stream;
      const ctx = new AudioContext();
      ctxRef.current = ctx;
      const src = ctx.createMediaStreamSource(stream);
      const an = ctx.createAnalyser();
      an.fftSize = 2048;
      an.smoothingTimeConstant = 0.2;
      src.connect(an);
      analyserRef.current = an;

      capturedRef.current = [];
      startTsRef.current = performance.now();
      pumpAnalyser();

      const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "audio/webm";
      const rec = new MediaRecorder(stream, { mimeType: mime });
      chunksRef.current = [];
      rec.ondataavailable = (e) => { if (e.data.size) chunksRef.current.push(e.data); };
      rec.onstop = () => {
        const blob = new Blob(chunksRef.current, { type: mime });
        const samples = new Float32Array(capturedRef.current);
        const dur = (performance.now() - startTsRef.current) / 1000;
        onAudio(blob, samples, dur);
        cleanup();
        setTransport("stop");
      };
      rec.start();
      mediaRef.current = rec;
      setRecording(true);
      setTransport("rec");
    } catch (e: any) {
      setError(e?.message || "could not access microphone");
      cleanup();
    }
  }

  function stop() {
    mediaRef.current?.stop();
    setRecording(false);
  }

  async function handleFile(f: File) {
    setError(null);
    try {
      // decode for visualization
      const arr = await f.arrayBuffer();
      const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
      const buf = await ctx.decodeAudioData(arr.slice(0));
      const ch = buf.getChannelData(0);
      // downsample for viz
      const stride = Math.max(1, Math.floor(ch.length / 4000));
      const viz = new Float32Array(Math.floor(ch.length / stride));
      for (let i = 0, j = 0; i < ch.length && j < viz.length; i += stride, j++) viz[j] = ch[i];
      ctx.close();
      onAudio(f, viz, buf.duration);
    } catch (e: any) {
      setError(`decode failed: ${e?.message || e}`);
      onAudio(f, new Float32Array(0), 0);
    }
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDrag(false);
    const f = e.dataTransfer.files?.[0];
    if (f) handleFile(f);
  }

  return (
    <div className="relative">
      <div
        ref={dragRef}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={onDrop}
        className={`panel-inset rounded-[2px] relative overflow-hidden transition
          ${drag ? "ring-1 ring-[var(--color-phosphor)]" : ""}`}
      >
        <div className="absolute inset-0 grid-bg opacity-30 pointer-events-none" />
        <div className="px-4 pt-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className={recording ? "rec-dot" : "led-dot off"} />
            <span className="label-xs">
              {recording ? "capturing" : drag ? "release to load" : "input / mic"}
            </span>
            <span className="font-mono text-[10px] text-[var(--color-dim)]">fft 2048 / hann</span>
          </div>
          <div className="font-mono text-[11px] text-[var(--color-muted)] tabular-nums">
            t {elapsed.toFixed(2)}s
          </div>
        </div>

        <div className="px-2 pb-2 pt-1">
          <Waveform
            samples={recording ? liveSamples : null}
            height={220}
            animate={!recording}
            color="#3DFC8E"
            ticks
            label={recording ? "live" : "idle / sine probe"}
          />
        </div>

        <div className="px-4 pb-4 pt-2 flex items-center gap-3 border-t border-[var(--color-line)] bg-[var(--color-panel)]">
          <button
            onClick={recording ? stop : start}
            disabled={loading}
            className={`px-5 py-2.5 rounded-[2px] font-mono text-[12px] uppercase tracking-widest flex items-center gap-2
              ${recording ? "bg-[var(--color-red)] text-white" : "btn-primary"}
              disabled:opacity-40`}
          >
            {recording ? (
              <>
                <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor"><rect x="1" y="1" width="8" height="8" /></svg>
                stop / send
              </>
            ) : (
              <>
                <span className={recording ? "rec-dot" : "led-dot"} />
                arm + record
              </>
            )}
          </button>

          <div className="text-[10px] uppercase tracking-widest text-[var(--color-dim)]">or</div>

          <label className="btn-ghost px-4 py-2 rounded-[2px] font-mono text-[12px] uppercase tracking-widest cursor-pointer">
            drop / select file
            <input
              type="file"
              accept="audio/*"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) handleFile(f);
              }}
            />
          </label>

          <div className="ml-auto flex items-center gap-3 font-mono text-[10px] text-[var(--color-muted)] uppercase tracking-widest">
            <span>top_k <span className="text-[var(--color-text)]">{topK}</span></span>
            <span>threshold <span className="text-[var(--color-text)]">{threshold.toFixed(2)}</span></span>
            {loading && <span className="text-[var(--color-phosphor)]">computing</span>}
          </div>
        </div>
      </div>

      {error && (
        <div className="mt-2 px-3 py-2 border border-[var(--color-amber)] bg-[rgba(245,158,11,0.06)] text-[var(--color-amber)] font-mono text-xs">
          err / {error}
        </div>
      )}
    </div>
  );
}
