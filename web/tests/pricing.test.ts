// Run: pnpm test (tsx --test tests/*.test.ts)
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import * as page from "../app/pricing/page";

const SRC = readFileSync(
  new URL("../app/pricing/page.tsx", import.meta.url),
  "utf8",
);

test("pricing page exports default component", () => {
  assert.equal(typeof page.default, "function");
});

test("pricing page exports metadata with title and description", () => {
  assert.ok(page.metadata, "metadata must be exported");
  assert.match(String(page.metadata.title), /clawhum/i);
  assert.ok(
    typeof page.metadata.description === "string" &&
      page.metadata.description.length > 20,
    "description must be non-trivial",
  );
});

test("pricing page declares three real tiers with quotas", () => {
  for (const name of ["Free", "Studio", "Label"]) {
    assert.ok(SRC.includes(`name: "${name}"`), `tier ${name} missing`);
  }
  for (const quota of ["500", "10,000", "200,000"]) {
    assert.ok(SRC.includes(quota), `quota ${quota} missing`);
  }
});

test("pricing page wires paid CTAs to env-driven Stripe links with mailto fallback", () => {
  assert.ok(SRC.includes("NEXT_PUBLIC_STRIPE_LINK_STUDIO"));
  assert.ok(SRC.includes("NEXT_PUBLIC_STRIPE_LINK_LABEL"));
  assert.ok(
    SRC.includes("mailto:hello@clawhum.com"),
    "must provide a real fallback when Stripe link is unset",
  );
});

test("pricing page free plan CTA points at the capture page", () => {
  assert.ok(
    SRC.includes('href="/"') && SRC.includes("start humming"),
    "free plan must link to the in-product capture page",
  );
});

test("pricing page ships an accessible FAQ section", () => {
  assert.ok(SRC.includes("<details"), "FAQ should use native <details>");
  assert.ok(
    SRC.includes('aria-label="Frequently asked questions"'),
    "FAQ section must be labelled",
  );
});
