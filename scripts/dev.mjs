// Development: one operator token, the Vite dev server (web/) and Electron, which
// starts the backend from the repository virtualenv (`python -m relay`).
//   npm run dev
//
// Environment handed to the children:
//   Vite      RELAY_API_PORT, RELAY_OPERATOR_TOKEN  (browser-only development; the
//             Electron renderer gets both from window.relay instead)
//   Electron  RELAY_WEB_URL, RELAY_HTTP_PORT, RELAY_OPERATOR_TOKEN
import crypto from "node:crypto";
import fs from "node:fs";

import { pickPort, portIsFree, waitForHttp } from "../electron/ports.mjs";
import { bin, exists, fail, log, NPM, run, start, VENV_PYTHON } from "./lib.mjs";

if (!exists("web/package.json")) fail("web/package.json is missing: the interface has not been set up yet.");
if (!fs.existsSync(VENV_PYTHON)) fail("The virtualenv .venv is missing. Create it with python3.12 -m venv .venv.");
if (!exists("web/node_modules")) {
  run(NPM, exists("web/package-lock.json") ? ["ci", "--prefix", "web"] : ["install", "--prefix", "web"]);
}

const token = crypto.randomBytes(32).toString("hex");
const apiPort = await pickPort();
// The backend's CORS allowlist names this port (backend/relay/api/origins.py).
const vitePort = 5173;
if (!(await portIsFree(vitePort))) fail(`127.0.0.1:${vitePort} is in use; the dev server needs it (CORS allowlist).`);
const webUrl = `http://127.0.0.1:${vitePort}/`;

const children = [];
let exiting = false;
function shutdown(code) {
  if (exiting) return;
  exiting = true;
  for (const child of children) if (child.exitCode === null) child.kill("SIGTERM");
  setTimeout(() => process.exit(code), 2000).unref();
}
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => shutdown(0));

const vite = start(NPM, ["run", "dev", "--prefix", "web", "--", "--host", "127.0.0.1", "--port", String(vitePort), "--strictPort"], {
  env: { ...process.env, RELAY_API_PORT: String(apiPort), RELAY_OPERATOR_TOKEN: token },
});
children.push(vite);
vite.done.then((code) => {
  log(`Vite exited (${code})`);
  shutdown(code || 1);
});

try {
  await waitForHttp(webUrl, { timeoutMs: 60000 });
} catch (error) {
  log(`Vite did not come up: ${error.message}`);
  shutdown(1);
}
if (!exiting) {
  log(`web ${webUrl} · api http://127.0.0.1:${apiPort}`);
  const electron = start(bin("electron"), ["."], {
    env: { ...process.env, RELAY_WEB_URL: webUrl, RELAY_HTTP_PORT: String(apiPort), RELAY_OPERATOR_TOKEN: token },
  });
  children.push(electron);
  electron.done.then((code) => shutdown(code));
}
