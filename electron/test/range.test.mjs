// Byte ranges of app://relay/ (05 §2.2): the boot films are read by <video> with Range requests.
//   node --test electron/test/range.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

import { parseRange, rangeResponse } from "../range.mjs";

const body = Uint8Array.from({ length: 100 }, (_, i) => i);

test("parses single byte ranges", () => {
  assert.deepEqual(parseRange("bytes=0-", 100), { start: 0, end: 99 });
  assert.deepEqual(parseRange("bytes=10-19", 100), { start: 10, end: 19 });
  assert.deepEqual(parseRange("bytes=90-500", 100), { start: 90, end: 99 });
  assert.deepEqual(parseRange("bytes=-10", 100), { start: 90, end: 99 });
  assert.deepEqual(parseRange("bytes=-500", 100), { start: 0, end: 99 });
  assert.deepEqual(parseRange(" BYTES = 5 - 6 ", 100), { start: 5, end: 6 });
});

test("ignores what is not a single byte range", () => {
  assert.equal(parseRange(null, 100), null);
  assert.equal(parseRange(undefined, 100), null);
  assert.equal(parseRange("bytes=-", 100), null);
  assert.equal(parseRange("bytes=0-1,5-6", 100), null);
  assert.equal(parseRange("items=0-1", 100), null);
  assert.equal(parseRange("bytes=9-3", 100), null);
});

test("reports unsatisfiable ranges", () => {
  assert.equal(parseRange("bytes=100-", 100), "unsatisfiable");
  assert.equal(parseRange("bytes=-0", 100), "unsatisfiable");
  assert.equal(parseRange("bytes=0-", 0), "unsatisfiable");
});

test("answers 206 with Content-Range and the slice", () => {
  const r = rangeResponse(body, "bytes=10-19", { "Content-Type": "video/webm" });
  assert.equal(r.status, 206);
  assert.equal(r.headers["Content-Range"], "bytes 10-19/100");
  assert.equal(r.headers["Content-Length"], "10");
  assert.equal(r.headers["Accept-Ranges"], "bytes");
  assert.equal(r.headers["Content-Type"], "video/webm");
  assert.deepEqual([...r.body], [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]);
});

test("answers 200 with the whole body without a usable range", () => {
  const r = rangeResponse(body, null);
  assert.equal(r.status, 200);
  assert.equal(r.headers["Content-Length"], "100");
  assert.equal(r.headers["Accept-Ranges"], "bytes");
  assert.equal(r.body.byteLength, 100);
});

test("answers 416 past the end", () => {
  const r = rangeResponse(body, "bytes=200-");
  assert.equal(r.status, 416);
  assert.equal(r.headers["Content-Range"], "bytes */100");
  assert.equal(r.body.byteLength, 0);
});
