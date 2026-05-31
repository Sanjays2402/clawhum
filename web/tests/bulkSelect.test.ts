import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  toggleId,
  toggleAll,
  pruneStale,
  summarize,
  mergeTags,
} from "../lib/bulkSelect";

describe("bulkSelect", () => {
  it("toggleId adds and removes ids", () => {
    const a = toggleId(new Set(), "x");
    assert.ok(a.has("x"));
    const b = toggleId(a, "x");
    assert.equal(b.has("x"), false);
  });

  it("toggleAll selects when none selected, deselects when all selected", () => {
    const ids = ["a", "b", "c"];
    const sel1 = toggleAll(new Set(), ids);
    assert.equal(sel1.size, 3);
    const sel2 = toggleAll(sel1, ids);
    assert.equal(sel2.size, 0);
  });

  it("toggleAll on partial selection completes the set", () => {
    const ids = ["a", "b", "c"];
    const sel = toggleAll(new Set(["a"]), ids);
    assert.deepEqual([...sel].sort(), ["a", "b", "c"]);
  });

  it("toggleAll preserves selections outside the visible page", () => {
    const ids = ["a", "b"];
    const sel = toggleAll(new Set(["a", "z"]), ids);
    // Completes the page and keeps z (off-page selection).
    assert.deepEqual([...sel].sort(), ["a", "b", "z"]);
  });

  it("pruneStale drops ids that are no longer visible", () => {
    const sel = pruneStale(new Set(["a", "b", "c"]), ["a", "c"]);
    assert.deepEqual([...sel].sort(), ["a", "c"]);
  });

  it("summarize reports counts and flags", () => {
    const ids = ["a", "b", "c"];
    assert.deepEqual(summarize(new Set(), ids), { count: 0, allSelected: false, someSelected: false });
    assert.deepEqual(summarize(new Set(["a"]), ids), { count: 1, allSelected: false, someSelected: true });
    assert.deepEqual(summarize(new Set(["a", "b", "c"]), ids), { count: 3, allSelected: true, someSelected: false });
  });

  it("summarize never marks allSelected when there are no visible ids", () => {
    assert.deepEqual(summarize(new Set(["a"]), []), { count: 1, allSelected: false, someSelected: true });
  });

  it("mergeTags lowercases, dedupes, trims, and sorts", () => {
    const out = mergeTags(["Demo", "fresh"], [" demo ", "PIANO", ""]);
    assert.deepEqual(out, ["demo", "fresh", "piano"]);
  });
});
