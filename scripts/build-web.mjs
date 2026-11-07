// Build the renderer (web/) into web/dist, installing its dependencies when needed.
import { exists, fail, log, NPM, run } from "./lib.mjs";

if (!exists("web/package.json")) fail("web/package.json is missing: the interface has not been set up yet.");

if (!exists("web/node_modules")) {
  run(NPM, exists("web/package-lock.json") ? ["ci", "--prefix", "web"] : ["install", "--prefix", "web"]);
}
run(NPM, ["run", "build", "--prefix", "web"]);

if (!exists("web/dist/index.html")) fail("web/dist/index.html was not produced.");
log("web/dist is ready");
