// app://relay/ serves web/dist from inside the asar. A standard, secure scheme gives
// the renderer a stable origin (app://relay) instead of file://, so absolute asset
// paths, ES modules, fonts and fetch() behave as on a web server. Byte ranges are
// answered here (206 / 416, electron/range.mjs): the boot films (05 §2.2) are read by
// <video> with Range requests, and the file loader behind net.fetch is asked for the
// whole file.
import { net, protocol } from "electron";
import crypto from "node:crypto";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { rangeResponse } from "./range.mjs";

export const SCHEME = "app";
export const APP_HOST = "relay";
export const APP_ORIGIN = `${SCHEME}://${APP_HOST}`;
export const APP_INDEX_URL = `${APP_ORIGIN}/`;

// The renderer talks to the local backend and nothing else. Inline scripts are allowed
// only by hash (computed per document in inlineScriptHashes), never 'unsafe-inline'.
const csp = (scriptHashes = []) => [
  "default-src 'self'",
  ["script-src 'self' 'wasm-unsafe-eval'", ...scriptHashes].join(" "),
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "connect-src 'self' data: blob: http://127.0.0.1:* ws://127.0.0.1:*",
  "media-src 'self' blob: mediastream:",
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-src 'none'",
  "frame-ancestors 'none'",
].join("; ");

const INLINE_SCRIPT = /<script\b(?![^>]*\bsrc\s*=)[^>]*>([\s\S]*?)<\/script>/gi;

/** CSP source expressions for the inline <script> blocks of an HTML document. */
function inlineScriptHashes(html) {
  const hashes = [];
  for (const m of html.matchAll(INLINE_SCRIPT)) {
    if (m[1].trim() === "") continue;
    hashes.push(`'sha256-${crypto.createHash("sha256").update(m[1], "utf8").digest("base64")}'`);
  }
  return hashes;
}

/** Must run before app "ready". */
export function registerScheme() {
  protocol.registerSchemesAsPrivileged([
    {
      scheme: SCHEME,
      privileges: { standard: true, secure: true, supportFetchAPI: true, corsEnabled: true, stream: true },
    },
  ]);
}

/** Serve `root` on app://relay/. Paths without an extension fall back to index.html (SPA). */
export function handleScheme(root) {
  const base = path.resolve(root);
  protocol.handle(SCHEME, async (request) => {
    const url = new URL(request.url);
    if (url.host !== APP_HOST) return new Response("Not found", { status: 404 });
    if (request.method !== "GET" && request.method !== "HEAD") return new Response("Method not allowed", { status: 405 });
    let rel;
    try {
      rel = decodeURIComponent(url.pathname);
    } catch {
      return new Response("Bad request", { status: 400 });
    }
    let file = path.resolve(base, `.${path.posix.normalize(rel)}`);
    if (file !== base && !file.startsWith(base + path.sep)) return new Response("Forbidden", { status: 403 });
    if (rel.endsWith("/") || path.extname(file) === "") file = path.join(base, "index.html");

    const res = await net.fetch(pathToFileURL(file).toString()).catch(() => null);
    if (!res || !res.ok) return new Response("Not found", { status: 404 });
    const headers = new Headers(res.headers);
    headers.set("X-Content-Type-Options", "nosniff");
    if (!file.endsWith(".html")) {
      headers.set("Content-Security-Policy", csp());
      headers.set("Accept-Ranges", "bytes");
      const range = request.headers.get("range");
      if (!range) return new Response(request.method === "HEAD" ? null : res.body, { status: 200, headers });
      const r = rangeResponse(new Uint8Array(await res.arrayBuffer()), range);
      for (const [k, v] of Object.entries(r.headers)) headers.set(k, v);
      if (r.status === 416) headers.delete("Content-Type");
      return new Response(request.method === "HEAD" ? null : r.body, { status: r.status, headers });
    }
    const html = await res.text();
    headers.delete("Content-Length");
    headers.set("Content-Type", "text/html; charset=utf-8");
    headers.set("Content-Security-Policy", csp(inlineScriptHashes(html)));
    headers.set("Cache-Control", "no-cache");
    return new Response(html, { status: 200, headers });
  });
}
