"use client";

import { useEffect } from "react";
import { startNotifyEngine } from "@/lib/notifyEngine";

/**
 * Mounts the notification poll loop once at the root.
 * Renders nothing.
 */
export default function NotifyEngineMount(): null {
  useEffect(() => {
    const stop = startNotifyEngine();
    return () => stop();
  }, []);
  return null;
}
