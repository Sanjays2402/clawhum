// Run: pnpm test (tsx --test tests/*.test.ts)
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  allTags,
  filterMatches,
  normaliseTags,
  sortMatches,
} from "../lib/history";
import type { StoredMatch } from "../lib/history";

function mk(
  id: string,
  ts: number,
  bestScore: number,
  opts: Partial<StoredMatch> & { title?: string; artist?: string } = {},
): StoredMatch {
  return {
    query_id: id,
    ts,
    elapsed_ms: opts.elapsed_ms ?? 100,
    count: 1,
    filename: opts.filename,
    duration_sec: opts.duration_sec,
    name: opts.name,
    tags: opts.tags,
    results: [
      {
        track_id: `t-${id}`,
        title: opts.title ?? `Song ${id}`,
        artist: opts.artist ?? "Artist",
        album: "",
        score: bestScore,
        segment_index: 0,
        source: "local",
      } as never,
    ],
  };
}

const NOW = 1_750_000_000_000;
const HOUR = 3600_000;
const sample: StoredMatch[] = [
  mk("alpha", NOW - 1 * HOUR, 0.91, { tags: ["jazz", "demo"], name: "morning hum" }),
  mk("beta", NOW - 25 * HOUR, 0.42, { tags: ["jazz"], title: "Take Five", artist: "Brubeck" }),
  mk("gamma", NOW - 10 * 24 * HOUR, 0.15, { tags: ["rock"] }),
  mk("delta", NOW - 2 * HOUR, 0.77, { filename: "snippet.wav" }),
];

test("normaliseTags lowercases, trims, dedupes, drops empties, hyphenates spaces", () => {
  assert.deepEqual(
    normaliseTags(["Jazz", "jazz", "  Indie Rock ", "", null, undefined, "DEMO"]),
    ["jazz", "indie-rock", "demo"],
  );
});

test("allTags counts by frequency desc", () => {
  const t = allTags(sample);
  assert.equal(t.find(x => x.tag === "jazz")?.count, 2);
  assert.equal(t[0].tag, "jazz");
});

test("filterMatches: free-text matches id, name, filename, track, artist, tag", () => {
  assert.equal(filterMatches(sample, { q: "morning" }).length, 1);
  assert.equal(filterMatches(sample, { q: "BRUBECK" }).length, 1);
  assert.equal(filterMatches(sample, { q: "snippet" }).length, 1);
  assert.equal(filterMatches(sample, { q: "rock" }).length, 1);
  assert.equal(filterMatches(sample, { q: "alph" }).length, 1);
  assert.equal(filterMatches(sample, { q: "nothing-here" }).length, 0);
});

test("filterMatches: tag ANY-of filter", () => {
  const out = filterMatches(sample, { tags: ["jazz"] });
  assert.deepEqual(out.map(m => m.query_id).sort(), ["alpha", "beta"]);
});

test("filterMatches: minScore inclusive", () => {
  const out = filterMatches(sample, { minScore: 0.5 }).map(m => m.query_id).sort();
  assert.deepEqual(out, ["alpha", "delta"]);
});

test("filterMatches: since/until date window", () => {
  const since = NOW - 5 * HOUR;
  const out = filterMatches(sample, { since }).map(m => m.query_id).sort();
  assert.deepEqual(out, ["alpha", "delta"]);
});

test("sortMatches: score_desc puts best first", () => {
  const out = sortMatches(sample, "score_desc").map(m => m.query_id);
  assert.deepEqual(out, ["alpha", "delta", "beta", "gamma"]);
});

test("sortMatches: ts_asc oldest first", () => {
  const out = sortMatches(sample, "ts_asc").map(m => m.query_id);
  assert.deepEqual(out, ["gamma", "beta", "delta", "alpha"]);
});

test("sortMatches: latency_asc fastest first", () => {
  const items = [
    mk("a", NOW, 0.5, { elapsed_ms: 200 } as never),
    mk("b", NOW, 0.5, { elapsed_ms: 50 } as never),
    mk("c", NOW, 0.5, { elapsed_ms: 120 } as never),
  ];
  assert.deepEqual(sortMatches(items, "latency_asc").map(m => m.query_id), ["b", "c", "a"]);
});

test("filterMatches: combined filters apply together", () => {
  const out = filterMatches(sample, {
    tags: ["jazz"],
    minScore: 0.5,
    since: NOW - 5 * HOUR,
  }).map(m => m.query_id);
  assert.deepEqual(out, ["alpha"]);
});
