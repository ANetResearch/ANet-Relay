// Run the test suites: backend (pytest), web (npm test in web/) and e2e (Playwright
// driving the Electron app; under xvfb on Linux without a display).
//   node scripts/test.mjs [backend] [web] [e2e]      (no argument: all three)
//   node scripts/test.mjs walkthrough                (02 §20 screenshots into e2e/artifacts/walkthrough)
//   node scripts/test.mjs media                      (README images into docs/media)
// A suite whose directory does not exist yet is reported as missing and fails the run.
import { spawnSync } from "node:child_process";

import { bin, ensureVenv, exists, log, NPM, ROOT, run } from "./lib.mjs";

const ALL = ["backend", "web", "e2e"];
const EXTRA = ["walkthrough", "media"];
const wanted = process.argv.slice(2).filter((a) => ALL.includes(a) || EXTRA.includes(a));
const suites = wanted.length ? wanted : ALL;

function hasXvfb() {
  return spawnSync("sh", ["-c", "command -v xvfb-run"], { stdio: "ignore" }).status === 0;
}

const SUITES = {
  backend: {
    present: () => exists("backend/tests"),
    run: () => run(ensureVenv(), ["-m", "pytest", "-q"], { cwd: `${ROOT}/backend` }),
  },
  web: {
    present: () => exists("web/package.json"),
    run: () => run(NPM, ["test", "--prefix", "web"]),
  },
  e2e: {
    present: () => exists("e2e/playwright.config.ts"),
    run: () => playwright([]),
  },
  // 02 §20 visual walkthrough: screenshots of every surface at three sizes in both languages (not part of "all")
  walkthrough: {
    present: () => exists("e2e/walkthrough.e2e.ts"),
    run: () => playwright(["walkthrough"], { RELAY_WALKTHROUGH: process.env.RELAY_WALKTHROUGH || "1" }),
  },
  // README images (03 §13): hero, relay and course at 1600 x 900 into docs/media (not part of "all")
  media: {
    present: () => exists("e2e/media.e2e.ts"),
    run: () => playwright(["media"], { RELAY_MEDIA: "1" }),
  },
};

function playwright(extra, env = {}) {
  const args = ["test", "--config", "e2e/playwright.config.ts", ...extra];
  const options = { env: { ...process.env, ...env } };
  if (process.platform === "linux" && !process.env.DISPLAY && hasXvfb()) {
    run("xvfb-run", ["-a", bin("playwright"), ...args], options);
  } else {
    run(bin("playwright"), args, options);
  }
}

const results = [];
for (const name of suites) {
  const suite = SUITES[name];
  if (!suite.present()) {
    results.push([name, "missing"]);
    continue;
  }
  try {
    suite.run();
    results.push([name, "passed"]);
  } catch {
    results.push([name, "failed"]);
  }
}

for (const [name, state] of results) log(`${name.padEnd(8)} ${state}`);
process.exit(results.every(([, state]) => state === "passed") ? 0 : 1);
