// Native copy for menus and dialogs. English by default (R-61); the renderer reports
// the interface language through window.relay.setLanguage() and the shell follows.

const en = {
  // Menus
  about: (name) => `About ${name}`,
  settings: "Settings…",
  hide: (name) => `Hide ${name}`,
  hideOthers: "Hide Others",
  showAll: "Show All",
  quit: (name) => `Quit ${name}`,
  edit: "Edit",
  view: "View",
  toggleFleet: "Toggle Fleet",
  toggleInspector: "Toggle Inspector",
  toggleTimeline: "Toggle Timeline",
  actualSize: "Actual Size",
  fullScreen: "Full Screen",
  reload: "Reload",
  devTools: "Developer Tools",
  fly: "Fly",
  stopAll: "Stop All",
  possess: "Possess or Release Selected Aircraft",
  window: "Window",
  help: "Help",
  agentApi: "Agent API",
  localApiReference: "Local API Reference",
  hardwareGuide: "Hardware Guide",
  openLogs: "Open Logs",
  github: "ANet Relay on GitHub",
  // Backend dialogs
  dialogTitle: "ANet Relay",
  backendStartFailed: "The backend did not start.",
  backendStopped: "Relay lost its backend.",
  backendStoppedDetail: "Sticks are not being sent. Aircraft follow their own failsafes.",
  portsBusy: "Ports 8765–8775 are in use. Close other Relay instances.",
  portBusy: (port) => `Port ${port} is in use. Close the program that holds it or choose another port.`,
  detail: (reason, log) => `${reason}\n\nLog file: ${log}`,
  restartBackend: "Restart Backend",
  viewLog: "View Log",
  quitButton: "Quit",
  webMissing: "The interface has not been built. Run npm run build:web and start again.",
  fatal: "ANet Relay encountered an internal error and will close.",
  bridgeTitle: (host) => `Telemetry Radio Settings · ${host}`,
};

const zh = {
  about: (name) => `关于 ${name}`,
  settings: "设置…",
  hide: (name) => `隐藏 ${name}`,
  hideOthers: "隐藏其他",
  showAll: "全部显示",
  quit: (name) => `退出 ${name}`,
  edit: "编辑",
  view: "视图",
  toggleFleet: "显示或隐藏机群栏",
  toggleInspector: "显示或隐藏检查器",
  toggleTimeline: "显示或隐藏时间线",
  actualSize: "实际大小",
  fullScreen: "全屏",
  reload: "重新加载",
  devTools: "开发者工具",
  fly: "飞行",
  stopAll: "全部急停",
  possess: "附身或释放选中飞机",
  window: "窗口",
  help: "帮助",
  agentApi: "智能体接口",
  localApiReference: "本机接口参考",
  hardwareGuide: "硬件指南",
  openLogs: "打开日志",
  github: "GitHub 上的 ANet Relay",
  dialogTitle: "ANet Relay",
  backendStartFailed: "后端未能启动。",
  backendStopped: "Relay 与后端的连接已中断。",
  backendStoppedDetail: "杆量已停止发送，飞机将按各自的失控保护处理。",
  portsBusy: "端口 8765–8775 均被占用。请关闭其他 Relay 实例。",
  portBusy: (port) => `端口 ${port} 已被占用。请关闭占用它的程序，或改用其他端口。`,
  detail: (reason, log) => `${reason}\n\n日志文件：${log}`,
  restartBackend: "重启后端",
  viewLog: "查看日志",
  quitButton: "退出",
  webMissing: "界面尚未构建。请执行 npm run build:web 后重新启动。",
  fatal: "ANet Relay 发生内部错误，即将关闭。",
  bridgeTitle: (host) => `数传设置 · ${host}`,
};

export const LANGUAGES = ["en", "zh"];

let current = "en";

export function setLanguage(lang) {
  if (!LANGUAGES.includes(lang) || lang === current) return false;
  current = lang;
  return true;
}

export const language = () => current;
export const strings = () => (current === "zh" ? zh : en);
