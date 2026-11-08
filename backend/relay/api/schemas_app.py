"""Models of the application-level interface (04 §3). The renderer owns UiState; unknown fields pass."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .schemas import _Model


class Gamepad(_Model):
    index: int
    id: str
    name: str | None = None
    mapping: str | None = None
    connected: bool = True
    vehicle: str | None = None
    input: Literal["gamepad"] = "gamepad"


class PossessionRef(_Model):
    vehicle: str
    input: str
    pad: int | None = None


class UiInspector(_Model):
    open: bool = False
    tab: str | None = None


class UiWindow(_Model):
    focused: bool = True
    width: int | None = None
    height: int | None = None
    fullscreen: bool = False


class UiRecord(_Model):
    session_id: int | None = None
    playing: bool = False
    t: float = 0.0


class UiState(_Model):
    """What the Relay window currently shows (04 §3.1)."""

    lang: Literal["en", "zh"] = "en"
    workspace: Literal["fly", "agents", "records", "lab"] = "fly"  # WP-3: lab
    selected: str | None = None
    view: Literal["overview", "chase", "top"] = "overview"
    inspector: UiInspector = Field(default_factory=UiInspector)
    dialog: str | None = None
    settings_section: str | None = None
    copilot_open: bool = False
    prefs: dict[str, Any] = Field(default_factory=dict)
    gamepads: list[Gamepad] = Field(default_factory=list)
    possession: list[PossessionRef] = Field(default_factory=list)
    window: UiWindow = Field(default_factory=UiWindow)
    record: UiRecord | None = None
    version: str | None = None


class UiCommandBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    action: str = Field(min_length=1, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)


class UiCommandMessage(_Model):
    """Server -> window over the operator WebSocket."""

    type: Literal["ui_command"]
    id: str
    action: str
    args: dict[str, Any]
    by: str
    operator: bool = False  # the person at the window issued it; otherwise ``by`` is a session name


class UiStateMessage(_Model):
    """Window -> server."""

    type: Literal["ui_state"]
    data: UiState


class UiResultMessage(_Model):
    """Window -> server, answering a ui_command."""

    type: Literal["ui_result"]
    id: str
    ok: bool
    error: str | None = None
    data: dict[str, Any] | None = None


EXPORTED = {
    "UiState": UiState,
    "UiCommandMessage": UiCommandMessage,
    "UiStateMessage": UiStateMessage,
    "UiResultMessage": UiResultMessage,
}
