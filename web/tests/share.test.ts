import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  historyToShareInput,
  isShareable,
  normaliseResult,
  toShareInput,
} from "../lib/share";
import type { StoredMatch } from "../lib/history";

const baseResult = {
  track_id: "trk_1",
  title: "First Light",
  artist: "Aurora",
  album: "Dawn",
  score: 0.92,
  segment_index: 3,
  preview_url: null,
  artwork_url: null,
  source: "library",
  tempo_bpm: 96,
};

test("toShareInput strips waveform and pitch from StoredMatch", () => {
  const m: StoredMatch = {
    query_id: "q_abc",
    ts: 0,
    elapsed_ms: 412,
    count: 1,
    filename: "hum.wav",
    duration_sec: 4.2,
    query_waveform: new Array(2048).fill(0),
    query_pitch: {
      sr: 16000,
      duration_sec: 4.2,
      hop_sec: 0.01,
      times: [0],
      hz: [220],
      midi: [57],
      voiced_ratio: 1,
      median_hz: 220,
    },
    results: [baseResult],
  };
  const out = toShareInput(m);
  assert.equal(out.query_id, "q_abc");
  assert.equal(out.elapsed_ms, 412);
  assert.equal(out.count, 1);
  assert.equal(out.filename, "hum.wav");
  assert.equal(out.duration_sec, 4.2);
  assert.equal(out.results.length, 1);
  assert.equal(out.results[0].track_id, "trk_1");
  // Compact payload must not leak heavy fields the share endpoint will reject.
  assert.equal((out as any).query_waveform, undefined);
  assert.equal((out as any).query_pitch, undefined);
});

test("historyToShareInput threads name through as the share note", () => {
  const it = {
    query_id: "q_xyz",
    elapsed_ms: 200,
    count: 2,
    results: [baseResult, { ...baseResult, track_id: "trk_2", score: 0.4 }],
    filename: null,
    duration_sec: 3.1,
  };
  const out = historyToShareInput(it, "Tuesday hum");
  assert.equal(out.note, "Tuesday hum");
  assert.equal(out.results.length, 2);
  const empty = historyToShareInput(it);
  assert.equal(empty.note, null);
});

test("normaliseResult fills safe defaults for partial rows", () => {
  const r = normaliseResult({ track_id: "trk_9", title: "x", score: 0.1 } as any);
  assert.equal(r.preview_url, null);
  assert.equal(r.artwork_url, null);
  assert.equal(r.tempo_bpm, null);
  assert.equal(r.artist, undefined);
});

test("isShareable rejects empty, oversized, and idless payloads", () => {
  assert.equal(isShareable(null), false);
  assert.equal(isShareable(undefined), false);
  assert.equal(
    isShareable({
      query_id: "",
      elapsed_ms: 1,
      count: 1,
      results: [baseResult],
      filename: null,
      duration_sec: null,
    }),
    false,
  );
  assert.equal(
    isShareable({
      query_id: "q",
      elapsed_ms: 1,
      count: 0,
      results: [],
      filename: null,
      duration_sec: null,
    }),
    false,
  );
  const big = {
    query_id: "q",
    elapsed_ms: 1,
    count: 51,
    results: Array.from({ length: 51 }, () => baseResult),
    filename: null,
    duration_sec: null,
  };
  assert.equal(isShareable(big), false);
  const ok = {
    query_id: "q",
    elapsed_ms: 1,
    count: 1,
    results: [baseResult],
    filename: null,
    duration_sec: null,
  };
  assert.equal(isShareable(ok), true);
});
