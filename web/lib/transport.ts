"use client";

import { useEffect, useState } from "react";

// Lightweight pub-sub store for transport + meters. No external dep.
type TransportState = "stop" | "play" | "pause" | "rec";

interface Meters {
  peak: number;   // 0..1
  rms: number;    // 0..1
  lufs: number;   // dB, negative
}

interface Store {
  state: TransportState;
  meters: Meters;
}

type Listener = (s: Store) => void;

const store: Store = {
  state: "stop",
  meters: { peak: 0, rms: 0, lufs: -Infinity },
};

const listeners = new Set<Listener>();

function emit() {
  for (const l of listeners) l({ ...store, meters: { ...store.meters } });
}

export function setTransport(s: TransportState) {
  store.state = s;
  emit();
}

export function setMeters(m: Meters) {
  store.meters = m;
  emit();
}

export function useTransport() {
  const [snap, setSnap] = useState<Store>(() => ({ ...store, meters: { ...store.meters } }));
  useEffect(() => {
    listeners.add(setSnap);
    return () => { listeners.delete(setSnap); };
  }, []);
  return snap;
}

export type { TransportState, Meters };
