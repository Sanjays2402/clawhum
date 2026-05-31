// Run with: node --test --import tsx web/tests/export.test.ts
// (or `npm run test:web` from repo root if a runner is wired up later)
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildJsonExport,
  csvField,
  flattenMatches,
  rowsToCsv,
  timestampSlug,
} from "../lib/export";
import type { StoredMatch } from "../lib/history";

const sample: StoredMatch[] = [
  {
    query_id: "q-abc-123",
    ts: 1735689600000, // 2025-01-01T00:00:00Z
    elapsed_ms: 42,
    count: 2,
    filename: "hum.wav",
    duration_sec: 3.5,
    results: [
      {
        track_id: "t1",
        title: "Hello, World",
        artist: "Test \"Quoted\" Artist",
        album: "Album\nLine",
        score: 0.875,
        segment_index: 0,
        source: "local",
        preview_url: null,
        artwork_url: null,
        tempo_bpm: 120,
      },
      {
        track_id: "t2",
        title: "Other",
        artist: "Someone",
        score: 0.5,
        segment_index: 3,
        source: "spotify",
      },
    ],
  },
  {
    query_id: "q-empty",
    ts: 1735776000000,
    elapsed_ms: 9,
    count: 0,
    results: [],
  },
];

test("csvField quotes commas, quotes, and newlines", () => {
  assert.equal(csvField("plain"), "plain");
  assert.equal(csvField("has,comma"), '"has,comma"');
  assert.equal(csvField('has "quote"'), '"has ""quote"""');
  assert.equal(csvField("line\nbreak"), '"line\nbreak"');
  assert.equal(csvField(null), "");
  assert.equal(csvField(undefined), "");
  assert.equal(csvField(0), "0");
});

test("flattenMatches emits one row per candidate and a placeholder for empty queries", () => {
  const rows = flattenMatches(sample);
  assert.equal(rows.length, 3);
  assert.equal(rows[0].query_id, "q-abc-123");
  assert.equal(rows[0].candidate_rank, 1);
  assert.equal(rows[0].title, "Hello, World");
  assert.equal(rows[0].timestamp_iso, "2025-01-01T00:00:00.000Z");
  assert.equal(rows[1].candidate_rank, 2);
  assert.equal(rows[2].query_id, "q-empty");
  assert.equal(rows[2].candidate_rank, 0);
  assert.equal(rows[2].title, "");
});

test("rowsToCsv produces a valid header and escaped rows", () => {
  const csv = rowsToCsv(flattenMatches(sample));
  const lines = csv.split("\r\n");
  assert.ok(lines[0].startsWith("query_id,timestamp_iso,"));
  // 1 header + 3 data + trailing empty
  assert.equal(lines.length, 5);
  assert.equal(lines[4], "");
  assert.match(lines[1], /"Hello, World"/);
  assert.match(lines[1], /"Test ""Quoted"" Artist"/);
  assert.match(lines[1], /"Album\nLine"/);
});

test("buildJsonExport strips waveform/pitch and stamps schema metadata", () => {
  const payload = buildJsonExport(sample);
  assert.equal(payload.schema, "clawhum.matches.export.v1");
  assert.equal(payload.count, 2);
  assert.equal(payload.matches[0].query_id, "q-abc-123");
  assert.equal(payload.matches[0].results.length, 2);
  assert.equal((payload.matches[0] as any).query_waveform, undefined);
  assert.equal((payload.matches[0] as any).query_pitch, undefined);
  assert.ok(typeof payload.exported_at === "string" && payload.exported_at.includes("T"));
});

test("timestampSlug produces a sortable compact stamp", () => {
  const d = new Date(Date.UTC(2026, 4, 30, 22, 15, 7));
  // Use the local-time formatter; just assert it is YYYYMMDD-HHMMSS shape.
  assert.match(timestampSlug(d), /^\d{8}-\d{6}$/);
});
