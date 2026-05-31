import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  SHARE_ID_RE,
  buildEmbedHtml,
  clampSize,
  DEFAULT_SIZE,
  parseShareUrl,
} from "../lib/oembed";

test("parseShareUrl accepts canonical /r/<id> URLs", () => {
  const r = parseShareUrl("https://clawhum.app/r/abcd1234");
  assert.ok(r);
  assert.equal(r!.id, "abcd1234");
  assert.equal(r!.origin, "https://clawhum.app");
});

test("parseShareUrl tolerates trailing slash", () => {
  const r = parseShareUrl("http://localhost:7452/r/abcd1234/");
  assert.ok(r);
  assert.equal(r!.id, "abcd1234");
});

test("parseShareUrl rejects junk", () => {
  for (const bad of [
    "",
    "not a url",
    "ftp://clawhum.app/r/abcd1234",
    "https://clawhum.app/",
    "https://clawhum.app/r/",
    "https://clawhum.app/r/abcd1234/extra",
    "https://clawhum.app/share/abcd1234",
    "https://clawhum.app/r/has spaces",
    "https://clawhum.app/r/" + "x".repeat(200),
    "javascript:alert(1)",
  ]) {
    assert.equal(parseShareUrl(bad), null, `expected null for ${bad}`);
  }
});

test("parseShareUrl enforces expectedOrigin when given", () => {
  assert.equal(
    parseShareUrl("https://other.example/r/abcd1234", "https://clawhum.app"),
    null,
  );
  const ok = parseShareUrl("https://clawhum.app/r/abcd1234", "https://clawhum.app");
  assert.ok(ok);
});

test("SHARE_ID_RE matches realistic ids and rejects bad ones", () => {
  assert.ok(SHARE_ID_RE.test("a1B2c3"));
  assert.ok(SHARE_ID_RE.test("share_id-42"));
  assert.equal(SHARE_ID_RE.test("ab"), false); // too short
  assert.equal(SHARE_ID_RE.test("x".repeat(80)), false); // too long
  assert.equal(SHARE_ID_RE.test("has space"), false);
  assert.equal(SHARE_ID_RE.test("has/slash"), false);
});

test("buildEmbedHtml emits an iframe with the right src and dims", () => {
  const html = buildEmbedHtml({
    origin: "https://clawhum.app",
    id: "abcd1234",
    width: 480,
    height: 360,
  });
  assert.match(html, /^<iframe /);
  assert.match(html, /src="https:\/\/clawhum\.app\/r\/abcd1234\/embed"/);
  assert.match(html, /width="480"/);
  assert.match(html, /height="360"/);
  assert.match(html, /loading="lazy"/);
  assert.match(html, /title="clawhum shared match abcd1234"/);
});

test("buildEmbedHtml encodes the id so funky ids cannot break out", () => {
  const html = buildEmbedHtml({
    origin: "https://clawhum.app",
    id: "a/b",
    width: 1,
    height: 1,
  });
  // The forward slash in the id must be percent encoded in the src.
  assert.match(html, /src="https:\/\/clawhum\.app\/r\/a%2Fb\/embed"/);
});

test("clampSize falls back to defaults for blank input", () => {
  const { width, height } = clampSize(null, undefined, DEFAULT_SIZE);
  assert.equal(width, DEFAULT_SIZE.defaultWidth);
  assert.equal(height, DEFAULT_SIZE.defaultHeight);
});

test("clampSize clamps within min and max", () => {
  const tiny = clampSize("10", "10", DEFAULT_SIZE);
  assert.equal(tiny.width, DEFAULT_SIZE.minWidth);
  assert.equal(tiny.height, DEFAULT_SIZE.minHeight);

  const huge = clampSize("999999", "999999", DEFAULT_SIZE);
  assert.equal(huge.width, DEFAULT_SIZE.maxWidth);
  assert.equal(huge.height, DEFAULT_SIZE.maxHeight);
});

test("clampSize rejects negative or non numeric", () => {
  const out = clampSize("-5", "abc", DEFAULT_SIZE);
  assert.equal(out.width, DEFAULT_SIZE.defaultWidth);
  assert.equal(out.height, DEFAULT_SIZE.defaultHeight);
});

test("clampSize floors fractional input", () => {
  const out = clampSize("500.7", "400.2", DEFAULT_SIZE);
  assert.equal(out.width, 500);
  assert.equal(out.height, 400);
});
