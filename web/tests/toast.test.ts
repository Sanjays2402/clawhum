// Run with: npx tsx --test web/tests/toast.test.ts
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  _reset,
  dismissToast,
  getToasts,
  showToast,
  stripEmDash,
  subscribe,
  toast,
} from "../lib/toast";

test("showToast pushes newest first and assigns unique ids", () => {
  _reset();
  const a = showToast({ title: "one" });
  const b = showToast({ title: "two", variant: "success" });
  const all = getToasts();
  assert.equal(all.length, 2);
  assert.equal(all[0].id, b.id);
  assert.equal(all[1].id, a.id);
  assert.notEqual(a.id, b.id);
  assert.equal(all[0].variant, "success");
  assert.equal(all[1].variant, "info");
});

test("dismissToast removes the matching toast and is idempotent", () => {
  _reset();
  const a = showToast({ title: "keep" });
  const b = showToast({ title: "go" });
  assert.equal(dismissToast(b.id), true);
  assert.equal(getToasts().length, 1);
  assert.equal(getToasts()[0].id, a.id);
  assert.equal(dismissToast(b.id), false);
  assert.equal(dismissToast("nonexistent"), false);
});

test("stack caps at MAX_TOASTS (5) and drops oldest", () => {
  _reset();
  const ids: string[] = [];
  for (let i = 0; i < 7; i++) ids.push(showToast({ title: `n${i}` }).id);
  const stack = getToasts();
  assert.equal(stack.length, 5);
  // Newest first; should be the last 5 ids in reverse.
  assert.deepEqual(
    stack.map((t) => t.id),
    ids.slice(-5).reverse(),
  );
});

test("subscribe receives current snapshot and updates", () => {
  _reset();
  const events: number[] = [];
  const unsub = subscribe((ts) => events.push(ts.length));
  // Initial snapshot.
  assert.deepEqual(events, [0]);
  showToast({ title: "x" });
  showToast({ title: "y" });
  assert.deepEqual(events, [0, 1, 2]);
  unsub();
  showToast({ title: "z" });
  // No new events after unsubscribe.
  assert.deepEqual(events, [0, 1, 2]);
});

test("default duration depends on variant and 0 means sticky", () => {
  _reset();
  const info = toast.info("hi");
  const err = toast.error("oops");
  const sticky = showToast({ title: "stay", durationMs: 0 });
  assert.equal(info.durationMs, 4000);
  assert.equal(err.durationMs, 8000);
  assert.equal(sticky.durationMs, 0);
});

test("stripEmDash sanitises user-visible copy", () => {
  assert.equal(stripEmDash("hello \u2014 world"), "hello ,  world");
  assert.equal(stripEmDash("range 1\u20133"), "range 1-3");
  // Toast titles go through the same path.
  _reset();
  const t = showToast({ title: "match \u2014 done", description: "ok\u2013ish" });
  assert.equal(t.title, "match ,  done");
  assert.equal(t.description, "ok-ish");
});

test("action callback runs and toast is dismissable", () => {
  _reset();
  let fired = 0;
  const t = showToast({
    title: "open?",
    action: { label: "open", onClick: () => fired++ },
  });
  // Simulate the click path the Toaster component takes.
  t.action!.onClick();
  dismissToast(t.id);
  assert.equal(fired, 1);
  assert.equal(getToasts().length, 0);
});
