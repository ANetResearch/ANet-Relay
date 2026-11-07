// Regenerate the application icons from brand/icon-1024.png (02 §19).
//   node scripts/icons.mjs [--preview /tmp/icon-preview.png]
import { ensureVenv, run } from "./lib.mjs";

const python = ensureVenv();
run(python, ["-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", "tools/icons/requirements.txt"]);
run(python, ["tools/icons/build_icons.py", ...process.argv.slice(2)]);
