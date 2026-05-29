"use client";
import { useEffect, useRef, useState } from "react";

interface Props { onAudio: (b: Blob) => void; loading?: boolean; }

export default function HumRecorder({ onAudio, loading }: Props) {
  const [recording, setRecording] = useState(false);
  const [hasMic, setHasMic] = useState<boolean | null>(null);
  const mediaRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const rafRef = useRef<number | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);

  useEffect(() => {
    if (typeof navigator !== "undefined" && navigator.mediaDevices) setHasMic(true);
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, []);

  function drawWave() {
    const cvs = canvasRef.current;
    const an = analyserRef.current;
    if (!cvs || !an) return;
    const ctx = cvs.getContext("2d");
    if (!ctx) return;
    const buf = new Uint8Array(an.fftSize);
    const render = () => {
      an.getByteTimeDomainData(buf);
      ctx.clearRect(0, 0, cvs.width, cvs.height);
      ctx.lineWidth = 2;
      ctx.strokeStyle = "rgba(29,185,84,.9)";
      ctx.beginPath();
      const slice = cvs.width / buf.length;
      let x = 0;
      for (let i = 0; i < buf.length; i++) {
        const v = buf[i] / 128.0;
        const y = (v * cvs.height) / 2;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        x += slice;
      }
      ctx.stroke();
      rafRef.current = requestAnimationFrame(render);
    };
    render();
  }

  async function start() {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const ctx = new AudioContext();
    const src = ctx.createMediaStreamSource(stream);
    const an = ctx.createAnalyser();
    an.fftSize = 1024;
    src.connect(an);
    ctxRef.current = ctx; analyserRef.current = an;
    drawWave();

    const rec = new MediaRecorder(stream);
    chunksRef.current = [];
    rec.ondataavailable = (e) => { if (e.data.size) chunksRef.current.push(e.data); };
    rec.onstop = () => {
      const blob = new Blob(chunksRef.current, { type: "audio/webm" });
      stream.getTracks().forEach(t => t.stop());
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      ctx.close();
      onAudio(blob);
    };
    rec.start();
    mediaRef.current = rec;
    setRecording(true);
  }

  function stop() {
    mediaRef.current?.stop();
    setRecording(false);
  }

  return (
    <div className="flex flex-col items-center gap-6">
      <button
        onClick={recording ? stop : start}
        disabled={loading || hasMic === false}
        className={`mic-ring w-32 h-32 rounded-full flex items-center justify-center text-3xl font-bold transition
          ${recording ? "bg-red-500 text-white" : "bg-[var(--accent)] text-black hover:scale-105"}`}>
        {recording ? "STOP" : "HUM"}
      </button>
      <canvas ref={canvasRef} width={640} height={80}
              className="rounded-lg bg-black/40 border border-[var(--line)]" />
      {loading && <p className="text-sm text-[var(--muted)]">Matching...</p>}
    </div>
  );
}
