// Copilot host in the main process (03 §16): endpoint settings and the API key (config.mjs, 06 §9: an
// AES-256-GCM file store by default, the system keychain only when the operator opts in), the IPC surface of
// window.relay.copilot, and the agent's lifetime. The agent module (pi) loads lazily on the first prompt or
// test: from source in development, from agent.bundle.mjs (scripts/build-copilot.mjs) in a packaged build.
import { app, ipcMain, safeStorage } from "electron";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { userDataDir } from "../paths.mjs";
import { createCopilotConfig } from "./config.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));

// safeStorage is handed over lazily: config.mjs reaches it only with Settings > Copilot > keychain on.
const store = createCopilotConfig({ dir: userDataDir, keychain: () => safeStorage });

/** Window opacity setting, 60..100 (read when the floating window is created). */
export function configuredOpacity() {
  return store.opacity();
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
    const cfg = store.load();
    const base = relayBase();
    if (!cfg.baseUrl || !cfg.model) throw new Error("not_configured");
    if (!base) throw new Error("backend_unavailable");
    const scope = store.view(cfg).toolScope;
    // The stamp names the stored key without reading it, so a keychain key is decrypted only on a rebuild.
    const view = store.view(cfg);
    const stamp = JSON.stringify([cfg.api, cfg.baseUrl, cfg.model, store.keyStamp(cfg), base, language(), scope, view.thinking, view.maxTokens]);
    if (copilot && builtFor === stamp) return copilot;
    await dispose();
    const mod = await loadModule();
    const { models, model, thinking } = mod.buildModels({ ...cfg, thinking: view.thinking, maxTokens: view.maxTokens }, store.apiKey(cfg));
    relay = new mod.RelayHttp(base);
    copilot = mod.createCopilot({ relay, models, model, emit, lang: language, scope: () => store.view().toolScope, thinking });
    builtFor = stamp;
    return copilot;
  };

  const handle = (channel, fn) =>
    ipcMain.handle(channel, async (event, ...args) => {
      if (!trusted(event)) return false;
      return fn(...args);
    });

  handle("relay:copilot-config", () => store.view());

  handle("relay:copilot-save", async (input) => {
    const beforeStamp = store.keyStamp(store.load());
    const { before, cfg: next } = store.save(input);
    // A conversation survives window-only changes such as opacity; the agent is rebuilt on the next prompt otherwise.
    const agentKey = (c, stamp) => JSON.stringify([c.api, c.baseUrl, c.model, stamp, c.toolScope, c.keyStorage, c.thinking, c.maxTokens]);
    if (agentKey(before, beforeStamp) !== agentKey(next, store.keyStamp(next))) await dispose();
    const view = store.view(next);
    copilotWindow.setOpacity(view.opacity / 100);
    emit({ type: "config" }); // the floating window may be open while Settings > Copilot is edited in the main window
    return view;
  });

  handle("relay:copilot-test", async () => {
    const cfg = store.load();
    if (!cfg.baseUrl || !cfg.model) return { ok: false, error: "not_configured" };
    try {
      const mod = await loadModule();
      return await mod.testEndpoint(cfg, store.apiKey(cfg));
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
