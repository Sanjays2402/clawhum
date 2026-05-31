// Run: pnpm test (tsx --test tests/*.test.ts)
import { test } from "node:test";
import assert from "node:assert/strict";
import * as og from "../app/c/[id]/opengraph-image";

test("collection opengraph-image exports correct size", () => {
  assert.equal(og.size.width, 1200);
  assert.equal(og.size.height, 630);
});

test("collection opengraph-image exports png content type", () => {
  assert.equal(og.contentType, "image/png");
});

test("collection opengraph-image exports descriptive alt", () => {
  assert.ok(og.alt && og.alt.length > 0);
  assert.match(og.alt, /clawhum/i);
  assert.match(og.alt, /collection/i);
});

test("collection opengraph-image uses node runtime", () => {
  assert.equal(og.runtime, "nodejs");
});

test("collection opengraph-image default export is a function", () => {
  assert.equal(typeof og.default, "function");
});

test("fetchCollectionForOg returns null on non-200", async () => {
  const fakeFetch = (async () =>
    new Response("nope", { status: 404 })) as unknown as typeof fetch;
  const result = await og.fetchCollectionForOg("nope", fakeFetch);
  assert.equal(result, null);
});

test("fetchCollectionForOg returns null on network error", async () => {
  const fakeFetch = (async () => {
    throw new Error("boom");
  }) as unknown as typeof fetch;
  const result = await og.fetchCollectionForOg("err", fakeFetch);
  assert.equal(result, null);
});

test("fetchCollectionForOg parses a successful response", async () => {
  const payload = {
    id: "abc",
    created_at: 1,
    updated_at: 1,
    title: "demo",
    note: null,
    items: [],
  };
  const fakeFetch = (async () =>
    new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "content-type": "application/json" },
    })) as unknown as typeof fetch;
  const result = await og.fetchCollectionForOg("abc", fakeFetch);
  assert.ok(result);
  assert.equal(result?.id, "abc");
  assert.equal(result?.title, "demo");
});

test("fetchCollectionForOg url-encodes the id", async () => {
  let captured = "";
  const fakeFetch = (async (input: string) => {
    captured = String(input);
    return new Response("{}", { status: 200 });
  }) as unknown as typeof fetch;
  await og.fetchCollectionForOg("a/b c", fakeFetch);
  assert.match(captured, /\/collections\/a%2Fb%20c$/);
});
