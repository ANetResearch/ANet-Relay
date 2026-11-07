// Bundle the Copilot agent (electron/copilot/agent.mjs and the pi libraries it imports) into one ESM file for
// the packaged app, so pi and its provider SDKs stay devDependencies (03 §16). Licence comments of the bundled
// packages go to agent.bundle.mjs.LEGAL.txt; the licences of every bundled package to agent.bundle.LICENSES.txt.
import fs from "node:fs";
import path from "node:path";

import { build } from "esbuild";

import { log, ROOT } from "./lib.mjs";

const result = await build({
  absWorkingDir: ROOT,
  entryPoints: ["electron/copilot/agent.mjs"],
  outfile: "electron/copilot/agent.bundle.mjs",
  bundle: true,
  platform: "node",
  format: "esm",
  target: "node22",
  minify: true,
  legalComments: "external",
  // Some provider SDKs call require() for Node built-ins.
  banner: { js: 'import { createRequire as __relayRequire } from "node:module"; const require = __relayRequire(import.meta.url);' },
  logLevel: "warning",
  metafile: true,
});

// One licence text per bundled npm package (MIT / Apache-2.0 notices travel with the code).
const packages = new Map();
for (const input of Object.keys(result.metafile.inputs)) {
  const m = /node_modules\/((?:@[^/]+\/)?[^/]+)\//.exec(input);
  if (m) packages.set(m[1], path.join(ROOT, input.slice(0, input.indexOf(m[1]) + m[1].length)));
}
const sections = [];
for (const [name, dir] of [...packages].sort()) {
  const pkg = JSON.parse(fs.readFileSync(path.join(dir, "package.json"), "utf8"));
  const file = fs.readdirSync(dir).find((f) => /^(licen[cs]e|copying)(\.|$)/i.test(f));
  const text = file ? fs.readFileSync(path.join(dir, file), "utf8").trim() : `License: ${pkg.license ?? "see package"}`;
  sections.push(`${name}@${pkg.version} (${pkg.license ?? "unknown"})\n\n${text}`);
}
fs.writeFileSync(
  path.join(ROOT, "electron/copilot/agent.bundle.LICENSES.txt"),
  `Third-party packages bundled into agent.bundle.mjs\n\n${sections.join(`\n\n${"-".repeat(72)}\n\n`)}\n`,
);
log("electron/copilot/agent.bundle.mjs is ready");
