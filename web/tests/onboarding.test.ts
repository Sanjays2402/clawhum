// Run: pnpm test (tsx --test tests/*.test.ts)
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  completedCount,
  emptyState,
  isComplete,
  normalise,
  STEP_ORDER,
  withDismissed,
  withHidden,
  withStep,
} from "../lib/onboarding";

test("emptyState has no completed steps and is not dismissed", () => {
  const s = emptyState();
  assert.equal(s.dismissed, false);
  assert.equal(s.hidden, false);
  assert.equal(completedCount(s), 0);
  assert.equal(isComplete(s), false);
  for (const id of STEP_ORDER) assert.equal(s.steps[id], false);
});

test("withStep flips a single step without touching others", () => {
  const a = emptyState();
  const b = withStep(a, "tried");
  assert.equal(b.steps.tried, true);
  assert.equal(b.steps.viewed, false);
  assert.equal(b.steps.saved, false);
  assert.equal(completedCount(b), 1);
  // pure: a unchanged
  assert.equal(a.steps.tried, false);
});

test("withStep returns same reference when value is unchanged", () => {
  const a = withStep(emptyState(), "tried", true);
  const b = withStep(a, "tried", true);
  assert.strictEqual(a, b);
});

test("isComplete only true when all three steps are set", () => {
  let s = emptyState();
  for (const id of STEP_ORDER) {
    assert.equal(isComplete(s), false);
    s = withStep(s, id);
  }
  assert.equal(isComplete(s), true);
  assert.equal(completedCount(s), 3);
});

test("withDismissed and withHidden are independent flags", () => {
  const s = withDismissed(withHidden(emptyState(), true), true);
  assert.equal(s.dismissed, true);
  assert.equal(s.hidden, true);
  // Steps untouched.
  assert.equal(completedCount(s), 0);
});

test("normalise rejects bad input and accepts partial shapes", () => {
  assert.deepEqual(normalise(null), emptyState());
  assert.deepEqual(normalise("garbage"), emptyState());
  assert.deepEqual(normalise({}), emptyState());
  const partial = normalise({ dismissed: true, steps: { tried: true, bogus: 1 } });
  assert.equal(partial.dismissed, true);
  assert.equal(partial.hidden, false);
  assert.equal(partial.steps.tried, true);
  assert.equal(partial.steps.viewed, false);
  assert.equal(partial.steps.saved, false);
});

test("normalise coerces truthy non-booleans", () => {
  const s = normalise({ dismissed: 1, hidden: "yes", steps: { tried: 1, viewed: "ok", saved: null } });
  assert.equal(s.dismissed, true);
  assert.equal(s.hidden, true);
  assert.equal(s.steps.tried, true);
  assert.equal(s.steps.viewed, true);
  assert.equal(s.steps.saved, false);
});
