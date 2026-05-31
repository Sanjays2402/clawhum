// Run: pnpm test (tsx --test tests/*.test.ts)
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import "./_windowShim";

const ADMIN_SRC = readFileSync(
  new URL("../app/admin/page.tsx", import.meta.url),
  "utf8",
);
const NAV_SRC = readFileSync(
  new URL("../components/SiteNav.tsx", import.meta.url),
  "utf8",
);

test("admin console page is a client component default export", () => {
  assert.match(ADMIN_SRC, /^"use client";/);
  assert.match(ADMIN_SRC, /export default function AdminConsolePage/);
});

test("admin console reads every enterprise surface from the API", () => {
  for (const path of [
    "/api/me",
    "/api/members",
    "/api/keys",
    "/api/audit",
    "/api/usage",
    "/api/mfa/status",
    "/api/quotas",
  ]) {
    assert.ok(
      ADMIN_SRC.includes(path),
      `admin page must pull ${path} for the overview`,
    );
  }
});

test("admin console forwards the workspace api key on every read", () => {
  // authedFetcher must inject X-API-Key so reads are tenant scoped, not
  // anonymous. If a refactor drops this, cross-tenant reads become
  // possible from the dashboard and procurement review fails.
  assert.match(
    ADMIN_SRC,
    /headers\["X-API-Key"\]\s*=\s*k/,
    "admin reads must be authed with the stored api key",
  );
  assert.ok(
    ADMIN_SRC.includes("cache: \"no-store\""),
    "admin reads must bypass cache so audit / usage are live",
  );
});

test("admin console renders loading, error and empty states", () => {
  assert.match(ADMIN_SRC, /function Skeleton/);
  assert.match(ADMIN_SRC, /function ErrBlock/);
  assert.ok(
    ADMIN_SRC.includes("No members yet") &&
      ADMIN_SRC.includes("No personal access tokens") &&
      ADMIN_SRC.includes("No audit events"),
    "every list must show a real empty state",
  );
});

test("admin console warns when auth is in open / dev mode", () => {
  assert.ok(
    ADMIN_SRC.includes("dev mode") && ADMIN_SRC.includes("open mode"),
    "must surface open-auth warning so reviewers do not mistake it for prod",
  );
});

test("admin console uses no em-dashes in user-visible copy", () => {
  assert.ok(
    !ADMIN_SRC.includes("\u2014"),
    "AI tell: em-dash detected in admin console copy",
  );
});

test("site nav exposes the admin console link", () => {
  assert.match(NAV_SRC, /href:\s*"\/admin"/);
});
