// HTTP byte ranges for app://relay/ (05 §2.2). Chromium's media stack asks for the boot films with
// "Range: bytes=..." and expects 206 Partial Content; the scheme handler reads the file (inside the asar) and
// answers here. Pure, without Electron, so it is tested with node:test (electron/test/range.test.mjs).

/**
 * Parses a single-range "bytes=" header against a body of `size` bytes.
 * Returns { start, end } (inclusive), "unsatisfiable", or null when the header is absent or not a single byte
 * range (the full body is then served with 200, as RFC 9110 allows).
 */
export function parseRange(header, size) {
  if (typeof header !== "string") return null;
  const m = /^\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$/i.exec(header);
  if (!m) return null;
  const [, a, b] = m;
  if (a === "" && b === "") return null;
  if (a === "") {
    // suffix range: the last b bytes
    const n = Number(b);
    if (n === 0 || size === 0) return "unsatisfiable";
    return { start: Math.max(0, size - n), end: size - 1 };
  }
  const start = Number(a);
  if (start >= size) return "unsatisfiable";
  const end = b === "" ? size - 1 : Math.min(Number(b), size - 1);
  if (end < start) return null;
  return { start, end };
}

/**
 * Status, headers and body for a request with `rangeHeader` over `body` (a Uint8Array). `headers` are the base
 * headers (content type, CSP, ...); Accept-Ranges is always added.
 */
export function rangeResponse(body, rangeHeader, headers = {}) {
  const size = body.byteLength;
  const out = { ...headers, "Accept-Ranges": "bytes" };
  const r = parseRange(rangeHeader, size);
  if (r === "unsatisfiable") {
    return { status: 416, headers: { ...out, "Content-Range": `bytes */${size}`, "Content-Length": "0" }, body: new Uint8Array(0) };
  }
  if (r === null) {
    return { status: 200, headers: { ...out, "Content-Length": String(size) }, body };
  }
  const slice = body.subarray(r.start, r.end + 1);
  return {
    status: 206,
    headers: { ...out, "Content-Range": `bytes ${r.start}-${r.end}/${size}`, "Content-Length": String(slice.byteLength) },
    body: slice,
  };
}
