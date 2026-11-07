"""Application-level interface: the Relay window as seen and driven by agents (04 §3.2)."""

from __future__ import annotations

import platform
import sys
from typing import Any

from fastapi import APIRouter, Request

from ..app_bridge import AppBridge
from ..config import VERSION
from ..errors import err
from . import schemas
from .deps import caller, service
from .schemas_app import UiCommandBody

router = APIRouter()
ERRORS = {s: {"model": schemas.ErrorBody} for s in (400, 401, 403, 409, 503, 504)}


def bridge(request: Request) -> AppBridge:
    return request.app.state.app_bridge


def _arg(
    name: str, type_: str, en: str, zh: str, required: bool = False, enum: list[str] | None = None
) -> dict:
    out: dict[str, Any] = {
        "name": name,
        "type": type_,
        "required": required,
        "description_en": en,
        "description_zh": zh,
    }
    if enum:
        out["enum"] = enum
    return out


def _action(name: str, en: str, zh: str, *args: dict) -> dict[str, Any]:
    return {"name": name, "description_en": en, "description_zh": zh, "args": list(args)}


_VIEWS = ["overview", "chase", "top"]
_INPUTS = ["keyboard", "screen", "gamepad"]

ACTIONS: list[dict[str, Any]] = [
    _action("get_state", "Return the current window state.", "返回窗口当前状态。"),
    _action(
        "set_language",
        "Switch the interface language (the native menu follows).",
        "切换界面语言（原生菜单同步）。",
        _arg("lang", "string", "Language.", "语言。", True, ["en", "zh"]),
    ),
    _action(
        "set_workspace",
        "Switch the workspace.",
        "切换工作区。",
        _arg("workspace", "string", "Workspace.", "工作区。", True, ["fly", "agents", "records"]),
    ),
    _action(
        "select_vehicle",
        "Select an aircraft, or clear the selection.",
        "选中飞机，或清除选中。",
        _arg("vehicle", "string|null", "Aircraft id; null clears.", "飞机编号；null 表示清除。", True),
    ),
    _action(
        "set_view",
        "Set the stage camera.",
        "设置舞台视角。",
        _arg("view", "string", "Camera.", "视角。", True, _VIEWS),
    ),
    _action(
        "open_inspector",
        "Open the inspector.",
        "打开检查器。",
        _arg("tab", "string", "Tab to show.", "要显示的页签。"),
    ),
    _action("close_inspector", "Close the inspector.", "关闭检查器。"),
    _action(
        "open_settings",
        "Open the settings dialog.",
        "打开设置。",
        _arg("section", "string", "Section to show.", "要显示的分区。"),
    ),
    _action(
        "open_connect",
        "Open the connect dialog.",
        "打开接入对话框。",
        _arg("tab", "string", "Tab to show.", "要显示的页签。", False, ["scan", "address", "bridge"]),
    ),
    _action("close_dialogs", "Close every dialog and panel.", "关闭所有对话框与面板。"),
    _action(
        "set_pref",
        "Change an interface preference (units, start view, pad dead zone, expo, chase on course).",
        "修改界面偏好（单位、起始视角、手柄死区、Expo、穿环时自动追尾）。",
        _arg("key", "string", "Preference key.", "偏好名。", True),
        _arg("value", "any", "New value.", "新值。", True),
    ),
    _action(
        "notify",
        "Show a notice in the window.",
        "在窗口中显示提示。",
        _arg("text", "string", "Notice text.", "提示内容。", True),
        _arg("level", "string", "Notice level.", "提示级别。", False, ["info", "warn"]),
    ),
    _action(
        "bind_gamepad",
        "Bind a controller to an aircraft.",
        "将手柄与飞机绑定。",
        _arg("index", "integer", "Controller index.", "手柄序号。", True),
        _arg("vehicle", "string", "Aircraft id.", "飞机编号。"),
    ),
    _action(
        "unbind_gamepad",
        "Unbind a controller.",
        "解除手柄绑定。",
        _arg("index", "integer", "Controller index.", "手柄序号。", True),
    ),
    _action(
        "possess",
        "Possess an aircraft as the operator.",
        "以操作员身份附身飞机。",
        _arg("vehicle", "string", "Aircraft id.", "飞机编号。", True),
        _arg("input", "string", "Input source.", "输入源。", False, _INPUTS),
        _arg("pad", "integer", "Controller index for gamepad input.", "手柄输入时的手柄序号。"),
    ),
    _action(
        "release",
        "Release an aircraft the operator possesses.",
        "释放操作员附身的飞机。",
        _arg("vehicle", "string", "Aircraft id.", "飞机编号。", True),
    ),
    _action(
        "open_record",
        "Open a flight record for review.",
        "打开飞行记录回看。",
        _arg("session_id", "integer", "Record session id.", "记录编号。", True),
    ),
    _action(
        "replay",
        "Control record playback.",
        "控制记录回看。",
        _arg("action", "string", "Playback action.", "回看操作。", True, ["play", "pause", "seek"]),
        _arg("t", "number", "Seconds from the start, for seek.", "跳转到的秒数（seek 时）。"),
    ),
    _action(
        "toggle_copilot",
        "Show or hide the Copilot window.",
        "显示或隐藏 Copilot 窗口。",
        _arg("open", "boolean", "Force shown or hidden; omit to toggle.", "指定显示或隐藏；省略则切换。"),
    ),
    _action(
        "window",
        "Control the main window.",
        "控制主窗口。",
        _arg(
            "action",
            "string",
            "Window action.",
            "窗口操作。",
            True,
            ["focus", "minimize", "maximize", "restore", "fullscreen"],
        ),
    ),
    _action(
        "screenshot",
        "Capture the main window as PNG (base64).",
        "截取主窗口为 PNG（base64）。",
        _arg(
            "max_width",
            "integer",
            "Maximum width in pixels, at most 1920.",
            "最大宽度（像素），不超过 1920。",
        ),
    ),
]


@router.get("/app", tags=["app"])
def app_info(request: Request) -> dict[str, Any]:
    """The application: version, listen port, counts and what the window shows."""
    svc = service(request)
    snapshot = svc.fleet()
    vehicles = snapshot.get("vehicles", [])
    ui = bridge(request).view()
    pads = (ui["state"] or {}).get("gamepads") or []
    return {
        "version": VERSION,
        "uptime_s": round(svc.clock() - svc.started, 1),
        "platform": {"os": sys.platform, "arch": platform.machine(), "python": platform.python_version()},
        "http_port": svc.http_port,
        "listen": snapshot.get("listen"),
        "scan": snapshot.get("scan"),
        "counts": {
            "vehicles": len(vehicles),
            "real": sum(1 for v in vehicles if v.get("kind") == "real"),
            "sims": sum(1 for v in vehicles if v.get("kind") == "sim"),
            "sessions": len(snapshot.get("sessions", [])),
            "gamepads": sum(1 for p in pads if p.get("connected", True)),
            "alerts": len(snapshot.get("alerts", [])),
        },
        "ui": ui,
        "agents_app_control": svc.settings.agents_app_control,
    }


@router.get("/app/ui", tags=["app"])
def app_ui(request: Request) -> dict[str, Any]:
    """The window's UiState and its age; ``state`` is null when no window is connected."""
    ui = bridge(request).view()
    return {"state": ui["state"], "age_s": ui["age_s"], "connected": ui["connected"]}


@router.get("/app/gamepads", tags=["app"])
def app_gamepads(request: Request) -> dict[str, Any]:
    state = bridge(request).view()["state"] or {}
    return {"gamepads": state.get("gamepads") or []}


@router.get("/app/actions", tags=["app"])
def app_actions() -> dict[str, Any]:
    return {"actions": ACTIONS}


@router.post("/app/ui", tags=["app"], responses=ERRORS)
def app_command(request: Request, body: UiCommandBody) -> dict[str, Any]:
    """Run an interface action in the Relay window and wait up to 5 s for its result."""
    svc = service(request)
    who = caller(request)
    if who.operator:
        by = "operator"
    else:
        assert who.session is not None
        if not svc.settings.agents_app_control:
            raise err("app_control_disabled")
        by = who.session.name
    data = bridge(request).command(body.action, body.args, by)
    svc.events.emit(
        "app_command",
        None,
        session=None if who.session is None else who.session.session_id,
        data={"action": body.action, "args": body.args, "by": by},
    )
    return {"ok": True, "data": data}
