// Run: pnpm test (tsx --test tests/*.test.ts)
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ERASE_CONFIRMATION,
  exportFilename,
  isEraseConfirmed,
  normaliseErase,
  summarise,
} from "../lib/privacy";

test("isEraseConfirmed only accepts the exact token, trimmed", () => {
  assert.equal(isEraseConfirmed(ERASE_CONFIRMATION), true);
  assert.equal(isEraseConfirmed("  ERASE  "), true);
  assert.equal(isEraseConfirmed("erase"), false);
  assert.equal(isEraseConfirmed("ERASE NOW"), false);
  assert.equal(isEraseConfirmed(""), false);
});

test("exportFilename produces a stable UTC stamped name", () => {
  const d = new Date(Date.UTC(2026, 4, 30, 9, 5, 7));
  assert.equal(exportFilename(d), "clawhum-export-20260530-090507.json");
});

test("summarise pulls counts safely even when fields are missing", () => {
  const s = summarise(
    {
      actor: "abc",
      api_key_name: null,
      tenant_id: "acme",
      audit_event_count: 12,
      audit_events: [],
      feedback_row_count: 3,
      feedback_rows: [],
    },
    "x".repeat(2048),
  );
  assert.equal(s.audit, 12);
  assert.equal(s.feedback, 3);
  assert.equal(s.bytes, 2048);
  assert.equal(s.actor, "abc");
  assert.equal(s.tenantId, "acme");

  const fallback = summarise(
    {
      actor: "",
      api_key_name: null,
      tenant_id: "",
      audit_event_count: Number.NaN as unknown as number,
      audit_events: [],
      feedback_row_count: Number.NaN as unknown as number,
      feedback_rows: [],
    },
    "",
  );
  assert.equal(fallback.audit, 0);
  assert.equal(fallback.feedback, 0);
  assert.equal(fallback.actor, "anonymous");
  assert.equal(fallback.tenantId, "default");
});

test("normaliseErase prefers canonical field names then falls back", () => {
  const canonical = normaliseErase({
    actor: "u1",
    redacted_events: 4,
    redacted_feedback_rows: 2,
    ok: true,
  });
  assert.equal(canonical.redacted_events, 4);
  assert.equal(canonical.redacted_feedback_rows, 2);
  assert.equal(canonical.ok, true);

  const legacy = normaliseErase({ actor: "u1", redacted: 7, feedback_redacted: 1 });
  assert.equal(legacy.redacted_events, 7);
  assert.equal(legacy.redacted_feedback_rows, 1);
  assert.equal(legacy.ok, true);

  const okFalse = normaliseErase({ ok: false });
  assert.equal(okFalse.ok, false);
  assert.equal(okFalse.actor, "anonymous");
});
