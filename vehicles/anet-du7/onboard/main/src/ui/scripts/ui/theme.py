"""主题管理器 — 浅色/深色模式切换。"""

from __future__ import annotations

from typing import Dict

from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from qfluentwidgets import Theme, setTheme

from ui.constants import DARK_COLORS, LIGHT_COLORS


class ThemeManager(QObject):
    """单例主题管理器，供所有自定义 widget 订阅。"""

    changed = pyqtSignal(str)  # "light" | "dark"

    _instance: ThemeManager | None = None

    def __init__(self):
        if ThemeManager._instance is not None:
            raise RuntimeError("ThemeManager 是单例，请使用 ThemeManager.instance()")
        super().__init__()
        self._mode: str = "light"
        self._switching = False

    @classmethod
    def instance(cls) -> ThemeManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def is_dark(self) -> bool:
        return self._mode == "dark"

    def colors(self) -> Dict[str, object]:
        return DARK_COLORS if self._mode == "dark" else LIGHT_COLORS

    def color(self, key: str) -> str:
        return str(self.colors().get(key, "#000000"))

    def toggle(self):
        if self._switching:
            return
        self._switching = True
        self.apply("dark" if self._mode == "light" else "light")
        QTimer.singleShot(150, self._unlock_switch)

    def _unlock_switch(self):
        self._switching = False

    def apply(self, mode: str):
        if mode == self._mode:
            self._switching = False
            return
        self._mode = mode
        setTheme(Theme.DARK if mode == "dark" else Theme.LIGHT)
        # 延迟发射信号，先让 qfluentwidgets 完成内部主题切换，再刷新自定义控件
        QTimer.singleShot(80, lambda: self.changed.emit(mode))
