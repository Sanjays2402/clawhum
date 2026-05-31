// Pure helpers for the GDPR data export + erase flow in /settings.
//
// The UI calls two endpoints behind the Next rewrite layer:
//   GET    /api/v1/privacy/export   -> JSON blob of audit + feedback rows
//   DELETE /api/v1/privacy/me       -> redacts the caller's actor id
//
// These helpers are intentionally framework free so they can be unit
// tested with tsx --test. The browser layer adds the X-API-Key header
// and turns the export payload into a download.

export interface PrivacyExport {
  actor: string;
  api_key_name: string | null;
  tenant_id: string;
  audit_event_count: number;
  audit_events: unknown[];
  feedback_row_count: number;
  feedback_rows: unknown[];
  notes?: unknown;
}

export interface EraseResult {
  actor: string;
  redacted_events: number;
  redacted_feedback_rows: number;
  ok: boolean;
}

// Confirmation token the user must type to enable the destructive button.
export const ERASE_CONFIRMATION = "ERASE";

export function isEraseConfirmed(input: string): boolean {
  return input.trim() === ERASE_CONFIRMATION;
}

// Build the filename used when saving an export. Timestamps in UTC keep
// filenames stable across timezones so the same data dumped at the same
// instant always sorts identically on disk.
export function exportFilename(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  const y = now.getUTCFullYear();
  const m = pad(now.getUTCMonth() + 1);
  const d = pad(now.getUTCDate());
  const hh = pad(now.getUTCHours());
  const mm = pad(now.getUTCMinutes());
  const ss = pad(now.getUTCSeconds());
  return `clawhum-export-${y}${m}${d}-${hh}${mm}${ss}.json`;
}

// Summarise an export payload for the UI so we never render the raw
// events array (which can be large) and never leak unknown shapes.
export interface ExportSummary {
  audit: number;
  feedback: number;
  bytes: number;
  actor: string;
  tenantId: string;
}

export function summarise(payload: PrivacyExport, raw: string): ExportSummary {
  return {
    audit: Number.isFinite(payload.audit_event_count) ? payload.audit_event_count : 0,
    feedback: Number.isFinite(payload.feedback_row_count) ? payload.feedback_row_count : 0,
    bytes: raw.length,
    actor: payload.actor || "anonymous",
    tenantId: payload.tenant_id || "default",
  };
}

// Coerce the erase endpoint response into the stable shape the UI uses.
// The backend returns slightly different field names depending on what
// was redacted; we normalise here so the React layer stays simple.
export function normaliseErase(body: Record<string, unknown>): EraseResult {
  const num = (k: string): number => {
    const v = body[k];
    return typeof v === "number" && Number.isFinite(v) ? v : 0;
  };
  return {
    actor: typeof body.actor === "string" ? body.actor : "anonymous",
    redacted_events: num("redacted_events") || num("redacted") || num("count"),
    redacted_feedback_rows: num("redacted_feedback_rows") || num("feedback_redacted"),
    ok: body.ok !== false,
  };
}
