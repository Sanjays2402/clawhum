// Run with: npx tsx --test web/tests/notifyPrefs.test.ts
import { memWindow } from "./_windowShim";
import { test } from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_PREFS,
  mergePrefs,
  loadPrefs,
  savePrefs,
  loadFired,
  appendFired,
  clearFired,
  NOTIFY_PREFS_KEY,
  NOTIFY_FIRED_KEY,
} from "../lib/notifyPrefs";

const g = globalThis as any;
// Reference memWindow so the import isn't tree-shaken in CJS transpile.
void memWindow;

test("mergePrefs fills missing fields from defaults", () => {
  const out = mergePrefs({});
  assert.deepEqual(out, DEFAULT_PREFS);
});

test("mergePrefs preserves explicit booleans including false", () => {
  const out = mergePrefs({
    enabled: true,
    sound: false,
    onlyWhenHidden: false,
    kinds: { match: false, delivery: true },
  });
  assert.equal(out.enabled, true);
  assert.equal(out.sound, false);
  assert.equal(out.onlyWhenHidden, false);
  assert.equal(out.kinds.match, false);
  assert.equal(out.kinds.delivery, true);
});

test("mergePrefs heals partial kinds objects", () => {
  const out = mergePrefs({ kinds: { match: false } as any });
  assert.equal(out.kinds.match, false);
  assert.equal(out.kinds.delivery, DEFAULT_PREFS.kinds.delivery);
});

test("mergePrefs rejects garbage and falls back", () => {
  const out = mergePrefs({ enabled: "yes" as any, kinds: "bogus" as any });
  assert.equal(out.enabled, DEFAULT_PREFS.enabled);
  assert.deepEqual(out.kinds, DEFAULT_PREFS.kinds);
});

test("loadPrefs returns defaults when localStorage is empty", () => {
  g.window.localStorage.clear();
  assert.deepEqual(loadPrefs(), DEFAULT_PREFS);
});

test("savePrefs and loadPrefs round trip", () => {
  g.window.localStorage.clear();
  const p = { ...DEFAULT_PREFS, enabled: true, sound: true };
  savePrefs(p);
  assert.equal(g.window.localStorage.getItem(NOTIFY_PREFS_KEY) !== null, true);
  assert.deepEqual(loadPrefs(), p);
});

test("loadPrefs survives corrupt JSON", () => {
  g.window.localStorage.setItem(NOTIFY_PREFS_KEY, "{not json");
  assert.deepEqual(loadPrefs(), DEFAULT_PREFS);
});

test("appendFired prepends newest first and dedupes by id", () => {
  g.window.localStorage.clear();
  appendFired({ id: "a", kind: "match", title: "t1", body: "b1", at: 1, href: "/x" });
  appendFired({ id: "b", kind: "delivery", title: "t2", body: "b2", at: 2, href: "/y" });
  appendFired({ id: "a", kind: "match", title: "dup", body: "dup", at: 3, href: "/z" });
  const got = loadFired();
  assert.equal(got.length, 2);
  assert.equal(got[0].id, "b");
  assert.equal(got[1].id, "a");
  assert.equal(got[1].title, "t1"); // original wins
});

test("appendFired caps log at 50 entries", () => {
  g.window.localStorage.clear();
  for (let i = 0; i < 60; i++) {
    appendFired({ id: `e${i}`, kind: "match", title: `t${i}`, body: "", at: i, href: "/" });
  }
  const got = loadFired();
  assert.equal(got.length, 50);
  assert.equal(got[0].id, "e59"); // newest first
});

test("clearFired empties the log", () => {
  g.window.localStorage.clear();
  appendFired({ id: "a", kind: "match", title: "t", body: "", at: 1, href: "/" });
  clearFired();
  assert.equal(loadFired().length, 0);
  assert.equal(g.window.localStorage.getItem(NOTIFY_FIRED_KEY), null);
});
