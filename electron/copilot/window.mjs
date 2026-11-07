// The floating Copilot window (04 §6.1): a separate frameless, always-on-top, non-modal BrowserWindow, so the
// operator can keep clicking the main window and any other application while it stays in front, and can drag it
// anywhere (including to the left of the main window). Position and size persist in userData/copilot-window.json.
// It loads the same web app with ?window=copilot; closing it only hides it.
import { app, BrowserWindow, screen } from "electron";
import fs from "node:fs";
import path from "node:path";

import { DEFAULT_HEIGHT, DEFAULT_WIDTH, fitBounds, MIN_HEIGHT, MIN_WIDTH } from "./window-bounds.mjs";

const SAVE_DELAY_MS = 400;
const FLASH_MS = 1600;
const MAC = process.platform === "darwin";

/**
 * @param {object} o
 * @param {string} o.preload absolute path of preload.cjs
 * @param {string} o.file where bounds persist (userData/copilot-window.json)
 * @param {(win: BrowserWindow) => Promise<unknown>} o.load loads the web app with ?window=copilot
 * @param {() => number} o.opacity 0.6..1
 * @param {() => boolean} o.quitting
 * @param {(visible: boolean) => void} o.onVisibility
 * @param {(win: BrowserWindow, url: string) => void} o.guard installs the navigation guards of the app windows
 */
export function createCopilotWindow({ preload, file, load, opacity, quitting, onVisibility, guard }) {
  let win = null;
  let saveTimer = null;
  let flashTimer = null;
  // The first frame is not ready right after creation: a show request waits for it (no white flash).
  let ready = false;
  let pending = null;

  const workAreas = () => screen.getAllDisplays().map((d) => d.workArea);

  const readBounds = () => {
    try {
      return JSON.parse(fs.readFileSync(file, "utf8"));
    } catch {
      return null;
    }
  };

  const saveBounds = () => {
    saveTimer = null;
    if (!win || win.isDestroyed() || win.isMinimized()) return;
    try {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.writeFileSync(`${file}.tmp`, JSON.stringify(win.getBounds()));
      fs.renameSync(`${file}.tmp`, file);
    } catch {
      // read-only profile: the window still works, it just forgets where it was
    }
  };
  const scheduleSave = () => {
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(saveBounds, SAVE_DELAY_MS);
  };

  const raise = (w) => {
    // Stay above other applications' windows, including full-screen ones.
    w.setAlwaysOnTop(true, "floating");
    w.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true, skipTransformProcessType: true });
  };

  const create = () => {
    const bounds = fitBounds(readBounds(), workAreas(), screen.getPrimaryDisplay().workArea);
    const w = new BrowserWindow({
      ...bounds,
      minWidth: MIN_WIDTH,
      minHeight: MIN_HEIGHT,
      show: false,
      frame: false,
      resizable: true,
      movable: true,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      skipTaskbar: true,
      alwaysOnTop: true,
      hasShadow: true,
      title: `${app.name} Copilot`,
      backgroundColor: "#0A0B0D",
      opacity: opacity(),
      webPreferences: {
        preload,
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
        webviewTag: false,
        spellcheck: false,
        backgroundThrottling: false,
      },
    });
    raise(w);
    guard(w);
    w.on("moved", scheduleSave);
    w.on("resized", scheduleSave);
    w.on("move", scheduleSave);
    w.on("resize", scheduleSave);
    w.on("close", (event) => {
      if (quitting()) return;
      event.preventDefault();
      w.hide();
    });
    w.on("show", () => onVisibility(true));
    w.on("hide", () => onVisibility(false));
    w.on("closed", () => {
      if (win === w) win = null;
      onVisibility(false);
    });
    ready = false;
    pending = null;
    w.once("ready-to-show", () => {
      ready = true;
      if (pending !== null) {
        const focus = pending;
        pending = null;
        if (focus) w.show();
        else w.showInactive();
      }
    });
    void load(w);
    win = w;
    return w;
  };

  const ensure = () => (win && !win.isDestroyed() ? win : create());

  const api = {
    /** The BrowserWindow, or null before the first show. */
    get window() {
      return win && !win.isDestroyed() ? win : null;
    },
    webContents() {
      const w = api.window;
      return w && !w.webContents.isDestroyed() ? w.webContents : null;
    },
    isVisible: () => Boolean(api.window?.isVisible()) || pending !== null,
    show({ focus = true } = {}) {
      const w = ensure();
      raise(w);
      if (!ready) {
        pending = focus;
        return;
      }
      if (focus) {
        w.show();
        w.focus();
      } else if (!w.isVisible()) w.showInactive();
    },
    hide() {
      pending = null;
      api.window?.hide();
    },
    toggle() {
      if (api.isVisible()) api.hide();
      else api.show();
    },
    setOpacity(value) {
      api.window?.setOpacity(value);
    },
    /** An approval is waiting: show the window without taking keyboard focus, flash its frame, bounce the Dock. */
    attention() {
      const w = ensure();
      if (!w.isVisible()) api.show({ focus: false });
      if (!w.isFocused()) {
        w.flashFrame(true);
        if (flashTimer) clearTimeout(flashTimer);
        flashTimer = setTimeout(() => {
          if (!w.isDestroyed()) w.flashFrame(false);
        }, FLASH_MS);
        if (MAC) app.dock?.bounce("informational");
      }
    },
    /** True for an IPC sender that is this window's main frame. */
    owns(event) {
      const w = api.window;
      return Boolean(w) && event.senderFrame === w.webContents.mainFrame;
    },
    destroy() {
      if (saveTimer) {
        clearTimeout(saveTimer);
        saveBounds();
      }
      if (flashTimer) clearTimeout(flashTimer);
      const w = api.window;
      if (w) {
        w.removeAllListeners("close");
        w.destroy();
      }
      win = null;
    },
  };
  return api;
}

export { DEFAULT_HEIGHT, DEFAULT_WIDTH };
