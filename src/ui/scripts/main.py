#!/usr/bin/env python3
"""
WS00 慧谷地面站 — 入口
"""

import sys

from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QApplication

from qfluentwidgets import Theme, setTheme, setThemeColor

from ui.theme import ThemeManager
from ui.main_window import MainWindow


def create_backend():
    """尝试加载真实后端，失败则报错退出（不再提供演示模式）。"""
    try:
        from px4_backend import Backend
        return Backend(), "真实后端"
    except ImportError as exc:
        raise SystemExit(
            "未检测到 px4_backend 模块。请确保 ROS2 与 MAVROS 环境已配置。\n"
            f"原始错误：{exc}"
        ) from exc
    except Exception as exc:
        raise SystemExit(
            f"后端初始化失败：{exc}\n请检查 ROS2 与 MAVROS 环境。"
        ) from exc


def main():
    backend, source_name = create_backend()

    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 10))

    # 默认浅色主题
    tm = ThemeManager.instance()
    tm.apply("light")
    setThemeColor("#4D8CFF")

    window = MainWindow(backend, source_name)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
