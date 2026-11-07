// Port selection and HTTP readiness helpers. No Electron imports: scripts/dev.mjs
// uses these too.
import http from "node:http";
import net from "node:net";

export const DEFAULT_PORT = 8765;
export const PORT_RANGE_END = 8775;
export const HOST = "127.0.0.1";

function canConnect(port, host) {
  return new Promise((resolve) => {
    const socket = net.connect({ port, host });
    const done = (result) => {
      socket.destroy();
      resolve(result);
    };
    socket.setTimeout(300, () => done(false));
    socket.once("connect", () => done(true));
    socket.once("error", () => done(false));
  });
}

function canListen(port, host) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.unref();
    server.once("error", () => resolve(false));
    server.listen({ port, host, exclusive: true }, () => server.close(() => resolve(true)));
  });
}

/** True when nothing answers on host:port and the port can be bound. */
export async function portIsFree(port, host = HOST) {
  if (await canConnect(port, host)) return false;
  return canListen(port, host);
}

/**
 * Pick the HTTP port. An explicit port (RELAY_HTTP_PORT) is used as is; otherwise
 * the first free port in 8765..8775. `prefer` lets a restart keep its old port.
 */
export async function pickPort({ explicit, prefer } = {}) {
  if (explicit !== undefined && explicit !== null && explicit !== "") {
    const port = Number(explicit);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      throw new PortError("invalid", port);
    }
    if (!(await portIsFree(port))) throw new PortError("busy", port);
    return port;
  }
  const candidates = [];
  if (prefer) candidates.push(prefer);
  for (let p = DEFAULT_PORT; p <= PORT_RANGE_END; p += 1) if (p !== prefer) candidates.push(p);
  for (const port of candidates) {
    if (await portIsFree(port)) return port;
  }
  throw new PortError("exhausted", DEFAULT_PORT);
}

export class PortError extends Error {
  constructor(reason, port) {
    super(
      reason === "exhausted"
        ? `No free port in ${HOST}:${DEFAULT_PORT}-${PORT_RANGE_END}`
        : reason === "busy"
          ? `${HOST}:${port} is already in use`
          : `Invalid port: ${port}`,
    );
    this.name = "PortError";
    this.reason = reason;
    this.port = port;
  }
}

/** One GET with a short timeout; resolves {status, body} or rejects. */
export function httpGet(url, timeoutMs = 1000) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => {
      const chunks = [];
      res.on("data", (c) => chunks.push(c));
      res.on("end", () => resolve({ status: res.statusCode ?? 0, body: Buffer.concat(chunks).toString("utf8") }));
      res.on("error", reject);
    });
    req.setTimeout(timeoutMs, () => req.destroy(new Error("timeout")));
    req.on("error", reject);
  });
}

/**
 * Poll `url` until `accept({status, body})` returns true. `abortSignal` stops early
 * (e.g. when the backend process exits). Rejects with the last error on timeout.
 */
export async function waitForHttp(url, { timeoutMs = 20000, intervalMs = 150, accept, signal } = {}) {
  const ok = accept ?? ((r) => r.status >= 200 && r.status < 500);
  const deadline = Date.now() + timeoutMs;
  let last = new Error("not reachable");
  while (Date.now() < deadline) {
    if (signal?.aborted) throw signal.reason ?? new Error("aborted");
    try {
      const res = await httpGet(url);
      if (ok(res)) return res;
      last = new Error(`HTTP ${res.status}`);
    } catch (error) {
      last = error;
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  const err = new Error(`Timed out after ${Math.round(timeoutMs / 1000)} s waiting for ${url} (${last.message})`);
  err.code = "ETIMEDOUT";
  throw err;
}
