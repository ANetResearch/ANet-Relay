// Application menu (03 §12). Actions that belong to the interface are delivered to
// the renderer over the validated "relay:menu" IPC channel (see MENU_ACTIONS).
import { app, Menu } from "electron";

import { language, strings } from "./strings.mjs";

export const REPO_URL = "https://github.com/ANetResearch/ANet-Relay";
const DOC_URL = (file) => `${REPO_URL}/blob/main/docs/${file}`;

/** Every action the main process may send to the renderer. preload.cjs keeps the same list. */
export const MENU_ACTIONS = Object.freeze([
  "stop-all",
  "toggle-fleet",
  "toggle-inspector",
  "toggle-timeline",
  "possess",
  "settings",
]);

/**
 * @param {object} ctx
 * @param {(action: string) => void} ctx.send        deliver a menu action to the renderer
 * @param {() => void} ctx.stopAll                   shell-side stop-all; also notifies the renderer
 * @param {(url: string) => void} ctx.openExternal
 * @param {() => string | null} ctx.apiOrigin        backend origin, null before it is up
 * @param {() => void} ctx.revealLogs
 * @param {boolean} ctx.dev
 */
export function buildMenu({ send, stopAll, openExternal, apiOrigin, revealLogs, dev }) {
  const s = strings();
  const mac = process.platform === "darwin";
  const name = app.name;
  const zh = language() === "zh";

  const appMenu = {
    label: name,
    submenu: [
      { role: "about", label: s.about(name) },
      { type: "separator" },
      { label: s.settings, accelerator: "CmdOrCtrl+,", click: () => send("settings") },
      { type: "separator" },
      ...(mac
        ? [
            { role: "hide", label: s.hide(name) },
            { role: "hideOthers", label: s.hideOthers },
            { role: "unhide", label: s.showAll },
            { type: "separator" },
          ]
        : []),
      { role: "quit", label: s.quit(name), accelerator: mac ? "Cmd+Q" : "Ctrl+Q" },
    ],
  };

  const editMenu = { role: "editMenu", label: s.edit };

  const viewMenu = {
    label: s.view,
    submenu: [
      { label: s.toggleFleet, accelerator: "Alt+CmdOrCtrl+B", click: () => send("toggle-fleet") },
      { label: s.toggleInspector, accelerator: "Alt+CmdOrCtrl+I", click: () => send("toggle-inspector") },
      { label: s.toggleTimeline, accelerator: "Alt+CmdOrCtrl+T", click: () => send("toggle-timeline") },
      { type: "separator" },
      { role: "resetZoom", label: s.actualSize },
      { role: "togglefullscreen", label: s.fullScreen },
      ...(dev
        ? [
            { type: "separator" },
            { role: "reload", label: s.reload },
            // Alt+Cmd+I belongs to Toggle Inspector, so DevTools moves to F12.
            { role: "toggleDevTools", label: s.devTools, accelerator: "F12" },
          ]
        : []),
    ],
  };

  const flyMenu = {
    label: s.fly,
    submenu: [
      {
        label: s.stopAll,
        accelerator: "CmdOrCtrl+.",
        click: () => stopAll(),
      },
      { type: "separator" },
      // A bare "P" must not be captured from text fields: the renderer owns the key
      // (02 §3); the menu only displays it and forwards clicks.
      { label: s.possess, accelerator: "P", registerAccelerator: false, click: () => send("possess") },
    ],
  };

  const windowMenu = { role: "windowMenu", label: s.window };

  const helpMenu = {
    role: "help",
    label: s.help,
    submenu: [
      { label: s.agentApi, click: () => openExternal(DOC_URL(zh ? "agent-api.zh-CN.md" : "agent-api.md")) },
      {
        label: s.localApiReference,
        click: () => {
          const origin = apiOrigin();
          if (origin) openExternal(`${origin}/docs`);
        },
      },
      { label: s.hardwareGuide, click: () => openExternal(DOC_URL("hardware.zh-CN.md")) },
      { type: "separator" },
      { label: s.openLogs, click: () => revealLogs() },
      { label: s.github, click: () => openExternal(REPO_URL) },
    ],
  };

  const template = [appMenu, editMenu, viewMenu, flyMenu, windowMenu, helpMenu];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

/**
 * Right-click menu of the app windows: Cut / Copy / Paste / Select All where they apply (Electron shows no menu of
 * its own). Selected text in the Copilot transcript or a log can be copied; an input can be pasted into.
 * @param {{ isEditable: boolean, selectionText: string, editFlags: { canCut: boolean, canCopy: boolean, canPaste: boolean, canSelectAll: boolean } }} params
 * @returns {import("electron").MenuItemConstructorOptions[]} empty when there is nothing to offer
 */
export function editContextMenu(params) {
  const s = strings();
  const f = params.editFlags ?? {};
  const selected = Boolean(params.selectionText && params.selectionText.trim());
  if (params.isEditable) {
    return [
      { role: "cut", label: s.cut, enabled: Boolean(f.canCut) },
      { role: "copy", label: s.copy, enabled: Boolean(f.canCopy) },
      { role: "paste", label: s.paste, enabled: Boolean(f.canPaste) },
      { type: "separator" },
      { role: "selectAll", label: s.selectAll, enabled: f.canSelectAll !== false },
    ];
  }
  return selected ? [{ role: "copy", label: s.copy }] : [];
}

/** Shows {@link editContextMenu} on a window's right click. */
export function installEditContextMenu(webContents) {
  webContents.on("context-menu", (_event, params) => {
    const template = editContextMenu(params);
    if (template.length) Menu.buildFromTemplate(template).popup();
  });
}
