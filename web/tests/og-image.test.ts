// Run: pnpm test (tsx --test tests/*.test.ts)
import { test } from "node:test";
import assert from "node:assert/strict";
import * as og from "../app/r/[id]/opengraph-image";

test("opengraph-image exports correct size", () => {
  assert.equal(og.size.width, 1200);
  assert.equal(og.size.height, 630);
});

test("opengraph-image exports png content type", () => {
  assert.equal(og.contentType, "image/png");
});

test("opengraph-image exports descriptive alt", () => {
  assert.ok(og.alt && og.alt.length > 0);
  assert.match(og.alt, /clawhum/i);
});

test("opengraph-image uses node runtime", () => {
  assert.equal(og.runtime, "nodejs");
});

test("opengraph-image default export is a function", () => {
  assert.equal(typeof og.default, "function");
});
