"use client";

import { useEffect } from "react";
import { installApiKeyFetch } from "@/lib/apiKey";

// Mounted once at the root. Installs the X-API-Key fetch shim so every
// existing /api/* call (match, share, stats, ...) carries the user's
// stored key when one is set in Settings.
export default function ApiKeyProvider() {
  useEffect(() => {
    installApiKeyFetch();
  }, []);
  return null;
}
