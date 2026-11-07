// Copilot host in the main process (03 §16): endpoint settings with the API key encrypted
// by the OS keychain (safeStorage), the IPC surface of window.relay.copilot, and the agent's
// lifetime. The agent module (pi) loads lazily on the first prompt or test: from source in
// development, from agent.bundle.mjs (scripts/build-copilot.mjs) in a packaged build.
import { app, ipcMain, safeStorage } from "electron";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { userDataDir } from "../paths.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APIS = ["openai-completions", "anthropic-messages", "openai-responses"];
const TOOL_SCOPES = ["flight", "flight+device", "all"];
const OPACITY_RANGE = [60, 100];
const DEFAULTS = { api: "openai-completions", baseUrl: "", model: "", toolScope: "all", opacity: 100 };

const configPath = () => path.join(userDataDir(), "copilot.json");

function readConfig() {
  try {
    const raw = JSON.parse(fs.readFileSync(configPath(), "utf8"));
    return { ...DEFAULTS, ...raw };
  } catch {
    return { ...DEFAULTS };
  }
}

function writeConfig(cfg) {
  fs.mkdirSync(userDataDir(), { recursive: true });
  const file = configPath();
  fs.writeFileSync(`${file}.tmp`, JSON.stringify(cfg, null, 2), { mode: 0o600 });
  fs.renameSync(`${file}.tmp`, file);
}

function decryptKey(cfg) {
  if (cfg.keyEnc) {
    try {
      return safeStorage.decryptString(Buffer.from(cfg.keyEnc, "base64"));
    } catch {
      return "";
    }
  }
  return cfg.keyPlain || "";
}

function clampOpacity(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return DEFAULTS.opacity;
  return Math.min(OPACITY_RANGE[1], Math.max(OPACITY_RANGE[0], Math.round(n)));
}

/** Window opacity setting, 60..100 (read when the floating window is created). */
export function configuredOpacity() {
  return clampOpacity(readConfig().opacity);
}

function publicView(cfg) {
  const key = decryptKey(cfg);
  return {
    api: cfg.api,
    baseUrl: cfg.baseUrl,
    model: cfg.model,
    hasKey: key.length > 0,
    keyHint: key.length >= 8 ? key.slice(-4) : null,
    secureStorage: safeStorage.isEncryptionAvailable(),
    configured: Boolean(cfg.baseUrl && cfg.model),
    toolScope: TOOL_SCOPES.includes(cfg.toolScope) ? cfg.toolScope : DEFAULTS.toolScope,
    opacity: clampOpacity(cfg.opacity),
  };
}

function validBaseUrl(url) {
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:";
  } catch {
    return false;
  }
}

function clean(input, current) {
  const api = APIS.includes(input?.api) ? input.api : current.api;
  const baseUrl = typeof input?.baseUrl === "string" ? input.baseUrl.trim().replace(/\/+$/, "") : current.baseUrl;
  const model = typeof input?.model === "string" ? input.model.trim() : current.model;
  if (baseUrl && !validBaseUrl(baseUrl)) throw new Error("The endpoint must be an http(s) URL.");
  if (baseUrl.length > 512 || model.length > 200) throw new Error("The endpoint or model name is too long.");
  const toolScope = TOOL_SCOPES.includes(input?.toolScope) ? input.toolScope : TOOL_SCOPES.includes(current.toolScope) ? current.toolScope : DEFAULTS.toolScope;
  const opacity = input?.opacity === undefined ? clampOpacity(current.opacity) : clampOpacity(input.opacity);
  return { api, baseUrl, model, toolScope, opacity };
}

/**
 * @param {object} o
 * @param {ReturnType<import("./window.mjs").createCopilotWindow>} o.copilotWindow the floating window (04 §6.1)
 * @param {() => import("electron").BrowserWindow | null} o.mainWindow
 * @param {() => string | null} o.relayBase backend origin, e.g. http://127.0.0.1:8765
 * @param {() => string} o.language
 * @param {(event: any) => boolean} o.trusted a page of the app (main or Copilot window)
 * @param {() => void} o.openSettings show Settings > Copilot in the main window
 */
export function createCopilotHost({ copilotWindow, mainWindow, relayBase, language, trusted, openSettings }) {
  let modPromise = null;
  let copilot = null;
  let relay = null;
  let builtFor = null;
  let busy = false;

  const loadModule = () => {
    if (!modPromise) {
      const bundle = path.join(HERE, "agent.bundle.mjs");
      const target = app.isPackaged || !fs.existsSync(path.join(HERE, "agent.mjs")) ? bundle : path.join(HERE, "agent.mjs");
      modPromise = import(pathToFileURL(target).href);
    }
    return modPromise;
  };

  const emit = (event) => {
    if (event.type === "busy") busy = event.busy;
    // The conversation lives in the floating window; the main window only learns whether Copilot is busy.
    const wc = copilotWindow.webContents();
    if (wc) wc.send("relay:copilot", event);
    const main = mainWindow()?.webContents;
    if (event.type === "busy" && main && !main.isDestroyed()) main.send("relay:copilot", event);
    if (event.type === "approval") copilotWindow.attention();
  };

  const dispose = async () => {
    const old = { copilot, relay };
    copilot = null;
    relay = null;
    builtFor = null;
    old.copilot?.abort();
    await old.relay?.close().catch(() => {});
  };

  const ensure = async () => {
    const cfg = readConfig();
    const base = relayBase();
    if (!cfg.baseUrl || !cfg.model) throw new Error("not_configured");
    if (!base) throw new Error("backend_unavailable");
    const key = decryptKey(cfg);
    const scope = publicView(cfg).toolScope;
    const stamp = JSON.stringify([cfg.api, cfg.baseUrl, cfg.model, key.length, key.slice(-6), base, language(), scope]);
    if (copilot && builtFor === stamp) return copilot;
    await dispose();
    const mod = await loadModule();
    const { models, model } = mod.buildModels(cfg, key);
    relay = new mod.RelayHttp(base);
    copilot = mod.createCopilot({ relay, models, model, emit, lang: language, scope: () => publicView(readConfig()).toolScope });
    builtFor = stamp;
    return copilot;
  };

  const handle = (channel, fn) =>
    ipcMain.handle(channel, async (event, ...args) => {
      if (!trusted(event)) return false;
      return fn(...args);
    });

  handle("relay:copilot-config", () => publicView(readConfig()));

  handle("relay:copilot-save", async (input) => {
    const current = readConfig();
    const next = { ...current, ...clean(input, current) };
    if (input && Object.hasOwn(input, "apiKey")) {
      delete next.keyEnc;
      delete next.keyPlain;
      const key = typeof input.apiKey === "string" ? input.apiKey.trim() : "";
      if (key) {
        if (key.length > 4096) throw new Error("The API key is too long.");
        if (safeStorage.isEncryptionAvailable()) next.keyEnc = safeStorage.encryptString(key).toString("base64");
        else next.keyPlain = key; // no OS keychain (some Linux desktops): file mode 0600 only
      }
    }
    writeConfig(next);
    // A conversation survives window-only changes such as opacity; the agent is rebuilt on the next prompt otherwise.
    const agentKey = (c) => JSON.stringify([c.api, c.baseUrl, c.model, decryptKey(c), c.toolScope]);
    if (agentKey(current) !== agentKey(next)) await dispose();
    copilotWindow.setOpacity(clampOpacity(next.opacity) / 100);
    return publicView(next);
  });

  handle("relay:copilot-test", async () => {
    const cfg = readConfig();
    if (!cfg.baseUrl || !cfg.model) return { ok: false, error: "not_configured" };
    try {
      const mod = await loadModule();
      return await mod.testEndpoint(cfg, decryptKey(cfg));
    } catch (err) {
      return { ok: false, error: String(err?.message || err).slice(0, 400) };
    }
  });

  handle("relay:copilot-prompt", async (payload) => {
    const text = typeof payload?.text === "string" ? payload.text.trim().slice(0, 8000) : "";
    const vehicle = typeof payload?.vehicle === "string" ? payload.vehicle.slice(0, 80) : null;
    if (!text) return { ok: false, error: "empty" };
    if (busy) return { ok: false, error: "busy" };
    let agent;
    try {
      agent = await ensure();
    } catch (err) {
      return { ok: false, error: String(err?.message || err) };
    }
    busy = true;
    agent.prompt(text, { vehicle }).catch((err) => {
      emit({ type: "error", error: String(err?.message || err).slice(0, 400) });
      emit({ type: "busy", busy: false });
    });
    return { ok: true };
  });

  handle("relay:copilot-abort", () => {
    copilot?.abort();
    return true;
  });
  handle("relay:copilot-reset", () => {
    copilot?.reset();
    return true;
  });
  handle("relay:copilot-approve", (payload) => Boolean(copilot?.approve(String(payload?.id ?? ""), Boolean(payload?.ok))));
  handle("relay:copilot-state", () => ({ busy }));

  // Floating window (04 §6.1)
  const windowState = () => ({ visible: copilotWindow.isVisible() });
  handle("relay:copilot-window", (action) => {
    if (action === "show") copilotWindow.show();
    else if (action === "hide") copilotWindow.hide();
    else if (action === "toggle") copilotWindow.toggle();
    else return windowState();
    return { visible: action === "show" ? true : action === "hide" ? false : copilotWindow.isVisible() };
  });
  handle("relay:copilot-window-state", () => windowState());
  handle("relay:copilot-open-settings", () => {
    openSettings();
    return true;
  });

  return { dispose };
}
